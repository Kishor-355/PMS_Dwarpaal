"""Layer 2 — HTTP surface: the fake Dwarpaal sandbox + the dashboard.

Endpoints mirror the real ones from the guide:

    POST /pms?source=openhotel          basic auth    -> process one PMS event
    GET  /findauthentry?authStatus=A&extEventId=<id>  -> { authId: ... }
    GET  /authentry/devicechars/notification?authId=  -> device chars + comms

Plus rig-only helpers used by the dashboard (clearly marked RIG ONLY):

    GET  /api/state          all tables as JSON
    POST /api/send?type=...  trigger a sample event from the dashboard
    POST /api/reset          wipe the tables
    POST /api/break?mode=... set BREAK_MODE at runtime
    POST /api/run-harness    run the practice harness and return its report
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PRACTICE = Path(__file__).resolve().parents[1]
if str(PRACTICE) not in sys.path:
    sys.path.insert(0, str(PRACTICE))

from fake_dwarpaal import db, logic  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "index.html"
RIG_DB = ROOT / "rig.db"

USER = os.environ.get("RIG_USER", "alice")
PASSWORD = os.environ.get("RIG_PASS", "test123")

STATE = {"break_mode": os.environ.get("BREAK_MODE") or None, "last_run": None}
LOCK = threading.Lock()


def _conn():
    return db.connect(str(RIG_DB))


def snapshot() -> dict:
    conn = _conn()
    try:
        return {
            "break_mode": STATE["break_mode"],
            "break_description": logic.BREAK_MODES.get(STATE["break_mode"], ""),
            "last_run": STATE["last_run"],
            "tables": {
                "DP_AUTH_ENTRY": db.rows(conn, "SELECT * FROM DP_AUTH_ENTRY ORDER BY UPDATED_DATE DESC"),
                "DP_AUTH_DEVICE_CHARS": db.rows(conn, "SELECT * FROM DP_AUTH_DEVICE_CHARS ORDER BY ID DESC"),
                "DP_COMMUNICATION_LOG": db.rows(conn, "SELECT * FROM DP_COMMUNICATION_LOG ORDER BY ID DESC"),
                "RIG_EVENTS": db.rows(conn, "SELECT ID, EVENT_TYPE, BOOKING_ID, NOTIFY, RECEIVED_AT FROM RIG_EVENTS ORDER BY ID DESC LIMIT 50"),
            },
            "break_modes": [{"mode": k, "description": v} for k, v in logic.BREAK_MODES.items()],
        }
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "RigDwarpaal/1.0"

    # -- helpers ----------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj, default=str).encode(), "application/json")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise logic.RigError(400, "BAD_JSON", f"body is not valid JSON: {exc}") from exc

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode()
        except Exception:
            return False
        return decoded == f"{USER}:{PASSWORD}"

    def log_message(self, fmt, *args):  # quieter logs
        if os.environ.get("RIG_VERBOSE"):
            super().log_message(fmt, *args)

    # -- routing ----------------------------------------------------------
    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path

        if path in ("/standalone", "/standalone.html"):
            standalone = ROOT / "standalone.html"
            return self._send(200, standalone.read_bytes(), "text/html; charset=utf-8")

        if path in ("/", "/dashboard", "/dashboard/"):
            html = DASHBOARD.read_bytes() if DASHBOARD.exists() else b"<h1>dashboard missing</h1>"
            return self._send(200, html, "text/html; charset=utf-8")

        if path == "/api/state":
            return self._json(200, snapshot())

        if path == "/healthz":
            return self._json(200, {"ok": True, "break_mode": STATE["break_mode"]})

        if not self._authorised():
            return self._json(401, {"error": {"code": "UNAUTHORISED", "message": "bad basic auth"}})

        if path == "/findauthentry":
            booking = (query.get("extEventId") or [""])[0]
            status = (query.get("authStatus") or ["A"])[0]
            conn = _conn()
            try:
                row = db.one(
                    conn,
                    "SELECT * FROM DP_AUTH_ENTRY WHERE EXT_EVENT_ID=? AND AUTH_STATUS=? "
                    "ORDER BY UPDATED_DATE DESC LIMIT 1",
                    (booking, status),
                )
                if not row:
                    return self._json(404, {"error": {"code": "NOT_FOUND", "message": "no auth entry"}})
                return self._json(200, {"authId": row["AUTH_ID"], "entry": row})
            finally:
                conn.close()

        if path == "/authentry/devicechars/notification":
            auth_id = (query.get("authId") or [""])[0]
            conn = _conn()
            try:
                chars = db.rows(conn, "SELECT * FROM DP_AUTH_DEVICE_CHARS WHERE AUTH_ID=? ORDER BY ID", (auth_id,))
                comms = db.rows(conn, "SELECT * FROM DP_COMMUNICATION_LOG WHERE AUTH_ID=? ORDER BY ID", (auth_id,))
                return self._json(200, {"authId": auth_id, "deviceChars": chars, "communication": comms})
            finally:
                conn.close()

        return self._json(404, {"error": {"code": "NO_ROUTE", "message": f"no route for GET {path}"}})

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path

        try:
            if path == "/pms":
                return self._handle_pms(query)

            if path == "/api/send":
                event_type = (query.get("type") or ["reservationcreate"])[0]
                from fake_pms import events as pms_events  # local import keeps startup cheap

                payload = pms_events.build(event_type)
                conn = _conn()
                try:
                    event_row = logic.record_event(conn, payload)
                    result = logic.process(conn, payload, STATE["break_mode"], event_row)
                finally:
                    conn.close()
                return self._json(200, {"sent": payload, **result})

            if path == "/api/tour":
                step = int((query.get("step") or ["1"])[0])
                return self._tour(step)

            if path == "/api/reset":
                conn = _conn()
                try:
                    db.reset(conn)
                finally:
                    conn.close()
                STATE["last_run"] = None
                return self._json(200, {"ok": True})

            if path == "/api/break":
                mode = (query.get("mode") or ["none"])[0]
                STATE["break_mode"] = None if mode in ("none", "", "null") else mode
                return self._json(200, {"break_mode": STATE["break_mode"]})

            if path == "/api/run-harness":
                from harness import runner  # local import on purpose

                report = runner.run_once(verbose=False)
                STATE["last_run"] = report["summary"]
                return self._json(200, report)

            return self._json(404, {"error": {"code": "NO_ROUTE", "message": f"no route for POST {path}"}})

        except logic.RigError as exc:
            return self._json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except Exception as exc:  # pragma: no cover - defensive
            traceback.print_exc()
            return self._json(500, {"error": {"code": "RIG_INTERNAL", "message": str(exc)}})

    # -- rig only: the guided tour ---------------------------------------
    def _tour(self, step: int):
        """One click = one chapter of the story. Same booking id across 1-4."""
        from fake_pms import events as pms_events

        demo = "RIG-DEMO-01"
        demo_room = "RIG-DEMO-01R"
        payloads: list[tuple[str, dict]] = []

        if step == 1:
            payloads = [("create 204", pms_events.build("reservationcreate", booking_id=demo,
                                                        booking_room_id=demo_room, room_id="609520"))]
        elif step == 2:
            payloads = [("check-in", pms_events.build("reservationcheckin", booking_id=demo,
                                                      booking_room_id=demo_room, room_id="609520"))]
        elif step == 3:
            payloads = [("modify 204 -> 210", pms_events.build("reservationModify", booking_id=demo,
                                                               booking_room_id=demo_room, room_id="609521"))]
        elif step == 4:
            payloads = [("check-out", pms_events.build("reservationcheckout", booking_id=demo,
                                                       booking_room_id=demo_room, room_id="609521"))]
        elif step == 5:
            quiet = pms_events.build("reservationcreate", booking_id="RIG-DEMO-02",
                                     booking_room_id="RIG-DEMO-02R", room_id="609518",
                                     notify=False, email=False, mobile=False)
            payloads = [("create with notify=false", quiet)]
        elif step == 6:
            same = pms_events.build("reservationcreate", booking_id="RIG-DEMO-03",
                                    booking_room_id="RIG-DEMO-03R", room_id="609519")
            payloads = [("first delivery", same), ("identical replay", same)]
        else:
            return self._json(400, {"error": {"code": "BAD_STEP", "message": "step must be 1..6"}})

        results = []
        for label, payload in payloads:
            conn = _conn()
            try:
                event_row = logic.record_event(conn, payload)
                outcome = logic.process(conn, payload, STATE["break_mode"], event_row)
            except logic.RigError as exc:
                results.append({"label": label, "status": exc.status,
                                "error": {"code": exc.code, "message": exc.message}})
                continue
            finally:
                conn.close()
            results.append({"label": label, "status": outcome["status"],
                            "body": outcome["body"], "trace": outcome["trace"]})
        return self._json(200, {"step": step, "results": results})

    def _handle_pms(self, query: dict):
        if not self._authorised():
            return self._json(401, {"error": {"code": "UNAUTHORISED", "message": "bad basic auth"}})
        source = (query.get("source") or [""])[0]
        if source != "openhotel":
            return self._json(
                400,
                {"error": {"code": "UNKNOWN_SOURCE", "message": f"unknown PMS source {source!r}"}},
            )
        payload = self._body()

        with LOCK:
            conn = _conn()
            try:
                event_row = logic.record_event(conn, payload)
                result = logic.process(conn, payload, STATE["break_mode"], event_row)
            finally:
                conn.close()

        return self._json(result["status"], {**result["body"], "trace": result["trace"]})


def serve(host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd


if __name__ == "__main__":
    db.connect(str(RIG_DB)).close()
    server = serve(port=int(os.environ.get("PORT", "8000")))
    print(f"fake Dwarpaal listening on http://0.0.0.0:{server.server_port}")
    server.serve_forever()
