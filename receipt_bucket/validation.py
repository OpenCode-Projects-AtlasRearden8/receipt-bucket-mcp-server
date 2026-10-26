"""Input validation and sanitizing helpers.

Everything the agent sends is treated as untrusted input: strings are
cleaned of control characters, amounts are coerced to exact integer cents,
dates are calendar-checked, and bucket names are restricted to a safe file
name pattern so paths can never escape the data directory.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import date, datetime, timezone

from .errors import ValidationError

MAX_AMOUNT_CENTS = 999_999_999  # $9,999,999.99

_BUCKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_TAG_RE = re.compile(r"^[a-z0-9 _-]+$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

# Control characters, zero-width and bidi characters are removed outright.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\u200b\u200e\u200f\u202a-\u202e\ufeff]")


def now_iso_utc() -> str:
    """Current UTC time as ``2026-10-26T12:34:56.789Z``."""
    stamp = datetime.now(timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S") + ".%03dZ" % (stamp.microsecond // 1000)


def today_local() -> str:
    """Today's date in the server's local timezone as ``YYYY-MM-DD``."""
    return datetime.now().date().isoformat()


def new_receipt_id() -> str:
    """A fresh receipt id like ``rcpt-3f9c2b1a7d04``."""
    return "rcpt-" + uuid.uuid4().hex[:12]


def sanitize_text(value, *, field: str, max_len: int, allow_empty: bool = True) -> str:
    """Coerce untrusted input to a clean, single-line-trimmed string.

    Raises :class:`ValidationError` if the value is not a printable string.
    This is only a cleaning step, not a full defense; no value from agent
    input is ever used as a filesystem path.
    """
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    cleaned = _CONTROL_RE.sub(" ", value)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_len:
        raise ValidationError(
            f"{field} must be at most {max_len} characters (got {len(cleaned)})"
        )
    if not allow_empty and not cleaned:
        raise ValidationError(f"{field} must not be empty")
    return cleaned


def sanitize_filename(value, *, field: str, max_len: int = 200) -> str:
    """Normalize a *reference* to a photo file to a bare, safe file name.

    The photo filename is stored as a label only — this server never reads
    the file — but we still refuse anything that smells like a path, a
    Windows drive letter, or a traversal trick.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    cleaned = _CONTROL_RE.sub(" ", value.strip())
    cleaned = cleaned.replace("\\", "/")
    # Keep only the last path component, dropping drive prefixes like "C:".
    if re.match(r"^[A-Za-z]:", cleaned):
        cleaned = cleaned[2:]
    basename = cleaned.rsplit("/", 1)[-1].strip()
    if basename in ("", ".", ".."):
        raise ValidationError(
            f"{field} must be a plain file name like 'IMG_0042.jpg' (got {value!r})"
        )
    if len(basename) > max_len:
        raise ValidationError(
            f"{field} must be at most {max_len} characters (got {len(basename)})"
        )
    return basename


def parse_amount_to_cents(value, *, field: str = "amount") -> int:
    """Convert an amount (number, or a human string like ``$1,204.50``) to cents."""
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be a number")
    if isinstance(value, int):
        raw = float(value)
    elif isinstance(value, float):
        raw = value
        if not math.isfinite(raw):
            raise ValidationError(f"{field} must be a finite number")
    elif isinstance(value, str):
        text = value.strip().lstrip("$").replace(",", "")
        try:
            raw = float(text)
        except ValueError:
            raise ValidationError(f"{field} must be a number like 23.45") from None
        if not math.isfinite(raw):
            raise ValidationError(f"{field} must be a finite number")
    else:
        raise ValidationError(f"{field} must be a number like 23.45")
    cents = round(raw * 100)
    if cents <= 0:
        raise ValidationError(f"{field} must be a positive amount greater than zero")
    if cents > MAX_AMOUNT_CENTS:
        raise ValidationError(
            f"{field} is too large (max {(MAX_AMOUNT_CENTS) / 100.0:,.2f})"
        )
    return cents


def cents_to_float(cents: int) -> float:
    """Exact-ish cents back out as a float for JSON output (``2345 -> 23.45``)."""
    return cents / 100.0


def cents_to_str(cents: int) -> str:
    """Format integer cents as a plain decimal string (``-4530 -> "-45.30"``)."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def validate_bucket_name(name, *, field: str = "bucket") -> str:
    """Bucket names become file names: allow only letters, digits, ``-`` and ``_``."""
    if not isinstance(name, str) or not _BUCKET_RE.match(name):
        raise ValidationError(
            f"{field} must be 1-64 characters of letters, digits, '-' or '_' "
            f"and start with a letter or digit (got {name!r})"
        )
    return name


def validate_id(value, *, field: str = "receipt_id") -> str:
    """Receipt ids are short token-like strings only (never used as paths)."""
    cleaned = sanitize_text(value, field=field, max_len=64, allow_empty=False)
    if not _ID_RE.match(cleaned):
        raise ValidationError(f"{field} must be an id like 'rcpt-3f9c2b1a7d04'")
    return cleaned


def validate_date(value, *, field: str = "date") -> str:
    """Validate a strict ``YYYY-MM-DD`` calendar date."""
    cleaned = sanitize_text(value, field=field, max_len=32, allow_empty=False)
    if not _DATE_RE.match(cleaned):
        raise ValidationError(f"{field} must be an ISO date like 2026-10-26 (got {value!r})")
    try:
        date.fromisoformat(cleaned)
    except ValueError:
        raise ValidationError(
            f"{field} is not a real calendar date (got {value!r})"
        ) from None
    return cleaned


def validate_month(value, *, field: str = "month") -> str:
    """Validate a strict ``YYYY-MM`` month string."""
    cleaned = sanitize_text(value, field=field, max_len=16, allow_empty=False)
    if not _MONTH_RE.match(cleaned):
        raise ValidationError(f"{field} must look like 2026-09 (got {value!r})")
    year, _, mon = cleaned.partition("-")
    if not 1 <= int(mon) <= 12:
        raise ValidationError(f"{field} month must be between 01 and 12 (got {value!r})")
    return cleaned


def validate_year(value, *, field: str = "year") -> str:
    """Validate a 4-digit year, returned as a string."""
    cleaned = sanitize_text(value, field=field, max_len=4, allow_empty=False)
    if not re.match(r"^\d{4}$", cleaned) or int(cleaned) < 1 or int(cleaned) > 9999:
        raise ValidationError(f"{field} must be a 4-digit year like 2026")
    return cleaned


def validate_currency(value, default: str) -> str:
    """3-letter ISO-style currency code."""
    if value in (None, ""):
        return default
    cleaned = sanitize_text(value, field="currency", max_len=8, allow_empty=False).upper()
    if not _CURRENCY_RE.match(cleaned):
        raise ValidationError("currency must be a 3-letter code like USD")
    return cleaned


def normalize_category(value, default: str = "uncategorized") -> str:
    """Lowercase, kebab-ish, compact category tags like ``office_supplies``."""
    cleaned = sanitize_text(value, field="category", max_len=40, allow_empty=True)
    if not cleaned:
        return default
    cleaned = cleaned.lower().replace(" ", "_")
    if not _TAG_RE.match(cleaned):
        raise ValidationError(
            "category may only use letters, digits, '_' or '-' "
            f"(got {value!r}; it becomes lowercase kebab-case)"
        )
    return cleaned


def normalize_payment_method(value) -> str:
    """Lowercase snake_case payment method like ``cash``, ``credit_card``."""
    if value in (None, ""):
        return ""
    cleaned = sanitize_text(value, field="payment_method", max_len=40, allow_empty=True)
    cleaned = cleaned.lower().replace(" ", "_")
    if not cleaned:
        return ""
    if not _TAG_RE.match(cleaned):
        raise ValidationError(
            "payment_method may only use letters, digits, '_' or '-' "
            f"(got {value!r})"
        )
    return cleaned


def normalize_tags(value, *, max_tags: int = 12, max_tag_len: int = 24) -> list:
    """Normalize a tag list: lowercase, deduplicated, sorted, charset-restricted."""
    if value is None:
        return []
    if isinstance(value, str) and not value.strip():
        return []
    if not isinstance(value, (list, tuple)):
        raise ValidationError("tags must be a list of short strings")
    tags = []
    for raw in value:
        cleaned = sanitize_text(raw, field="tag", max_len=max_tag_len, allow_empty=True)
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if not _TAG_RE.match(lowered):
            raise ValidationError(
                f"tag {cleaned!r} may only use letters, digits, spaces, '_' or '-'"
            )
        tags.append(lowered)
    if len(set(tags)) != len(tags):
        tags = sorted(set(tags))
    else:
        tags = sorted(tags)
    if len(tags) > max_tags:
        raise ValidationError(f"a receipt may carry at most {max_tags} tags (got {len(tags)})")
    return tags


def coerce_bool(value, *, field: str, default) -> bool:
    """Accept booleans or the strings ``true``/``false``/``yes``/``no``/1/0."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1", "on"):
            return True
        if lowered in ("false", "no", "0", "off"):
            return False
    raise ValidationError(f"{field} must be a boolean (true/false)")


def coerce_int(value, *, field: str, default: int, minimum: int, maximum: int) -> int:
    """Accept ints or numeric strings, clamped into ``minimum..maximum``."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be an integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    else:
        raise ValidationError(f"{field} must be an integer")
    if number < minimum:
        raise ValidationError(f"{field} must be at least {minimum}")
    if number > maximum:
        raise ValidationError(f"{field} must be at most {maximum}")
    return number
