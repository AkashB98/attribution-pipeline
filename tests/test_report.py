"""Report: channel math, spend joins, ROAS/CPA edges, disagreement."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import journey, touch
from models import MODELS
from report import build_report


def spend_rows(**by_channel):
    return [{"date": "2026-01-01", "channel": ch, "spend": amt}
            for ch, amt in by_channel.items()]


def two_journeys():
    return [
        journey([touch("2026-01-09T13:00:00Z", "paid_search"),
                 touch("2026-01-10T12:00:00Z", "email")],
                revenue=200.0, conversion_id="CNV-A"),
        journey([touch("2026-01-10T11:00:00Z", "email")],
                revenue=100.0, conversion_id="CNV-B"),
    ]


class TestReportMath(unittest.TestCase):
    def setUp(self):
        self.report = build_report(two_journeys(),
                                   spend_rows(paid_search=50.0, email=25.0))

    def test_totals(self):
        t = self.report["totals"]
        self.assertEqual(t["conversions"], 2)
        self.assertEqual(t["revenue"], 300.0)
        self.assertEqual(t["touchpoints"], 3)
        self.assertEqual(t["spend"], 75.0)

    def test_touchpoints_per_channel(self):
        ch = self.report["channels"]
        self.assertEqual(ch["paid_search"]["touchpoints"], 1)
        self.assertEqual(ch["email"]["touchpoints"], 2)

    def test_assisted_conversions_count_journey_once(self):
        j = journey([touch("2026-01-08T13:00:00Z", "email"),
                     touch("2026-01-09T13:00:00Z", "display"),
                     touch("2026-01-10T12:00:00Z", "email")])
        report = build_report([j], [])
        self.assertEqual(report["channels"]["email"]["assisted_conversions"], 1)
        self.assertEqual(report["channels"]["display"]["assisted_conversions"], 1)

    def test_last_touch_credited_revenue(self):
        ch = self.report["channels"]
        self.assertEqual(ch["email"]["models"]["last_touch"]["credited_revenue"], 300.0)
        self.assertEqual(ch["paid_search"]["models"]["last_touch"]["credited_revenue"], 0.0)

    def test_first_touch_credited_revenue(self):
        ch = self.report["channels"]
        self.assertEqual(ch["paid_search"]["models"]["first_touch"]["credited_revenue"], 200.0)
        self.assertEqual(ch["email"]["models"]["first_touch"]["credited_revenue"], 100.0)

    def test_linear_credited_conversions(self):
        ch = self.report["channels"]
        self.assertEqual(ch["email"]["models"]["linear"]["credited_conversions"], 1.5)
        self.assertEqual(ch["paid_search"]["models"]["linear"]["credited_conversions"], 0.5)

    def test_credited_revenue_conserved_per_model(self):
        for model in MODELS:
            total = sum(c["models"][model]["credited_revenue"]
                        for c in self.report["channels"].values())
            self.assertAlmostEqual(total, 300.0, places=6, msg=model)

    def test_roas_math(self):
        email_linear = self.report["channels"]["email"]["models"]["linear"]
        # linear email revenue = 100 (half of A) + 100 (all of B) = 200; spend 25
        self.assertEqual(email_linear["credited_revenue"], 200.0)
        self.assertEqual(email_linear["roas"], 8.0)

    def test_cpa_math(self):
        email_linear = self.report["channels"]["email"]["models"]["linear"]
        # spend 25 / 1.5 credited conversions = 16.67
        self.assertEqual(email_linear["cpa"], 16.67)

    def test_spend_joined_across_days(self):
        rows = [{"date": "2026-01-01", "channel": "email", "spend": 10.0},
                {"date": "2026-01-02", "channel": "email", "spend": 15.0}]
        report = build_report(two_journeys(), rows)
        self.assertEqual(report["channels"]["email"]["spend"], 25.0)


class TestReportEdges(unittest.TestCase):
    def test_zero_spend_roas_none_cpa_zero(self):
        j = journey([touch("2026-01-10T12:00:00Z", "direct")], revenue=80.0)
        report = build_report([j], spend_rows(direct=0.0))
        direct = report["channels"]["direct"]["models"]["linear"]
        self.assertIsNone(direct["roas"])
        self.assertEqual(direct["cpa"], 0.0)

    def test_zero_credit_cpa_none(self):
        report = build_report(two_journeys(),
                              spend_rows(display=100.0, paid_search=50.0, email=25.0))
        display = report["channels"]["display"]["models"]["linear"]
        self.assertEqual(display["credited_conversions"], 0.0)
        self.assertIsNone(display["cpa"])
        self.assertEqual(display["roas"], 0.0)

    def test_unattributed_bucket(self):
        j = journey([], revenue=70.0)
        report = build_report([j], [])
        self.assertIn("unattributed", report["channels"])
        bucket = report["channels"]["unattributed"]
        self.assertEqual(bucket["models"]["linear"]["credited_revenue"], 70.0)
        self.assertEqual(report["totals"]["unattributed_conversions"], 1)
        self.assertEqual(report["totals"]["unattributed_revenue"], 70.0)

    def test_no_unattributed_bucket_when_none(self):
        report = build_report(two_journeys(), [])
        self.assertNotIn("unattributed", report["channels"])
        self.assertEqual(report["totals"]["unattributed_conversions"], 0)

    def test_non_converting_journeys_count_touches_not_revenue(self):
        j = journey([touch("2026-01-10T12:00:00Z", "display")], converted=False)
        report = build_report([j], [])
        self.assertEqual(report["totals"]["touchpoints"], 1)
        self.assertEqual(report["totals"]["conversions"], 0)
        self.assertEqual(report["channels"]["display"]["assisted_conversions"], 0)

    def test_max_disagreement_picks_widest_channel(self):
        report = build_report(two_journeys(),
                              spend_rows(paid_search=50.0, email=25.0))
        md = report["comparison"]["max_disagreement"]
        # paid_search: first 200 vs last 0 -> spread 200; email: 300 vs 100 -> 200.
        # Tie breaks to the earliest channel in generator.CHANNELS order.
        self.assertEqual(md["spread"], 200.0)
        self.assertEqual(md["channel"], "paid_search")
        self.assertEqual(md["model_high"], "first_touch")
        self.assertEqual(md["model_low"], "last_touch")

    def test_empty_inputs(self):
        report = build_report([], [])
        self.assertEqual(report["totals"]["conversions"], 0)
        self.assertEqual(report["totals"]["revenue"], 0.0)
        self.assertEqual(report["comparison"]["max_disagreement"]["spread"], 0.0)


if __name__ == "__main__":
    unittest.main()
