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

**OmniBrain — server app, saves to local SQLite:**

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
