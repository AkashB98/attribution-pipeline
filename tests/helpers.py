"""Shared builders for the test suite (not a test module)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_seq = [0]


def event(user_id="HH-U00001", timestamp="2026-01-10T12:00:00Z",
          channel="paid_search", campaign="search-thermo-core",
          device="mobile", session_id=None, event_id=None):
    _seq[0] += 1
    return {
        "event_id": event_id or f"EVT-T{_seq[0]:05d}",
        "user_id": user_id,
        "session_id": session_id or f"{user_id}-S01",
        "timestamp": timestamp,
        "channel": channel,
        "campaign": campaign,
        "device": device,
    }


def conversion(user_id="HH-U00001", timestamp="2026-01-10T13:00:00Z",
               revenue=100.0, conversion_id="CNV-T0001"):
    return {"conversion_id": conversion_id, "user_id": user_id,
            "timestamp": timestamp, "revenue": revenue}


def touch(timestamp, channel, campaign=None, device="mobile"):
    return {"timestamp": timestamp, "channel": channel,
            "campaign": campaign or f"{channel}-camp", "device": device}


def journey(touches, revenue=100.0, converted=True,
            conversion_timestamp="2026-01-10T13:00:00Z",
            user_id="HH-U00001", conversion_id="CNV-T0001"):
    return {"user_id": user_id, "conversion_id": conversion_id if converted else None,
            "converted": converted,
            "conversion_timestamp": conversion_timestamp if converted else None,
            "revenue": revenue if converted else 0.0, "touches": touches}
