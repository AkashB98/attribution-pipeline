"""Golden evals for attribution-pipeline.

SYNTHETIC DATA ONLY (fictional Helios Home store). Every eval is
deterministic: run_evals.py writes eval_report.json and the file must
be byte-identical across runs. No timestamps, ports, or temp paths
enter the report — only names, booleans, pinned counts and hashes.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading
import urllib.request
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import EXIT_ERROR, EXIT_FAIL, EXIT_OK, main as cli_main, make_server, reset_state  # noqa: E402
from generator import (events_to_jsonl, generate_dataset, load_dataset,  # noqa: E402
                       write_dataset)
from models import MODELS, attribute_journey, touch_weights  # noqa: E402
from report import build_report  # noqa: E402
from stitching import build_journeys  # noqa: E402
from validation import validate_dataset  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(ROOT, "fixtures")
RESULTS = []

EVENTS_MD5 = "3b7d4f7e164ce3d73ae0c25078ec374e"


def check(name, passed, detail=""):
    RESULTS.append({"name": name, "passed": bool(passed), "detail": detail})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return passed


def _run_cli(argv):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return cli_main(argv)


def _journey(touch_specs, revenue=100.0, conv_ts="2026-01-10T13:00:00Z"):
    return {"user_id": "HH-U00001", "conversion_id": "CNV-EVAL",
            "converted": True, "conversion_timestamp": conv_ts,
            "revenue": revenue,
            "touches": [{"timestamp": ts, "channel": ch,
                         "campaign": f"{ch}-camp", "device": "mobile"}
                        for ts, ch in touch_specs]}


def _event(eid, ts, channel="paid_search", campaign="search-thermo-core",
           user="HH-U00001"):
    return {"event_id": eid, "user_id": user, "session_id": f"{user}-S01",
            "timestamp": ts, "channel": channel, "campaign": campaign,
            "device": "mobile"}


def _conv(cid="CNV-EVAL", ts="2026-01-10T13:00:00Z", revenue=100.0,
          user="HH-U00001"):
    return {"conversion_id": cid, "user_id": user, "timestamp": ts,
            "revenue": revenue}


def main():
    print("== golden evals: attribution-pipeline ==")
    ds = generate_dataset(users=1500, seed=42)
    journeys = build_journeys(ds["events"], ds["conversions"])
    converting = [j for j in journeys if j["converted"]]

    # E1: generator determinism (event bytes pinned)
    text1 = events_to_jsonl(generate_dataset(users=1500, seed=42)["events"])
    text2 = events_to_jsonl(generate_dataset(users=1500, seed=42)["events"])
    md5 = hashlib.md5(text1.encode()).hexdigest()
    check("E1 generator determinism (seed=42, 1500 users)",
          text1 == text2 and md5 == EVENTS_MD5, f"events_jsonl_md5={md5}")

    # E2: seeded counts + revenue pinned
    revenue = round(sum(c["revenue"] for c in ds["conversions"]), 2)
    stitched = sum(len(j["touches"]) for j in journeys)
    check("E2 seeded counts pinned (4257 events, 235 conversions, $45,160)",
          len(ds["events"]) == 4257 and len(ds["conversions"]) == 235
          and revenue == 45160.0 and len(journeys) == 1500 and stitched == 4087,
          f"events={len(ds['events'])} conversions={len(ds['conversions'])} "
          f"revenue={revenue} journeys={len(journeys)} touches={stitched}")

    # E3: credit-sum invariant + revenue conservation for all 5 models
    invariant = True
    for model in MODELS:
        credited = 0.0
        for j in converting:
            credits = attribute_journey(j, model)
            if j["touches"]:
                invariant = invariant and abs(sum(credits.values()) - 1.0) < 1e-9
            credited += sum(credits.values()) * j["revenue"]
        unattributed = sum(j["revenue"] for j in converting if not j["touches"])
        invariant = invariant and abs(credited + unattributed - revenue) < 1e-6
    check("E3 credit sums to 1.0 and revenue conserved (all 5 models)", invariant)

    # E4: first vs last touch disagreement pinned (direct channel)
    report = build_report(journeys, ds["spend"])
    direct = report["channels"]["direct"]["models"]
    md = report["comparison"]["max_disagreement"]
    check("E4 first vs last touch pinned on 'direct' (7256.0 vs 3067.0)",
          direct["first_touch"]["credited_revenue"] == 7256.0
          and direct["last_touch"]["credited_revenue"] == 3067.0
          and md["channel"] == "direct" and md["spread"] == 4189.0
          and md["model_high"] == "first_touch" and md["model_low"] == "last_touch",
          f"spread={md['spread']}")

    # E5: single-touch journeys get full credit in every model
    singles = [j for j in converting if len(j["touches"]) == 1]
    single_ok = len(singles) == 46 and all(
        attribute_journey(j, m) == {j["touches"][0]["channel"]: 1.0}
        for j in singles for m in MODELS)
    check("E5 single-touch journeys (46) get full credit in every model", single_ok)

    # E6: position-based weights pinned on 1/2/3/5-touch journeys
    def pb(n):
        specs = [(f"2026-01-{10 - k:02d}T13:00:00Z", f"ch{k}") for k in range(n)]
        return touch_weights(_journey(specs), "position_based")
    pos_ok = (pb(1) == [1.0] and pb(2) == [0.5, 0.5]
              and pb(3) == [0.4, 0.2, 0.4]
              and len(pb(5)) == 5 and pb(5)[0] == 0.4 and pb(5)[-1] == 0.4
              and all(abs(w - 0.2 / 3) < 1e-12 for w in pb(5)[1:-1]))
    check("E6 position-based weights pinned (1/2/3/5-touch)", pos_ok)

    # E7: time-decay ordering + half-life exactness
    decay = attribute_journey(_journey([
        ("2026-01-03T13:00:00Z", "display"),      # exactly 7 days before
        ("2026-01-10T13:00:00Z", "email")]), "time_decay")  # at conversion
    decay_ok = (abs(decay["email"] - 2 / 3) < 1e-9
                and abs(decay["display"] - 1 / 3) < 1e-9)
    ordered = attribute_journey(_journey([
        ("2026-01-05T13:00:00Z", "display"),
        ("2026-01-08T13:00:00Z", "paid_search"),
        ("2026-01-10T12:00:00Z", "email")]), "time_decay")
    decay_ok = decay_ok and ordered["email"] > ordered["paid_search"] > ordered["display"]
    check("E7 time-decay: 7-day half-life exact, recent > older", decay_ok,
          f"email={round(decay['email'], 4)} display={round(decay['display'], 4)}")

    # E8: spend / ROAS readout pinned (paid_search under linear)
    ps = report["channels"]["paid_search"]
    ps_linear = ps["models"]["linear"]
    check("E8 paid_search readout pinned (spend, linear revenue, ROAS)",
          ps["spend"] == 12577.9 and report["totals"]["spend"] == 32517.84
          and ps_linear["credited_revenue"] == 8396.65
          and ps_linear["roas"] == 0.6676,
          f"roas={ps_linear['roas']} cpa={ps_linear['cpa']}")

    # E9: validation flags planted defects; generated dataset clean
    import copy
    bad = copy.deepcopy(ds)
    bad["events"][0]["channel"] = "tiktok_ads"
    bad["conversions"][0]["revenue"] = -10.0
    bad["events"].append(copy.deepcopy(bad["events"][1]))
    bad["spend"][0]["spend"] = -1.0
    planted = validate_dataset(bad)
    kinds = ("invalid channel", "revenue must be >= 0", "duplicate event_id",
             "spend must be >= 0")
    check("E9 validation flags planted defects, generated dataset clean",
          all(any(k in issue for issue in planted) for k in kinds)
          and validate_dataset(ds) == [],
          f"planted_issues={len(planted)}")

    # E10: CLI check exit codes 0 / 1 / 2
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ap-evals-")
    good_dir = os.path.join(tmp, "good")
    write_dataset(generate_dataset(users=60, seed=7), good_dir)
    bad_dir = os.path.join(tmp, "bad")
    bad_ds = generate_dataset(users=10, seed=7)
    bad_ds["events"][0]["channel"] = "tiktok_ads"
    write_dataset(bad_ds, bad_dir)
    codes = (_run_cli(["check", "--input", good_dir]),
             _run_cli(["check", "--input", good_dir, "--min-conversions", "100000"]),
             _run_cli(["check", "--input", bad_dir]),
             _run_cli(["check", "--input", "/tmp/no-such-dataset-xyz"]))
    check("E10 CLI check exit codes 0/1/2/2",
          codes == (EXIT_OK, EXIT_FAIL, EXIT_ERROR, EXIT_ERROR), f"codes={codes}")

    # E11: serve round-trip matches local attribution + report exactly
    small = generate_dataset(users=60, seed=7)
    small_journeys = build_journeys(small["events"], small["conversions"])
    reset_state(small)
    server = make_server(0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/attribute",
                                     data=json.dumps({"model": "time_decay"}).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            served_attr = json.loads(resp.read())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/report", timeout=10) as resp:
            served_report = json.loads(resp.read())
        from models import attribute_all
        serve_ok = (served_attr["credits"] == json.loads(json.dumps(
                        attribute_all(small_journeys, "time_decay")))
                    and served_report == json.loads(json.dumps(
                        build_report(small_journeys, small["spend"]))))
    finally:
        server.shutdown()
        server.server_close()
        reset_state()
    check("E11 serve round-trip matches local attribute + report", serve_ok)

    # E12: bundled fixture pinned (seed 42, 60 users; JSONL == CSV loads)
    fixture = load_dataset(FIXTURES)
    fx_journeys = build_journeys(fixture["events"], fixture["conversions"])
    fx_revenue = round(sum(c["revenue"] for c in fixture["conversions"]), 2)
    check("E12 fixture pinned (165 events, 11 conversions, $3,364, clean)",
          len(fixture["events"]) == 165 and len(fixture["conversions"]) == 11
          and fx_revenue == 3364.0 and len(fx_journeys) == 60
          and len(fixture["spend"]) == 240
          and validate_dataset(fixture) == [],
          f"events={len(fixture['events'])} conversions={len(fixture['conversions'])}")

    # E13: stitching rules pinned (lookback, boundary, next-journey, dedupe)
    conv = _conv()
    j_old = build_journeys([_event("EVT-1", "2025-12-10T13:00:00Z")], [conv])
    j_edge = build_journeys([_event("EVT-1", "2025-12-11T13:00:00Z")], [conv])
    evs = [_event("EVT-1", "2026-01-10T10:00:00Z"),
           _event("EVT-2", "2026-01-10T10:01:00Z"),
           _event("EVT-3", "2026-01-12T10:00:00Z", channel="email",
                  campaign="email-welcome-series")]
    convs = [conv, _conv(cid="CNV-2", ts="2026-01-12T13:00:00Z", revenue=50.0)]
    j_split = build_journeys(evs, convs)
    stitch_ok = (j_old[0]["touches"] == [] and len(j_edge[0]["touches"]) == 1
                 and len(j_split) == 2
                 and len(j_split[0]["touches"]) == 1  # duplicate collapsed
                 and [t["channel"] for t in j_split[1]["touches"]] == ["email"])
    check("E13 stitching rules pinned (30d lookback, dedupe, next journey)",
          stitch_ok)

    passed = sum(1 for r in RESULTS if r["passed"])
    total = len(RESULTS)
    summary_doc = {"passed": passed, "total": total,
                   "all_passed": passed == total, "evals": RESULTS}
    with open(os.path.join(HERE, "eval_report.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(summary_doc, sort_keys=True) + "\n")
    print(f"\n{passed}/{total} evals passed -> evals/eval_report.json")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
