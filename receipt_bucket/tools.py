"""MCP tool definitions and their handlers.

Each handler takes ``(store, arguments)`` and returns a JSON-ready dict.
Handlers never mutate ``store.receipts`` until a write has succeeded; they
build the new list, persist it, then publish it to the store. This keeps a
failed save from leaving memory and disk disagreeing.

The server presents tool results as pretty-printed JSON text — friendly for
both machines and humans reading the transcript.
"""
from __future__ import annotations

import json

from . import __version__
from .errors import ValidationError
from .ledger_export import COLUMNS, export_csv_text
from .receipts import (
    build_receipt,
    currency_tally,
    group_by_month,
    locate,
    match_filters,
    normalize_search_filters,
    present_totals,
    retitle_receipt,
    sort_receipts,
    summarize_one_month,
)
from .validation import (
    MAX_AMOUNT_CENTS,
    cents_to_float,
    coerce_bool,
    coerce_int,
    sanitize_text,
    validate_month,
    validate_year,
)


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

def add_receipt(store, arguments: dict) -> dict:
    receipt = build_receipt(arguments, store.currency)
    new_list = list(store.receipts) + [receipt]
    store.save_receipts(new_list)
    tally = currency_tally(new_list)
    mine = tally.get(receipt["currency"], 0)
    return {
        "added": receipt,
        "bucket": store.bucket,
        "storage_file": str(store.path),
        "running_total_for_currency": cents_to_float(mine),
    }


def search_receipts(store, arguments: dict) -> dict:
    filters = normalize_search_filters(arguments)
    sort = arguments.get("sort") if isinstance(arguments, dict) else None
    sort = sort if sort in ("date_desc", "date_asc", "amount_desc", "amount_asc", "merchant") else "date_desc"
    limit = coerce_int(arguments.get("limit") if isinstance(arguments, dict) else None,
                       field="limit", default=50, minimum=1, maximum=200)
    matches = [r for r in store.receipts if match_filters(r, filters)]
    ordered = sort_receipts(matches, sort)
    return {
        "bucket": store.bucket,
        "filters": filters,
        "sort": sort,
        "match_count": len(matches),
        "returned": min(limit, len(ordered)),
        "matching_total": present_totals(currency_tally(matches)),
        "receipts": ordered[:limit],
    }


def tag_receipt(store, arguments: dict) -> dict:
    receipt = locate(store.receipts, arguments.get("receipt_id"))
    updates = retitle_receipt(receipt, arguments)
    new_list = [updates if r is receipt or r.get("id") == receipt["id"] else r for r in store.receipts]
    store.save_receipts(new_list)
    return {
        "updated": updates,
        "bucket": store.bucket,
        "storage_file": str(store.path),
    }


def delete_receipt(store, arguments: dict) -> dict:
    receipt = locate(store.receipts, arguments.get("receipt_id"))
    expected = arguments.get("expected_merchant")
    if expected:
        expected_clean = sanitize_text(expected, field="expected_merchant", max_len=120, allow_empty=True).lower()
        if expected_clean and expected_clean != receipt.get("merchant", "").lower():
            raise ValidationError(
                f"expected_merchant {expected_clean!r} does not match receipt "
                f"merchant {receipt.get('merchant', '').lower()!r} — refusing to delete"
            )
    new_list = [r for r in store.receipts if r.get("id") != receipt["id"]]
    store.save_receipts(new_list)
    return {
        "deleted": receipt,
        "bucket": store.bucket,
        "remaining_count": len(new_list),
        "note": "A timestamped backup of the bucket was saved before this deletion.",
    }


def monthly_summary(store, arguments: dict) -> dict:
    args = arguments if isinstance(arguments, dict) else {}
    filters = normalize_search_filters(args)
    month = args.get("month")
    year = args.get("year")
    if month and year:
        raise ValidationError("pass either month (YYYY-MM) or year (YYYY), not both")
    month = validate_month(month) if month else None
    year = validate_year(year) if year else None

    matches = [r for r in store.receipts if match_filters(r, filters)]
    if month is not None:
        matches = [r for r in matches if r.get("date", "").startswith(month + "-")]
    elif year is not None:
        matches = [r for r in matches if r.get("date", "").startswith(year + "-")]

    months = [summarize_one_month(items, m) for m, items in group_by_month(matches)]
    first = min((r.get("date", "") for r in matches if r.get("date")), default=None)
    last = max((r.get("date", "") for r in matches if r.get("date")), default=None)
    period = (
        {"month": month} if month is not None
        else {"year": year} if year is not None
        else {"all_time": True}
    )
    return {
        "bucket": store.bucket,
        "period": period,
        "filters": filters,
        "overall": {
            "receipt_count": len(matches),
            "total": present_totals(currency_tally(matches)),
            "first_date": first,
            "last_date": last,
        },
        "months": months,
    }


def export_csv(store, arguments: dict) -> dict:
    args = arguments if isinstance(arguments, dict) else {}
    filters = normalize_search_filters(args)
    month = args.get("month")
    year = args.get("year")
    if month and year:
        raise ValidationError("pass either month (YYYY-MM) or year (YYYY), not both")
    month = validate_month(month) if month else None
    year = validate_year(year) if year else None
    delimiter = args.get("delimiter") if args.get("delimiter") in (",", ";", "tab") else ","
    amount_sign = args.get("amount_sign") if args.get("amount_sign") in ("negative", "positive") else "negative"
    include_header = coerce_bool(args.get("include_header"), field="include_header", default=True)
    include_total_row = coerce_bool(args.get("include_total_row"), field="include_total_row", default=False)

    matches = [r for r in store.receipts if match_filters(r, filters)]
    if month is not None:
        matches = [r for r in matches if r.get("date", "").startswith(month + "-")]
    elif year is not None:
        matches = [r for r in matches if r.get("date", "").startswith(year + "-")]
    ordered = sort_receipts(matches, "date_asc")
    csv_text = export_csv_text(
        ordered,
        delimiter=delimiter,
        include_header=include_header,
        include_total_row=include_total_row,
        amount_sign=amount_sign,
    )
    if month is not None:
        period = {"month": month}
    elif year is not None:
        period = {"year": year}
    else:
        period = {"all_time": True}
    return {
        "csv": csv_text,
        "columns": COLUMNS,
        "row_count": len(ordered),
        "total": present_totals(currency_tally(ordered)),
        "delimiter": delimiter,
        "amount_sign": amount_sign,
        "period": period,
        "import_note": (
            "Copy the csv value into a file (e.g. receipts-2026-09.csv) and import it into "
            "cashflow-simple-ledger with column order: date,description,amount,currency,flow,"
            "category,payment_method,receipt,tags,notes,id. Amounts are money-out; with "
            "amount_sign=negative they are negative numbers. See README 'Importing' for details."
        ),
    }


def bucket_info(store, arguments: dict) -> dict:  # noqa: ARG001 (no args by design)
    tally = currency_tally(store.receipts)
    dates = sorted(r.get("date", "") for r in store.receipts if r.get("date"))
    backups = store.backup_files()
    return {
        "bucket": store.bucket,
        "data_dir": str(store.data_dir),
        "storage_file": str(store.path),
        "file_size_bytes": store.path.stat().st_size if store.path.exists() else 0,
        "receipt_count": len(store.receipts),
        "total_all_time": present_totals(tally),
        "currencies_seen": sorted(tally),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "backups": {
            "directory": str(store.backup_dir),
            "count": len(backups),
            "keep_policy": f"newest {store.keep_backups} per bucket, taken before every write",
            "oldest": str(backups[0]) if backups else None,
            "newest": str(backups[-1]) if backups else None,
        },
        "server_version": __version__,
    }


HANDLERS = {
    "add_receipt": add_receipt,
    "search_receipts": search_receipts,
    "tag_receipt": tag_receipt,
    "delete_receipt": delete_receipt,
    "monthly_summary": monthly_summary,
    "export_csv": export_csv,
    "bucket_info": bucket_info,
}


# --------------------------------------------------------------------------
# Tool declarations (JSON Schema input descriptions for MCP clients)
# --------------------------------------------------------------------------

def _money(desc: str) -> dict:
    return {
        "type": "number",
        "minimum": 0.01,
        "maximum": MAX_AMOUNT_CENTS / 100.0,
        "description": desc,
    }


def _date_prop() -> dict:
    return {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}


def _tags_prop() -> dict:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": "Short lowercase labels (letters, digits, space, '_', '-') for grouping receipts.",
    }


_SHARED_FILTER_PROPS = {
    "merchant": {"type": "string", "maxLength": 120, "description": "Case-insensitive substring match on merchant."},
    "category": {"type": "string", "maxLength": 40, "description": "Exact (lowercase kebab-case) category match."},
    "tags": _tags_prop(),
    "tags_any": _tags_prop(),
    "payment_method": {"type": "string", "maxLength": 40},
    "text": {"type": "string", "maxLength": 120, "description": "Free-text search across merchant, note, category and tags."},
    "from_date": {**_date_prop(), "description": "Inclusive lower bound on receipt date (YYYY-MM-DD)."},
    "to_date": {**_date_prop(), "description": "Inclusive upper bound on receipt date (YYYY-MM-DD)."},
    "min_amount": _money("Inclusive lower bound on receipt amount."),
    "max_amount": _money("Inclusive upper bound on receipt amount."),
}

TOOL_DEFS = [
    {
        "name": "add_receipt",
        "description": (
            "Drop a receipt into the local receipts bucket: amount (positive money-out), "
            "merchant, category (lowercase kebab-case like 'office_supplies') and optional "
            "tags, note, payment method, photo filename and date. Everything is stored in a "
            "local JSON file (auto-backed-up before every write); nothing leaves this machine."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["amount", "merchant"],
            "properties": {
                "amount": _money("Positive money-out amount, e.g. 23.45 ('$1,204.50' style strings also work)."),
                "merchant": {"type": "string", "maxLength": 120, "description": "Where the money went, e.g. 'Corner Coffee Co.'"},
                "category": {"type": "string", "maxLength": 40},
                "date": {**_date_prop(), "description": "YYYY-MM-DD; defaults to today (local time)."},
                "note": {"type": "string", "maxLength": 500},
                "receipt_photo": {"type": "string", "maxLength": 200, "description": "Plain photo filename (e.g. IMG_0042.jpg); stored as a reference label only."},
                "payment_method": {"type": "string", "maxLength": 40, "description": "E.g. cash, credit_card, debit_card."},
                "tags": _tags_prop(),
                "currency": {"type": "string", "minLength": 3, "maxLength": 3, "description": "3-letter code; defaults to the bucket currency (USD)."},
            },
        },
    },
    {
        "name": "search_receipts",
        "description": (
            "Search the local receipts bucket by merchant, category, tags, payment method, "
            "date range, amount range or free text. Returns matching receipts plus the summed "
            "amount of all matches (cents-exact)."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                **_SHARED_FILTER_PROPS,
                "sort": {"type": "string", "enum": ["date_desc", "date_asc", "amount_desc", "amount_asc", "merchant"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
    },
    {
        "name": "tag_receipt",
        "description": (
            "Update an existing receipt: add/remove tags and/or move it to a different "
            "category (e.g. re-tag 'books' as 'education'). Pass receipt_id from "
            "search_receipts or from an earlier add_receipt."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["receipt_id"],
            "properties": {
                "receipt_id": {"type": "string", "description": "Receipt id like 'rcpt-3f9c2b1a7d04'."},
                "add_tags": _tags_prop(),
                "remove_tags": _tags_prop(),
                "set_category": {"type": "string", "maxLength": 40},
            },
        },
    },
    {
        "name": "delete_receipt",
        "description": (
            "Remove one receipt after a mistake or duplicate. The bucket is auto-backed-up "
            "before the deletion. Optionally pass expected_merchant as a safety check."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["receipt_id"],
            "properties": {
                "receipt_id": {"type": "string"},
                "expected_merchant": {"type": "string", "description": "Refuse to delete unless merchant matches this string."},
            },
        },
    },
    {
        "name": "monthly_summary",
        "description": (
            "Monthly spend summaries grouped by calendar month, with cents-exact totals plus "
            "per-category and per-payment-method breakdowns. Scope by month (YYYY-MM), year, "
            "or any of the shared search filters."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "month": {"type": "string", "pattern": r"^\d{4}-\d{2}$", "description": "One month, e.g. 2026-09."},
                "year": {"type": "string", "pattern": r"^\d{4}$"},
                **_SHARED_FILTER_PROPS,
            },
        },
    },
    {
        "name": "export_csv",
        "description": (
            "Produce a bookkeeping-ready CSV suitable for cashflow-simple-ledger import: "
            "columns date,description,amount,currency,flow,category,payment_method,receipt,"
            "tags,notes,id. Amounts are money-out (negative by default); spreadsheet-safe."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "month": {"type": "string", "pattern": r"^\d{4}-\d{2}$", "description": "Optionally export one month (YYYY-MM)."},
                "year": {"type": "string", "pattern": r"^\d{4}$", "description": "Optionally export a whole year."},
                "delimiter": {"type": "string", "enum": [",", ";", "tab"], "default": ","},
                "include_header": {"type": "boolean", "default": True},
                "include_total_row": {"type": "boolean", "default": False},
                "amount_sign": {"type": "string", "enum": ["negative", "positive"], "default": "negative"},
                **_SHARED_FILTER_PROPS,
            },
        },
    },
    {
        "name": "bucket_info",
        "description": (
            "Show where the bucket lives on disk, its size, receipt count, date range, "
            "all-time totals, and how many automatic backups exist. Takes no arguments."
        ),
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
]

assert set(HANDLERS) == {tool["name"] for tool in TOOL_DEFS}
