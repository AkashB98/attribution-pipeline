"""End-to-end demo: generate -> stitch -> attribute -> report -> serve.

SYNTHETIC DATA ONLY (fictional Helios Home store). Runs fully offline;
the serve round-trip is loopback-only on an ephemeral port. Every stage
asserts.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import EXIT_OK, main as cli_main, make_server, reset_state  # noqa: E402
from generator import generate_dataset  # noqa: E402
from models import MODELS, attribute_all, attribute_journey  # noqa: E402
from report import build_report  # noqa: E402
from stitching import build_journeys  # noqa: E402
from validation import validate_dataset  # noqa: E402


def _request(port: int, path: str, payload=None, method: str = "GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def main() -> None:
    print("== 1. generate synthetic Helios Home dataset (seed=42) ==")
    dataset = generate_dataset(users=1500, seed=42)
    again = generate_dataset(users=1500, seed=42)
    assert dataset == again, "generator must be deterministic"
    assert validate_dataset(dataset) == [], validate_dataset(dataset)[:3]
    print(f"   {len(dataset['events'])} events, "
          f"{len(dataset['conversions'])} conversions, "
          f"{len(dataset['spend'])} spend rows — deterministic, validates clean")

    print("\n== 2. stitch: journeys partition every touchpoint ==")
    journeys = build_journeys(dataset["events"], dataset["conversions"])
    converting = [j for j in journeys if j["converted"]]
    assert len(converting) == len(dataset["conversions"])
    stitched = sum(len(j["touches"]) for j in journeys)
    raw = len(dataset["events"])
    assert stitched <= raw, "dedupe may only remove touches"
    print(f"   {len(journeys)} journeys ({len(converting)} converting); "
          f"{stitched} touches after dedupe of {raw} raw events")

    print("\n== 3. attribute: every model conserves credit and revenue ==")
    total_revenue = sum(j["revenue"] for j in converting)
    for model in MODELS:
        for journey in converting:
            credits = attribute_journey(journey, model)
            if journey["touches"]:
                assert abs(sum(credits.values()) - 1.0) < 1e-9, (model, journey)
        credited = sum(sum(c.values()) * j["revenue"]
                       for j, c in zip(converting,
                                       [attribute_journey(j, model) for j in converting]))
        unattributed = sum(j["revenue"] for j in converting if not j["touches"])
        assert abs(credited + unattributed - total_revenue) < 1e-6, model
    print(f"   all {len(MODELS)} models: credits sum to 1.0 per conversion; "
          f"credited revenue == ${total_revenue:,.2f} actual revenue")

    print("\n== 4. report: models disagree, and the report says where ==")
    report = build_report(journeys, dataset["spend"])
    assert report["totals"]["conversions"] == len(converting)
    assert report["totals"]["revenue"] == round(total_revenue, 2)
    md = report["comparison"]["max_disagreement"]
    assert md["spread"] > 0, "models must disagree somewhere on seeded data"
    print(f"   max disagreement: {md['channel']} — {md['model_high']} credits "
          f"${md['credited_revenue_high']:,.2f} vs {md['model_low']} "
          f"${md['credited_revenue_low']:,.2f} (spread ${md['spread']:,.2f})")

    print("\n== 5. CLI check gate exit codes ==")
    import tempfile
    from generator import write_dataset
    tmp = tempfile.mkdtemp(prefix="ap-demo-")
    good_dir = os.path.join(tmp, "good")
    write_dataset(generate_dataset(users=60, seed=7), good_dir)
    code_ok = cli_main(["check", "--input", good_dir])
    code_fail = cli_main(["check", "--input", good_dir, "--min-conversions", "100000"])
    code_err = cli_main(["check", "--input", "/tmp/no-such-dataset-xyz"])
    print(f"   pass={code_ok} min-conversions-breach={code_fail} missing-dir={code_err}")
    assert (code_ok, code_fail, code_err) == (EXIT_OK, 1, 2)

    print("\n== 6. live localhost serve round-trip ==")
    small = generate_dataset(users=60, seed=7)
    reset_state(small)
    small_journeys = build_journeys(small["events"], small["conversions"])
    server = make_server(0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, health = _request(port, "/health")
        assert status == 200 and health == {"status": "ok"}
        status, served_report = _request(port, "/report")
        local_report = build_report(small_journeys, small["spend"])
        assert status == 200 and served_report == local_report, \
            "served report must match local report exactly"
        status, served_attr = _request(port, "/attribute",
                                       payload={"model": "position_based"},
                                       method="POST")
        local_attr = attribute_all(small_journeys, "position_based")
        assert status == 200 and served_attr["credits"] == local_attr
        print("   GET /report matches local report exactly; "
              "POST /attribute matches local attribution")
    finally:
        server.shutdown()
        server.server_close()
        reset_state()

    print("\nDemo OK: generate, stitch, attribute, report, check and serve verified.")


if __name__ == "__main__":
    main()
