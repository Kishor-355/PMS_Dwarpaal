"""Data transformation — the part that makes replaying production traffic safe.

Three jobs, in order:

1. mask()      replace real guest contact details with synthetic ones
2. fresh_ids() give the booking new identifiers so it cannot collide with real data
3. shift()     move the stay into the test window, preserving the length of stay

Every function returns a new dict. Nothing mutates its input; a test harness that quietly
rewrites the caller's object is a bug factory.
"""

from __future__ import annotations

import copy
import os
import random
from datetime import datetime, timedelta, timezone

TEST_PHONE = os.environ.get("TEST_PHONE", "+919000000001")
TEST_EMAIL = os.environ.get("TEST_EMAIL", "guest@example.test")


def mask(payload: dict) -> dict:
    """Replace guest PII with synthetic values. Every person, not just person[0]."""
    out = copy.deepcopy(payload)
    for index, person in enumerate(out.get("person") or []):
        details = person.setdefault("contactDetails", {})
        if details.get("PP"):
            details["PP"] = TEST_PHONE if index == 0 else f"{TEST_PHONE[:-2]}{index:02d}"
        if details.get("PE"):
            details["PE"] = TEST_EMAIL if index == 0 else f"guest{index}@example.test"
        if person.get("name"):
            person["name"] = f"Test Guest {index + 1}"
    return out


def fresh_ids(payload: dict, run_id: str, sequence: int) -> dict:
    """Deterministic, collision-free identifiers derived from the run id.

    The guide says 'use fresh values' but never says how. A run-scoped scheme means a
    later run can never collide with an earlier one, or with a real booking.
    """
    out = copy.deepcopy(payload)
    event = out.setdefault("event", {})
    event["bookingId"] = f"RIG-{run_id}-{sequence:02d}"
    event["bookingRoomId"] = f"RIGR-{run_id}-{sequence:02d}"
    return out


def shift(payload: dict, offset_days: int = 0) -> dict:
    """Move arrival/departure into the test window, preserving length of stay."""
    out = copy.deepcopy(payload)
    event = out.setdefault("event", {})
    now = datetime.now(timezone.utc).replace(microsecond=0)

    arrival = _parse(event.get("arrivalDate")) or now
    departure = _parse(event.get("departureDate")) or (arrival + timedelta(days=1))
    stay = departure - arrival

    new_arrival = (now + timedelta(days=offset_days)).replace(second=0)
    event["arrivalDate"] = _iso(new_arrival)
    event["departureDate"] = _iso(new_arrival + stay)
    return out


def shift_hours(payload: dict, hours: int) -> dict:
    """Move both ends earlier by N hours — the 'early check-in / check-out' scenario."""
    out = copy.deepcopy(payload)
    event = out.setdefault("event", {})
    delta = timedelta(hours=hours)
    for key in ("arrivalDate", "departureDate"):
        moment = _parse(event.get(key))
        if moment:
            event[key] = _iso(moment - delta)
    return out


def with_room(payload: dict, room_id: str, room_number: str) -> dict:
    out = copy.deepcopy(payload)
    for unit in out.get("unit") or []:
        unit["extRoomId"] = room_id
        unit["extRoomNumber"] = room_number
    return out


def without_notifications(payload: dict) -> dict:
    """Only the top-level switch is off. The per-channel flags stay true on purpose —
    that is the realistic regression: someone reads notification.email but forgets the
    master 'notify' flag. If the harness only tested 'everything false', it would miss it.
    """
    out = copy.deepcopy(payload)
    out["notify"] = False
    return out


def new_run_id() -> str:
    return f"{datetime.now(timezone.utc):%m%d%H%M}-{random.randint(100, 999)}"


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")
