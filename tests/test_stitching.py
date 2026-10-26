"""Stitching: lookback window, conversion boundaries, dedupe rule."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import conversion, event
from stitching import build_journeys


class TestBasicStitching(unittest.TestCase):
    def test_single_user_single_conversion(self):
        events = [event(timestamp="2026-01-10T10:00:00Z"),
                  event(timestamp="2026-01-10T11:00:00Z", channel="email",
                        campaign="email-welcome-series")]
        journeys = build_journeys(events, [conversion()])
        self.assertEqual(len(journeys), 1)
        j = journeys[0]
        self.assertTrue(j["converted"])
        self.assertEqual(j["revenue"], 100.0)
        self.assertEqual([t["channel"] for t in j["touches"]],
                         ["paid_search", "email"])

    def test_events_sorted_by_timestamp(self):
        events = [event(timestamp="2026-01-10T11:00:00Z", channel="email",
                        campaign="email-welcome-series"),
                  event(timestamp="2026-01-10T10:00:00Z")]
        journeys = build_journeys(events, [conversion()])
        self.assertEqual([t["channel"] for t in journeys[0]["touches"]],
                         ["paid_search", "email"])

    def test_same_timestamp_tie_break_by_event_id(self):
        e1 = event(timestamp="2026-01-10T10:00:00Z", channel="email",
                   campaign="email-welcome-series", event_id="EVT-0000002")
        e2 = event(timestamp="2026-01-10T10:00:00Z", channel="display",
                   campaign="display-prospecting", event_id="EVT-0000001")
        journeys = build_journeys([e1, e2], [conversion()])
        self.assertEqual([t["channel"] for t in journeys[0]["touches"]],
                         ["display", "email"])

    def test_non_converting_user_single_open_journey(self):
        events = [event(), event(timestamp="2026-01-11T10:00:00Z",
                                 channel="email",
                                 campaign="email-welcome-series")]
        journeys = build_journeys(events, [])
        self.assertEqual(len(journeys), 1)
        self.assertFalse(journeys[0]["converted"])
        self.assertIsNone(journeys[0]["conversion_id"])
        self.assertEqual(journeys[0]["revenue"], 0.0)
        self.assertEqual(len(journeys[0]["touches"]), 2)

    def test_users_do_not_mix(self):
        events = [event(user_id="HH-U00001"),
                  event(user_id="HH-U00002", channel="email",
                        campaign="email-welcome-series")]
        journeys = build_journeys(events, [conversion(user_id="HH-U00002")])
        by_user = {j["user_id"]: j for j in journeys}
        self.assertFalse(by_user["HH-U00001"]["converted"])
        self.assertTrue(by_user["HH-U00002"]["converted"])
        self.assertEqual(len(by_user["HH-U00002"]["touches"]), 1)


class TestLookback(unittest.TestCase):
    def test_touch_31_days_before_excluded(self):
        old = event(timestamp="2025-12-10T13:00:00Z")  # 31 days before conv
        recent = event(timestamp="2026-01-09T13:00:00Z", channel="email",
                       campaign="email-welcome-series")
        journeys = build_journeys([old, recent], [conversion()])
        self.assertEqual([t["channel"] for t in journeys[0]["touches"]], ["email"])

    def test_touch_exactly_30_days_before_included(self):
        edge = event(timestamp="2025-12-11T13:00:00Z")  # exactly 30 days
        journeys = build_journeys([edge], [conversion()])
        self.assertEqual(len(journeys[0]["touches"]), 1)

    def test_touch_after_conversion_excluded(self):
        after = event(timestamp="2026-01-10T14:00:00Z")
        journeys = build_journeys([after], [conversion()])
        self.assertEqual(journeys[0]["touches"], [])
        self.assertTrue(journeys[0]["converted"])

    def test_custom_lookback(self):
        old = event(timestamp="2026-01-01T13:00:00Z")
        journeys = build_journeys([old], [conversion()], lookback_days=7)
        self.assertEqual(journeys[0]["touches"], [])
        journeys = build_journeys([old], [conversion()], lookback_days=14)
        self.assertEqual(len(journeys[0]["touches"]), 1)


class TestConversionBoundaries(unittest.TestCase):
    def test_touch_after_conversion_belongs_to_next_journey(self):
        events = [event(timestamp="2026-01-10T10:00:00Z"),
                  event(timestamp="2026-01-12T10:00:00Z", channel="email",
                        campaign="email-welcome-series")]
        convs = [conversion(conversion_id="CNV-1"),
                 conversion(timestamp="2026-01-12T13:00:00Z",
                            conversion_id="CNV-2", revenue=50.0)]
        journeys = build_journeys(events, convs)
        conv_journeys = [j for j in journeys if j["converted"]]
        self.assertEqual(len(conv_journeys), 2)
        self.assertEqual([t["channel"] for t in conv_journeys[0]["touches"]],
                         ["paid_search"])
        self.assertEqual([t["channel"] for t in conv_journeys[1]["touches"]],
                         ["email"])
        # No touch double-counted, no trailing journey needed.
        self.assertEqual(len(journeys), 2)

    def test_trailing_touches_form_open_journey(self):
        events = [event(timestamp="2026-01-10T10:00:00Z"),
                  event(timestamp="2026-01-15T10:00:00Z", channel="display",
                        campaign="display-prospecting")]
        journeys = build_journeys(events, [conversion()])
        self.assertEqual(len(journeys), 2)
        self.assertFalse(journeys[1]["converted"])
        self.assertEqual([t["channel"] for t in journeys[1]["touches"]], ["display"])

    def test_conversion_without_any_events(self):
        journeys = build_journeys([], [conversion()])
        self.assertEqual(len(journeys), 1)
        self.assertTrue(journeys[0]["converted"])
        self.assertEqual(journeys[0]["touches"], [])

    def test_journeys_sorted_by_user(self):
        events = [event(user_id="HH-U00002"), event(user_id="HH-U00001")]
        journeys = build_journeys(events, [])
        self.assertEqual([j["user_id"] for j in journeys],
                         ["HH-U00001", "HH-U00002"])


class TestDedupe(unittest.TestCase):
    def test_consecutive_same_channel_campaign_collapse(self):
        events = [event(timestamp="2026-01-10T10:00:00Z"),
                  event(timestamp="2026-01-10T10:01:00Z"),
                  event(timestamp="2026-01-10T10:02:00Z")]
        journeys = build_journeys(events, [conversion()])
        self.assertEqual(len(journeys[0]["touches"]), 1)
        self.assertEqual(journeys[0]["touches"][0]["timestamp"],
                         "2026-01-10T10:00:00Z")

    def test_same_channel_different_campaign_kept(self):
        events = [event(timestamp="2026-01-10T10:00:00Z", campaign="search-brand"),
                  event(timestamp="2026-01-10T10:01:00Z",
                        campaign="search-cam-core")]
        journeys = build_journeys(events, [conversion()])
        self.assertEqual(len(journeys[0]["touches"]), 2)

    def test_non_consecutive_same_channel_kept(self):
        events = [event(timestamp="2026-01-10T10:00:00Z"),
                  event(timestamp="2026-01-10T10:30:00Z", channel="email",
                        campaign="email-welcome-series"),
                  event(timestamp="2026-01-10T11:00:00Z")]
        journeys = build_journeys(events, [conversion()])
        self.assertEqual([t["channel"] for t in journeys[0]["touches"]],
                         ["paid_search", "email", "paid_search"])


class TestGeneratedData(unittest.TestCase):
    def test_every_conversion_gets_exactly_one_journey(self):
        from generator import generate_dataset
        ds = generate_dataset(users=300, seed=42)
        journeys = build_journeys(ds["events"], ds["conversions"])
        converting = [j for j in journeys if j["converted"]]
        self.assertEqual(len(converting), len(ds["conversions"]))
        self.assertEqual({j["conversion_id"] for j in converting},
                         {c["conversion_id"] for c in ds["conversions"]})

    def test_no_touch_counted_twice_across_journeys(self):
        from generator import generate_dataset
        ds = generate_dataset(users=300, seed=42)
        journeys = build_journeys(ds["events"], ds["conversions"])
        stitched = sum(len(j["touches"]) for j in journeys)
        self.assertLessEqual(stitched, len(ds["events"]))


if __name__ == "__main__":
    unittest.main()
