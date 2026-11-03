# OmniBrain AI Agent (SPM-Dums) — workspace documentation

OmniBrain is a private, server-backed SecOps product-management app. It is **not** a static site, so it cannot be exported as HTML the way `soc-demo/` can. This folder documents what it does, its actions, and its seeded workspace, so the product work is reviewable from this repo without claiming the live app's code is here.

## What it does — Senior PM, SecOps work
- **Mission Control + AI Daily Brief:** fidelity 79%, MTTR 38m, coverage 66%, 1,176 hours saved (seeded snapshot, 2026-11-03)
- **Intake & Backlog:** structured intake, AI triage, automatic RICE ranking (8 items in the seeded workspace)
- **Detection Lifecycle:** 7 stages, 8 detections, with fidelity and false-positive tracking
- **Telemetry onboarding:** 7 sources queued/prioritised; Kubernetes audit logs (EKS) flagged Blocked in the seeded snapshot
- **SOAR governance:** 6 playbooks; Low-risk enrichment auto-runs, High/Critical actions require human approval, audit log, and rollback
- **Roadmap + decisions:** Now / Next / Later roadmap (6 items) + decision log (2)
- **Launch Readiness:** checklists that block a launch until runbook, training, support, and success measures are done
- **Ask OmniBrain:** AI assistant grounded in the live workspace — prioritises, explains risk, drafts PRDs / runbooks / launch plans

## Actions (live app)
approveplaybook, askagent, createbacklog, createdecision, createdetection, createlaunch, createplaybook, createroadmap, createtelemetry, deletebacklog, draftdocument, getdashboard, movedetection, refreshdailybrief, togglelaunchcheck, triagewithai, updatebacklogstatus, updateplaybookstatus, updatetelemetrystatus

See `workspace-summary.md` for the seeded KPIs, backlog, detections, telemetry, playbooks, roadmap, decisions, and launches.

## Honest limits
- Seeded rows are a working workspace derived from the Senior PM, SecOps job description, not live employer data.
- No live connection to a real SIEM, SOAR, Jira, or Slack in this snapshot.
- High/Critical playbooks still require a human approver, by design.
- All data here is simulated for demonstration.
