"""Five fractional attribution models — pure functions over journeys.

Each model answers the same question differently: of this conversion's
revenue, how much credit does each channel in the journey deserve?
Every model returns ``{channel: credit}`` with credits in [0, 1] that
sum to exactly 1.0 (within 1e-9) for any journey with at least one
touch — the invariant the whole report layer is built on. Repeated
touches on the same channel have their credits summed.

  first_touch     — all credit to the first touch. The "what started it"
                    model; flatters prospecting channels.
  last_touch      — all credit to the last touch. The GA4-default-era
                    model; flatters closers (and brand search).
  linear          — equal credit to every touch.
  position_based  — U-shaped: 40% first, 40% last, the middle touches
                    split the remaining 20%. One touch = 100%; two
                    touches = 50/50 (no middle exists to pay).
  time_decay      — exponential decay with a 7-day half-life: a touch
                    ``age`` days before the conversion weighs
                    ``2 ** (-age / 7)``, normalised to sum to 1.

A journey with no touches returns ``{}`` — the caller (report layer)
books that revenue under "unattributed" rather than inventing credit.
"""

from __future__ import annotations

from typing import Dict, List

from generator import parse_timestamp

MODELS = ["first_touch", "last_touch", "linear", "position_based", "time_decay"]
HALF_LIFE_DAYS = 7.0
UNATTRIBUTED = "unattributed"


def _weights_first(touches: List[Dict]) -> List[float]:
    return [1.0] + [0.0] * (len(touches) - 1)


def _weights_last(touches: List[Dict]) -> List[float]:
    return [0.0] * (len(touches) - 1) + [1.0]


def _weights_linear(touches: List[Dict]) -> List[float]:
    return [1.0 / len(touches)] * len(touches)


def _weights_position(touches: List[Dict]) -> List[float]:
    n = len(touches)
    if n == 1:
        return [1.0]
    if n == 2:
        return [0.5, 0.5]
    middle = 0.2 / (n - 2)
    return [0.4] + [middle] * (n - 2) + [0.4]


def _weights_time_decay(touches: List[Dict], conversion_timestamp: str) -> List[float]:
    conv_ts = parse_timestamp(conversion_timestamp)
    raw = []
    for touch in touches:
        age_days = (conv_ts - parse_timestamp(touch["timestamp"])).total_seconds() / 86400.0
        raw.append(2.0 ** (-age_days / HALF_LIFE_DAYS))
    total = sum(raw)
    return [w / total for w in raw]


def touch_weights(journey: Dict, model: str) -> List[float]:
    """Per-touch weights (aligned with ``journey["touches"]``)."""
    touches = journey["touches"]
    if model == "first_touch":
        return _weights_first(touches)
    if model == "last_touch":
        return _weights_last(touches)
    if model == "linear":
        return _weights_linear(touches)
    if model == "position_based":
        return _weights_position(touches)
    if model == "time_decay":
        return _weights_time_decay(touches, journey["conversion_timestamp"])
    raise ValueError(f"unknown attribution model: {model!r} "
                     f"(expected one of {MODELS})")


def attribute_journey(journey: Dict, model: str) -> Dict[str, float]:
    """Return ``{channel: credit}`` for one journey under ``model``.

    Non-converting journeys and touch-less journeys return ``{}``.
    """
    if model not in MODELS:
        raise ValueError(f"unknown attribution model: {model!r} "
                         f"(expected one of {MODELS})")
    if not journey.get("converted") or not journey.get("touches"):
        return {}
    credits: Dict[str, float] = {}
    for touch, weight in zip(journey["touches"], touch_weights(journey, model)):
        if weight:
            credits[touch["channel"]] = credits.get(touch["channel"], 0.0) + weight
    return dict(sorted(credits.items()))


def attribute_all(journeys: List[Dict], model: str) -> Dict[str, Dict[str, float]]:
    """Return ``{conversion_id: {channel: credit}}`` for every conversion."""
    if model not in MODELS:
        raise ValueError(f"unknown attribution model: {model!r} "
                         f"(expected one of {MODELS})")
    return {j["conversion_id"]: attribute_journey(j, model)
            for j in journeys if j.get("converted")}
