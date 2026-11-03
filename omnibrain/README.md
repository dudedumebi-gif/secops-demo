# OmniBrain — SecOps product-management app (runnable)

OmniBrain does the Senior Product Manager, SecOps product work behind a SOC
console: intake and RICE-ranked backlog, detection lifecycle, telemetry
onboarding, SOAR governance with approval gates, roadmap and launch
readiness, metrics and operating cadence, and an assistant grounded in the
live workspace.

All data is **simulated for demonstration**. Seeded rows are derived from
the Senior PM, SecOps job scope, not live employer data. No employer
branding is used.

## Run it (no install, no API keys)

Requirements: Python 3 only — standard library only
(`http.server`, `sqlite3`, `json`, `urllib`). No npm, no pip packages,
no external services.

```bash
cd omnibrain
python3 server.py
```

Then open http://localhost:8765

- Port override: `PORT=9000 python3 server.py`
- Reset to the seed data: `python3 server.py --reset`
  (deletes `omnibrain.db`; it is recreated and seeded on first run)

Data is saved locally to `omnibrain.db` (SQLite, git-ignored). Edits in
the UI persist across restarts. The SOC demo in the parent repo is
different: it saves nothing.

## What is inside

- `server.py` — HTTP server + JSON API + SQLite store + offline,
  rule-based assistant (keyword matching, RICE-grounded prioritisation,
  template drafts). It never calls an external service.
- `public/index.html`, `public/app.js`, `public/styles.css` — vanilla
  JavaScript frontend, no build step. Tabs: Mission Control, Backlog,
  Detections, Telemetry, SOAR, Roadmap, Launches, MITRE, Ask OmniBrain.
- `workspace-summary.md` — seed snapshot reference for the workspace
  the app starts with.

## MITRE ATT&CK heat map (active, not a placeholder)

The MITRE tab is derived live from the detections in the database
(`GET /api/mitre`, also included in `GET /api/dashboard` as
`mitre_heatmap`) — it splits each detection's MITRE field on ` / `, so
adding or changing a detection changes the grid. Unknown technique
codes are shown under “Unknown”, never dropped.

Primary tactic mapping used (Enterprise ATT&CK): T1078 Valid Accounts →
Persistence (also Initial Access / Privilege Escalation / Defense
Evasion), T1550 Use Alternate Authentication Material → Defense
Evasion (also Lateral Movement / Persistence), T1059.001 PowerShell →
Execution, T1003.001 LSASS Memory → Credential Access, T1110 Brute
Force → Credential Access, T1071.004 DNS → Command and Control,
T1098 Account Manipulation → Persistence, T1486 Data Encrypted for
Impact → Impact.

Coverage score per technique: 0 = no detection, 1 = Idea/Development,
2 = Testing/Tuning/Deployment, 3 = Production. Tactic status: covered
(at least one Production technique), partial (techniques present, none
in Production), gap (no techniques). Seed result: 8 unique techniques;
gaps include Reconnaissance, Resource Development, Discovery,
Collection, and Exfiltration.

## MTTR that works (not a constant)

MTTR is computed from a new `incidents` table, not hardcoded. Seed:
three Critical simulated sample incidents at 32, 38, and 44 minutes,
averaging exactly 38.0 minutes, so the headline KPI is preserved as a
computed mean. If the table were empty, the dashboard falls back to
38.0 with source “seed baseline”; with rows, the source is “measured
from incident log”.

Thresholds — industry benchmarks, configurable as `MTTR_THRESHOLDS`
at the top of `server.py`. There is no single official NIST/ISO MTTR
standard, so do not present these as one:

| Severity | Threshold |
| --- | --- |
| Critical | 60 minutes |
| High | 120 minutes |
| Medium | 240 minutes |
| Low | 480 minutes |

Overall benchmark reference range: 120–240 minutes (2–4 hours), for
context only, not a hard threshold. Per severity the dashboard shows
mean, median, count, threshold, and pass/breach/no_data status.

Log a real incident from Mission Control (“Log incident response
time”) or `POST /api/incidents {title, severity, mttr_mins}` — severity
must be Critical/High/Medium/Low and `mttr_mins` must be > 0, otherwise
HTTP 400. Logging one moves overall MTTR and its threshold status
immediately. `GET /api/incidents` lists the log.

The assistant remains offline and rule-based for now — Ask / Triage /
Draft are grounded in the live database and call no external service.
Online LLM support, live endpoints, and auto-refresh are separate
future work, not implemented here.

## Key behaviours to try

- **Approval gate:** High / Critical playbooks require a named
  approver. Approving “Isolate endpoint (EDR)” with no approver fails
  with HTTP 400 and its status stays In Review; with an approver it
  moves to Approved.
- **Launch gate:** “Composite: auth anomaly chain — v2 rollout”
  shows BLOCKED until every checklist item is ticked.
- **RICE:** backlog RICE is computed as
  `reach × impact × (confidence / 100) / effort`, e.g.
  10 × 8 × 0.80 / 4 = 16.0.
- **Ask / Triage / Draft:** grounded only in rows actually in the
  database — Ask references real backlog, detection, and telemetry
  titles; it does not invent detections or sources.

## API (JSON in/out)

- `GET /api/dashboard` — KPIs recomputed from live tables, brief, all lists
- `POST /api/backlog` (auto-computes RICE), `PATCH /api/backlog/{id}`,
  `DELETE /api/backlog/{id}`
- `POST /api/triage {text, save?}` — structured scored recommendation
- `POST /api/detections`, `PATCH /api/detections/{id} {stage}`
- `POST /api/telemetry`, `PATCH /api/telemetry/{id} {status, coverage}`
- `POST /api/playbooks`, `POST /api/playbooks/{id}/approve {approver}`,
  `PATCH /api/playbooks/{id} {status}`
- `POST /api/roadmap`, `POST /api/decisions`, `POST /api/launches`,
  `POST /api/launches/{id}/checks/{index}/toggle`
- `POST /api/ask {question}`, `POST /api/draft {kind, backlog_id?, topic?}`,
  `POST /api/refresh-brief`
- `GET /api/mitre` — MITRE heat map derived from live detections
- `GET /api/incidents`, `POST /api/incidents {title, severity, mttr_mins}` — incident log that drives MTTR

## Optional real LLM (never required)

If you later want a real LLM, set `OMNIBRAIN_LLM_URL` to an endpoint
you control and wire it into `ask_answer` — the offline, rule-based
path remains the default and the app runs fully without it. No key
is read from the app by default.
