# Mock backend (temporary)

Rough stand-in API so `../frontend` has something to talk to before the real
Phase-4 backend (Flask/FastAPI + SQLite, per `Plan.md`) exists. In-memory only
— all data resets every time you restart it.

## Run

```
pip install -r requirements.txt
python app.py
```

Listens on `http://127.0.0.1:8000`, matching `API_BASE` in `../frontend/script.js`.

## Demo logins

- `admin` / `admin123` — admin account (sees the admin panel, pending-user approvals)
- `agent.soc` / `demo123` — regular SOC agent
- `agent.l1` / `demo123` — regular L1 agent
- `agent.pending` — registered but not yet approved, for testing the approve/reject flow

Change these freely — they're seeded fresh every restart in `app.py`, not stored anywhere.

## What's mocked vs. real

- No database — a Python dict in memory. Restarting the server wipes everything back to the seed data.
- No real classification — seed tickets already have a group (`tag`) assigned; the actual
  keyword-matching logic lives client-side in `script.js`'s `classify()`.
- Auth is a random token handed out on login with no expiry — fine for a local demo, not for anything else.
- `/api/track/<token>` — the tracking tokens for the seed tickets print to the console on startup.

## When the real backend is ready

Swap this file out for the Phase 4 Flask/FastAPI service that reads from SQLite (Fault → Syslog →
Classify → Diagnose). Keep the same route paths/response shapes and `../frontend` doesn't need to change.
