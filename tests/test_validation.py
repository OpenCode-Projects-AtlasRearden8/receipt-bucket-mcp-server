"""Security and round-trip tests for validation helpers."""
from __future__ import annotations

import unittest

from receipt_bucket.errors import ValidationError
from receipt_bucket.validation import (
    cents_to_float,
    cents_to_str,
    coerce_bool,
    coerce_int,
    normalize_category,
    normalize_payment_method,
    normalize_tags,
    parse_amount_to_cents,
    sanitize_filename,
    sanitize_text,
    today_local,
    validate_bucket_name,
    validate_currency,
    validate_date,
    validate_id,
    validate_month,
    validate_year,
)

WEIRD_CONTROLS = "bad\x00\n\x1b[31mtext\u200b\u202ef\ufeff"


class TestSanitizeText(unittest.TestCase):
    def test_strips_control_and_invisible_characters(self):
        cleaned = sanitize_text(WEIRD_CONTROLS, field="note", max_len=500)
        self.assertFalse(any(ch for ch in cleaned if ord(ch) < 32))

    def test_collapses_whitespace(self):
        self.assertEqual(sanitize_text("a b  \n c d    ".strip() and " a\t b  c ", field="note", max_len=500), "a b c")

    def test_max_length(self):
        with self.assertRaises(ValidationError):
            sanitize_text("x" * 11, field="note", max_len=10)

    def test_non_string_rejected(self):
        with self.assertRaises(ValidationError):
            sanitize_text(21.5, field="note", max_len=10)

    def test_required_string(self):
        with self.assertRaises(ValidationError):
            sanitize_text("", field="merchant", max_len=10, allow_empty=False)


class TestSanitizeFilename(unittest.TestCase):
    def test_plain_name_unchanged(self):
        self.assertEqual(sanitize_filename("IMG_0042.jpg", field="receipt_photo"), "IMG_0042.jpg")

    def test_unix_path_collapses_to_basename(self):
        self.assertEqual(sanitize_filename("/etc/passwd.jpg", field="receipt_photo"), "passwd.jpg")
        self.assertEqual(sanitize_filename("receipts/../../note.jpg", field="receipt_photo"), "note.jpg")

    def test_windows_path_collapses_to_basename(self):
        self.assertEqual(sanitize_filename("C:\\receipts\\IMG_1.jpg", field="receipt_photo"), "IMG_1.jpg")

    def test_empty_and_dots_rejected(self):
        for bad in ("", "  ", ".", "..", "folder/", "/"):
            with self.assertRaises(ValidationError):
                sanitize_filename(bad, field="receipt_photo")

    def test_long_name_rejected(self):
        with self.assertRaises(ValidationError):
            sanitize_filename("a" * 300 + ".jpg", field="receipt_photo")


class TestAmounts(unittest.TestCase):
    def test_positive_rounding(self):
        self.assertEqual(parse_amount_to_cents(23.45), 2345)
        self.assertEqual(parse_amount_to_cents("12.5"), 1250)
        self.assertEqual(parse_amount_to_cents("$1,204.50"), 120450)

    def test_cents_helpers(self):
        self.assertEqual(cents_to_str(-4530), "-45.30")
        self.assertEqual(cents_to_str(5), "0.05")
        self.assertEqual(cents_to_float(2345), 23.45)

    def test_rejects_zero_and_negative(self):
        for bad in (0, -1, "0.00", "-0.01"):
            with self.assertRaises(ValidationError):
                parse_amount_to_cents(bad, field="amount")

    def test_rejects_non_numbers(self):
        for bad in ("abc", None, [], object()):
            with self.assertRaises(ValidationError):
                parse_amount_to_cents(bad, field="amount")

    def test_rejects_bool_nan_and_infinity(self):
        for bad in (True, False, float("nan"), float("inf"), float("-inf"), "NaN"):
            with self.assertRaises(ValidationError):
                parse_amount_to_cents(bad, field="amount")

    def test_rejects_huge_amounts(self):
        with self.assertRaises(ValidationError):
            parse_amount_to_cents(10_000_000.0, field="amount")


class TestNamesDatesIds(unittest.TestCase):
    def test_bucket_names(self):
        for ok_name in ("receipts", "sample-2", "A_b1", "x"):
            self.assertEqual(validate_bucket_name(ok_name), ok_name)

    def test_bucket_names_rejected(self):
        for bad in ("", "x y", "../x", "a/b", "a.b", ".hi", "-hi", 42, "x" * 65):
            with self.assertRaises(ValidationError):
                validate_bucket_name(bad)

    def test_dates(self):
        self.assertEqual(validate_date("2026-10-26"), "2026-10-26")

    def test_date_impossible_calendar_rejected(self):
        with self.assertRaises(ValidationError):
            validate_date("2026-02-30")

    def test_date_format_rejected(self):
        for bad in ("2026-1-2", "not-a-date", "2026/10/26", "2026-10-26extra"):
            with self.assertRaises(ValidationError):
                validate_date(bad)

    def test_month_and_year(self):
        self.assertEqual(validate_month("2026-09"), "2026-09")
        with self.assertRaises(ValidationError):
            validate_month("2026-13")
        with self.assertRaises(ValidationError):
            validate_month("junk-09")
        self.assertEqual(validate_year("2026"), "2026")
        with self.assertRaises(ValidationError):
            validate_year("26")

    def test_id_shape(self):
        self.assertEqual(validate_id("rcpt-3f9c2b1a7d04"), "rcpt-3f9c2b1a7d04")
        with self.assertRaises(ValidationError):
            validate_id("../etc/passwd")
        with self.assertRaises(ValidationError):
            validate_id("")

    def test_currency(self):
        self.assertEqual(validate_currency("usd", default="EUR"), "USD")
        self.assertEqual(validate_currency(None, default="EUR"), "EUR")
        with self.assertRaises(ValidationError):
            validate_currency("DOLLARS", default="USD")


class TestNormalizers(unittest.TestCase):
    def test_tags_lowercased_sorted_deduped(self):
        self.assertEqual(normalize_tags(["Coffee", "coffee", "A B"]), ["a b", "coffee"])

    def test_tags_charset_restricted(self):
        with self.assertRaises(ValidationError):
            normalize_tags(["bad;tag"])

    def test_tags_cap(self):
        with self.assertRaises(ValidationError):
            normalize_tags([f"tag{i}" for i in range(13)])

    def test_category_kebab(self):
        self.assertEqual(normalize_category("Office Supplies"), "office_supplies")
        self.assertEqual(normalize_category(""), "uncategorized")

    def test_payment_method(self):
        self.assertEqual(normalize_payment_method("Credit Card"), "credit_card")
        self.assertEqual(normalize_payment_method(None), "")


class TestCoercion(unittest.TestCase):
    def test_bool(self):
        self.assertTrue(coerce_bool("yes", field="flag", default=False))
        self.assertFalse(coerce_bool("0", field="flag", default=True))
        self.assertTrue(coerce_bool(True, field="flag", default=False))
        self.assertTrue(coerce_bool(None, field="flag", default=True))

    def test_int(self):
        self.assertEqual(coerce_int("12", field="limit", default=50, minimum=1, maximum=200), 12)
        self.assertEqual(coerce_int(None, field="limit", default=50, minimum=1, maximum=200), 50)
        with self.assertRaises(ValidationError):
            coerce_int("-1", field="limit", default=50, minimum=1, maximum=200)
        with self.assertRaises(ValidationError):
            coerce_int(True, field="limit", default=50, minimum=1, maximum=200)


class TestMisc(unittest.TestCase):
    def test_today_shape(self):
        self.assertEqual(len(today_local()), 10)
        parts = today_local().split("-")
        self.assertEqual([len(p) for p in parts], [4, 2, 2])


if __name__ == "__main__":
    unittest.main()
