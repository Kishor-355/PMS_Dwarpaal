"""Layer 4 — the harness itself.

This is the part that survives into the real project. Everything else in practice/ is a
stand-in for something Dwarpaal owns; this file is the shape of what we will ship.

Flow, per scenario:
    1. build a synthetic PMS event
    2. mask + regenerate ids + shift dates          (mutate.py)
    3. POST it to the API                            (client.py)
    4. look the auth entry up by booking id          (client.py)
    5. read the two satellite tables                 (client.py)
    6. assert every expected property                (checks.py)
    7. write report.md + results.json                (report.py)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PRACTICE = Path(__file__).resolve().parents[1]
if str(PRACTICE) not in sys.path:
    sys.path.insert(0, str(PRACTICE))

from harness import client as client_mod  # noqa: E402
from harness import mutate, report  # noqa: E402
from harness.checks import CheckList  # noqa: E402
from fake_pms import events  # noqa: E402

OUT_DIR = PRACTICE / "out"
ROOM_A = ("609520", "204")
ROOM_B = ("609521", "210")


def _wait_for_auth_id(client, booking_id: str, attempts: int = 10) -> str | None:
    """Poll rather than sleep. Fixed sleeps are how suites become flaky."""
    for _ in range(attempts):
        response = client.find_auth_entry(booking_id)
        if response.ok and response.body.get("authId"):
            return response.body["authId"]
        time.sleep(0.15)
    return None


def _scenario_result(name: str, event_type: str, checks: CheckList, extra: dict | None = None) -> dict:
    return {
        "name": name,
        "event_type": event_type,
        "passed": checks.passed,
        "checks": checks.as_list(),
        **(extra or {}),
    }


def _send(client, payload: dict) -> dict:
    response = client.post_event(payload)
    return {
        "status": response.status,
        "body": response.body,
        "elapsed_ms": response.elapsed_ms,
    }


def run_once(verbose: bool = True) -> dict:
    run_id = mutate.new_run_id()
    client = client_mod.Client()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    break_mode = _current_break_mode(client)

    client.reset()

    booking_id = f"RIG-{run_id}"
    booking_room_id = f"RIGR-{run_id}"
    arrival = None
    scenarios: list[dict] = []

    # ------------------------------------------------------------------ A1
    # create: one auth entry, live key, notifications when notify=true
    payload = events.build(
        "reservationcreate",
        booking_id=booking_id,
        booking_room_id=booking_room_id,
        room_id=ROOM_A[0],
    )
    payload = mutate.shift(mutate.mask(payload))
    sent = _send(client, payload)
    auth_id = _wait_for_auth_id(client, booking_id)

    checks = CheckList()
    checks.add("A1.1", "POST /pms returns HTTP 200", 200, sent["status"])
    checks.true("A1.2", "an auth entry exists for the booking", auth_id is not None, f"authId={auth_id}")
    checks.add("A1.3", "framework reports OK", "OK", sent["body"].get("result"))

    entry = stored = comms = chars = {}
    if auth_id:
        entry = client.find_auth_entry(booking_id).body.get("entry") or {}
        bundle = client.device_chars(auth_id).body
        comms = {"rows": bundle.get("communication") or []}
        chars = {"rows": bundle.get("deviceChars") or []}

        checks.add("A1.4", "auth entry is active", "A", entry.get("AUTH_STATUS"))
        checks.add("A1.5", "auth entry holds the replayed room", ROOM_A[1], entry.get("ROOM_NUMBER"))
        checks.add("A1.6", "arrival stored is the shifted value", payload["event"]["arrivalDate"], entry.get("ARRIVAL_DATE"))
        checks.add("A1.7", "departure stored is the shifted value", payload["event"]["departureDate"], entry.get("DEPARTURE_DATE"))

        active = [c for c in chars["rows"] if c.get("CHAR_STATUS") == "ACTIVE"]
        checks.add("A1.8", "exactly one active lock credential", 1, len(active))
        checks.add("A1.9", "credential sits on the room's lock", f"LOCK-{ROOM_A[1]}", active[0]["DEVICE_ID"] if active else None)
        checks.add("A1.10", "SMS + EMAIL logged (notify=true)", 2, len(comms["rows"]))

    scenarios.append(_scenario_result("A1 create", "reservationcreate", checks))

    # ------------------------------------------------------------------ A2
    # check-in: state stays active, no duplicate credential
    payload = events.build(
        "reservationcheckin",
        booking_id=booking_id,
        booking_room_id=booking_room_id,
        room_id=ROOM_A[0],
    )
    payload = mutate.shift(mutate.mask(payload))
    sent = _send(client, payload)

    checks = CheckList()
    checks.add("A2.1", "POST /pms returns HTTP 200", 200, sent["status"])
    if auth_id:
        entry = client.find_auth_entry(booking_id).body.get("entry") or {}
        bundle = client.device_chars(auth_id).body
        active = [c for c in (bundle.get("deviceChars") or []) if c.get("CHAR_STATUS") == "ACTIVE"]
        checks.add("A2.2", "auth entry still active", "A", entry.get("AUTH_STATUS"))
        checks.add("A2.3", "still exactly one active credential", 1, len(active))
    scenarios.append(_scenario_result("A2 check-in", "reservationcheckin", checks))

    # ------------------------------------------------------------------ A3
    # modify with a ROOM CHANGE — the highest-value assertion in the project
    payload = events.build(
        "reservationModify",
        booking_id=booking_id,
        booking_room_id=booking_room_id,
        room_id=ROOM_B[0],
    )
    payload = mutate.shift(mutate.mask(payload), offset_days=1)
    sent = _send(client, payload)

    checks = CheckList()
    checks.add("A3.1", "POST /pms returns HTTP 200", 200, sent["status"])
    if auth_id:
        entry = client.find_auth_entry(booking_id).body.get("entry") or {}
        bundle = client.device_chars(auth_id).body
        all_chars = bundle.get("deviceChars") or []
        active = [c for c in all_chars if c.get("CHAR_STATUS") == "ACTIVE"]
        revoked = [c for c in all_chars if c.get("CHAR_STATUS") == "REVOKED"]

        checks.add("A3.2", "auth entry points at the new room", ROOM_B[1], entry.get("ROOM_NUMBER"))
        checks.add("A3.3", "old lock has a REVOKED credential row", True,
                   any(c.get("DEVICE_ID") == f"LOCK-{ROOM_A[1]}" for c in revoked))
        checks.add("A3.4", "exactly one active credential", 1, len(active))
        checks.add("A3.5", "active credential is on the NEW room's lock", f"LOCK-{ROOM_B[1]}", active[0]["DEVICE_ID"] if active else None)
        checks.add("A3.6", "no ACTIVE credential on the old lock", 0, len([c for c in active if c.get("DEVICE_ID") == f"LOCK-{ROOM_A[1]}"]))
        checks.add("A3.7", "old lock credential is no longer usable", "REVOKED", next((c["CHAR_STATUS"] for c in all_chars if c.get("DEVICE_ID") == f"LOCK-{ROOM_A[1]}"), None))
        checks.add("A3.8", "modify moved the stored arrival date", payload["event"]["arrivalDate"], entry.get("ARRIVAL_DATE"))
        checks.add("A3.9", "modify moved the stored departure date", payload["event"]["departureDate"], entry.get("DEPARTURE_DATE"))
    scenarios.append(_scenario_result("A3 modify (room change)", "reservationModify", checks))

    # ------------------------------------------------------------------ A4
    # check-out: everything revoked
    payload = events.build(
        "reservationcheckout",
        booking_id=booking_id,
        booking_room_id=booking_room_id,
        room_id=ROOM_B[0],
    )
    payload = mutate.shift(mutate.mask(payload), offset_days=2)
    sent = _send(client, payload)

    checks = CheckList()
    checks.add("A4.1", "POST /pms returns HTTP 200", 200, sent["status"])
    if auth_id:
        entry = client.find_auth_entry(booking_id, status="I").body.get("entry") or {}
        bundle = client.device_chars(auth_id).body
        active = [c for c in (bundle.get("deviceChars") or []) if c.get("CHAR_STATUS") == "ACTIVE"]
        checks.add("A4.2", "auth entry is inactive", "I", entry.get("AUTH_STATUS"))
        checks.add("A4.3", "no credential is still active", 0, len(active))
        checks.add("A4.4", "every credential row for this booking is REVOKED", 0,
                   len([c for c in (bundle.get("deviceChars") or []) if c.get("CHAR_STATUS") != "REVOKED"]))
    scenarios.append(_scenario_result("A4 check-out", "reservationcheckout", checks))

    # ------------------------------------------------------------------ A5
    # negative case: notify=false must produce no communication rows
    payload = events.build("reservationcreate", room_id=ROOM_A[0])
    payload = mutate.without_notifications(mutate.shift(mutate.mask(payload)))
    other_id = payload["event"]["bookingId"]
    sent = _send(client, payload)
    other_auth = _wait_for_auth_id(client, other_id)

    checks = CheckList()
    checks.add("A5.1", "POST /pms returns HTTP 200", 200, sent["status"])
    if other_auth:
        bundle = client.device_chars(other_auth).body
        checks.add("A5.2", "no SMS/EMAIL rows when notify=false", 0, len(bundle.get("communication") or []))
        entry = client.find_auth_entry(other_id).body.get("entry") or {}
        checks.add("A5.3", "access is still granted even when notify=false", "A", entry.get("AUTH_STATUS"))
    scenarios.append(_scenario_result("A5 negative (notify=false)", "reservationcreate", checks))

    # ------------------------------------------------------------------ A6
    # idempotency: the same payload twice must not double the grant
    payload = events.build("reservationcreate", room_id=ROOM_A[0])
    payload = mutate.shift(mutate.mask(payload))
    replay_id = payload["event"]["bookingId"]
    first = _send(client, payload)
    second = _send(client, payload)
    replay_auth = _wait_for_auth_id(client, replay_id)

    checks = CheckList()
    checks.add("A6.1", "first delivery accepted", 200, first["status"])
    checks.add("A6.2", "second delivery also accepted (no 500)", 200, second["status"])
    if replay_auth:
        bundle = client.device_chars(replay_auth).body
        active = [c for c in (bundle.get("deviceChars") or []) if c.get("CHAR_STATUS") == "ACTIVE"]
        checks.add("A6.3", "still exactly one active credential after replay", 1, len(active))
        checks.add("A6.4", "no duplicate notification rows", 2, len(bundle.get("communication") or []))
    scenarios.append(_scenario_result("A6 idempotency (duplicate delivery)", "reservationcreate", checks))

    summary = {
        "total": sum(len(s["checks"]) for s in scenarios),
        "passed": sum(1 for s in scenarios for c in s["checks"] if c["passed"]),
        "failed": sum(1 for s in scenarios for c in s["checks"] if not c["passed"]),
    }

    run = {
        "run_id": run_id,
        "started": started,
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": client.base_url,
        "break_mode": break_mode,
        "summary": summary,
        "scenarios": scenarios,
    }

    md_path, json_path = report.write(OUT_DIR, run)
    run["report_md"] = str(md_path)
    run["report_json"] = str(json_path)

    if verbose:
        print(f"\nPractice harness — run {run_id}")
        print(f"target: {client.base_url}   break_mode: {break_mode or 'none'}")
        print("-" * 64)
        for scenario in scenarios:
            mark = "PASS" if scenario["passed"] else "FAIL"
            print(f"[{mark}] {scenario['name']}  ({len(scenario['checks'])} checks)")
            for check in scenario["checks"]:
                if not check["passed"]:
                    print(f"        x {check['id']} {check['description']}")
                    print(f"          expected {check['expected']!r}  got {check['actual']!r}")
        print("-" * 64)
        print(f"{summary['passed']}/{summary['total']} checks passed")
        print(f"report: {md_path}")

    return run


def _current_break_mode(client) -> str | None:
    try:
        with urllib.request.urlopen(client.base_url.rstrip("/") + "/healthz", timeout=3) as response:
            return json.loads(response.read().decode()).get("break_mode")
    except Exception:
        return os.environ.get("BREAK_MODE") or None


if __name__ == "__main__":
    result = run_once()
    sys.exit(0 if result["summary"]["failed"] == 0 else 1)
