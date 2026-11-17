"""Layer 3 — the fake PMS.

Builds payloads shaped like the Open Hotel sample in the guide, with a deliberate
difference from the real thing: these are already synthetic. The harness still runs its
masker over them, because in production it will be fed real data.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

ROOMS = {
    "609518": "101",
    "609519": "102",
    "609520": "204",
    "609521": "210",
}


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build(
    event_type: str,
    *,
    booking_id: str | None = None,
    booking_room_id: str | None = None,
    room_id: str = "609520",
    arrival: datetime | None = None,
    departure: datetime | None = None,
    notify: bool = True,
    email: bool = True,
    mobile: bool = True,
    phone: str | None = None,
    mail: str | None = None,
    guests: int = 1,
) -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    arrival = arrival or (now + timedelta(minutes=30))
    departure = departure or (arrival + timedelta(days=2))
    booking_id = booking_id or f"BK{random.randint(100000, 999999)}"
    booking_room_id = booking_room_id or f"BR{random.randint(100000, 999999)}"

    persons = []
    for index in range(guests):
        persons.append(
            {
                "name": f"Test Guest {index + 1}",
                "externalId": f"EXT{random.randint(1000, 9999)}{index}",
                "entityType": "GUEST",
                "contactDetails": {
                    "PP": phone or f"+91900000{1000 + index}",
                    "PE": mail or f"guest{index + 1}@example.test",
                    "FN": "Test",
                    "LN": f"Guest{index + 1}",
                },
            }
        )

    return {
        "updatePerson": True,
        "person": persons,
        "event": {
            "eventType": event_type,
            "bookingId": booking_id,
            "bookingRoomId": booking_room_id,
            "arrivalDate": _iso(arrival),
            "departureDate": _iso(departure),
        },
        "unit": [
            {
                "extRoomId": room_id,
                "extRoomNumber": ROOMS.get(room_id, "204"),
                "extLocationId": "238462",
            }
        ],
        "notify": notify,
        "notification": {"email": email, "mobile": mobile},
    }
