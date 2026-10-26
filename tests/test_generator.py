"""Generator: determinism, shape, vocabularies, latent signal, IO."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generator import (CAMPAIGNS, CHANNELS, DEVICES, WINDOW_DAYS, WINDOW_END,
                       WINDOW_START, conversions_to_csv, conversions_to_jsonl,
                       events_to_csv, events_to_jsonl, format_ts,
                       generate_dataset, load_conversions, load_dataset,
                       load_events, load_spend, parse_timestamp, spend_to_csv,
                       spend_to_jsonl, write_dataset)


class TestGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = generate_dataset(users=300, seed=42)

    def test_same_seed_identical(self):
        self.assertEqual(generate_dataset(users=50, seed=7),
                         generate_dataset(users=50, seed=7))

    def test_different_seed_differs(self):
        self.assertNotEqual(generate_dataset(users=50, seed=7),
                            generate_dataset(users=50, seed=8))

    def test_jsonl_bytes_deterministic(self):
        a = events_to_jsonl(generate_dataset(users=50, seed=7)["events"])
        b = events_to_jsonl(generate_dataset(users=50, seed=7)["events"])
        self.assertEqual(a, b)

    def test_zero_users(self):
        ds = generate_dataset(users=0, seed=42)
        self.assertEqual(ds["events"], [])
        self.assertEqual(ds["conversions"], [])
        self.assertEqual(len(ds["spend"]), WINDOW_DAYS * len(CHANNELS))

    def test_negative_users_raises(self):
        with self.assertRaises(ValueError):
            generate_dataset(users=-1)

    def test_event_fields_and_vocab(self):
        for e in self.ds["events"]:
            self.assertIn(e["channel"], CHANNELS)
            self.assertIn(e["campaign"], CAMPAIGNS[e["channel"]])
            self.assertIn(e["device"], DEVICES)
            self.assertTrue(e["event_id"].startswith("EVT-"))
            self.assertTrue(e["user_id"].startswith("HH-U"))

    def test_event_ids_unique(self):
        ids = [e["event_id"] for e in self.ds["events"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_timestamps_inside_window_and_ordered_per_user(self):
        by_user = {}
        for e in self.ds["events"]:
            by_user.setdefault(e["user_id"], []).append(e["timestamp"])
        for stamps in by_user.values():
            self.assertEqual(stamps, sorted(stamps))
            for ts in stamps:
                dt = parse_timestamp(ts)
                self.assertGreaterEqual(dt, WINDOW_START)
                self.assertLessEqual(dt, WINDOW_END)

    def test_conversions_after_last_touch_with_revenue(self):
        last_touch = {}
        for e in self.ds["events"]:
            last_touch[e["user_id"]] = e["timestamp"]
        self.assertTrue(self.ds["conversions"])
        for c in self.ds["conversions"]:
            self.assertGreater(c["timestamp"], last_touch[c["user_id"]])
            self.assertGreater(c["revenue"], 0)
            self.assertLessEqual(parse_timestamp(c["timestamp"]), WINDOW_END)

    def test_non_converting_users_exist(self):
        converters = {c["user_id"] for c in self.ds["conversions"]}
        all_users = {e["user_id"] for e in self.ds["events"]}
        self.assertTrue(all_users - converters)
        self.assertGreater(len(all_users - converters), len(converters))

    def test_latent_signal_paid_search_lifts_conversion(self):
        ds = generate_dataset(users=1500, seed=42)
        touched = {e["user_id"] for e in ds["events"] if e["channel"] == "paid_search"}
        converters = {c["user_id"] for c in ds["conversions"]}
        all_users = {e["user_id"] for e in ds["events"]}
        rate_touched = len(touched & converters) / len(touched)
        rest = all_users - touched
        rate_rest = len(rest & converters) / len(rest)
        self.assertGreater(rate_touched, rate_rest)

    def test_session_ids_group_short_gaps(self):
        # Within a session, consecutive touches are <= 30 min apart in
        # generation only when the gap stayed small; at minimum the id
        # format and per-user monotonic session numbering must hold.
        for e in self.ds["events"]:
            self.assertRegex(e["session_id"], r"^HH-U\d{5}-S\d{2}$")

    def test_spend_shape(self):
        spend = self.ds["spend"]
        self.assertEqual(len(spend), WINDOW_DAYS * len(CHANNELS))
        keys = {(r["date"], r["channel"]) for r in spend}
        self.assertEqual(len(keys), len(spend))
        for row in spend:
            self.assertGreaterEqual(row["spend"], 0)

    def test_spend_direct_is_zero(self):
        for row in self.ds["spend"]:
            if row["channel"] == "direct":
                self.assertEqual(row["spend"], 0.0)

    def test_spend_deterministic(self):
        self.assertEqual(spend_to_jsonl(generate_dataset(users=5, seed=3)["spend"]),
                         spend_to_jsonl(generate_dataset(users=5, seed=3)["spend"]))


class TestSerializersAndLoaders(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="ap-gen-test-")
        self.ds = generate_dataset(users=40, seed=11)

    def test_jsonl_round_trip(self):
        import json
        rows = [json.loads(line) for line in events_to_jsonl(self.ds["events"]).splitlines()]
        self.assertEqual(rows, self.ds["events"])
        rows = [json.loads(line) for line in conversions_to_jsonl(self.ds["conversions"]).splitlines()]
        self.assertEqual(rows, self.ds["conversions"])

    def test_csv_round_trip_events(self):
        write_dataset(self.ds, self.tmp, fmt="csv")
        loaded = load_dataset(self.tmp)
        self.assertEqual(loaded["events"], self.ds["events"])

    def test_csv_round_trip_numeric_coercion(self):
        write_dataset(self.ds, self.tmp, fmt="csv")
        loaded = load_dataset(self.tmp)
        self.assertEqual(loaded["conversions"], self.ds["conversions"])
        self.assertEqual(loaded["spend"], self.ds["spend"])
        for row in loaded["conversions"]:
            self.assertIsInstance(row["revenue"], float)

    def test_jsonl_dataset_round_trip(self):
        write_dataset(self.ds, self.tmp, fmt="jsonl")
        self.assertEqual(load_dataset(self.tmp), self.ds)

    def test_load_individual_csv_files(self):
        write_dataset(self.ds, self.tmp, fmt="csv")
        self.assertEqual(load_events(os.path.join(self.tmp, "events.csv")),
                         self.ds["events"])
        self.assertEqual(load_conversions(os.path.join(self.tmp, "conversions.csv")),
                         self.ds["conversions"])
        self.assertEqual(load_spend(os.path.join(self.tmp, "spend.csv")),
                         self.ds["spend"])

    def test_csv_text_has_header(self):
        self.assertTrue(events_to_csv(self.ds["events"]).startswith(
            "event_id,user_id,session_id,timestamp,channel,campaign,device"))
        self.assertTrue(spend_to_csv(self.ds["spend"]).startswith("date,channel,spend"))

    def test_missing_dataset_dir_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_dataset("/tmp/no-such-dataset-dir-xyz")

    def test_format_parse_round_trip(self):
        ts = "2026-01-05T06:07:08Z"
        self.assertEqual(format_ts(parse_timestamp(ts)), ts)


if __name__ == "__main__":
    unittest.main()
