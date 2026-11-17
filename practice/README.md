# Practice rig — the whole system, visible

A local, zero-install simulator of Dwarpaal's PMS integration, built so the concepts can
be **run** instead of read. It is a teaching artefact. It is **not** the deliverable, and
it is deletable with `git rm -r practice` without touching the real project.

> **मराठी:** ही फक्त शिकण्यासाठीची जुडी आहे. Supabase/Vercel/internet/credentials —
> काहीच लागत नाही. फक्त Python 3.9+.

---

## Run it

```bash
python3 practice/run_demo.py
# open http://127.0.0.1:8000/dashboard
```

Second terminal, to run the harness against it:

```bash
python3 practice/harness/runner.py
```

`pip install` — **none**. Everything uses the standard library (`http.server`, `sqlite3`,
`urllib`) on purpose, so day one cannot fail on a dependency problem.

---

## What is inside

```
practice/
  run_demo.py                 start everything
  fake_dwarpaal/
    db.py                     Layer 1 — SQLite schema (the three DP_* tables)
    logic.py                  Layer 2 — the "framework": event → rows
    server.py                 Layer 2 — HTTP endpoints + dashboard server
  fake_pms/events.py          Layer 3 — builds realistic PMS payloads
  harness/
    client.py                 the only place that talks HTTP
    mutate.py                 mask PII, regenerate ids, shift dates
    checks.py                 assertions as data
    report.py                 report.md + results.json
    runner.py                 Layer 4 — THE part that becomes the real project
  dashboard/index.html        Layer 5 — the browser view
  out/                        generated reports
  postman_collection.json     Layer 6 — poke the API by hand
```

---

## The guided tour (dashboard, left column)

| Step | What happens | What you should notice |
| --- | --- | --- |
| 1 create | one event arrives | 1 row in `DP_AUTH_ENTRY`, 1 **ACTIVE** key, 2 notification rows |
| 2 check-in | guest arrives | still exactly **one** key — no duplicate issued |
| 3 modify 204 → 210 | room changes | old lock's key turns **REVOKED**, new one **ACTIVE** |
| 4 check-out | stay ends | auth status `I`, every key **REVOKED** |
| 5 notify=false | master switch off | access granted, `DP_COMMUNICATION_LOG` stays **empty** |
| 6 same event twice | duplicate delivery | second one returns `IGNORED_DUPLICATE`, no second key |

---

## Break it on purpose

`BREAK_MODE` injects one realistic bug at a time. Select it in the dashboard, then press
**Run the harness** and read which check goes red.

| Break mode | Injected bug | Checks that fail |
| --- | --- | --- |
| `room_change_no_revoke` | modify keeps the old room's key alive | A3.3, A3.4, A3.5, A3.6, A3.7 |
| `dates_not_updated` | modify ignores new arrival/departure | A3.8, A3.9 |
| `duplicate_double_key` | replaying an event issues a second key | A2.3, A6.3, A6.4 |
| `notify_ignored` | sends even when `notify=false` | A5.2 |
| `early_checkin_rejected` | refuses a check-in >1h before booked arrival | (see note) |

**Note on `early_checkin_rejected`:** the harness currently always sends check-in at the
shifted arrival time, so it does not yet exercise this branch. That is a *harness gap*,
deliberately left visible — an untested branch is exactly the kind of thing that looks
green forever. The fix is a scenario that sends check-in at `arrival − 4h`, which is what
the guide calls "Step 5: early check-in".

---

## The assumptions the rig encodes

Each is a **guess**, not a fact. They exist so the team can correct them in one place.

| ID | Assumption | Where |
| --- | --- | --- |
| ASSUMPTION-01 | four event types: create, checkin, `reservationModify`, checkout | `logic.VALID_EVENTS` |
| ASSUMPTION-02 | `eventType` is case-sensitive and modify is camelCase | `logic.VALID_EVENTS` |
| ASSUMPTION-03 | a room id absent from the mapping is rejected with **422**, not silently skipped | `logic._extract` |
| ASSUMPTION-04 | a communication row needs **both** `notify` and the per-channel flag | `logic._notify` |
| ASSUMPTION-05 | checkin/modify/checkout before create is a **409**, not a silent create | `logic.process` |
| ASSUMPTION-06 | `extRoomId` maps 1:1 to a physical lock | `logic.ROOMS` |
| ASSUMPTION-07 | check-out sets `AUTH_STATUS='I'` and revokes every key | `logic.process` |
| ASSUMPTION-08 | a byte-identical replay is ignored (idempotent) | `logic.process` |
| ASSUMPTION-09 | key validity is not date-gated in the data model (only status) | `logic.process` |
| ASSUMPTION-10 | `extRoomNumber` is an opaque label, not numeric | everywhere |

ASSUMPTION-09 deserves attention: the real product must stop a key working *before* arrival
and *after* departure. The rig only models status, not time windows. So "early check-in"
cannot be fully reasoned about here — another honest gap.

---

## Try it by hand (Layer 6)

```bash
# unauthorised — expect 401
curl -i -X POST 'http://127.0.0.1:8000/pms?source=openhotel' \
  -H 'Content-Type: application/json' -d '{}'

# authorised — expect 200
curl -i -u alice:test123 -X POST 'http://127.0.0.1:8000/pms?source=openhotel' \
  -H 'Content-Type: application/json' \
  -d '{"event":{"eventType":"reservationcreate","bookingId":"BK1","bookingRoomId":"BR1",
       "arrivalDate":"2026-11-17T10:00:00.000Z","departureDate":"2026-11-19T10:00:00.000Z"},
       "person":[{"contactDetails":{"PP":"+919000000001","PE":"a@example.test"}}],
       "unit":[{"extRoomId":"609520","extRoomNumber":"204"}],
       "notify":true,"notification":{"email":true,"mobile":true}}'
```

Then look it up exactly as the guide says:

```bash
curl -s -u alice:test123 \
  'http://127.0.0.1:8000/findauthentry?authStatus=A&extEventId=BK1'
curl -s -u alice:test123 \
  'http://127.0.0.1:8000/authentry/devicechars/notification?authId=<the id>'
```

A Postman import file is at `practice/postman_collection.json`.

---

## What the rig deliberately does **not** simulate

- **Kafka** — the harness calls HTTP directly, as the real one does
- **Real locks / BLE / keypad / RFID** — `DP_AUTH_DEVICE_CHARS` is just a table
- **Real SMS/email** — "notifications" are rows
- **Concurrency** — one request at a time
- **Time-based key expiry** — see ASSUMPTION-09
- **Dwarpaal's real business rules** — every rule here is our guess

---

## Why this is worth the effort

Reading "assert that the old key is revoked" teaches almost nothing.
**Watching five checks go red because a room change did not revoke the old key** teaches
it permanently — and produces a report that a colleague can read without you.
