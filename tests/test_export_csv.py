"""Tests for the cashflow-simple-ledger CSV export, incl. formula-injection guards."""
from __future__ import annotations

import csv
import io
import unittest

from receipt_bucket.ledger_export import COLUMNS, export_csv_text
from receipt_bucket.receipts import build_receipt, sort_receipts


def receipt(amount, merchant="M", **kw):
    return build_receipt({"amount": amount, "merchant": merchant, **kw}, "USD")


def rows(text, delimiter=","):
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


class TestExport(unittest.TestCase):
    def test_header_and_columns(self):
        text = export_csv_text([])
        parsed = rows(text)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0], COLUMNS)

    def test_row_values_negative_by_default(self):
        r = receipt(
            23.45,
            "Corner Cafe",
            category="Meals",
            date="2026-10-24",
            tags=["coffee", "team"],
            note="handout memo",
            payment_method="cash",
            receipt_photo="PIC.jpg",
        )
        text = export_csv_text([r])
        row = rows(text)[1]
        self.assertEqual(row[0], "2026-10-24")
        self.assertEqual(row[1], "Corner Cafe")
        self.assertEqual(row[2], "-23.45")
        self.assertEqual(row[3], "USD")
        self.assertEqual(row[4], "out")
        self.assertEqual(row[5], "meals")
        self.assertEqual(row[6], "cash")
        self.assertEqual(row[7], "PIC.jpg")
        self.assertEqual(row[8], "coffee; team")
        self.assertEqual(row[9], "handout memo")

    def test_amount_sign_positive_option(self):
        r = receipt(23.45)
        text = export_csv_text([r], amount_sign="positive")
        self.assertEqual(rows(text)[1][2], "23.45")

    def test_delimiter_options(self):
        r = receipt(5.0)
        text = export_csv_text([r], delimiter=";")
        self.assertEqual(rows(text, ";")[0], COLUMNS)
        self.assertEqual(rows(text, ";")[1][1], "M")
        text = export_csv_text([r], delimiter="tab")
        self.assertEqual(rows(text, "\t")[0], COLUMNS)

    def test_comma_merchant_quoted_correctly(self):
        r = receipt(1.0, "Corner Cafe, LLC")
        row = rows(export_csv_text([r]))[1]
        self.assertEqual(row[1], "Corner Cafe, LLC")

    def test_formula_injection_guards(self):
        r1 = receipt(1.0, "=SUM(A1:A9)")
        r2 = receipt(2.0, merchant="Plain Store", note="-2; rm -rf /")
        r3 = receipt(3.0, merchant="@evil", tags=["ok"])
        text = export_csv_text([r1, r2, r3])
        flat = text.replace('"', "")
        self.assertIn("'=SUM(A1:A9)", flat)
        self.assertIn("'-2; rm -rf /", flat)
        self.assertIn("'@evil", flat)
        # amount cell stays numeric-innocent (negatives are intentional)
        self.assertIn("-1.00", text)

    def test_total_row_toggle(self):
        items = [receipt(10.10, date="2026-09-01"), receipt(20.20, date="2026-09-02")]
        text = export_csv_text(sort_receipts(items, "date_asc"))
        parsed = rows(text)
        self.assertEqual(len(parsed), 3)
        self.assertNotIn("TOTAL", text)
        text = export_csv_text(sort_receipts(items, "date_asc"), include_total_row=True)
        parsed = rows(text)
        self.assertEqual(parsed[-1][1], "TOTAL")
        self.assertEqual(parsed[-1][2], "-30.30")

    def test_totals_rounding_is_cents_exact(self):
        items = [receipt(0.10), receipt(0.20), receipt(30.25)]
        text = export_csv_text(items, include_total_row=True)
        self.assertEqual(rows(text)[-1][2], "-30.55")


if __name__ == "__main__":
    unittest.main()
