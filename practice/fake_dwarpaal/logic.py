"""Layer 2 — the "framework logic".

This is the rig's stand-in for Dwarpaal's integration service: the thing that reads a
PMS event and decides what rows to write.

IMPORTANT: every rule below is a GUESS about how the real system behaves. Each guess is
tagged ASSUMPTION-nn and listed in practice/README.md. When the real team answers the
open questions, these rules must be corrected. Do not treat the rig as the truth; treat
it as an executable question.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import db

# ---------------------------------------------------------------------------
# Room inventory. In the real system this mapping lives in Dwarpaal's own tables.
# ASSUMPTION-06: a room id that is not mapped is rejected with 422 rather than
# silently ignored. Strict is the safer default: a silent skip looks like success.
# ---------------------------------------------------------------------------
ROOMS: dict[str, dict[str, str]] = {
    "609518": {"number": "101", "device": "LOCK-101"},
    "609519": {"number": "102", "device": "LOCK-102"},
    "609520": {"number": "204", "device": "LOCK-204"},
    "609521": {"number": "210", "device": "LOCK-210"},
}

VALID_EVENTS = {
    "reservationcreate",
    "reservationcheckin",
    "reservationModify",
    "reservationcheckout",
}

BREAK_MODES = {
    None: "no bug injected",
    "room_change_no_revoke": "modify changes the room but the old key stays valid",
    "notify_ignored": "notifications are sent even when notify=false",
    "dates_not_updated": "modify ignores the new arrival/departure dates",
    "duplicate_double_key": "a replayed event issues a second key",
    "early_checkin_rejected": "check-in earlier than the booked arrival is refused",
}


class RigError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event") or {}
    units = payload.get("unit") or []
    persons = payload.get("person") or []

    if not units:
        raise RigError(422, "NO_UNIT", "payload has no unit[] — no room to grant access to")
    if not persons:
        raise RigError(422, "NO_PERSON", "payload has no person[] — nobody to notify")

    first_unit = units[0]
    room_id = str(first_unit.get("extRoomId") or "")
    if room_id not in ROOMS:
        raise RigError(
            422,
            "UNKNOWN_ROOM",
            f"extRoomId {room_id!r} is not mapped to a lock on this property",
        )

    return {
        "event_type": str(event.get("eventType") or ""),
        "booking_id": str(event.get("bookingId") or ""),
        "booking_room_id": str(event.get("bookingRoomId") or ""),
        "arrival": _parse(event.get("arrivalDate")),
        "departure": _parse(event.get("departureDate")),
        "room_id": room_id,
        "room_number": str(first_unit.get("extRoomNumber") or ""),
        "persons": persons,
        "notify": bool(payload.get("notify")),
        "channels": {
            "SMS": bool((payload.get("notification") or {}).get("mobile")),
            "EMAIL": bool((payload.get("notification") or {}).get("email")),
        },
    }


def _iso(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z") if value else None


def _grant_key(conn: sqlite3.Connection, auth_id: str, room_id: str, break_mode: str | None) -> None:
    """Place a credential on the lock. Reuses the existing row if one is live."""
    existing = db.one(
        conn,
        "SELECT * FROM DP_AUTH_DEVICE_CHARS "
        "WHERE AUTH_ID=? AND DEVICE_ID=? AND CHAR_STATUS='ACTIVE'",
        (auth_id, ROOMS[room_id]["device"]),
    )
    if existing and break_mode != "duplicate_double_key":
        return
    stamp = db.now()
    conn.execute(
        "INSERT INTO DP_AUTH_DEVICE_CHARS "
        "(AUTH_ID, DEVICE_ID, DEVICE_CHAR, CHAR_STATUS, CREATED_DATE, UPDATED_DATE) "
        "VALUES (?,?,?,?,?,?)",
        (
            auth_id,
            ROOMS[room_id]["device"],
            str(100000 + int(db.scalar(conn, "SELECT COALESCE(MAX(ID),0)+1 FROM DP_AUTH_DEVICE_CHARS")) * 7 % 900000),
            "ACTIVE",
            stamp,
            stamp,
        ),
    )


def _revoke_all(conn: sqlite3.Connection, auth_id: str, keep_device: str | None = None) -> int:
    stamp = db.now()
    sql = "UPDATE DP_AUTH_DEVICE_CHARS SET CHAR_STATUS='REVOKED', UPDATED_DATE=? WHERE AUTH_ID=? AND CHAR_STATUS='ACTIVE'"
    params: list[Any] = [stamp, auth_id]
    if keep_device:
        sql += " AND DEVICE_ID<>?"
        params.append(keep_device)
    cur = conn.execute(sql, params)
    return cur.rowcount


def _notify(
    conn: sqlite3.Connection,
    auth_id: str,
    info: dict[str, Any],
    event_type: str,
    break_mode: str | None,
) -> int:
    """Write communication rows according to the flags.

    ASSUMPTION-04: a row is written only when BOTH the top-level notify flag AND the
    per-channel flag are true. Any send when notify=false is a bug.
    """
    if not info["notify"] and break_mode != "notify_ignored":
        return 0

    written = 0
    stamp = db.now()
    for person in info["persons"]:
        details = person.get("contactDetails") or {}
        recipients = {"SMS": details.get("PP"), "EMAIL": details.get("PE")}
        for channel, enabled in info["channels"].items():
            if not enabled:
                continue
            conn.execute(
                "INSERT INTO DP_COMMUNICATION_LOG "
                "(AUTH_ID, CHANNEL, RECIPIENT, STATUS, EVENT_TYPE, CREATED_DATE) "
                "VALUES (?,?,?,?,?,?)",
                (auth_id, channel, recipients.get(channel), "SENT", event_type, stamp),
            )
            written += 1
    return written


def _find_entry(conn: sqlite3.Connection, info: dict[str, Any]) -> dict | None:
    return db.one(
        conn,
        "SELECT * FROM DP_AUTH_ENTRY WHERE EXT_EVENT_ID=? AND BOOKING_ROOM_ID=?",
        (info["booking_id"], info["booking_room_id"]),
    )


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def process(
    conn: sqlite3.Connection,
    payload: dict,
    break_mode: str | None = None,
    exclude_event_id: int | None = None,
) -> dict:
    """Handle one PMS event. Returns a response envelope shaped for the dashboard."""
    info = _extract(payload)
    event_type = info["event_type"]

    if event_type not in VALID_EVENTS:
        raise RigError(422, "UNKNOWN_EVENT", f"unsupported eventType {event_type!r}")
    if not info["booking_id"]:
        raise RigError(422, "NO_BOOKING", "payload has no event.bookingId")

    # A6 — idempotency. A byte-identical replayed payload must not create a second grant.
    if break_mode != "duplicate_double_key":
        canonical = _canonical(payload)
        replay = db.one(
            conn,
            "SELECT ID FROM RIG_EVENTS WHERE EVENT_TYPE=? AND BOOKING_ID=? AND BODY=? AND ID<>?",
            (event_type, info["booking_id"], canonical, exclude_event_id or -1),
        )
        if replay:
            entry = _find_entry(conn, info)
            return {
                "status": 200,
                "body": {"result": "IGNORED_DUPLICATE", "authId": entry["AUTH_ID"] if entry else None},
                "trace": ["duplicate payload ignored (idempotent)"],
            }

    entry = _find_entry(conn, info)
    trace: list[str] = []
    stamp = db.now()

    if event_type == "reservationcreate":
        if entry:
            trace.append("auth entry already existed — updated instead of duplicated")
            auth_id = entry["AUTH_ID"]
            conn.execute(
                "UPDATE DP_AUTH_ENTRY SET ROOM_ID=?, ROOM_NUMBER=?, ARRIVAL_DATE=?, "
                "DEPARTURE_DATE=?, AUTH_STATUS='A', UPDATED_DATE=? WHERE AUTH_ID=?",
                (
                    info["room_id"],
                    info["room_number"],
                    _iso(info["arrival"]),
                    _iso(info["departure"]),
                    stamp,
                    auth_id,
                ),
            )
        else:
            auth_id = db.new_id("a")
            conn.execute(
                "INSERT INTO DP_AUTH_ENTRY (AUTH_ID, EXT_EVENT_ID, BOOKING_ROOM_ID, ROOM_ID, "
                "ROOM_NUMBER, ARRIVAL_DATE, DEPARTURE_DATE, AUTH_STATUS, CREATED_DATE, UPDATED_DATE) "
                "VALUES (?,?,?,?,?,?,?,'A',?,?)",
                (
                    auth_id,
                    info["booking_id"],
                    info["booking_room_id"],
                    info["room_id"],
                    info["room_number"],
                    _iso(info["arrival"]),
                    _iso(info["departure"]),
                    stamp,
                    stamp,
                ),
            )
            trace.append(f"auth entry created ({auth_id})")
        _grant_key(conn, auth_id, info["room_id"], break_mode)
        trace.append(f"key granted on {ROOMS[info['room_id']]['device']}")

    elif event_type == "reservationcheckin":
        if not entry:
            raise RigError(409, "NO_BOOKING_TO_CHECKIN", "check-in arrived before any create")
        auth_id = entry["AUTH_ID"]

        booked_arrival = _parse(entry["ARRIVAL_DATE"])
        if (
            break_mode == "early_checkin_rejected"
            and booked_arrival
            and info["arrival"]
            and (booked_arrival - info["arrival"]).total_seconds() > 3600
        ):
            raise RigError(
                422,
                "EARLY_CHECKIN_NOT_ALLOWED",
                f"check-in at {_iso(info['arrival'])} is more than 1h before the booked arrival",
            )

        conn.execute(
            "UPDATE DP_AUTH_ENTRY SET AUTH_STATUS='A', UPDATED_DATE=? WHERE AUTH_ID=?",
            (stamp, auth_id),
        )
        _grant_key(conn, auth_id, entry["ROOM_ID"], break_mode)
        trace.append("auth entry confirmed active")

    elif event_type == "reservationModify":
        if not entry:
            raise RigError(409, "NO_BOOKING_TO_MODIFY", "modify arrived before any create")
        auth_id = entry["AUTH_ID"]
        old_room = entry["ROOM_ID"]
        new_room = info["room_id"]

        arrival = entry["ARRIVAL_DATE"] if break_mode == "dates_not_updated" else _iso(info["arrival"])
        departure = entry["DEPARTURE_DATE"] if break_mode == "dates_not_updated" else _iso(info["departure"])

        conn.execute(
            "UPDATE DP_AUTH_ENTRY SET ROOM_ID=?, ROOM_NUMBER=?, ARRIVAL_DATE=?, "
            "DEPARTURE_DATE=?, UPDATED_DATE=? WHERE AUTH_ID=?",
            (new_room, info["room_number"], arrival, departure, stamp, auth_id),
        )
        trace.append(f"auth entry updated to room {new_room}")

        if old_room != new_room:
            keep = ROOMS[old_room]["device"] if break_mode == "room_change_no_revoke" else None
            revoked = _revoke_all(conn, auth_id, keep_device=keep)
            trace.append(
                f"old room {old_room} key revoked ({revoked} row)"
                if break_mode != "room_change_no_revoke"
                else f"BREAK: old room {old_room} key NOT revoked"
            )
            _grant_key(conn, auth_id, new_room, break_mode)
            trace.append(f"new key granted on {ROOMS[new_room]['device']}")

    else:  # reservationcheckout
        if not entry:
            raise RigError(409, "NO_BOOKING_TO_CHECKOUT", "check-out arrived before any create")
        auth_id = entry["AUTH_ID"]
        conn.execute(
            "UPDATE DP_AUTH_ENTRY SET AUTH_STATUS='I', UPDATED_DATE=? WHERE AUTH_ID=?",
            (stamp, auth_id),
        )
        revoked = _revoke_all(conn, auth_id)
        trace.append(f"auth entry inactive; {revoked} key(s) revoked")

    notifications = _notify(conn, auth_id, info, event_type, break_mode)
    trace.append(f"{notifications} notification row(s) written")

    return {
        "status": 200,
        "body": {"result": "OK", "authId": auth_id, "eventType": event_type},
        "trace": trace,
    }


def record_event(conn: sqlite3.Connection, payload: dict) -> int:
    """Log the arrival and return its row id, so duplicate detection can skip itself."""
    event = payload.get("event") or {}
    cur = conn.execute(
        "INSERT INTO RIG_EVENTS (EVENT_TYPE, BOOKING_ID, NOTIFY, BODY, RECEIVED_AT) "
        "VALUES (?,?,?,?,?)",
        (
            str(event.get("eventType") or ""),
            str(event.get("bookingId") or ""),
            1 if payload.get("notify") else 0,
            _canonical(payload),
            db.now(),
        ),
    )
    return int(cur.lastrowid)
