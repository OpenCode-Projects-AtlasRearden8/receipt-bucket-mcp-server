"""Bookkeeping-ready CSV export for cashflow-simple-ledger import.

Output format (11 columns, stable order):

    date, description, amount, currency, flow, category, payment_method,
    receipt, tags, notes, id

* ``amount`` is the money-out total, negative by default (a cashflow
  ledger treats negative as spend); ``amount_sign`` can flip this.
* ``flow`` is always ``out`` — this server only records money-out receipts.
* Text cells get a spreadsheet-safety nibble: any cell that a spreadsheet
  could misread as a formula (``=``, ``+``, ``-``, ``@`` or TAB prefix) is
  written with a leading apostrophe so pasting it into Excel/Sheets is safe.
"""
from __future__ import annotations

import csv
import io

from .receipts import currency_tally
from .validation import cents_to_str

COLUMNS = [
    "date",
    "description",
    "amount",
    "currency",
    "flow",
    "category",
    "payment_method",
    "receipt",
    "tags",
    "notes",
    "id",
]


def _shield(text: str) -> str:
    """Prevent spreadsheet formula injection in text cells."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv_text(
    receipts,
    *,
    delimiter: str = ",",
    include_header: bool = True,
    include_total_row: bool = False,
    amount_sign: str = "negative",
) -> str:
    """Render receipts (already filtered and sorted) as CSV text."""
    delimiter = {"tab": "\t"}.get(delimiter, delimiter)
    sign = -1 if amount_sign == "negative" else 1
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    if include_header:
        writer.writerow(COLUMNS)
    for receipt in receipts:
        cents = round(receipt.get("amount", 0) * 100)
        writer.writerow(
            [
                receipt.get("date", ""),
                _shield(receipt.get("merchant", "")),
                cents_to_str(sign * cents),
                receipt.get("currency", "USD"),
                "out",
                _shield(receipt.get("category", "")),
                _shield(receipt.get("payment_method") or ""),
                _shield(receipt.get("receipt_photo") or ""),
                _shield("; ".join(receipt.get("tags") or [])),
                _shield(receipt.get("note", "")),
                receipt.get("id", ""),
            ]
        )
    if include_total_row:
        tally = currency_tally(receipts)
        codes = sorted(tally)
        if len(codes) == 1:
            total = cents_to_str(sign * tally[codes[0]]) if tally else "0.00"
            currency = codes[0] if codes else ""
        else:
            total = ""
            currency = "; ".join(codes)
        writer.writerow(
            [
                "",
                "TOTAL",
                total,
                currency,
                "out",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )
    return buffer.getvalue()
