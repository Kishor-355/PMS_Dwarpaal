#!/usr/bin/env python3
"""One command to start the whole practice rig.

    python3 practice/run_demo.py            # port 8000, opens nothing
    PORT=8080 python3 practice/run_demo.py  # different port

No pip install needed: http.server, sqlite3 and urllib are standard library.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PRACTICE = Path(__file__).resolve().parent
if str(PRACTICE) not in sys.path:
    sys.path.insert(0, str(PRACTICE))

from fake_dwarpaal import db, logic, server  # noqa: E402

PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")


def main() -> None:
    db.connect(str(server.RIG_DB)).close()

    print("")
    print("  PMS practice rig")
    print("  " + "-" * 58)
    print(f"  [1/4] database       {server.RIG_DB.name:<28} ready")
    print(f"  [2/4] fake PMS       4 event types{'':<16} ready")
    print(f"  [3/4] fake Dwarpaal  http://{HOST}:{PORT:<18} listening")
    print(f"  [4/4] dashboard      http://127.0.0.1:{PORT}/dashboard")
    print("  " + "-" * 58)
    print(f"  basic auth: {server.USER} / {server.PASSWORD}")
    print(f"  break mode: {logic.BREAK_MODES.get(server.STATE['break_mode'], 'none')}")
    print("")
    print("  Harness in another terminal:  python3 practice/harness/runner.py")
    print("")

    httpd = server.serve(host=HOST, port=PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped")


if __name__ == "__main__":
    main()
