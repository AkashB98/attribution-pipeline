"""Models: per-model math, invariants, edge cases."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helpers import journey, touch
from models import (MODELS, attribute_all, attribute_journey, touch_weights)

CONV_TS = "2026-01-10T13:00:00Z"


def j3():
    return journey([touch("2026-01-08T13:00:00Z", "display"),
                    touch("2026-01-09T13:00:00Z", "paid_search"),
                    touch("2026-01-10T12:00:00Z", "email")])


class TestFirstLastLinear(unittest.TestCase):
    def test_first_touch(self):
        self.assertEqual(attribute_journey(j3(), "first_touch"), {"display": 1.0})

    def test_last_touch(self):
        self.assertEqual(attribute_journey(j3(), "last_touch"), {"email": 1.0})

    def test_linear_splits_evenly(self):
        credits = attribute_journey(j3(), "linear")
        for ch in ("display", "paid_search", "email"):
            self.assertAlmostEqual(credits[ch], 1 / 3, places=12)

    def test_linear_aggregates_repeated_channel(self):
        j = journey([touch("2026-01-08T13:00:00Z", "email"),
                     touch("2026-01-09T13:00:00Z", "display"),
                     touch("2026-01-10T12:00:00Z", "email")])
        credits = attribute_journey(j, "linear")
        self.assertAlmostEqual(credits["email"], 2 / 3, places=12)
        self.assertAlmostEqual(credits["display"], 1 / 3, places=12)


class TestPositionBased(unittest.TestCase):
    def test_single_touch_full_credit(self):
        j = journey([touch("2026-01-10T12:00:00Z", "email")])
        self.assertEqual(touch_weights(j, "position_based"), [1.0])

    def test_two_touch_even_split(self):
        j = journey([touch("2026-01-09T13:00:00Z", "display"),
                     touch("2026-01-10T12:00:00Z", "email")])
        self.assertEqual(touch_weights(j, "position_based"), [0.5, 0.5])

    def test_three_touch_u_shape(self):
        self.assertEqual(touch_weights(j3(), "position_based"), [0.4, 0.2, 0.4])

    def test_four_touch_middles_share_twenty(self):
        j = journey([touch("2026-01-07T13:00:00Z", "a"),
                     touch("2026-01-08T13:00:00Z", "b"),
                     touch("2026-01-09T13:00:00Z", "c"),
                     touch("2026-01-10T12:00:00Z", "d")])
        self.assertEqual(touch_weights(j, "position_based"),
                         [0.4, 0.1, 0.1, 0.4])

    def test_five_touch_middles_share_twenty(self):
        j = journey([touch(f"2026-01-0{d}T13:00:00Z", ch)
                     for d, ch in zip((5, 6, 7, 8, 9), "abcde")])
        weights = touch_weights(j, "position_based")
        self.assertEqual(weights[0], 0.4)
        self.assertEqual(weights[-1], 0.4)
        for w in weights[1:-1]:
            self.assertAlmostEqual(w, 0.2 / 3, places=12)
        self.assertAlmostEqual(sum(weights), 1.0, places=12)


class TestTimeDecay(unittest.TestCase):
    def test_recent_touch_beats_older_touch(self):
        credits = attribute_journey(j3(), "time_decay")
        self.assertGreater(credits["email"], credits["paid_search"])
        self.assertGreater(credits["paid_search"], credits["display"])

    def test_half_life_exact(self):
        # Touch at conversion time weighs 1; touch exactly 7 days older
        # weighs 1/2 -> normalised 2/3 vs 1/3.
        j = journey([touch("2026-01-03T13:00:00Z", "display"),
                     touch("2026-01-10T13:00:00Z", "email")])
        credits = attribute_journey(j, "time_decay")
        self.assertAlmostEqual(credits["email"], 2 / 3, places=9)
        self.assertAlmostEqual(credits["display"], 1 / 3, places=9)

    def test_fourteen_days_is_quarter_weight(self):
        j = journey([touch("2025-12-27T13:00:00Z", "display"),
                     touch("2026-01-10T13:00:00Z", "email")])
        credits = attribute_journey(j, "time_decay")
        self.assertAlmostEqual(credits["email"], 0.8, places=9)
        self.assertAlmostEqual(credits["display"], 0.2, places=9)

    def test_same_timestamp_touches_equal(self):
        j = journey([touch("2026-01-10T12:00:00Z", "display"),
                     touch("2026-01-10T12:00:00Z", "email")])
        credits = attribute_journey(j, "time_decay")
        self.assertAlmostEqual(credits["display"], 0.5, places=12)
        self.assertAlmostEqual(credits["email"], 0.5, places=12)


class TestInvariants(unittest.TestCase):
    def test_single_touch_full_credit_every_model(self):
        j = journey([touch("2026-01-10T12:00:00Z", "referral")])
        for model in MODELS:
            self.assertEqual(attribute_journey(j, model), {"referral": 1.0}, model)

    def test_credits_sum_to_one_all_models(self):
        for n in (1, 2, 3, 4, 5, 8):
            j = journey([touch(f"2026-01-{10 - k:02d}T13:00:00Z", f"ch{k}")
                         for k in range(n)])
            for model in MODELS:
                total = sum(attribute_journey(j, model).values())
                self.assertLess(abs(total - 1.0), 1e-9, (model, n))

    def test_credits_within_unit_interval(self):
        for model in MODELS:
            for credit in attribute_journey(j3(), model).values():
                self.assertGreater(credit, 0.0)
                self.assertLessEqual(credit, 1.0)

    def test_empty_touch_journey_no_credit(self):
        j = journey([])
        for model in MODELS:
            self.assertEqual(attribute_journey(j, model), {})

    def test_non_converting_journey_no_credit(self):
        j = journey([touch("2026-01-10T12:00:00Z", "email")], converted=False)
        for model in MODELS:
            self.assertEqual(attribute_journey(j, model), {})

    def test_unknown_model_raises(self):
        with self.assertRaises(ValueError):
            attribute_journey(j3(), "markov_chain")
        with self.assertRaises(ValueError):
            touch_weights(j3(), "data_driven")
        with self.assertRaises(ValueError):
            attribute_all([j3()], "shapley")

    def test_deterministic_repeat(self):
        self.assertEqual(attribute_journey(j3(), "time_decay"),
                         attribute_journey(j3(), "time_decay"))

    def test_attribute_all_keys(self):
        other = journey([touch("2026-01-10T12:00:00Z", "email")],
                        conversion_id="CNV-T0002")
        result = attribute_all([j3(), other], "linear")
        self.assertEqual(set(result), {"CNV-T0001", "CNV-T0002"})
        self.assertEqual(result["CNV-T0002"], {"email": 1.0})

    def test_generated_book_invariant_all_models(self):
        from generator import generate_dataset
        from stitching import build_journeys
        ds = generate_dataset(users=400, seed=42)
        journeys = build_journeys(ds["events"], ds["conversions"])
        for model in MODELS:
            for j in journeys:
                if j["converted"] and j["touches"]:
                    total = sum(attribute_journey(j, model).values())
                    self.assertLess(abs(total - 1.0), 1e-9, model)


if __name__ == "__main__":
    unittest.main()
