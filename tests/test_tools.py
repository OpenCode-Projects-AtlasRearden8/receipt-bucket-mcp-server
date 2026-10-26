"""Tool-handler tests: add/search/tag/summary/export/delete/info round-trips."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from receipt_bucket.errors import ReceiptNotFoundError, ValidationError
from receipt_bucket.storage import ReceiptStore
from receipt_bucket.tools import bucket_info, add_receipt, delete_receipt, export_csv, monthly_summary, search_receipts, tag_receipt

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = ReceiptStore(self.dir, "receipts")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_then_search_round_trip(self):
        add_receipt(self.store, {"amount": 12.34, "merchant": "Corner Cafe", "tags": ["coffee"], "date": "2026-10-24", "category": "meals"})
        add_receipt(self.store, {"amount": 56.78, "merchant": "Book Barn", "tags": ["books"], "date": "2026-10-25", "category": "education"})
        hit = search_receipts(self.store, {"merchant": "cafe"})
        self.assertEqual(hit["match_count"], 1)
        self.assertEqual(hit["receipts"][0]["amount"], 12.34)
        self.assertEqual(hit["matching_total"], {"USD": 12.34})
        everything = search_receipts(self.store, {})
        self.assertEqual(everything["matching_total"], {"USD": 69.12})

    def test_add_persists_to_disk(self):
        added = add_receipt(self.store, {"amount": 3.0, "merchant": "Cafe A"})["added"]
        reopened = ReceiptStore(self.dir, "receipts")
        self.assertEqual(reopened.receipts[0]["id"], added["id"])

    def test_tag_updates_and_persistence(self):
        added = add_receipt(self.store, {"amount": 9.0, "merchant": "Cafe B", "tags": ["a"], "date": "2026-10-01"})["added"]
        update = tag_receipt(self.store, {"receipt_id": added["id"], "add_tags": ["b"], "set_category": "meals"})
        self.assertEqual(update["updated"]["tags"], ["a", "b"])
        self.assertEqual(update["updated"]["category"], "meals")
        reopened = ReceiptStore(self.dir, "receipts")
        self.assertEqual(reopened.receipts[0]["tags"], ["a", "b"])

    def test_tag_error_paths(self):
        with self.assertRaises(ReceiptNotFoundError):
            tag_receipt(self.store, {"receipt_id": "rcpt-missing0000"})
        with self.assertRaises(ValidationError):
            tag_receipt(self.store, {})

    def test_delete_with_merchant_guard(self):
        added = add_receipt(self.store, {"amount": 4.0, "merchant": "Cafe C"})["added"]
        with self.assertRaises(ValidationError):
            delete_receipt(self.store, {"receipt_id": added["id"], "expected_merchant": "Wrong Place"})
        gone = delete_receipt(self.store, {"receipt_id": added["id"], "expected_merchant": "Cafe C"})
        self.assertEqual(gone["remaining_count"], 0)
        self.assertTrue(self.store.backup_files())

    def test_monthly_summary_totals(self):
        for row in (
            {"amount": 10.10, "merchant": "A", "category": "meals", "date": "2026-09-02", "tags": ["fast"]},
            {"amount": 20.20, "merchant": "B", "category": "beers", "date": "2026-09-19", "payment_method": "cash"},
            {"amount": 30.25, "merchant": "C", "category": "office_supplies", "date": "2026-10-01"},
        ):
            add_receipt(self.store, row)
        summary = monthly_summary(self.store, {"month": "2026-09"})
        self.assertEqual(summary["period"], {"month": "2026-09"})
        self.assertEqual(summary["overall"]["total"], {"USD": 30.30})
        self.assertEqual(summary["months"][0]["by_category"], {"beers": 20.2, "meals": 10.1})
        year = monthly_summary(self.store, {"year": "2026"})
        self.assertEqual(len(year["months"]), 2)
        self.assertEqual(year["overall"]["total"], {"USD": 60.55})
        alltime = monthly_summary(self.store, {})
        self.assertEqual(alltime["period"], {"all_time": True})
        with self.assertRaises(ValidationError):
            monthly_summary(self.store, {"month": "2026-09", "year": "2026"})

    def test_monthly_summary_with_search_filters(self):
        add_receipt(self.store, {"amount": 5.0, "merchant": "Cafe", "category": "meals", "date": "2026-09-03"})
        add_receipt(self.store, {"amount": 50.0, "merchant": "Office", "category": "office_supplies", "date": "2026-09-04"})
        summary = monthly_summary(self.store, {"category": "meals"})
        self.assertEqual(summary["overall"]["total"], {"USD": 5.0})

    def test_export_csv_round_trip_via_tool(self):
        add_receipt(self.store, {"amount": 10.05, "merchant": "Office Store", "category": "office_supplies", "date": "2026-09-05"})
        add_receipt(self.store, {"amount": 2.03, "merchant": "Cafe", "category": "meals", "date": "2026-09-06"})
        exported = export_csv(self.store, {"month": "2026-09"})
        self.assertEqual(exported["row_count"], 2)
        self.assertEqual(exported["total"], {"USD": 12.08})
        lines = exported["csv"].strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].startswith("2026-09-05,"))
        scoped = export_csv(self.store, {"category": "meals"})
        self.assertEqual(scoped["row_count"], 1)
        widths = export_csv(self.store, {"year": "2027", "include_total_row": True})
        self.assertEqual(widths["row_count"], 0)

    def test_bucket_info(self):
        add_receipt(self.store, {"amount": 1.0, "merchant": "A", "date": "2025-01-01"})
        info = bucket_info(self.store, {})
        self.assertEqual(info["receipt_count"], 1)
        self.assertEqual(info["first_date"], "2025-01-01")
        self.assertEqual(info["total_all_time"], {"USD": 1.0})
        self.assertEqual(info["backups"]["count"], 0)

    def test_validation_errors_from_tool(self):
        with self.assertRaises(ValidationError):
            add_receipt(self.store, {"merchant": "M"})  # no amount
        with self.assertRaises(ValidationError):
            add_receipt(self.store, {"amount": "abc", "merchant": "M"})
        with self.assertRaises(ValidationError):
            search_receipts(self.store, {"from_date": "2026-13-taco"})


class TestSampleBucketThroughTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        sample = Path(self.tmp.name) / "sample.json"
        sample.write_bytes((REPO_ROOT / "data" / "sample.json").read_bytes())
        self.store = ReceiptStore(self.tmp.name, "sample")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sample_summary_and_export(self):
        summary = monthly_summary(self.store, {"month": "2026-09"})
        self.assertEqual(summary["overall"]["receipt_count"], 6)
        self.assertEqual(summary["overall"]["total"], {"USD": 128.98})
        exported = export_csv(self.store, {"month": "2026-09"})
        self.assertEqual(exported["row_count"], 6)
        tagged = tag_receipt(self.store, {"receipt_id": "rcpt-a1b2c3d4e509", "add_tags": ["morning"]})
        self.assertIn("morning", tagged["updated"]["tags"])
        # sample bucket is read-only data for tests; revert the tag edit
        tag_receipt(self.store, {"receipt_id": "rcpt-a1b2c3d4e509", "remove_tags": ["morning"]})


if __name__ == "__main__":
    unittest.main()
