"""Journey stitching: raw touchpoint events + conversions -> journeys.

A *journey* is the ordered list of touchpoints credited with one
conversion (or, for users who never convert, their whole touch history).

Rules (each pinned by unit tests):

- Events are grouped by user and sorted by ``(timestamp, event_id)`` —
  the event id makes the order deterministic even for same-second events.
- A conversion's journey is every touchpoint in the 30-day lookback
  window ending at the conversion: ``conversion_ts - 30d <= ts <=
  conversion_ts`` (both bounds inclusive).
- Touchpoints after a conversion belong to the *next* journey: a touch
  must be strictly after the user's previous conversion timestamp to
  join a later conversion's journey, so no touch is ever double-counted.
- Dedupe: *consecutive* touches with the same ``(channel, campaign)``
  collapse into one, keeping the earliest timestamp — GA4 exports fire
  duplicate events on page reloads / tag double-fires, and counting them
  would hand free credit to whichever channel happened to glitch. Only
  consecutive duplicates collapse; the same channel returning later in
  the journey is a genuine second touch and stays.
- A user with touches after their last conversion gets one extra
  ``converted=False`` journey holding those trailing touches. A user
  with no conversions gets exactly one ``converted=False`` journey.
- A conversion whose lookback window contains no touchpoints still gets
  a journey (``touches == []``); the models credit it to an
  "unattributed" bucket in the report rather than inventing a channel.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional

from generator import parse_timestamp

LOOKBACK_DAYS = 30


def _dedupe(touches: List[Dict]) -> List[Dict]:
    out: List[Dict] = []
    for touch in touches:
        if out and out[-1]["channel"] == touch["channel"] \
                and out[-1]["campaign"] == touch["campaign"]:
            continue
        out.append(touch)
    return out


def _touch(event: Dict) -> Dict:
    return {"timestamp": event["timestamp"], "channel": event["channel"],
            "campaign": event["campaign"], "device": event["device"]}


def build_journeys(events: List[Dict], conversions: List[Dict],
                   lookback_days: int = LOOKBACK_DAYS) -> List[Dict]:
    """Stitch ``events`` and ``conversions`` into journeys.

    Returns journeys sorted by ``(user_id, conversion order)``; each is
    ``{user_id, conversion_id, converted, conversion_timestamp, revenue,
    touches}`` where the conversion fields are ``None`` / ``False`` /
    ``0.0`` for non-converting journeys.
    """
    by_user: Dict[str, List[Dict]] = {}
    for event in events:
        by_user.setdefault(event["user_id"], []).append(event)
    conv_by_user: Dict[str, List[Dict]] = {}
    for conv in conversions:
        conv_by_user.setdefault(conv["user_id"], []).append(conv)

    lookback = timedelta(days=lookback_days)
    journeys: List[Dict] = []
    for user_id in sorted(set(by_user) | set(conv_by_user)):
        user_events = sorted(by_user.get(user_id, []),
                             key=lambda e: (e["timestamp"], e["event_id"]))
        user_convs = sorted(conv_by_user.get(user_id, []),
                            key=lambda c: (c["timestamp"], c["conversion_id"]))
        prev_conv_ts: Optional[object] = None
        consumed = 0  # index of first event not yet assigned to a journey
        for conv in user_convs:
            conv_ts = parse_timestamp(conv["timestamp"])
            window_start = conv_ts - lookback
            touches = []
            i = consumed
            while i < len(user_events):
                ts = parse_timestamp(user_events[i]["timestamp"])
                if ts > conv_ts:
                    break
                if ts >= window_start and (prev_conv_ts is None or ts > prev_conv_ts):
                    touches.append(_touch(user_events[i]))
                i += 1
            consumed = i
            journeys.append({
                "user_id": user_id,
                "conversion_id": conv["conversion_id"],
                "converted": True,
                "conversion_timestamp": conv["timestamp"],
                "revenue": float(conv["revenue"]),
                "touches": _dedupe(touches),
            })
            prev_conv_ts = conv_ts
        trailing = [_touch(e) for e in user_events[consumed:]]
        if trailing or not user_convs:
            journeys.append({
                "user_id": user_id,
                "conversion_id": None,
                "converted": False,
                "conversion_timestamp": None,
                "revenue": 0.0,
                "touches": _dedupe(trailing),
            })
    return journeys
