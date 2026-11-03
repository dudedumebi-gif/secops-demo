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
  Detections, Telemetry, SOAR, Roadmap, Launches, Ask OmniBrain.
- `workspace-summary.md` — seed snapshot reference for the workspace
  the app starts with.

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

## Optional real LLM (never required)

If you later want a real LLM, set `OMNIBRAIN_LLM_URL` to an endpoint
you control and wire it into `ask_answer` — the offline, rule-based
path remains the default and the app runs fully without it. No key
is read from the app by default.
