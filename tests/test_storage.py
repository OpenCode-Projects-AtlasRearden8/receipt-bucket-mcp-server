"""Path-safety, backup, and persistence tests for the storage layer."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from receipt_bucket.errors import CorruptBucketError, PathSafetyError, ValidationError
from receipt_bucket.receipts import build_receipt, currency_tally
from receipt_bucket.storage import ReceiptStore

CAN_SYMLINK = hasattr(os, "symlink")


def make_store(d, **kwargs):
    return ReceiptStore(d, kwargs.pop("bucket", "receipts"), **kwargs)


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_save_load_roundtrip(self):
        store = make_store(self.dir)
        receipt = build_receipt(
            {"amount": 23.45, "merchant": "Corner Cafe", "tags": ["coffee"]}, "USD"
        )
        store.save_receipts([receipt])
        again = make_store(self.dir)
        self.assertEqual(len(again.receipts), 1)
        self.assertEqual(again.receipts[0]["amount"], 23.45)
        self.assertEqual(again.receipts[0]["merchant"], "Corner Cafe")

    def test_file_shape_and_permissions(self):
        store = make_store(self.dir)
        store.save_receipts([build_receipt({"amount": 1.0, "merchant": "M"}, "USD")])
        payload = json.loads(store.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["name"], "receipts")
        self.assertEqual(payload["receipt_count"], 1)
        mode = store.path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        leftovers = list(self.dir.glob("*.tmp-*"))
        self.assertEqual(leftovers, [])

    def test_absent_bucket_is_empty(self):
        store = make_store(self.dir, bucket="fresh")
        self.assertEqual(store.receipts, [])
        self.assertFalse(store.path.exists())


class TestPathSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_traversal_bucket_name_rejected(self):
        for bad in ("../evil", "a/b", "sub/../x", ".", "..", "con\x00"):
            with self.assertRaises(ValidationError):
                make_store(self.dir, bucket=bad)

    def test_bucket_never_seen_as_path_component(self):
        with self.assertRaises(ValidationError):
            make_store(self.dir, bucket="good/../bad")

    def test_data_dir_is_resolved_absolute(self):
        store = make_store(str(self.dir / ".." / self.dir.name))
        self.assertTrue(store.data_dir.is_absolute())
        self.assertTrue(store.path.parent.samefile(store.data_dir))

    @unittest.skipUnless(CAN_SYMLINK, "symlinks unavailable")
    def test_symlinked_bucket_file_escaping_is_refused(self):
        # The bucket file is a symlink pointing OUTSIDE the data directory.
        outer = tempfile.TemporaryDirectory()
        try:
            outside = Path(outer.name) / "outside.json"
            outside.write_text('{"schema": 1, "receipts": []}', encoding="utf-8")
            data_dir = self.dir / "data"
            data_dir.mkdir()
            os.symlink(outside, data_dir / "evil.json")
            with self.assertRaises(PathSafetyError):
                make_store(data_dir, bucket="evil")
        finally:
            outer.cleanup()

    @unittest.skipUnless(CAN_SYMLINK, "symlinks unavailable")
    def test_symlink_to_nested_target_is_refused(self):
        # A bucket file must resolve to exactly data_dir/<name>.json — even a
        # symlink that stays inside the data dir but adds nesting is refused.
        (self.dir / "sub").mkdir()
        target = self.dir / "sub" / "ok.json"
        target.write_text('{"schema": 1, "receipts": []}', encoding="utf-8")
        os.symlink(target, self.dir / "needle.json")
        with self.assertRaises(PathSafetyError):
            make_store(self.dir, bucket="needle")


class TestBackupsAndRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_taken_before_every_write(self):
        store = make_store(self.dir, bucket="backups-demo", keep_backups=10)
        store.save_receipts([])  # first write: no backup yet
        self.assertEqual(len(store.backup_files()), 0)
        receipt = build_receipt({"amount": 5.0, "merchant": "A"}, "USD")
        store.save_receipts([receipt])
        self.assertEqual(len(store.backup_files()), 1)
        backup = store.backup_files()[-1]
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["receipts"], [])
        store.save_receipts([receipt, receipt])
        self.assertEqual(len(store.backup_files()), 2)

    def test_backup_rotation_keeps_newest(self):
        store = make_store(self.dir, bucket="rotate", keep_backups=2)
        for n in range(6):
            receipt = build_receipt({"amount": n + 1.0, "merchant": "M"}, "USD")
            store.save_receipts([receipt] * 1)
        files = store.backup_files()
        self.assertEqual(len(files), 2)
        # The newest backup holds the state before the final write (amount 5.0).
        newest_total = sum(
            round(r["amount"] * 100) for r in json.loads(files[-1].read_text(encoding="utf-8"))["receipts"]
        )
        self.assertEqual(newest_total, 500)

    def test_backup_names_unique_and_safe(self):
        store = make_store(self.dir, bucket="names")
        store.save_receipts([])
        store.save_receipts([])
        names = [p.name for p in store.backup_files()]
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith("names@@"))
        # Bucket "names" prunes must not touch a bucket "names-2" backup either:
        store2 = make_store(self.dir, bucket="names-2")
        store2.save_receipts([])
        store2.save_receipts([])
        self.assertEqual(len(store.backup_files()), 1)
        self.assertEqual(len(store2.backup_files()), 1)

    def test_corrupt_file_reports_newest_backup(self):
        store = make_store(self.dir, bucket="corrupt")
        store.save_receipts([])  # first write creates the file
        store.save_receipts([build_receipt({"amount": 7.7, "merchant": "A"}, "USD")])
        self.assertTrue(store.backup_files())
        store.path.write_text("{not json at all", encoding="utf-8")
        with self.assertRaises(CorruptBucketError) as caught:
            make_store(self.dir, bucket="corrupt")
        message = str(caught.exception)
        self.assertIn("backup", message.lower())
        self.assertIn(".json", message)

    def test_nan_in_file_refused(self):
        path = self.dir / "nan-bucket.json"
        # json.dumps happily emits bare NaN tokens; our loader must refuse them.
        nan_json = json.dumps({"schema": 1, "name": "nan-bucket", "receipts": [{"amount": float("nan")}]})
        path.write_text(nan_json, encoding="utf-8")
        with self.assertRaises(CorruptBucketError):
            make_store(self.dir, bucket="nan-bucket")

    def test_sample_bucket_shipped_and_parses(self):
        repo_data = Path(__file__).resolve().parent.parent / "data"
        store = make_store(repo_data, bucket="sample")
        self.assertGreaterEqual(len(store.receipts), 10)
        self.assertEqual(store.bucket, "sample")
        tally = currency_tally(store.receipts)
        self.assertEqual(list(tally), ["USD"])


if __name__ == "__main__":
    unittest.main()
