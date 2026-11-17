"""HTTP client — the only place that talks to the network.

Compare with a UI test: instead of page.goto(), we call requests. Same idea, different
door. Kept tiny on purpose so there is nothing to hide behind.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


@dataclass
class Response:
    status: int
    body: dict
    elapsed_ms: int
    raw: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class Client:
    base_url: str = field(default_factory=lambda: os.environ.get("RIG_BASE_URL", "http://127.0.0.1:8000"))
    user: str = field(default_factory=lambda: os.environ.get("RIG_USER", "alice"))
    password: str = field(default_factory=lambda: os.environ.get("RIG_PASS", "test123"))
    source: str = "openhotel"
    timeout: int = 15

    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        return f"Basic {token}"

    def _request(self, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> Response:
        url = self.base_url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        data = None
        headers = {"Authorization": self._auth_header(), "Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            status = exc.code
        elapsed = int((time.time() - started) * 1000)

        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"_raw": raw}
        return Response(status=status, body=body, elapsed_ms=elapsed, raw=raw)

    def post_event(self, payload: dict) -> Response:
        return self._request("POST", "/pms", payload=payload, params={"source": self.source})

    def find_auth_entry(self, booking_id: str, status: str = "A") -> Response:
        return self._request(
            "GET", "/findauthentry", params={"authStatus": status, "extEventId": booking_id}
        )

    def device_chars(self, auth_id: str) -> Response:
        return self._request(
            "GET", "/authentry/devicechars/notification", params={"authId": auth_id}
        )

    def reset(self) -> Response:
        return self._request("POST", "/api/reset")

    def set_break_mode(self, mode: str | None) -> Response:
        return self._request("POST", "/api/break", params={"mode": mode or "none"})
