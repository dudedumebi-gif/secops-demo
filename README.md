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

### omnibrain/ — SecOps product-management workspace
Documentation and a seeded-workspace snapshot for OmniBrain AI Agent (SPM-Dums), the private app that does the Senior PM, SecOps product work behind the console: intake and RICE-ranked backlog, detection lifecycle, telemetry onboarding, SOAR governance with approval gates, roadmap and launch readiness, metrics and operating cadence, and an AI assistant grounded in the live workspace.

The live OmniBrain app itself is a private, server-backed app and cannot be exported as a static site, so this folder contains its documented scope, actions, and seeded workspace summary — not a claim that the live app code is in this repo. See `omnibrain/README.md`.

## How to use this repo
- `soc-demo/index.html` runs standalone in a browser. No build step, no server, no data is saved.
- `omnibrain/` is reference material for the product-management workflow the console supports.
