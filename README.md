# Automated Incident Triage

Automatically detects network faults → opens a Trouble Ticket → classifies which team owns it → diagnoses the root cause.
Course project for ENE372/473, King Mongkut's University of Technology Thonburi (KMUTT).

## How it works

```text
Routers (CML lab)
   │  syslog, UDP 514
   ▼
syslog_server.py    open/close ticket, classify team + priority
   │
   ▼
tickets.db (SQLite)
   │
   ├─▶ diagnose_worker.py   SSH in, find root cause with rules
   │      │  rules can't explain it
   │      ▼
   │   analyze_pending.py   ask a local LLM (Ollama)
   │
   └─▶ Dashboard            operator confirms or corrects the team
```

- **Tickets open and close automatically** from `%LINK-*` syslog messages (`down` → open, `up` → close)
- **Owner group + priority classification** using keyword rules (word-boundary matching), escalated by impact (a whole site down → at least P2)
- **Hybrid root-cause analysis**: rules first, LLM only for cases the rules don't cover
- **Human-in-the-loop**: the system suggests a team, but an operator must confirm it on the dashboard

## Project layout

| Part | Files |
| :-- | :-- |
| Core services | `syslog_server.py`, `diagnose_worker.py`, `analyze_pending.py` |
| Fault injection | `fault_injection.py`, `inject_menu.py`, `c06_congestion.py` |
| Library | `lib/` — syslog parser, classifier, correlation, diagnostics, cause rules, LLM client, etc. |
| Dashboard | `dashboard/backend/app.py` (Flask API), `dashboard/frontend/` |
| Testing / measurement | `measure_latency.py`, `run_*_batch.py`, `test_dispositions.py`, `replay_syslog.py`, `testcases/`, `tools/` |
| Windows shortcuts | `0_` – `8_*.bat` |

Code comments and console output are in Thai.

## Setup

Requires Python 3 (on Windows the command is `py`).

```bat
py -m pip install -r requirements.txt
copy lib\lab_secrets.example.py lib\lab_secrets.py
```

Then edit `lib\lab_secrets.py` with the router username/password for your lab.
Device IPs and ports are set in `lib/mapping.py` (`DEVICE_SSH`) and `fault_injection.py` (`CORE`).

The LLM analysis step needs [Ollama](https://ollama.com) with the `llama3.1:8b` model pulled.

## Running

Open several terminals at once (or double-click the numbered `.bat` files):

| Terminal | Command | |
| :-- | :-- | :-- |
| 1 | `py syslog_server.py` | Listens for syslog — keep running |
| 2 | `py diagnose_worker.py` | SSH diagnosis — keep running |
| 3 | `py dashboard\backend\app.py` | Dashboard API, then open `dashboard\frontend\index.html` |
| 4 | `py inject_menu.py` | Inject test faults |

Test the logic without the lab: `py lib\<file>.py` (every module in `lib/` has unit tests with sample data).

## Lab

Star topology on Cisco Modeling Labs (CML): one Core router, with Access Sites A/B/C each connected to the Core on its own link.
Faults must be injected on the Core, because IOL does not propagate carrier loss to the far end of a link.
