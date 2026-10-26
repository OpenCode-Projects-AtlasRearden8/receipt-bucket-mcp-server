"""Path-safe JSON storage with automatic rolling backups.

The bucket is one JSON file inside a configurable data directory. All rules
that keep paths safe live here:

* bucket names are strictly validated before they ever touch the filesystem;
* the bucket file is resolved and rejected if it would escape the data dir;
* symlinks that point outside the data directory are refused;
* writes are atomic (temp file + ``os.replace``) with ``0o600`` permissions;
* a timestamped backup is taken before every save (newest N kept).

The format is intentionally boring so users can inspect or restore a file
themselves:

.. code-block:: json

    {
      "schema": 1,
      "name": "receipts",
      "updated_at": "2026-10-26T12:00:00.000Z",
      "receipt_count": 2,
      "receipts": [ { ... } ]
    }
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .errors import CorruptBucketError, PathSafetyError, StorageError, ValidationError
from .validation import validate_bucket_name

SCHEMA_VERSION = 1
DEFAULT_KEEP_BACKUPS = 10
MODE_600 = 0o600


class ReceiptStore:
    """One bucket: a validated JSON file plus a rolling backup directory."""

    def __init__(self, data_dir, bucket: str = "receipts", *, currency: str = "USD",
                 keep_backups: int = DEFAULT_KEEP_BACKUPS) -> None:
        self.bucket = validate_bucket_name(bucket)
        if keep_backups < 0 or keep_backups > 100:
            raise ValidationError("keep_backups must be between 0 and 100")
        self.keep_backups = keep_backups
        self.currency = currency

        base = Path(data_dir).expanduser()
        self.data_dir = base if base.is_absolute() else Path.cwd() / base
        try:
            self.data_dir = self.data_dir.resolve(strict=False)
        except OSError as exc:  # pragma: no cover - unreachable on normal FSes
            raise PathSafetyError(f"cannot resolve data directory: {exc}") from None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PathSafetyError(f"cannot create data directory: {exc}") from None

        self.path = self.data_dir / f"{self.bucket}.json"
        self.backup_dir = self.data_dir / "backups"

        self.receipts = self._load()

    # ------------------------------------------------------------------ load

    def _load(self) -> list:
        """Load receipts from disk; an absent file is an empty bucket."""
        self._check_confined(self.path)
        if not self.path.exists():
            return []
        try:
            raw_text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CorruptBucketError(f"cannot read bucket file {self.path}: {exc}") from None
        if not raw_text.strip():
            return []
        try:
            loaded = json.loads(raw_text, parse_constant=self._reject_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            hint = self._newest_backup_hint()
            raise CorruptBucketError(
                f"bucket file {self.path} is not valid JSON ({exc})."
                + (f" The newest backup might be restorable: {hint}" if hint else " No backups exist yet.")
            ) from None
        receipts = loaded.get("receipts", []) if isinstance(loaded, dict) else loaded
        if isinstance(receipts, list):
            receipts = self._filter_valid_receipts(receipts)
        return receipts

    @staticmethod
    def _reject_constant(name: str):
        """Refuse NaN/Infinity on load: they are not real JSON and can't compute."""
        raise ValueError(f"not valid JSON: {name}")

    @staticmethod
    def _filter_valid_receipts(receipts: list) -> list:
        """Keep dict-shaped receipts; belt-and-braces check for hand-edited files."""
        from .receipts import sanitize_stored_receipt

        ok, warned = [], 0
        for raw in receipts:
            if not isinstance(raw, dict):
                warned += 1
                continue
            try:
                ok.append(sanitize_stored_receipt(raw))
            except (ValidationError, ValueError):
                warned += 1
        if warned:
            print(
                f"receipt-bucket: warning: skipped {warned} malformed receipt(s) in the bucket file",
                file=sys.stderr,
            )
        return ok

    def _newest_backup_hint(self) -> str:
        backups = self.backup_files()
        return str(backups[-1]) if backups else ""

    # ------------------------------------------------------------------ save

    def save_receipts(self, receipts: list) -> None:
        """Atomically persist ``receipts`` (the new list) and back up the old file."""
        self._check_confined(self.path)
        self._validate_receipts_list(receipts)
        if self.path.exists() and self.path.stat().st_size > 0:
            self._backup()
        payload = {
            "schema": SCHEMA_VERSION,
            "name": self.bucket,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            + ".%03dZ" % (datetime.now(timezone.utc).microsecond // 1000),
            "receipt_count": len(receipts),
            "receipts": receipts,
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        tmp_path = self.data_dir / f"{self.bucket}.json.tmp-{os.getpid()}-{random.randint(0x1000, 0xFFFF):04x}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, MODE_600)
            os.replace(tmp_path, self.path)
        except OSError as exc:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise StorageError(f"cannot write bucket file {self.path}: {exc}") from None
        finally:
            for leftover in self.data_dir.glob(f"{self.bucket}.json.tmp-*"):
                try:  # pragma: no cover - best-effort cleanup
                    leftover.unlink()
                except OSError:
                    pass
        self.receipts = list(receipts)

    @staticmethod
    def _validate_receipts_list(receipts: list) -> None:
        from .receipts import sanitize_stored_receipt

        for raw in receipts:
            sanitize_stored_receipt(raw)

    def _backup(self) -> None:
        """Timestamped copy of the current file; prune to the newest N."""
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc)
            dest = self.backup_dir / (
                f"{self.bucket}@@{stamp.strftime('%Y%m%d-%H%M%S')}"
                f"-{stamp.microsecond:06d}-{random.randint(0x1000, 0xFFFF):04x}.json"
            )
            shutil.copyfile(self.path, dest)
            os.chmod(dest, MODE_600)
            self._prune()
        except OSError as exc:
            raise PathSafetyError(f"auto-backup failed: {exc}") from None


    def _prune(self) -> None:
        pattern = f"{self.bucket}@@*.json"
        backups = sorted(self.backup_dir.glob(pattern))
        for stale in backups[: max(0, len(backups) - self.keep_backups)]:
            try:
                stale.unlink()
            except OSError:  # pragma: no cover - best effort
                pass

    def backup_files(self) -> list:
        """Existing backup files for this bucket, oldest first."""
        if not self.backup_dir.exists():
            return []
        backups = list(self.backup_dir.glob(f"{self.bucket}@@*.json"))
        return sorted(backups, key=lambda p: (p.stat().st_mtime_ns, p.name))

    # -------------------------------------------------------------- matching

    def _check_confined(self, candidate: Path) -> None:
        """Refuse anything that resolves outside the data directory.

        Also refuses writing through a multi-level path (the bucket file is
        always ``data_dir/<name>.json``, nothing deeper).
        """
        if candidate.parent != self.data_dir:
            raise PathSafetyError(
                f"refusing path {candidate} — bucket files must live directly in "
                f"the data directory ({self.data_dir})"
            )
        resolved = candidate.resolve(strict=False)
        if resolved != candidate:
            if resolved.parent != self.data_dir:
                raise PathSafetyError(
                    f"refusing path {candidate} — it resolves to {resolved}, which is "
                    "outside the data directory (symlink escape)"
                )
