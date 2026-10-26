"""Data-quality gate for attribution datasets.

Returns a list of human-readable issue strings; an empty list means the
dataset is clean. Validation never raises on bad *data* — it reports —
so the CLI ``check`` gate can map issues to exit code 2. (GA4 exports
are famously messy; a measurement pipeline that crashes on the first
malformed row never survives contact with a real export.)

Checked: required fields, channel / device vocabularies, timestamp
parseability, revenue and spend >= 0, duplicate event / conversion
ids, duplicate spend (date, channel) rows, and ordering — a conversion
timestamped before its own user's first touchpoint is flagged.
"""

from __future__ import annotations

from typing import Dict, List

from generator import (CHANNELS, CONVERSION_FIELDS, DEVICES, EVENT_FIELDS,
                       SPEND_FIELDS, parse_timestamp)

_TABLE_FIELDS = {"event": EVENT_FIELDS, "conversion": CONVERSION_FIELDS,
                 "spend": SPEND_FIELDS}


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_fields(row: Dict, kind: str, label: str) -> List[str]:
    issues = []
    for field in _TABLE_FIELDS[kind]:
        if field not in row or row[field] is None or row[field] == "":
            issues.append(f"{label}: missing field '{field}'")
    return issues


def _parses(ts) -> bool:
    try:
        parse_timestamp(ts)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def validate_event(event: Dict, index: int = 0) -> List[str]:
    label = event.get("event_id") or f"<event row {index}>"
    issues = _check_fields(event, "event", label)
    if issues:
        return issues
    if event["channel"] not in CHANNELS:
        issues.append(f"{label}: invalid channel '{event['channel']}'")
    if event["device"] not in DEVICES:
        issues.append(f"{label}: invalid device '{event['device']}'")
    if not _parses(event["timestamp"]):
        issues.append(f"{label}: unparseable timestamp '{event['timestamp']}'")
    return issues


def validate_conversion(conv: Dict, index: int = 0) -> List[str]:
    label = conv.get("conversion_id") or f"<conversion row {index}>"
    issues = _check_fields(conv, "conversion", label)
    if issues:
        return issues
    if not _parses(conv["timestamp"]):
        issues.append(f"{label}: unparseable timestamp '{conv['timestamp']}'")
    if not _is_number(conv["revenue"]):
        issues.append(f"{label}: revenue must be a number")
    elif conv["revenue"] < 0:
        issues.append(f"{label}: revenue must be >= 0")
    return issues


def validate_spend_row(row: Dict, index: int = 0) -> List[str]:
    label = f"<spend row {index}>"
    issues = _check_fields(row, "spend", label)
    if issues:
        return issues
    label = f"{row['date']}/{row['channel']}"
    date = str(row["date"])
    if len(date) != 10 or date[4] != "-" or date[7] != "-" \
            or not date[:4].isdigit() or not date[5:7].isdigit() \
            or not date[8:].isdigit():
        issues.append(f"{label}: date must be YYYY-MM-DD")
    if row["channel"] not in CHANNELS:
        issues.append(f"{label}: invalid channel '{row['channel']}'")
    if not _is_number(row["spend"]):
        issues.append(f"{label}: spend must be a number")
    elif row["spend"] < 0:
        issues.append(f"{label}: spend must be >= 0")
    return issues


def validate_dataset(dataset: Dict[str, List[Dict]]) -> List[str]:
    issues: List[str] = []
    events = dataset.get("events", [])
    conversions = dataset.get("conversions", [])
    spend = dataset.get("spend", [])
    if not events and not conversions and not spend:
        issues.append("dataset is empty")
        return issues

    seen_events = set()
    for i, event in enumerate(events):
        issues.extend(validate_event(event, index=i))
        eid = event.get("event_id")
        if eid is not None:
            if eid in seen_events:
                issues.append(f"{eid}: duplicate event_id")
            seen_events.add(eid)

    seen_convs = set()
    for i, conv in enumerate(conversions):
        issues.extend(validate_conversion(conv, index=i))
        cid = conv.get("conversion_id")
        if cid is not None:
            if cid in seen_convs:
                issues.append(f"{cid}: duplicate conversion_id")
            seen_convs.add(cid)

    seen_spend = set()
    for i, row in enumerate(spend):
        issues.extend(validate_spend_row(row, index=i))
        key = (row.get("date"), row.get("channel"))
        if key in seen_spend:
            issues.append(f"{key[0]}/{key[1]}: duplicate spend row")
        seen_spend.add(key)

    # Ordering: no conversion may precede its user's first touchpoint.
    first_touch: Dict[str, str] = {}
    for event in events:
        uid, ts = event.get("user_id"), event.get("timestamp")
        if uid and ts and _parses(ts):
            if uid not in first_touch or ts < first_touch[uid]:
                first_touch[uid] = ts
    for conv in conversions:
        uid, ts = conv.get("user_id"), conv.get("timestamp")
        if uid in first_touch and ts and _parses(ts) and ts < first_touch[uid]:
            issues.append(
                f"{conv.get('conversion_id')}: conversion at {ts} precedes "
                f"user's first touchpoint at {first_touch[uid]}")
    return issues
