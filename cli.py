"""CLI: generate | journeys | attribute | report | check | demo | serve.

SYNTHETIC DATA ONLY — Helios Home is a fictional smart-home store and
every dataset handled here is fabricated sample data.

A "dataset" is a directory holding three tables: ``events``,
``conversions`` and ``spend``, each as ``.jsonl`` or ``.csv``.

Exit codes:
  check : 0 = pass, 1 = a configured threshold breached (credit error or
          conversion floor), 2 = usage / IO / data-quality error
  others: 0 = success, 2 = usage / IO error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generator import generate_dataset, load_dataset, write_dataset  # noqa: E402
from models import MODELS, attribute_all, attribute_journey  # noqa: E402
from report import build_report  # noqa: E402
from stitching import build_journeys  # noqa: E402
from validation import validate_dataset  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_ERROR = 0, 1, 2
DEFAULT_MAX_CREDIT_ERROR = 1e-6
DEFAULT_MIN_CONVERSIONS = 1


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True)


def _load_or_error(directory: str) -> Optional[Dict[str, List[Dict]]]:
    try:
        return load_dataset(directory)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
    except (ValueError, OSError) as exc:
        print(f"error: cannot read dataset {directory}: {exc}", file=sys.stderr)
    return None


def cmd_generate(args) -> int:
    dataset = generate_dataset(users=args.count, seed=args.seed)
    fmt = args.format
    if fmt is None:
        fmt = "csv" if args.out_dir.endswith(".csv") else "jsonl"
    try:
        write_dataset(dataset, args.out_dir, fmt=fmt)
    except OSError as exc:
        print(f"error: cannot write {args.out_dir}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"generated synthetic Helios Home dataset (seed={args.seed}): "
          f"{args.count} users, {len(dataset['events'])} events, "
          f"{len(dataset['conversions'])} conversions, "
          f"{len(dataset['spend'])} spend rows -> {args.out_dir}/ ({fmt})")
    return EXIT_OK


def cmd_journeys(args) -> int:
    dataset = _load_or_error(args.input)
    if dataset is None:
        return EXIT_ERROR
    journeys = build_journeys(dataset["events"], dataset["conversions"])
    if args.limit is not None:
        journeys = journeys[:args.limit]
    print(canonical({"count": len(journeys), "journeys": journeys}))
    return EXIT_OK


def cmd_attribute(args) -> int:
    dataset = _load_or_error(args.input)
    if dataset is None:
        return EXIT_ERROR
    journeys = build_journeys(dataset["events"], dataset["conversions"])
    credits = attribute_all(journeys, args.model)
    print(canonical({"model": args.model, "count": len(credits),
                     "credits": credits}))
    return EXIT_OK


def cmd_report(args) -> int:
    dataset = _load_or_error(args.input)
    if dataset is None:
        return EXIT_ERROR
    journeys = build_journeys(dataset["events"], dataset["conversions"])
    print(canonical(build_report(journeys, dataset["spend"])))
    return EXIT_OK


def _credit_errors(journeys: List[Dict]) -> Dict[str, float]:
    """Per model: worst per-conversion credit-sum error and the revenue
    conservation error (credited revenue vs actual revenue)."""
    errors: Dict[str, float] = {}
    converting = [j for j in journeys if j["converted"]]
    total_revenue = sum(j["revenue"] for j in converting)
    for model in MODELS:
        worst_sum = 0.0
        credited_revenue = 0.0
        for journey in converting:
            credits = attribute_journey(journey, model)
            if journey["touches"]:
                worst_sum = max(worst_sum, abs(sum(credits.values()) - 1.0))
            credited_revenue += sum(credits.values()) * journey["revenue"]
        unattributed = sum(j["revenue"] for j in converting if not j["touches"])
        revenue_error = abs(credited_revenue + unattributed - total_revenue)
        errors[model] = max(worst_sum, revenue_error)
    return errors


def cmd_check(args) -> int:
    dataset = _load_or_error(args.input)
    if dataset is None:
        return EXIT_ERROR
    issues = validate_dataset(dataset)
    if issues:
        for issue in issues[:20]:
            print(f"data-quality: {issue}")
        print(f"check FAIL (data quality): {len(issues)} issue(s)")
        return EXIT_ERROR
    journeys = build_journeys(dataset["events"], dataset["conversions"])
    errors = _credit_errors(journeys)
    worst_model = max(MODELS, key=lambda m: errors[m])
    conversions = sum(1 for j in journeys if j["converted"])
    print(f"journeys={len(journeys)} conversions={conversions} "
          f"max_credit_error={errors[worst_model]:.3g} ({worst_model}) "
          f"threshold={args.max_credit_error:g} "
          f"min_conversions={args.min_conversions}")
    if errors[worst_model] > args.max_credit_error:
        print(f"check FAIL: credit error {errors[worst_model]:.3g} above threshold")
        return EXIT_FAIL
    if conversions < args.min_conversions:
        print(f"check FAIL: only {conversions} conversions "
              f"(< min {args.min_conversions})")
        return EXIT_FAIL
    print("check PASS")
    return EXIT_OK


def cmd_demo(args) -> int:
    import demo as demo_mod
    try:
        demo_mod.main()
    except AssertionError as exc:
        print(f"demo FAILED: {exc}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


# ------------------------------------------------------------------- serve

class _State:
    """Dataset + stitched journeys held by the serve process."""
    def __init__(self):
        self.dataset: Dict[str, List[Dict]] = generate_dataset(users=200, seed=42)
        self.journeys: List[Dict] = build_journeys(
            self.dataset["events"], self.dataset["conversions"])


_STATE = _State()


def get_state() -> _State:
    return _STATE


def reset_state(dataset: Optional[Dict[str, List[Dict]]] = None) -> None:
    _STATE.dataset = dataset if dataset is not None else generate_dataset(users=200, seed=42)
    _STATE.journeys = build_journeys(_STATE.dataset["events"],
                                     _STATE.dataset["conversions"])


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: Any) -> None:
        self._send(code, (canonical(payload) + "\n").encode(), "application/json")

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send_json(200, {"status": "ok"})
        if self.path == "/report":
            return self._send_json(200, build_report(_STATE.journeys,
                                                     _STATE.dataset["spend"]))
        return self._send_json(404, {"error": "unknown endpoint"})

    def do_POST(self):  # noqa: N802
        if self.path != "/attribute":
            return self._send_json(404, {"error": "unknown endpoint"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            model = body.get("model", "linear")
            if model not in MODELS:
                raise ValueError(f"unknown model: {model!r}")
            journeys = body.get("journeys", _STATE.journeys)
            credits = attribute_all(journeys, model)
            return self._send_json(200, {"model": model, "count": len(credits),
                                         "credits": credits})
        except (ValueError, TypeError, KeyError) as exc:
            return self._send_json(400, {"error": f"bad request: {exc}"})

    def log_message(self, *a: Any) -> None:
        pass


def make_server(port: int) -> HTTPServer:
    return HTTPServer(("127.0.0.1", port), _Handler)


def cmd_serve(args) -> int:
    if args.input:
        dataset = _load_or_error(args.input)
        if dataset is None:
            return EXIT_ERROR
        reset_state(dataset)
    server = make_server(args.port)
    print(f"serving on http://127.0.0.1:{args.port}  "
          f"(POST /attribute, GET /report, GET /health)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="attribution-pipeline",
        description="Synthetic multi-touch attribution (Helios Home sample data).")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate a synthetic dataset directory")
    g.add_argument("--count", type=int, default=1500, help="number of users")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--out-dir", default="dataset")
    g.add_argument("--format", choices=["csv", "jsonl"], default=None)
    g.set_defaults(func=cmd_generate)

    j = sub.add_parser("journeys", help="stitch a dataset into user journeys")
    j.add_argument("--input", required=True, help="dataset directory")
    j.add_argument("--limit", type=int, default=None)
    j.set_defaults(func=cmd_journeys)

    a = sub.add_parser("attribute", help="fractional credit per conversion")
    a.add_argument("--input", required=True, help="dataset directory")
    a.add_argument("--model", choices=MODELS, default="linear")
    a.set_defaults(func=cmd_attribute)

    r = sub.add_parser("report", help="channel readout across all five models")
    r.add_argument("--input", required=True, help="dataset directory")
    r.set_defaults(func=cmd_report)

    c = sub.add_parser("check", help="CI gate: exit 0 pass / 1 threshold breach / 2 error")
    c.add_argument("--input", required=True, help="dataset directory")
    c.add_argument("--max-credit-error", type=float, default=DEFAULT_MAX_CREDIT_ERROR)
    c.add_argument("--min-conversions", type=int, default=DEFAULT_MIN_CONVERSIONS)
    c.set_defaults(func=cmd_check)

    sub.add_parser("demo", help="run the end-to-end demo").set_defaults(func=cmd_demo)

    s = sub.add_parser("serve", help="localhost JSON API: POST /attribute, GET /report")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--input", default=None, help="dataset directory to serve")
    s.set_defaults(func=cmd_serve)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
