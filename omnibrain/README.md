# OmniBrain — SecOps product-management app (runnable)

OmniBrain does the Senior Product Manager, SecOps product work behind a SOC
console: intake and RICE-ranked backlog, detection lifecycle, telemetry
onboarding, SOAR governance with approval gates, roadmap and launch
readiness, metrics and operating cadence, and an assistant grounded in the
live workspace.

All data is **simulated for demonstration**. Seeded rows are derived from
the Senior PM, SecOps job scope, not live employer data. No employer
branding is used.

## Run it (no install, no API keys, SQLite by default)

Requirements: Python 3 only — standard library only
(`http.server`, `sqlite3`, `json`, `urllib`). No npm, no pip packages,
no external services, unless you choose Turso or Postgres below.

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

## Stateful hosting: Turso (optional, Render — the chosen route)

Local SQLite is fine locally, but on Render Free the filesystem is
ephemeral and SQLite edits are lost on spin-down/redeploy. For a
Render deploy, set `TURSO_DATABASE_URL` (libsql:// form) and
`TURSO_AUTH_TOKEN` and the app uses Turso remote instead — same
handlers, same seed, data survives spin-down. See `TURSO.md` for
the exact dashboard steps and the spin-down proof test. Turso wins
over Postgres if both are set; if the Turso URL is set without a
token, startup fails fast rather than silently reseeding.
Locally, with both Turso variables unset, the app still means
SQLite and no install; with Turso set, the only extra install is
`pip install -r requirements.txt` (`libsql`, imported lazily,
never required for SQLite). Never commit a real URL or token;
`.env` is git-ignored.

Postgres fallback (not the chosen route): with a postgres
`DATABASE_URL` set and no Turso variables, the app uses Postgres
instead (see `NEON.md`; Neon itself was ruled out because the
user's free projects are used up, but the adapter works for any
postgres:// provider). `psycopg[binary]` is likewise lazy.

## What is inside

- `server.py` — HTTP server + JSON API + SQLite store + rule-based
  assistant (keyword matching, RICE-grounded prioritisation,
  template drafts) that stays authoritative; with `OPENAI_API_KEY`
  set it adds a gpt-4o-mini natural-language layer on top (see
  `LLM.md`). Without the key it calls no external service.
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

The assistant is rule-based by default — Ask / Triage / Draft are
grounded in the live database; with `OPENAI_API_KEY` set, a
gpt-4o-mini layer (see `LLM.md`) rephrases/drafts from the
rule-based Steps 1–3 result and real rows, with silent fallback to
rule-based when the key is unset or the call fails. Auto-refresh
remains manual (refresh-brief).

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
  `POST /api/refresh-brief` — ask/triage/draft responses include
  `llm_answer`, `llm_used`, `llm_model`, `llm_error` (see `LLM.md`)
- `GET /api/llm-status` — `{configured, model: "gpt-4o-mini"}`; `GET
  /api/dashboard` includes `llm_available`
- `GET /api/mitre` — MITRE heat map derived from live detections
- `GET /api/incidents`, `POST /api/incidents {title, severity, mttr_mins}` — incident log that drives MTTR

## Optional LLM layer — gpt-4o-mini (wired, see LLM.md)

Set `OPENAI_API_KEY` (Render dashboard env var, `sync: false` in
`render.yaml`) to enable one gpt-4o-mini call per ask/triage/draft,
grounded only in the rule-based Steps 1–3 result and real DB rows.
Without the key the app is fully offline and rule-based; any LLM
failure falls back silently to the rule-based response. No key is
read from the app by default, and `OMNIBRAIN_LLM_URL` is an old
placeholder that is not read.

## Render prep (review, not deployed; Turso now the stateful option)

See `RENDER.md` and the repo-root `render.yaml`. Locally the server
binds `127.0.0.1` by default; Render needs `HOST=0.0.0.0`
(`HOST=0.0.0.0 python3 server.py`, Render injects `PORT`).
Free tier works for a demo URL but its filesystem is ephemeral, so
SQLite edits are lost on spin-down/redeploy and the app reseeds.
With Turso set (see `TURSO.md`), edits survive
spin-down/redeploy; only the first-visit wake delay after ~15
minutes idle remains. Without Turso (or Postgres) set, Render Free
still reseeds — that is the old behaviour, not a bug.
A Render URL is public by default and this app has no login —
local-only is the private mode, not the private GitHub repo.
`OPENAI_API_KEY` is an optional, unset-by-default placeholder in
`render.yaml` (`sync: false`); never commit a real key.

## Rule-based reasoning — Steps 1–3 (explicit in ask/triage/draft)

Every ask, triage, and draft response now includes:

- **Step 1 MITRE:** technique + tactic identified from keywords,
  with confidence and evidence keywords. If no technique matches,
  it says "no MITRE technique matched — human review required"
  rather than inventing one. Techniques recognised include the
  original eight (T1078, T1550, T1059.001, T1003.001, T1110,
  T1071.004, T1098, T1486) plus gap-tactic ones (T1190, T1133,
  T1566, T1548, T1068, T1083, T1087, T1021, T1005, T1048, T1595,
  T1583, T1588).
- **Step 2 NIST CSF 2.0:** one primary function — Govern, Identify,
  Protect, Detect, Respond, or Recover — with category and
  rationale (e.g. detection -> Detect / Continuous Monitoring,
  containment -> Respond / Incident Mitigation, approval gate ->
  Govern / Roles, asset inventory -> Identify / Asset Management,
  MFA/allowlist -> Protect / Identity Management, rollback/lessons
  -> Recover / Incident Recovery).
- **Step 3 Decision:** RICE, owner team, approval_required
  (High/Critical actions = yes), launch-gate checklist, and
  success criteria.

## Free public threat-intel enrichment (external context only)

`GET /api/threat-intel` returns cached data; `POST
/api/threat-intel/refresh` attempts a live fetch (10s timeout,
per-source ok/failed, never fails the dashboard if offline).
Sources, in order, capped at 5 items each: CISA KEV
(free, no auth, most recent by dateAdded) and NVD CVE (free,
no key, rate-limited, best-effort, filtered to the last 7 days
so the list is recent, not oldest-first). abuse.ch URLhaus is
documented as unavailable, not attempted: verified Nov 2026,
its public no-key downloads return 410 Gone and are now
key-gated, and this app takes no keys. MITRE enrichment is the local
technique/tactic mapping — the full STIX bundle is NOT downloaded
at runtime (too large); its source is
https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json
for a future offline snapshot. All enrichment is labelled
"public external threat context, not internal telemetry" in the
UI and API. No internal data is sent to these feeds (they are
read-only GETs).

## MITRE gap mitigations

`GET /api/mitre` now includes `mitigations[]` for all 8
primary-tactic gaps, plus a note that some gaps are
primary-tactic only (T1078 also covers Initial Access and T1550
also covers Lateral Movement as secondary tactics, so those
tactics are not wholly blind). Each mitigation has tactic,
recommended detection, technique, log source needed, NIST
function, effort, priority, and suggested backlog title.
Priority order: P0 Initial Access, Privilege Escalation,
Lateral Movement (attacker progression); then P1 Exfiltration,
Collection (data loss); then P1 Discovery, P2 Reconnaissance
(early warning); then P2 Resource Development (threat-intel
monitoring, lowest direct detection value).
Use `POST /api/mitre/mitigations/{tactic}/create-backlog`
(e.g. `Initial%20Access`) to turn a mitigation into a backlog
item (auto-RICE, status intake), or the "Add to backlog" button
on each mitigation card in the MITRE tab.
