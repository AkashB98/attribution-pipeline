"""Channel readout: what each attribution model says each channel earned.

For every channel (plus an ``unattributed`` bucket that only appears when
a conversion's lookback window held no touchpoints):

  touchpoints          — touches in all journeys (post-dedupe), converting
                         or not
  assisted_conversions — converting journeys the channel appears in at
                         least once (a journey counts once per channel,
                         however many touches it had)
  spend                — total spend over the window, joined by channel
  per model: credited_conversions (fractional), credited_revenue,
  roas = credited_revenue / spend, cpa = spend / credited_conversions

``roas`` is ``None`` when spend is 0 (direct earns revenue against zero
spend — an infinite ROAS is a true fact and a useless number, so the
report says ``None`` instead of printing one). ``cpa`` is ``None`` when
the model credits the channel with zero conversions.

The comparison block names the channel the models disagree about most:
the largest spread between any two models' credited revenue, with the
high and low models named. Ties break deterministically (earliest
channel in ``generator.CHANNELS`` order, then model order in
``models.MODELS``).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from generator import CHANNELS
from models import MODELS, UNATTRIBUTED, attribute_journey


def _roas(credited_revenue: float, spend: float) -> Optional[float]:
    if spend == 0:
        return None
    return round(credited_revenue / spend, 4)


def _cpa(spend: float, credited_conversions: float) -> Optional[float]:
    if credited_conversions == 0:
        return None
    return round(spend / credited_conversions, 2)


def build_report(journeys: List[Dict], spend_rows: List[Dict]) -> Dict:
    spend_by_channel: Dict[str, float] = {}
    for row in spend_rows:
        spend_by_channel[row["channel"]] = \
            spend_by_channel.get(row["channel"], 0.0) + float(row["spend"])

    channel_names = list(CHANNELS)
    touchpoints: Dict[str, int] = {c: 0 for c in channel_names}
    assisted: Dict[str, int] = {c: 0 for c in channel_names}
    credited_conv: Dict[str, Dict[str, float]] = {m: {} for m in MODELS}
    credited_rev: Dict[str, Dict[str, float]] = {m: {} for m in MODELS}
    unattributed_conversions = 0
    unattributed_revenue = 0.0

    for journey in journeys:
        seen = set()
        for touch in journey["touches"]:
            touchpoints[touch["channel"]] = touchpoints.get(touch["channel"], 0) + 1
            seen.add(touch["channel"])
        if not journey["converted"]:
            continue
        for channel in seen:
            assisted[channel] = assisted.get(channel, 0) + 1
        if not journey["touches"]:
            unattributed_conversions += 1
            unattributed_revenue += journey["revenue"]
        for model in MODELS:
            for channel, credit in attribute_journey(journey, model).items():
                credited_conv[model][channel] = \
                    credited_conv[model].get(channel, 0.0) + credit
                credited_rev[model][channel] = \
                    credited_rev[model].get(channel, 0.0) + credit * journey["revenue"]

    channels: Dict[str, Dict] = {}
    for channel in channel_names:
        spend = round(spend_by_channel.get(channel, 0.0), 2)
        per_model = {}
        for model in MODELS:
            cc = round(credited_conv[model].get(channel, 0.0), 4)
            cr = round(credited_rev[model].get(channel, 0.0), 2)
            per_model[model] = {
                "credited_conversions": cc,
                "credited_revenue": cr,
                "roas": _roas(cr, spend),
                "cpa": _cpa(spend, cc),
            }
        channels[channel] = {
            "touchpoints": touchpoints.get(channel, 0),
            "assisted_conversions": assisted.get(channel, 0),
            "spend": spend,
            "models": per_model,
        }
    if unattributed_conversions:
        channels[UNATTRIBUTED] = {
            "touchpoints": 0,
            "assisted_conversions": 0,
            "spend": 0.0,
            "models": {m: {"credited_conversions": float(unattributed_conversions),
                           "credited_revenue": round(unattributed_revenue, 2),
                           "roas": None,
                           "cpa": 0.0 if unattributed_conversions else None}
                       for m in MODELS},
        }

    converting = [j for j in journeys if j["converted"]]
    totals = {
        "journeys": len(journeys),
        "users": len({j["user_id"] for j in journeys}),
        "conversions": len(converting),
        "revenue": round(sum(j["revenue"] for j in converting), 2),
        "touchpoints": sum(touchpoints.values()),
        "spend": round(sum(spend_by_channel.values()), 2),
        "unattributed_conversions": unattributed_conversions,
        "unattributed_revenue": round(unattributed_revenue, 2),
    }

    best = None
    for channel in channel_names:
        values = {m: channels[channel]["models"][m]["credited_revenue"] for m in MODELS}
        high_model = max(MODELS, key=lambda m: values[m])
        low_model = min(MODELS, key=lambda m: values[m])
        spread = round(values[high_model] - values[low_model], 2)
        candidate = {"channel": channel, "spread": spread,
                     "model_high": high_model, "model_low": low_model,
                     "credited_revenue_high": values[high_model],
                     "credited_revenue_low": values[low_model]}
        if best is None or spread > best["spread"]:
            best = candidate

    return {"channels": channels, "totals": totals,
            "comparison": {"max_disagreement": best}}
