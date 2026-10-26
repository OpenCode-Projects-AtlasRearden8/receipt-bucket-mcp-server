"""Round-trip tests for receipt building, matching, and monthly grouping."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from receipt_bucket.errors import ValidationError
from receipt_bucket.receipts import (
    build_receipt,
    currency_tally,
    group_by_month,
    match_filters,
    normalize_search_filters,
    present_totals,
    retitle_receipt,
    sort_receipts,
    sanitize_stored_receipt,
    summarize_one_month,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def receipt(amount, merchant="M", **kw):
    return build_receipt({"amount": amount, "merchant": merchant, **kw}, "USD")


class TestBuild(unittest.TestCase):
    def test_full_fields_roundtrip(self):
        made = receipt(
            "$1,234.50",
            "Corner Coffee Co.",
            category="Meals",
            date="2026-10-24",
            note="team coffee ",
            receipt_photo="a/pic.jpg",
            payment_method="Credit Card",
            tags=["Team", "coffee", "team"],
        )
        self.assertEqual(made["amount"], 1234.5)
        self.assertEqual(made["merchant"], "Corner Coffee Co.")
        self.assertEqual(made["category"], "meals")
        self.assertEqual(made["date"], "2026-10-24")
        self.assertEqual(made["note"], "team coffee")
        self.assertEqual(made["receipt_photo"], "pic.jpg")
        self.assertEqual(made["payment_method"], "credit_card")
        self.assertEqual(made["tags"], ["coffee", "team"])
        self.assertEqual(len(made["id"]), 17)  # 'rcpt-' + 12 hex chars

    def test_date_defaults_to_today(self):
        made = receipt(1.0)
        self.assertEqual(len(made["date"]), 10)

    def test_amount_required(self):
        with self.assertRaises(ValidationError):
            build_receipt({"merchant": "M"}, "USD")

    def test_category_normalization(self):
        self.assertEqual(receipt(1.0, category="Gift Cards")["category"], "gift_cards")
        self.assertEqual(receipt(1.0)["category"], "uncategorized")

    def test_ids_unique(self):
        ids = {receipt(1.0)["id"] for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_bad_date_rejected(self):
        with self.assertRaises(ValidationError):
            receipt(1.0, date="2026-13-99")


class TestRetitle(unittest.TestCase):
    def test_add_remove_and_set_category(self):
        base = receipt(2.0, tags=["a", "b"])
        updated = retitle_receipt(
            base,
            {"add_tags": ["c"], "remove_tags": ["a"], "set_category": "Books"},
        )
        self.assertEqual(updated["tags"], ["b", "c"])
        self.assertEqual(updated["category"], "books")
        self.assertGreaterEqual(updated["updated_at"], base["updated_at"])

    def test_noop_rejected(self):
        base = receipt(2.0, tags=["a"])
        with self.assertRaises(ValidationError):
            retitle_receipt(base, {"add_tags": ["a"]})

    def test_cap_enforced(self):
        base = receipt(2.0, tags=[f"t{i:03d}" for i in range(10)])
        with self.assertRaises(ValidationError):
            retitle_receipt(base, {"add_tags": ["overflow", "worse", "again"]})


class TestFilters(unittest.TestCase):
    def setUp(self):
        self.items = [
            receipt(10.0, "Alpha Cafe", category="meals", date="2026-09-01", tags=["coffee"], payment_method="cash"),
            receipt(20.0, "Beta Books", category="education", date="2026-09-15", tags=["books"]),
            receipt(40.0, "Alpha Outfitters", category="shopping", date="2026-10-05", note="winter jacket"),
        ]
        self.filters = normalize_search_filters

    def test_merchant_substring(self):
        f = self.filters({"merchant": "alpha"})
        self.assertEqual([r["merchant"] for r in self.items if match_filters(r, f)], ["Alpha Cafe", "Alpha Outfitters"])

    def test_category_exact(self):
        f = self.filters({"category": "meals"})
        self.assertEqual([r["merchant"] for r in self.items if match_filters(r, f)], ["Alpha Cafe"])

    def test_tags_all_then_any(self):
        all_of = self.filters({"tags": ["coffee"]})
        self.assertEqual(len([1 for r in self.items if match_filters(r, all_of)]), 1)
        any_of = self.filters({"tags_any": ["books", "coffee"]})
        self.assertEqual(len([1 for r in self.items if match_filters(r, any_of)]), 2)

    def test_date_range_inclusive(self):
        f = self.filters({"from_date": "2026-09-15", "to_date": "2026-10-05"})
        self.assertEqual([r["merchant"] for r in self.items if match_filters(r, f)], ["Beta Books", "Alpha Outfitters"])

    def test_amount_range(self):
        f = self.filters({"min_amount": 20.0, "max_amount": 40.0})
        self.assertEqual([r["merchant"] for r in self.items if match_filters(r, f)], ["Beta Books", "Alpha Outfitters"])

    def test_text_search(self):
        f = self.filters({"text": "jacket"})
        self.assertEqual([r["merchant"] for r in self.items if match_filters(r, f)], ["Alpha Outfitters"])

    def test_combined_filters_are_and(self):
        f = self.filters({"merchant": "alpha", "category": "meals"})
        self.assertEqual([r["merchant"] for r in self.items if match_filters(r, f)], ["Alpha Cafe"])


class TestSortingAndSummaries(unittest.TestCase):
    def test_sort_orders(self):
        items = [receipt(30.0, "C", date="2026-01-03"), receipt(10.0, "A", date="2026-01-01"), receipt(20.0, "B", date="2026-01-02")]
        self.assertEqual([r["merchant"] for r in sort_receipts(items, "date_asc")], ["A", "B", "C"])
        self.assertEqual([r["merchant"] for r in sort_receipts(items, "date_desc")], ["C", "B", "A"])
        self.assertEqual([r["amount"] for r in sort_receipts(items, "amount_asc")], [10.0, 20.0, 30.0])
        self.assertEqual([r["amount"] for r in sort_receipts(items, "amount_desc")], [30.0, 20.0, 10.0])
        self.assertEqual([r["merchant"] for r in sort_receipts(items, "merchant")], ["A", "B", "C"])

    def test_month_grouping_skips_blank_dates(self):
        items = [
            receipt(1.0, date="2026-09-01"),
            receipt(2.0, date="2026-09-30"),
            receipt(4.0, date="2026-10-01"),
            {**receipt(8.0), "date": ""},
        ]
        groups = group_by_month(items)
        self.assertEqual([m for m, _ in groups], ["2026-09", "2026-10"])
        self.assertEqual(sum(len(v) for _, v in groups), 3)

    def test_cents_exact_summary(self):
        items = [receipt(10.10), receipt(20.20), receipt(30.25)]
        card = summarize_one_month(items, "2026-06")
        self.assertEqual(card["total"], 60.55)
        self.assertEqual(card["average"], 20.18)
        self.assertEqual(card["receipt_count"], 3)

    def test_summary_breakdowns_and_largest(self):
        items = [
            receipt(5.0, "Cafe", category="meals", payment_method="cash"),
            receipt(19.99, "Games", category="gaming", payment_method="credit_card"),
        ]
        card = summarize_one_month(items, "2026-08")
        self.assertEqual(card["by_category"], {"gaming": 19.99, "meals": 5.0})
        self.assertEqual(card["by_payment_method"], {"cash": 5.0, "credit_card": 19.99})
        self.assertEqual(card["largest"]["merchant"], "Games")

    def test_currency_tally_and_presentation(self):
        items = [receipt(1.0), {**receipt(2.0), "currency": "EUR"}]
        self.assertEqual(present_totals(currency_tally(items)), {"EUR": 2.0, "USD": 1.0})
        self.assertEqual(present_totals(currency_tally([receipt(3.5)])), {"USD": 3.5})


class TestSanitizeStored(unittest.TestCase):
    def test_normalizes_hand_edited_receipt(self):
        raw = {
            "id": "rcpt-handedit1",
            "amount": "19.99",
            "merchant": "Hand Edited Store",
            "category": "MISC",
            "date": "2026-04-02",
            "tags": "not-a-list",
        }
        cleaned = sanitize_stored_receipt(raw)
        self.assertEqual(cleaned["amount"], 19.99)
        self.assertEqual(cleaned["category"], "misc")
        self.assertEqual(cleaned["tags"], [])
        self.assertEqual(cleaned["id"], "rcpt-handedit1")

    def test_missing_id_rejected(self):
        with self.assertRaises(ValidationError):
            sanitize_stored_receipt({"amount": 1.0, "merchant": "M"})


if __name__ == "__main__":
    unittest.main()
