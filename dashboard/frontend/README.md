# Dashboard frontend (KMUTT senior project)

Static HTML/CSS/JS dashboard carried over from the CSMC project as a starting
point — not React. 3 files (`index.html`, `script.js`, `style.css`) plus a
standalone public tracking page (`track.html` / `track.js`). Zero build step.

## Run

Open `index.html` in a browser, or serve the folder with any static file
server. It expects an API at `API_BASE` (`http://127.0.0.1:8000` — see
`script.js`) — without a backend running there, login and every ticket
action will fail with "Cannot reach the server."

See `../backend` for the mock API that fills that role for now.

## Classification placeholder

`GROUP_FULL`, `GROUP_DATA`, and `classify()` in `script.js` use generic
placeholder group names and keyword rules (`SERVICE-A`, `OWNER-A`, etc.) —
scrubbed of the original project's real customer/carrier identifiers.
Replace with this project's own Owner Group / keyword mapping table once
designed (see `Plan.md` item 5 in the project root).

## Expected ticket-upload CSV columns

Header row, case/spacing-insensitive, common aliases accepted:

```
Incident Number, Event_Start_Time, Event_End_Time, Description, Status, Assigned Group
```

- `Incident Number` — also accepts `IncidentNumber`, `Ticket ID`, `ID`. Blank → row is kept but
  flagged `missingId` and given a placeholder `NO-ID-n`.
- `Assigned Group` — also accepts `AssignedGroup`. Passed through as-is for display; has no effect
  on which dashboard card (SOC/CIC/L1/L2/EVENT) the ticket lands in.
- `Description` is pipe-delimited, e.g. `"SERVICE-A | CUSTOMER-X | LINK DOWN"`. Classification
  (`classify()` in `script.js`, runs client-side) reads field 1 as the keyword and, for the
  `SERVICE-A`/`SERVICE-B` case, field 2 as an owner hint to decide SOC vs L1. Rows that match
  nothing land in "Not yet assigned" for manual triage.
