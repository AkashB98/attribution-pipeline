"""Deterministic synthetic GA4-style event generator — Helios Home (fictional).

SYNTHETIC DATA ONLY. No real analytics, customer, or revenue data is ever
used. Every event, conversion, and spend row is fabricated from a seeded
PRNG so the same seed always yields the same dataset, byte-for-byte.

A dataset has three tables:

  events      — touchpoints: event_id, user_id, session_id, timestamp,
                channel, campaign, device
  conversions — conversion_id, user_id, timestamp, revenue
  spend       — date, channel, spend (one row per channel per day)

Conversion behaviour is driven by a latent propensity: a small base rate
plus a per-touch lift that depends on the touch's channel. Paid search
and email genuinely lift conversion probability more than display does,
so the five attribution models in ``models.py`` have real signal to
disagree about. Users who never convert are generated too — most
journeys in any real GA4 export do not convert.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List

CHANNELS = ["organic_search", "paid_search", "paid_social", "organic_social",
            "display", "email", "referral", "direct"]
# Share of touchpoints per channel (traffic mix for the fictional store).
CHANNEL_WEIGHTS = [0.18, 0.16, 0.14, 0.10, 0.12, 0.10, 0.08, 0.12]

CAMPAIGNS = {
    "organic_search": ["seo-thermostat-guide", "seo-cam-setup", "seo-lighting-ideas"],
    "paid_search": ["search-thermo-core", "search-cam-core", "search-brand"],
    "paid_social": ["social-video-thermo", "social-ugc-cam", "social-retargeting"],
    "organic_social": ["social-community", "social-launch-post"],
    "display": ["display-prospecting", "display-retargeting"],
    "email": ["email-welcome-series", "email-cart-abandon", "email-seasonal-promo"],
    "referral": ["referral-review-sites", "referral-partner-blog"],
    "direct": ["direct-none"],
}

DEVICES = ["mobile", "desktop", "tablet"]
DEVICE_WEIGHTS = [0.55, 0.35, 0.10]

# Latent conversion lift added per touchpoint, by channel. This is the
# ground truth the attribution models are (unknowingly) arguing about:
# paid_search and email touches really do move this population.
CHANNEL_LIFT = {
    "paid_search": 0.060,
    "email": 0.055,
    "organic_search": 0.040,
    "referral": 0.032,
    "paid_social": 0.028,
    "organic_social": 0.020,
    "direct": 0.018,
    "display": 0.012,
}
BASE_CONVERSION_P = 0.05
MAX_CONVERSION_P = 0.85

TOUCH_COUNTS = [1, 2, 3, 4, 5, 6, 7, 8]
TOUCH_COUNT_WEIGHTS = [0.28, 0.24, 0.18, 0.12, 0.08, 0.05, 0.03, 0.02]

# (product, price USD, weight) — revenue = price x quantity (1, or 2 with
# probability 0.18).
PRODUCTS = [("Helios Thermo", 249.0, 0.22), ("Helios Cam", 129.0, 0.24),
            ("Helios Bell", 99.0, 0.18), ("Helios Lumen Kit", 79.0, 0.16),
            ("Helios Hub", 149.0, 0.12), ("Helios Bundle", 399.0, 0.08)]

# Baseline daily media/tooling spend per channel (USD). ``direct`` is
# free by definition; the organic channels carry small tooling costs.
DAILY_SPEND_BASE = {
    "paid_search": 420.0,
    "paid_social": 310.0,
    "display": 180.0,
    "email": 45.0,
    "organic_search": 60.0,
    "organic_social": 35.0,
    "referral": 25.0,
    "direct": 0.0,
}

WINDOW_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
WINDOW_DAYS = 30
WINDOW_END = WINDOW_START + timedelta(days=WINDOW_DAYS)
SESSION_GAP_MINUTES = 30

EVENT_FIELDS = ["event_id", "user_id", "session_id", "timestamp",
                "channel", "campaign", "device"]
CONVERSION_FIELDS = ["conversion_id", "user_id", "timestamp", "revenue"]
SPEND_FIELDS = ["date", "channel", "spend"]

DEFAULT_SEED = 42
DEFAULT_USERS = 1500


def format_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def _weighted(rng: random.Random, items, weights):
    return rng.choices(items, weights=weights, k=1)[0]


def generate_dataset(users: int = DEFAULT_USERS,
                     seed: int = DEFAULT_SEED) -> Dict[str, List[Dict]]:
    """Return ``{"events": [...], "conversions": [...], "spend": [...]}``,
    deterministic for ``seed``.

    Journeys start within the first 22 days of the 30-day window and a
    conversion lands within 48h of the last touch, so every conversion
    stays inside the window (the tail is right-censored by construction —
    see README scope notes).
    """
    if users < 0:
        raise ValueError("users must be >= 0")
    rng = random.Random(seed)
    events: List[Dict] = []
    conversions: List[Dict] = []
    event_seq = 0
    conv_seq = 0

    for i in range(users):
        user_id = f"HH-U{i + 1:05d}"
        n_touches = _weighted(rng, TOUCH_COUNTS, TOUCH_COUNT_WEIGHTS)
        t = WINDOW_START + timedelta(minutes=rng.randint(0, 22 * 24 * 60))
        session_no = 0
        device = None
        user_channels: List[str] = []
        last_ts = t
        for k in range(n_touches):
            if k > 0:
                gap = rng.randint(5, 20 * 60)
                t = t + timedelta(minutes=gap)
                if gap > SESSION_GAP_MINUTES:
                    session_no += 1
                    device = None
            if device is None:
                device = _weighted(rng, DEVICES, DEVICE_WEIGHTS)
            channel = _weighted(rng, CHANNELS, CHANNEL_WEIGHTS)
            campaign = rng.choice(CAMPAIGNS[channel])
            event_seq += 1
            events.append({
                "event_id": f"EVT-{event_seq:07d}",
                "user_id": user_id,
                "session_id": f"{user_id}-S{session_no + 1:02d}",
                "timestamp": format_ts(t),
                "channel": channel,
                "campaign": campaign,
                "device": device,
            })
            user_channels.append(channel)
            last_ts = t

        propensity = BASE_CONVERSION_P + sum(CHANNEL_LIFT[c] for c in user_channels)
        propensity = min(MAX_CONVERSION_P, propensity)
        if rng.random() < propensity:
            conv_ts = last_ts + timedelta(minutes=rng.randint(10, 48 * 60))
            if conv_ts > WINDOW_END:
                conv_ts = WINDOW_END
            product = _weighted(rng, PRODUCTS, [w for _, _, w in PRODUCTS])
            qty = 2 if rng.random() < 0.18 else 1
            conv_seq += 1
            conversions.append({
                "conversion_id": f"CNV-{conv_seq:05d}",
                "user_id": user_id,
                "timestamp": format_ts(conv_ts),
                "revenue": round(product[1] * qty, 2),
            })

    spend: List[Dict] = []
    for day in range(WINDOW_DAYS):
        date = (WINDOW_START + timedelta(days=day)).strftime("%Y-%m-%d")
        for channel in CHANNELS:
            base = DAILY_SPEND_BASE[channel]
            amount = 0.0 if base == 0 else round(base * rng.uniform(0.85, 1.15), 2)
            spend.append({"date": date, "channel": channel, "spend": amount})

    return {"events": events, "conversions": conversions, "spend": spend}


# ------------------------------------------------------------ serializers

def _to_jsonl(rows: List[Dict]) -> str:
    import json
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def _to_csv(rows: List[Dict], fields: List[str]) -> str:
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row[k] for k in fields})
    return buf.getvalue()


def events_to_jsonl(events: List[Dict]) -> str:
    return _to_jsonl(events)


def events_to_csv(events: List[Dict]) -> str:
    return _to_csv(events, EVENT_FIELDS)


def conversions_to_jsonl(conversions: List[Dict]) -> str:
    return _to_jsonl(conversions)


def conversions_to_csv(conversions: List[Dict]) -> str:
    return _to_csv(conversions, CONVERSION_FIELDS)


def spend_to_jsonl(spend: List[Dict]) -> str:
    return _to_jsonl(spend)


def spend_to_csv(spend: List[Dict]) -> str:
    return _to_csv(spend, SPEND_FIELDS)


# --------------------------------------------------------------- loaders
# CSV parses every value as a string, so numeric fields are coerced
# explicitly here (the sibling builds pin the same lesson: without this
# layer, revenue/spend silently stay strings and report math breaks).

def _coerce_float(row: Dict, field: str) -> Dict:
    out = dict(row)
    if isinstance(out.get(field), str):
        try:
            out[field] = float(out[field])
        except ValueError:
            pass
    return out


def _load_rows(path: str) -> List[Dict]:
    import csv
    import io
    import json
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    stripped = text.strip()
    if not stripped:
        return []
    if path.endswith(".csv"):
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]
    if stripped.startswith("["):
        return [dict(x) for x in json.loads(stripped)]
    return [json.loads(line) for line in stripped.splitlines() if line.strip()]


def load_events(path: str) -> List[Dict]:
    return _load_rows(path)


def load_conversions(path: str) -> List[Dict]:
    return [_coerce_float(row, "revenue") for row in _load_rows(path)]


def load_spend(path: str) -> List[Dict]:
    return [_coerce_float(row, "spend") for row in _load_rows(path)]


def _table_path(directory: str, table: str) -> str:
    import os
    for ext in ("jsonl", "csv"):
        candidate = os.path.join(directory, f"{table}.{ext}")
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        f"no {table}.jsonl or {table}.csv in dataset directory: {directory}")


def load_dataset(directory: str) -> Dict[str, List[Dict]]:
    """Load a dataset directory written by ``write_dataset`` (or the CLI)."""
    return {
        "events": load_events(_table_path(directory, "events")),
        "conversions": load_conversions(_table_path(directory, "conversions")),
        "spend": load_spend(_table_path(directory, "spend")),
    }


def write_dataset(dataset: Dict[str, List[Dict]], directory: str,
                  fmt: str = "jsonl") -> None:
    import os
    os.makedirs(directory, exist_ok=True)
    writers = {
        "jsonl": (events_to_jsonl, conversions_to_jsonl, spend_to_jsonl),
        "csv": (events_to_csv, conversions_to_csv, spend_to_csv),
    }
    if fmt not in writers:
        raise ValueError(f"unknown format: {fmt}")
    ev_w, cv_w, sp_w = writers[fmt]
    for table, writer in (("events", ev_w), ("conversions", cv_w), ("spend", sp_w)):
        with open(os.path.join(directory, f"{table}.{fmt}"), "w",
                  encoding="utf-8") as fh:
            fh.write(writer(dataset[table]))
