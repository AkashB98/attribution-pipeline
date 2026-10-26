"""Validation: clean data passes, planted defects flagged, never raises."""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generator import generate_dataset
from helpers import conversion, event
from validation import (validate_conversion, validate_dataset, validate_event,
                        validate_spend_row)


def clean_dataset():
    return {"events": [event()],
            "conversions": [conversion()],
            "spend": [{"date": "2026-01-01", "channel": "email", "spend": 10.0}]}


class TestClean(unittest.TestCase):
    def test_handbuilt_clean(self):
        self.assertEqual(validate_dataset(clean_dataset()), [])

    def test_generated_clean(self):
        self.assertEqual(validate_dataset(generate_dataset(users=200, seed=42)), [])

    def test_empty_dataset_flagged(self):
        issues = validate_dataset({"events": [], "conversions": [], "spend": []})
        self.assertEqual(len(issues), 1)


class TestEvents(unittest.TestCase):
    def test_missing_field(self):
        bad = event()
        del bad["channel"]
        issues = validate_event(bad)
        self.assertTrue(any("missing field 'channel'" in i for i in issues))

    def test_missing_everything_reports_not_raises(self):
        issues = validate_event({})
        self.assertEqual(len(issues), 7)

    def test_invalid_channel(self):
        issues = validate_event(event(channel="tiktok"))
        self.assertTrue(any("invalid channel" in i for i in issues))

    def test_invalid_device(self):
        issues = validate_event(event(device="smart_tv"))
        self.assertTrue(any("invalid device" in i for i in issues))

    def test_unparseable_timestamp(self):
        issues = validate_event(event(timestamp="yesterday-ish"))
        self.assertTrue(any("unparseable timestamp" in i for i in issues))

    def test_duplicate_event_id(self):
        ds = clean_dataset()
        ds["events"].append(copy.deepcopy(ds["events"][0]))
        issues = validate_dataset(ds)
        self.assertTrue(any("duplicate event_id" in i for i in issues))


class TestConversions(unittest.TestCase):
    def test_negative_revenue(self):
        issues = validate_conversion(conversion(revenue=-5.0))
        self.assertTrue(any("revenue must be >= 0" in i for i in issues))

    def test_zero_revenue_ok(self):
        self.assertEqual(validate_conversion(conversion(revenue=0.0)), [])

    def test_string_revenue_flagged(self):
        issues = validate_conversion(conversion(revenue="100"))
        self.assertTrue(any("revenue must be a number" in i for i in issues))

    def test_bool_revenue_flagged(self):
        issues = validate_conversion(conversion(revenue=True))
        self.assertTrue(any("revenue must be a number" in i for i in issues))

    def test_duplicate_conversion_id(self):
        ds = clean_dataset()
        ds["conversions"].append(copy.deepcopy(ds["conversions"][0]))
        issues = validate_dataset(ds)
        self.assertTrue(any("duplicate conversion_id" in i for i in issues))

    def test_conversion_before_first_touch_flagged(self):
        ds = clean_dataset()
        ds["conversions"][0]["timestamp"] = "2026-01-01T00:00:00Z"
        issues = validate_dataset(ds)
        self.assertTrue(any("precedes" in i for i in issues))

    def test_conversion_for_unknown_user_not_flagged(self):
        ds = clean_dataset()
        ds["conversions"][0]["user_id"] = "HH-U99999"
        ds["conversions"][0]["timestamp"] = "2026-01-01T00:00:00Z"
        self.assertEqual(validate_dataset(ds), [])


class TestSpend(unittest.TestCase):
    def test_negative_spend(self):
        issues = validate_spend_row(
            {"date": "2026-01-01", "channel": "email", "spend": -1.0})
        self.assertTrue(any("spend must be >= 0" in i for i in issues))

    def test_bad_date_format(self):
        issues = validate_spend_row(
            {"date": "01/02/2026", "channel": "email", "spend": 1.0})
        self.assertTrue(any("YYYY-MM-DD" in i for i in issues))

    def test_invalid_channel(self):
        issues = validate_spend_row(
            {"date": "2026-01-01", "channel": "radio", "spend": 1.0})
        self.assertTrue(any("invalid channel" in i for i in issues))

    def test_duplicate_spend_row(self):
        ds = clean_dataset()
        ds["spend"].append(dict(ds["spend"][0]))
        issues = validate_dataset(ds)
        self.assertTrue(any("duplicate spend row" in i for i in issues))


if __name__ == "__main__":
    unittest.main()
