"""Receipt domain logic: building, updating, filtering, and summarizing.

Money handling note: amounts are converted to *integer cents* for every
calculation so totals are bookkeeping-exact (no floating-point drift).
"""
from __future__ import annotations

from .errors import ReceiptNotFoundError, ValidationError
from .validation import (
    cents_to_float,
    new_receipt_id,
    normalize_category,
    normalize_payment_method,
    normalize_tags,
    now_iso_utc,
    parse_amount_to_cents,
    sanitize_filename,
    sanitize_text,
    today_local,
    validate_date,
    validate_currency,
    validate_id,
)

MAX_TAGS_PER_RECEIPT = 12
MAX_NOTE_LEN = 500
MAX_MERCHANT_LEN = 120
MAX_PHOTO_LEN = 200

SEARCH_SORTS = ("date_desc", "date_asc", "amount_desc", "amount_asc", "merchant")


# --------------------------------------------------------------------------
# Build / update / locate
# --------------------------------------------------------------------------

def build_receipt(arguments: dict, default_currency: str) -> dict:
    """Validate ``add_receipt`` arguments and build a clean receipt record."""
    if not isinstance(arguments, dict):
        raise ValidationError("arguments must be an object")
    amount = arguments.get("amount")
    if amount is None:
        raise ValidationError("amount is required (a positive number like 23.45)")
    cents = parse_amount_to_cents(amount)
    merchant = sanitize_text(
        arguments.get("merchant"), field="merchant", max_len=MAX_MERCHANT_LEN, allow_empty=False
    )
    currency = validate_currency(arguments.get("currency"), default_currency)
    when = arguments.get("date")
    note = sanitize_text(arguments.get("note"), field="note", max_len=MAX_NOTE_LEN)
    photo = arguments.get("receipt_photo")
    photo = sanitize_filename(photo, field="receipt_photo", max_len=MAX_PHOTO_LEN) if photo else ""
    stamp = now_iso_utc()
    return {
        "id": new_receipt_id(),
        "amount": cents_to_float(cents),
        "currency": currency,
        "merchant": merchant,
        "category": normalize_category(arguments.get("category")),
        "date": validate_date(when) if when not in (None, "") else today_local(),
        "payment_method": normalize_payment_method(arguments.get("payment_method")),
        "receipt_photo": photo,
        "note": note,
        "tags": normalize_tags(
            arguments.get("tags"), max_tags=MAX_TAGS_PER_RECEIPT
        ),
        "created_at": stamp,
        "updated_at": stamp,
    }


def locate(receipts: list, receipt_id: str) -> dict:
    """Return the receipt with this id or raise ``ReceiptNotFoundError``."""
    receipt_id = validate_id(receipt_id)
    for receipt in receipts:
        if receipt.get("id") == receipt_id:
            return receipt
    raise ReceiptNotFoundError(
        f"no receipt with id '{receipt_id}' in the bucket (ids look like 'rcpt-...'; "
        "use search_receipts to find one)"
    )


def retitle_receipt(receipt: dict, arguments: dict, *, allow_category: bool = True) -> dict:
    """Apply ``tag_receipt`` edits to a copy of ``receipt`` and return it."""
    updates = dict(receipt)
    set_category = arguments.get("set_category")
    if set_category is not None:
        if not allow_category:
            raise ValidationError("set_category is not supported by this tool")
        updates["category"] = normalize_category(set_category)
    add_tags = normalize_tags(arguments.get("add_tags"), max_tags=MAX_TAGS_PER_RECEIPT)
    remove_tags = normalize_tags(arguments.get("remove_tags"), max_tags=MAX_TAGS_PER_RECEIPT)
    merged = sorted((set(updates.get("tags", [])) | set(add_tags)) - set(remove_tags))
    if len(merged) > MAX_TAGS_PER_RECEIPT:
        raise ValidationError(
            f"a receipt may carry at most {MAX_TAGS_PER_RECEIPT} tags "
            f"(the edit would produce {len(merged)})"
        )
    updates["tags"] = merged
    if updates == receipt:
        raise ValidationError(
            "nothing to change: pass add_tags, remove_tags, or set_category"
        )
    updates["updated_at"] = now_iso_utc()
    return updates


def sanitize_stored_receipt(raw: dict) -> dict:
    """Re-validate a receipt loaded (or hand-edited) from disk."""
    if not isinstance(raw, dict):
        raise ValidationError("receipt must be an object")
    receipt_id = raw.get("id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise ValidationError("receipt id is missing")
    merchant = sanitize_text(raw.get("merchant"), field="merchant", max_len=MAX_MERCHANT_LEN, allow_empty=False)
    cents = parse_amount_to_cents(raw.get("amount"))
    when = raw.get("date")
    note = sanitize_text(raw.get("note"), field="note", max_len=MAX_NOTE_LEN)
    photo_raw = raw.get("receipt_photo") or ""
    photo = sanitize_filename(photo_raw, field="receipt_photo", max_len=MAX_PHOTO_LEN) if photo_raw else ""
    created = sanitize_text(raw.get("created_at", ""), field="created_at", max_len=40)
    updated = sanitize_text(raw.get("updated_at", ""), field="updated_at", max_len=40) or created
    try:
        tags = normalize_tags(raw.get("tags"))
    except ValidationError:
        tags = []  # tolerate odd hand-edited values rather than lose the receipt
    return {
        "id": receipt_id,
        "date": validate_date(when) if when not in (None, "") else today_local(),
        "amount": cents_to_float(cents),
        "currency": normalize_currency_or_default(raw.get("currency"), raw),
        "merchant": merchant,
        "category": normalize_category(
            raw.get("category") if raw.get("category") not in (None, "") else "uncategorized"
        ),
        "tags": tags,
        "payment_method": normalize_payment_method(raw.get("payment_method")),
        "receipt_photo": photo,
        "note": note,
        "created_at": created,
        "updated_at": updated,
    }


def normalize_currency_or_default(value, raw: dict) -> str:
    """Currency used when the stored record is missing one (default USD)."""
    return validate_currency(value, default="USD")


# --------------------------------------------------------------------------
# Search filters
# --------------------------------------------------------------------------

def normalize_search_filters(arguments: dict) -> dict:
    """Validate shared filter arguments (used by search, summary, CSV export)."""
    args = arguments if isinstance(arguments, dict) else {}
    lowered_category = args.get("category")
    return {
        "merchant": _lower_field(args.get("merchant"), field="merchant", max_len=MAX_MERCHANT_LEN) or None,
        "category": (normalize_category(lowered_category) if lowered_category not in (None, "") else None),
        "tags": normalize_tags(args.get("tags")),
        "tags_any": normalize_tags(args.get("tags_any")),
        "payment_method": normalize_payment_method(args.get("payment_method")) or None,
        "text": _lower_field(args.get("text"), field="text", max_len=120) or None,
        "from_date": validate_date(args["from_date"]) if args.get("from_date") else None,
        "to_date": validate_date(args["to_date"]) if args.get("to_date") else None,
        "min_amount": parse_amount_to_cents(args["min_amount"]) if args.get("min_amount") is not None else None,
        "max_amount": parse_amount_to_cents(args["max_amount"]) if args.get("max_amount") is not None else None,
    }


def _lower_field(value, *, field: str, max_len: int) -> str:
    cleaned = sanitize_text(value, field=field, max_len=max_len)
    return cleaned.lower()


def match_filters(receipt: dict, filters: dict) -> bool:
    """True when the receipt satisfies every filter that was provided."""
    if filters.get("merchant") and filters["merchant"] not in receipt.get("merchant", "").lower():
        return False
    if filters.get("category") is not None and receipt.get("category") != filters["category"]:
        return False
    if filters.get("tags"):
        receipt_tags = set(receipt.get("tags") or [])
        if not set(filters["tags"]).issubset(receipt_tags):
            return False
    if filters.get("tags_any"):
        receipt_tags = set(receipt.get("tags") or [])
        if not (set(filters["tags_any"]) & receipt_tags):
            return False
    if filters.get("payment_method") and receipt.get("payment_method") != filters["payment_method"]:
        return False
    haystack = " ".join(
        (
            receipt.get("merchant", ""),
            receipt.get("note", ""),
            receipt.get("category", ""),
            " ".join(receipt.get("tags") or []),
        )
    ).lower()
    if filters.get("text") and filters["text"] not in haystack:
        return False
    if filters.get("from_date") and receipt.get("date", "") < filters["from_date"]:
        return False
    if filters.get("to_date") and receipt.get("date", "") > filters["to_date"]:
        return False
    amount_cents = round(receipt.get("amount", 0) * 100)
    if filters.get("min_amount") is not None and amount_cents < int(filters["min_amount"]):
        return False
    if filters.get("max_amount") is not None and amount_cents > int(filters["max_amount"]):
        return False
    return True


def sort_receipts(receipts: list, sort: str = "date_desc") -> list:
    """Deterministic ordering with id as an always-stable tie-break."""
    def key(receipt):
        created = receipt.get("created_at", "")
        return (
            receipt.get("date", ""),
            created,
            receipt.get("id", ""),
        )

    if sort == "date_asc":
        return sorted(receipts, key=key)
    if sort == "merchant":
        return sorted(
            receipts,
            key=lambda r: (r.get("merchant", "").lower(), r.get("date", ""), r.get("id", "")),
        )
    if sort in ("amount_asc", "amount_desc"):
        ordered = sorted(
            receipts,
            key=lambda r: (
                round(r.get("amount", 0) * 100),
                r.get("date", ""),
                r.get("id", ""),
            ),
        )
        return ordered if sort == "amount_asc" else list(reversed(ordered))
    return list(reversed(sorted(receipts, key=key)))


# --------------------------------------------------------------------------
# Money totals (integer cents all the way)
# --------------------------------------------------------------------------

def currency_tally(receipts) -> dict:
    """Map ``currency -> total cents`` over the given receipts."""
    tally: dict = {}
    for receipt in receipts:
        cents = round(receipt.get("amount", 0) * 100)
        code = receipt.get("currency", "USD")
        tally[code] = tally.get(code, 0) + cents
    return tally


def present_totals(tally: dict) -> dict:
    """One-currency tallies display as a number; many-currency as a dict."""
    codes = sorted(tally)
    if not codes:
        return {}
    if len(codes) == 1:
        return {codes[0]: cents_to_float(tally[codes[0]])}
    return {code: cents_to_float(tally[code]) for code in codes}


def group_by_month(receipts) -> list:
    """Return ``[(month, [receipt, ...]), ...]`` sorted ascending; drops blanks."""
    groups: dict = {}
    for receipt in receipts:
        month = (receipt.get("date") or "")[:7]
        if len(month) == 7 and month[4] == "-":
            groups.setdefault(month, []).append(receipt)
    return [(month, groups[month]) for month in sorted(groups)]


def summarize_one_month(items, month: str) -> dict:
    """Summary card for one calendar month."""
    tally = currency_tally(items)
    by_category: dict = {}
    by_method: dict = {}
    for receipt in items:
        cents = round(receipt.get("amount", 0) * 100)
        by_category[receipt.get("category", "uncategorized")] = (
            by_category.get(receipt.get("category", "uncategorized"), 0) + cents
        )
        method = receipt.get("payment_method") or "unspecified"
        by_method[method] = by_method.get(method, 0) + cents
    largest = max(
        items,
        key=lambda r: (round(r.get("amount", 0) * 100), r.get("date", "")),
    )
    if len(codes := sorted(tally)) == 1:
        total = cents_to_float(tally[codes[0]])
        currencies = None
    else:
        total = present_totals(tally)
        currencies = codes
    card = {
        "month": month,
        "receipt_count": len(items),
        "total": total,
        "by_category": {
            name: cents_to_float(cents)
            for name, cents in sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))
        },
        "by_payment_method": {
            name: cents_to_float(cents)
            for name, cents in sorted(by_method.items(), key=lambda kv: (-kv[1], kv[0]))
        },
        "average": cents_to_float(round(sum(tally.values()) / len(items))),
        "largest": {
            "id": largest.get("id"),
            "date": largest.get("date"),
            "merchant": largest.get("merchant"),
            "amount": largest.get("amount"),
        },
    }
    if currencies:
        card["currencies"] = currencies
    return card
