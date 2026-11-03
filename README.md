# secops-demo

A lightweight, de-branded SecOps portfolio demo for a Senior Product Manager, SecOps interview.

All data in both demos is **simulated for demonstration**. Nothing here connects to a live SIEM, SOAR, or employer system, and no employer branding is used.

## What's in here

### soc-demo/ — SOC console demo
A lightweight, Splunk-style interactive SOC console (static HTML, open `soc-demo/index.html` in a browser).

9 views:
- Mission Control (KPIs, 24h alert volume, live event feed, coverage snapshot)
- Incident Queue (10 incidents, severity/status filters)
- Incident investigation drawer (timeline, artifacts, MITRE mapping, assign/escalate)
- Search & Hunt (4 runnable SPL hunts, convert-to-detection)
- Detection Lifecycle board (6 stages, false-positive rates)
- SOAR playbooks (9, executing step-by-step, with a human approval gate for high-impact actions)
- MITRE ATT&CK coverage heat map
- Telemetry & Log Onboarding (CIM health, prioritization scorecard)
- Metrics & KPIs + Roadmap & Backlog (working intake form that scores and ranks requests)

Try first: open the critical ransomware incident, then run its SOAR playbook — it pauses at the approval gate, and after approval it completes and the incident moves to Contained.

### omnibrain/ — SecOps product-management app (runnable)
OmniBrain AI Agent (SPM-Dums), the app that does the Senior PM, SecOps
product work behind the console: intake and RICE-ranked backlog,
detection lifecycle, telemetry onboarding, SOAR governance with approval
gates, roadmap and launch readiness, metrics and operating cadence, and
an assistant grounded in the live workspace. This is runnable code in
this repo, not just documentation — see `omnibrain/README.md` and
`omnibrain/workspace-summary.md` for the seed snapshot it starts with.

## How to run BOTH

No install step and no API keys are required for either demo.
Python 3 is the only requirement, and only for serving OmniBrain.

**SOC demo — static, saves nothing:**

```bash
# Option A: just open the file in a browser
# soc-demo/index.html
# Option B: serve it locally
cd soc-demo
python3 -m http.server
```

**OmniBrain — server app, saves to local SQLite (Neon Postgres on Render):**

```bash
cd omnibrain
python3 server.py
# then open http://localhost:8765
# Port override: PORT=9000 python3 server.py
# Reset to seed data: python3 server.py --reset
```

Honest difference: the SOC demo saves nothing — reload and it resets.
OmniBrain saves your edits to `omnibrain/omnibrain.db` (git-ignored)
and they persist across restarts. Its assistant is offline and
rule-based by default; a real LLM endpoint is optional, never required.

## Render (review, not deployed)

See `render.yaml` at the repo root and `omnibrain/RENDER.md` +
`omnibrain/NEON.md`.
Render Free works for a demo URL for both parts (OmniBrain web
service + soc-demo static site). With `DATABASE_URL` set to a Neon
Postgres project, OmniBrain edits survive spin-down/redeploy; only
the first-visit wake delay after ~15 minutes idle remains. Without
`DATABASE_URL`, Free SQLite is ephemeral and the app reseeds. A
Render URL is public by default with no login in this app —
local-only remains the private mode, even though the GitHub repo
is private. Paid Starter + persistent disk is the paid alternative;
live Neon verification is still pending the user's own Neon project
(see `NEON.md`).

## External threat context (free public feeds)

OmniBrain can refresh public external threat context only —
CISA KEV and NVD CVE (last 7 days) — via
`POST /api/threat-intel/refresh`, shown on Mission Control with
source and fetch time. abuse.ch URLhaus is documented as
unavailable: its public no-key downloads are key-gated as of
Nov 2026, so it is not attempted. It never sends internal data to those
feeds, and the dashboard still loads offline from cache or empty.
MITRE enrichment stays local (technique/tactic mapping); the
full STIX bundle is not downloaded at runtime.

## MITRE gap mitigations

The MITRE tab now lists a mitigation card for each of the 8
primary-tactic gaps, in P0-first order (Initial Access,
Privilege Escalation, Lateral Movement, then Exfiltration and
Collection, then Discovery and Reconnaissance, then Resource
Development), each with detection, log source, NIST function,
effort, priority, and an "Add to backlog" button.
