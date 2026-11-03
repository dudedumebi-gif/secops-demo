# OmniBrain seeded workspace — snapshot 2026-11-03

Source: live app dashboard (`getdashboard`) on 2026-11-03. Seeded workspace, simulated data, not live employer data.

## KPIs
- Detection fidelity: 79%
- MTTR: 38 minutes
- Telemetry coverage: 66% across 7 sources
- Automation rate: 50%
- Hours saved: 1,176
- Open backlog: 8 items; production detections: 3; blocked items: 2; noisy detections: 2

## Daily brief (2026-11-03)
Production detection fidelity is 79%. Biggest noise lever: PowerShell -EncodedCommand (noisy) at 38% FP. Telemetry coverage averages 66%; 1 blocked (Kubernetes audit logs, EKS) — needs an owner decision to unblock. Top RICE item: Tune noisy PowerShell -EncodedCommand rule (FP 38%), RICE 25.2, P1. 1 playbook awaits governance review — high-impact actions stay gated until approved.

## Backlog (8, RICE-ranked)
1. Tune noisy PowerShell -EncodedCommand rule (FP 38%) — Detection Eng, P1, RICE 25.2, in_progress
2. Detect impossible travel + token replay in Entra ID — Detection Eng, P0, RICE 23.0, prioritized
3. Retire legacy brute-force rule superseded by composite — Detection Eng, P3, RICE 19.0, intake
4. Analyst workflow: one-click enrichment in case view — Platform, P2, RICE 12.0, intake
5. Onboard CloudTrail Lake + GuardDuty findings to SIEM — Platform, P0, RICE 10.2, in_progress
6. SOAR: Auto-isolate endpoint on confirmed ransomware behavior — SOAR Eng, P1, RICE 7.0, triaged
7. Detection-as-code CI: unit tests + peer review gate — Platform, P1, RICE 6.8, prioritized
8. Onboard EDR DNS query logs for C2 coverage — Platform, P2, RICE 5.5, triaged

## Detections (8)
- Impossible travel + token replay — Testing, fidelity 82%, FP 6.2%, T1078 / T1550
- PowerShell -EncodedCommand (noisy) — Tuning, fidelity 48%, FP 38%, T1059.001
- LSASS memory access — Production, fidelity 94%, FP 2.1%, T1003.001
- Composite: auth anomaly chain — Production, fidelity 89%, FP 4.4%, T1110 / T1078
- DNS tunneling entropy — Development, fidelity 61%, FP 12%, T1071.004
- Cloud IAM privilege escalation — Deployment, fidelity 77%, FP 7.8%, T1098 / T1078
- Legacy brute-force threshold (old) — Production, fidelity 55%, FP 22%, T1110 (candidate for retirement)
- Ransomware canary file tamper — Idea, fidelity 0%, FP 0%, T1486

## Telemetry (7)
- Microsoft Entra ID sign-in + audit — Live, 96%
- AWS CloudTrail + GuardDuty — Onboarding, 62%
- EDR process + DNS query logs (CrowdStrike) — Parser Fix, 71%
- Okta System Log — Live, 91%
- Kubernetes audit logs (EKS) — Blocked, 18%
- Palo Alto firewall + DNS security — Queued, 34%
- Email gateway (Proofpoint) taps — Live, 88%

## SOAR playbooks (6)
- Enrich alert: asset + identity context — Low, Production, 98% success, 12,480 runs, 412 hrs saved, auto-runs
- Ticket auto-triage + dedupe — Low, Needs Tuning, 73% success, 8,920 runs, 204 hrs saved
- Block IOC at firewall / DNS — Medium, Production, 96% success, 3,210 runs, 178 hrs saved, approval required
- Phishing mailbox purge (search & delete) — Medium, Production, 94% success, 860 runs, 286 hrs saved, approval required
- Disable compromised account — High, Approved, 91% success, 142 runs, 96 hrs saved, approval required
- Isolate endpoint (EDR) — Critical, In Review, 0 runs, approval required (IR Lead + SPM approval, rollback, break-glass)

## Roadmap (6)
- Now, Committed: Detection-as-code + CI quality gate; AWS + EDR telemetry coverage sprint; High-impact SOAR approval model v2
- Next, Planned: Composite detections for identity attacks; AI-assisted triage summaries for analysts
- Later, Exploring: Unified OCSF schema for all priority sources

## Decisions (2)
- Prioritize identity + cloud telemetry over network expansion (SPM-Dums + SOC Lead)
- High-impact SOAR actions always need human approval, audit log, and rollback — no exceptions (SPM-Dums)

## Launches (1)
- Composite: auth anomaly chain — v2 rollout, target 2026-11-14, Detection Eng. Blocked on: analyst training / enablement, rollback plan documented, documentation published. Done: runbook, owner + support model, success criteria + KPI, test evidence.
