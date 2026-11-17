# PMS Dwarpaal

A safe, repeatable test harness for a hotel PMS → access-control integration.

The harness replays recorded PMS events (create, check-in, modify, check-out) against a
sandbox API in the same order production receives them, then reads the resulting records
back and checks them. It writes a human-readable report and a machine-readable result.

## What is in this repository

| Path | What it is |
| --- | --- |
| `practice/` | a self-contained practice rig: a fake hotel PMS, a fake access-control API, the verification harness, and a browser dashboard |
| `practice/README.md` | how the rig works, endpoint by endpoint, and what it deliberately does not simulate |
| `practice/harness/` | the harness itself — event replay, API client, checks, report writer |
| `practice/fake_pms/` | generates the four event types for one booking |
| `practice/fake_dwarpaal/` | the stand-in API and the SQLite tables that mirror the production schema |
| `practice/PMS-Practice-Rig.html` | a single-file copy of the dashboard, no server needed |
| `practice/postman_collection.json` | the same calls as a Postman collection |

## Requirements

Python 3.9 or newer. No third-party packages: the rig uses only the standard library
(`http.server`, `sqlite3`, `urllib`).

## Run it

```bash
python practice/run_demo.py
```

Then open <http://localhost:8000/dashboard>.

In a second terminal, run the harness:

```bash
cd practice
python -m harness.runner
```

It performs 33 checks across six scenarios and writes `practice/out/report_<time>.md`
plus `results_<time>.json`.

## Pointing it elsewhere

The harness reads two environment variables, so the same code can target another
environment without edits:

| Variable | Default | Meaning |
| --- | --- | --- |
| `RIG_BASE_URL` | `http://127.0.0.1:8000` | the API the harness sends events to |
| `PORT` | `8000` | the port the practice rig listens on |

## Credentials

None are stored in this repository. Anything environment-specific belongs in a local
`.env` file, which is git-ignored.

## Licence

Add the licence your organisation requires before publishing.
