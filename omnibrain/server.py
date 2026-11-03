#!/usr/bin/env python3
"""OmniBrain — SecOps product-management app. Python 3 standard library only.

Run from omnibrain/:
    python3 server.py            # http://localhost:8765
    python3 server.py --reset    # delete the local SQLite DB and reseed
    PORT=9000 python3 server.py  # override the port

No install step, no API keys, no external services. The AI assistant
(Ask / Triage / Draft) is rule-based and runs offline over the live
workspace in omnibrain.db. All data is simulated for demonstration.
Set OMNIBRAIN_LLM_URL only if you later want to point Ask at a real
LLM endpoint — it is never required, and the offline path is the default.
"""
import json
import os
import re
import sqlite3
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, "public")
DB_PATH = os.path.join(BASE, "omnibrain.db")
PORT = int(os.environ.get("PORT", "8765"))

# ---------------------------------------------------------------- seed data
BACKLOG = [
    # title, description, type, team, requester, priority, status, R, I, C, E, value, acceptance
    ("Tune noisy PowerShell -EncodedCommand rule (FP 38%)",
     "Analyst feedback: legitimate admin scripts flooding queue. Add allowlist + parent-process context.",
     "detection", "Detection Eng", "SOC Analysts", "P1", "in_progress", 8, 7, 90, 2,
     "Recover ~6 analyst hrs/week, improve alert fidelity.", "FP <8% for 14d, no TP loss in replay"),
    ("Detect impossible travel + token replay in Entra ID",
     "Threat-informed detection for session token replay after impossible travel. Needs correlation of sign-in risk + token reuse.",
     "detection", "Detection Eng", "Threat Intel", "P0", "prioritized", 9, 9, 85, 3,
     "Catch account takeover earlier; reduce MTTR for identity incidents.", "Backtest on 30d, FP <5%, runbook + analyst training done"),
    ("Retire legacy brute-force rule superseded by composite",
     "Old threshold rule duplicates composite auth detection; retirement review with analyst sign-off.",
     "detection", "Detection Eng", "Detection Eng", "P3", "intake", 5, 4, 95, 1,
     "Less duplicate alerting, cleaner queue.", "30d overlap analysis, analyst approval, retirement notice"),
    ("Analyst workflow: one-click enrichment in case view",
     "Surface asset criticality, owner, vuln context inside case — no tab-switching to CMDB.",
     "workflow", "Platform", "Monitoring & Response", "P2", "intake", 10, 6, 80, 4,
     "Reduce triage time per alert by ~4 minutes.", "Adoption >80% of analysts in 30d, triage time measured"),
    ("Onboard CloudTrail Lake + GuardDuty findings to SIEM",
     "Normalize AWS CloudTrail + GuardDuty into OCSF, fix parser gaps for AssumeRole events.",
     "telemetry", "Platform", "SOC Lead", "P0", "in_progress", 8, 8, 80, 5,
     "Close AWS coverage gap for 3 MITRE techniques.", "Parser tests pass, schema validated, coverage dashboard updated"),
    ("SOAR: Auto-isolate endpoint on confirmed ransomware behavior",
     "High-impact automation — requires human approval gate, rollback plan, and audit trail.",
     "soar", "SOAR Eng", "IR Lead", "P1", "triaged", 7, 10, 60, 6,
     "Cut containment from 42m to <5m when approved.", "Approval gate tested, failure mode documented, success criteria defined"),
    ("Detection-as-code CI: unit tests + peer review gate",
     "Standardize content management: Git-based rules, CI tests, staging deploy, versioned releases.",
     "platform", "Platform", "Detection Eng Lead", "P1", "prioritized", 9, 8, 75, 8,
     "Fewer broken deploys, auditable lifecycle.", "CI blocks failing rules, staging parity, rollback documented"),
    ("Onboard EDR DNS query logs for C2 coverage",
     "DNS telemetry gap for T1071.001 / T1105. Evaluate volume/cost vs investigative value.",
     "telemetry", "Platform", "Threat Intel", "P2", "triaged", 6, 7, 65, 5,
     "Enable 4 queued detections blocked on DNS data.", "Cost model, sampling decision, parser + retention set"),
]
DETECTIONS = [
    # name, stage, fidelity, fp, mitre, log_source, owner, severity, description
    ("Impossible travel + token replay", "Testing", 82, 6.2, "T1078 / T1550", "Entra ID", "Detection Eng", "Critical", "Correlation: risky sign-in + token reuse across ASN"),
    ("PowerShell -EncodedCommand (noisy)", "Tuning", 48, 38.0, "T1059.001", "EDR / Sysmon", "Detection Eng", "High", "Needs parent-process context + admin allowlist"),
    ("LSASS memory access", "Production", 94, 2.1, "T1003.001", "EDR", "Detection Eng", "Critical", "Credential dumping via LSASS handle access"),
    ("Composite: auth anomaly chain", "Production", 89, 4.4, "T1110 / T1078", "IdP + VPN", "Detection Eng", "High", "Brute force -> success -> privilege change composite"),
    ("DNS tunneling entropy", "Development", 61, 12.0, "T1071.004", "DNS (blocked: onboarding)", "Detection Eng", "Medium", "High-entropy subdomain exfil/C2 signal"),
    ("Cloud IAM privilege escalation", "Deployment", 77, 7.8, "T1098 / T1078", "CloudTrail", "Detection Eng", "High", "Policy attach + new key creation sequence"),
    ("Legacy brute-force threshold (old)", "Production", 55, 22.0, "T1110", "IdP", "Detection Eng", "Medium", "Candidate for retirement — superseded by composite"),
    ("Ransomware canary file tamper", "Idea", 0, 0.0, "T1486", "EDR / File", "Threat Intel", "Critical", "Threat-informed idea from recent IR lessons learned"),
]
TELEMETRY = [
    # name, vendor, status, coverage, effort, risk, score, use_case
    ("Microsoft Entra ID sign-in + audit", "Microsoft", "Live", 96, "S", "High", 98, "Identity detections, impossible travel"),
    ("AWS CloudTrail + GuardDuty", "AWS", "Onboarding", 62, "M", "High", 92, "Cloud IAM + control-plane detections"),
    ("EDR process + DNS query logs", "CrowdStrike", "Parser Fix", 71, "M", "High", 88, "C2, DNS tunneling, process lineage"),
    ("Okta System Log", "Okta", "Live", 91, "S", "Medium", 85, "Auth anomaly composite"),
    ("Kubernetes audit logs (EKS)", "AWS EKS", "Blocked", 18, "L", "High", 81, "Container escape / RBAC abuse — blocked on cluster owner decision"),
    ("Palo Alto firewall + DNS security", "Palo Alto", "Queued", 34, "L", "Medium", 74, "Network C2 + exfil coverage"),
    ("Email gateway (proofpoint) taps", "Proofpoint", "Live", 88, "S", "Medium", 70, "Phishing + BEC investigations"),
]
PLAYBOOKS = [
    # name, category, risk, status, approval_required, approved_by, runs, success, hrs, description
    ("Enrich alert: asset + identity context", "Enrichment", "Low", "Production", 0, "SPM-Dums", 12480, 98, 412, "Auto-enrich every alert with CMDB, owner, vuln + HR context"),
    ("Ticket auto-triage + dedupe", "Triage", "Low", "Needs Tuning", 0, "SPM-Dums", 8920, 73, 204, "Dedupe accuracy dropped — duplicate suppression misfiring on composite alerts"),
    ("Block IOC at firewall / DNS", "Response", "Medium", "Production", 1, "SOC Lead", 3210, 96, 178, "Auto-block confirmed IOCs with 7-day expiry + review"),
    ("Phishing mailbox purge (search & delete)", "Response", "Medium", "Production", 1, "IR Lead", 860, 94, 286, "On analyst approval, purge matched phishing mail org-wide"),
    ("Disable compromised account", "Containment", "High", "Approved", 1, "", 142, 91, 96, "Human approval required. Disables IdP account + revokes sessions"),
    ("Isolate endpoint (EDR)", "Containment", "Critical", "In Review", 1, "", 0, 0, 0, "Highest-impact action — needs IR Lead + SPM approval, rollback plan, break-glass path"),
]
ROADMAP = [
    ("Detection-as-code + CI quality gate", "Now", "Committed", "Detection Lifecycle", "Detection Eng Lead", "Zero broken deploys; every rule tested + versioned"),
    ("AWS + EDR telemetry coverage sprint", "Now", "Committed", "Telemetry", "Platform Lead", "Coverage 71% -> 85% on priority sources"),
    ("High-impact SOAR approval model v2", "Now", "Committed", "SOAR Governance", "SPM-Dums", "Safe scale: approval gates, audit, rollback for Critical actions"),
    ("Composite detections for identity attacks", "Next", "Planned", "Detection Lifecycle", "Detection Eng", "Fidelity +10pts on identity alert family"),
    ("AI-assisted triage summaries for analysts", "Next", "Planned", "Platform / AI", "Platform", "Triage time -25% in pilot cohort, analyst-validated"),
    ("Unified OCSF schema for all priority sources", "Later", "Exploring", "Telemetry", "Platform", "Faster onboarding: 3 weeks -> 1 week per source"),
]
DECISIONS = [
    ("Prioritize identity + cloud telemetry over network expansion",
     "Threat intel + incident learnings show identity/cloud as top attack path; firewall expansion is high effort, lower marginal coverage.",
     "SPM-Dums + SOC Lead", "Decided"),
    ("High-impact SOAR actions always need human approval",
     "Endpoint isolation / account disable can disrupt the business if triggered on a false positive.",
     "SPM-Dums", "Decided"),
]
LAUNCH_CHECKS = [
    ("Runbook written and linked", True),
    ("Owner + support model named", True),
    ("Success criteria + KPI defined", True),
    ("Analyst training / enablement done", False),
    ("Test evidence (backtest / staging) attached", True),
    ("Rollback plan documented", False),
    ("Documentation published", False),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS backlog (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  description TEXT DEFAULT '', type TEXT DEFAULT 'detection', team TEXT DEFAULT '', requester TEXT DEFAULT '',
  priority TEXT DEFAULT 'P2', status TEXT DEFAULT 'intake', reach REAL DEFAULT 0, impact REAL DEFAULT 0,
  confidence REAL DEFAULT 0, effort REAL DEFAULT 1, rice REAL DEFAULT 0, value_statement TEXT DEFAULT '',
  acceptance TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS detections (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  stage TEXT DEFAULT 'Idea', fidelity REAL DEFAULT 0, fp_rate REAL DEFAULT 0, mitre TEXT DEFAULT '',
  log_source TEXT DEFAULT '', owner TEXT DEFAULT '', severity TEXT DEFAULT 'Medium', description TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  vendor TEXT DEFAULT '', status TEXT DEFAULT 'Queued', coverage REAL DEFAULT 0, effort TEXT DEFAULT 'M',
  risk TEXT DEFAULT 'Medium', priority_score REAL DEFAULT 0, use_case TEXT DEFAULT '', owner TEXT DEFAULT 'Platform');
CREATE TABLE IF NOT EXISTS playbooks (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  category TEXT DEFAULT '', risk_level TEXT DEFAULT 'Low', status TEXT DEFAULT 'In Review',
  approval_required INTEGER DEFAULT 0, approved_by TEXT DEFAULT '', runs INTEGER DEFAULT 0,
  success_rate REAL DEFAULT 0, time_saved_hrs REAL DEFAULT 0, description TEXT DEFAULT '', owner TEXT DEFAULT 'SOAR Eng');
CREATE TABLE IF NOT EXISTS roadmap (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  quarter TEXT DEFAULT 'Next', status TEXT DEFAULT 'Planned', theme TEXT DEFAULT '', owner TEXT DEFAULT '', outcome TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  context TEXT DEFAULT '', owner TEXT DEFAULT '', status TEXT DEFAULT 'Decided');
CREATE TABLE IF NOT EXISTS launches (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  type TEXT DEFAULT 'Detection', owner TEXT DEFAULT '', target_date TEXT DEFAULT '', checks TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS briefs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  text TEXT NOT NULL, kpis TEXT DEFAULT '{}');
"""


def rice(reach, impact, confidence, effort):
    # Decimal round-half-up so seeded values match the spec exactly:
    # e.g. 9*9*85/100/3 = 22.95 -> 23.0 (float round() gives 22.9).
    if not float(effort):
        return 0.0
    from decimal import Decimal, ROUND_HALF_UP
    v = (Decimal(str(reach)) * Decimal(str(impact)) * Decimal(str(confidence))
         / Decimal(100) / Decimal(str(effort)))
    return float(v.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM backlog").fetchone()[0] == 0:
        for b in BACKLOG:
            conn.execute(
                "INSERT INTO backlog (title, description, type, team, requester, priority, status, reach, impact, confidence, effort, rice, value_statement, acceptance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*b[:7], b[7], b[8], b[9], b[10], rice(b[7], b[8], b[9], b[10]), b[11], b[12]))
        for d in DETECTIONS:
            conn.execute("INSERT INTO detections (name, stage, fidelity, fp_rate, mitre, log_source, owner, severity, description) VALUES (?,?,?,?,?,?,?,?,?)", d)
        for t in TELEMETRY:
            conn.execute("INSERT INTO telemetry (name, vendor, status, coverage, effort, risk, priority_score, use_case) VALUES (?,?,?,?,?,?,?,?)", t)
        for p in PLAYBOOKS:
            conn.execute("INSERT INTO playbooks (name, category, risk_level, status, approval_required, approved_by, runs, success_rate, time_saved_hrs, description) VALUES (?,?,?,?,?,?,?,?,?,?)", p)
        for r in ROADMAP:
            conn.execute("INSERT INTO roadmap (title, quarter, status, theme, owner, outcome) VALUES (?,?,?,?,?,?)", r)
        for d in DECISIONS:
            conn.execute("INSERT INTO decisions (title, context, owner, status) VALUES (?,?,?,?)", d)
        conn.execute("INSERT INTO launches (name, type, owner, target_date, checks) VALUES (?,?,?,?,?)",
                     ("Composite: auth anomaly chain — v2 rollout", "Detection", "Detection Eng", "2026-11-14",
                      json.dumps([{"label": l, "done": bool(v)} for l, v in LAUNCH_CHECKS])))
        conn.commit()
    conn.close()


def rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def kpis(conn):
    prod = rows(conn, "SELECT fidelity FROM detections WHERE stage='Production'")
    fidelity = round(sum(r["fidelity"] for r in prod) / len(prod)) if prod else 0
    tel = rows(conn, "SELECT coverage, status FROM telemetry")
    coverage = round(sum(r["coverage"] for r in tel) / len(tel)) if tel else 0
    pb = rows(conn, "SELECT success_rate, time_saved_hrs, status FROM playbooks")
    hours = int(sum(r["time_saved_hrs"] for r in pb))
    automation = round(100 * sum(1 for r in pb if r["status"] == "Production") / len(pb)) if pb else 0
    backlog_n = conn.execute("SELECT COUNT(*) FROM backlog WHERE status NOT IN ('done','rejected')").fetchone()[0]
    prod_n = len(prod)
    blocked = sum(1 for r in tel if r["status"] == "Blocked")
    launches = rows(conn, "SELECT checks FROM launches")
    blocked += sum(1 for la in launches if not all(c["done"] for c in json.loads(la["checks"])))
    noisy = conn.execute("SELECT COUNT(*) FROM detections WHERE fp_rate >= 20 AND stage != 'Idea'").fetchone()[0]
    return {"fidelity": fidelity, "mttr_mins": 38, "coverage": coverage, "automation_rate": automation,
            "hours_saved": hours, "open_backlog": backlog_n, "prod_detections": prod_n,
            "blocked_items": blocked, "noisy_detections": noisy}


def make_brief(conn):
    k = kpis(conn)
    dets = rows(conn, "SELECT * FROM detections ORDER BY fp_rate DESC")
    noisiest = dets[0] if dets else None
    blocked_tel = rows(conn, "SELECT * FROM telemetry WHERE status='Blocked'")
    top = rows(conn, "SELECT * FROM backlog ORDER BY rice DESC LIMIT 1")
    awaiting = conn.execute("SELECT COUNT(*) FROM playbooks WHERE status='In Review'").fetchone()[0]
    parts = [f"Daily brief — {date.today().isoformat()}: Production detection fidelity is {k['fidelity']}%."]
    if noisiest:
        parts.append(f"Biggest noise lever: “{noisiest['name']}” at {noisiest['fp_rate']:.0f}% FP — tuning it returns the most analyst time.")
    parts.append(f"Telemetry coverage averages {k['coverage']}% across {len(rows(conn, 'SELECT id FROM telemetry'))} sources; "
                 f"{len(blocked_tel)} blocked" + (f" ({blocked_tel[0]['name']}) — needs an owner decision to unblock." if blocked_tel else "."))
    if top:
        parts.append(f"Top RICE item to drive today: “{top[0]['title']}” (RICE {top[0]['rice']}, {top[0]['priority']}).")
    parts.append(f"{awaiting} playbook(s) await governance review — high-impact actions stay gated until approved.")
    return " ".join(parts)


def launch_status(checks):
    return "READY" if all(c["done"] for c in checks) else "BLOCKED"


# ---------------------------------------------------------------- offline AI
STOP = {"the", "and", "for", "with", "how", "should", "what", "our", "are", "this", "that", "from", "into", "about"}


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9][a-z0-9\-]+", text.lower()) if t not in STOP and len(t) > 2]


def triage_recommendation(text):
    low = text.lower()
    if any(w in low for w in ["powershell", "encodedcommand", "false positive", "noisy", "flood"]):
        kind, team, r, i, c, e = "detection", "Detection Eng", 8, 7, 90, 2
        title, acc = "Tune noisy detection rule flagged by analysts", "FP <8% for 14d, no TP loss in replay"
        risks = ["Allowlist too broad could hide real activity — scope it to verified admin hosts.", "Parent-process context may need a parser change."]
    elif any(w in low for w in ["eks", "kubernetes", "cloudtrail", "guardduty", "dns", "entra", "okta", "log", "telemetry", "onboard"]):
        kind, team, r, i, c, e = "telemetry", "Platform", 8, 8, 80, 5
        title, acc = "Onboard log source requested by analysts", "Parser tests pass, schema validated, coverage dashboard updated"
        risks = ["Volume/cost may exceed the estimate — confirm sampling and retention first.", "Blocked on an owner decision if the source owner has not approved access."]
    elif any(w in low for w in ["isolate", "disable", "phishing", "purge", "block", "soar", "automate", "playbook"]):
        kind, team, r, i, c, e = "soar", "SOAR Eng", 7, 10, 60, 6
        title, acc = "SOAR automation requested by responders", "Approval gate tested, failure mode documented, success criteria defined"
        risks = ["High-impact automation needs a human approval gate, audit trail, and rollback — no exceptions.", "False positives could disrupt the business if auto-run."]
    elif any(w in low for w in ["workflow", "enrich", "triage", "case", "analyst"]):
        kind, team, r, i, c, e = "workflow", "Platform", 10, 6, 80, 4
        title, acc = "Analyst workflow improvement", "Adoption >80% of analysts in 30d, triage time measured"
        risks = ["Workflow change needs analyst validation before rollout, not just engineering sign-off."]
    else:
        kind, team, r, i, c, e = "platform", "Platform", 6, 6, 70, 4
        title, acc = "New SecOps capability request (needs scoping)", "Scope, owner, and success criteria agreed with requester"
        risks = ["Request is underspecified — confirm the concrete use case and the detection/telemetry it depends on before prioritising."]
    if "ransomware" in low or "critical" in low:
        priority = "P0"
    elif rice(r, i, c, e) >= 20:
        priority = "P1"
    elif rice(r, i, c, e) >= 10:
        priority = "P2"
    else:
        priority = "P3"
    return {"title": title, "type": kind, "team": team, "reach": r, "impact": i, "confidence": c,
            "effort": e, "rice": rice(r, i, c, e), "priority": priority,
            "acceptance": acc, "risks": risks,
            "description": f"Triaged from stakeholder request: {text.strip()[:300]}"}


def ask_answer(conn, question):
    toks = tokens(question)
    def score(row, fields):
        hay = " ".join(str(row.get(f, "")) for f in fields).lower()
        return sum(1 for t in toks if t in hay)
    backlog = rows(conn, "SELECT * FROM backlog ORDER BY rice DESC")
    dets = rows(conn, "SELECT * FROM detections")
    tel = rows(conn, "SELECT * FROM telemetry")
    pb = rows(conn, "SELECT * FROM playbooks")
    ranked_b = sorted(backlog, key=lambda r: score(r, ["title", "description", "type", "team"]), reverse=True)
    ranked_d = sorted(dets, key=lambda r: score(r, ["name", "description", "mitre", "log_source"]), reverse=True)
    ranked_t = sorted(tel, key=lambda r: score(r, ["name", "use_case", "vendor"]), reverse=True)
    top_b = ranked_b[0] if ranked_b else None
    # Always ground in real rows: prefer keyword matches, fall back to top RICE / noisiest.
    actions = []
    if top_b and score(top_b, ["title", "description", "type", "team"]) > 0:
        actions.append(f"Drive the matching backlog item first: “{top_b['title']}” ({top_b['team']}, RICE {top_b['rice']}, {top_b['status']}).")
    elif top_b:
        actions.append(f"No exact keyword match in the backlog, so start with the top-ranked item: “{top_b['title']}” (RICE {top_b['rice']}, {top_b['status']}).")
    if ranked_d and score(ranked_d[0], ["name", "description", "mitre", "log_source"]) > 0:
        actions.append(f"Relevant detection: “{ranked_d[0]['name']}” ({ranked_d[0]['stage']}, fidelity {ranked_d[0]['fidelity']}%, FP {ranked_d[0]['fp_rate']}%).")
    noisiest = max(dets, key=lambda r: r["fp_rate"]) if dets else None
    if noisiest:
        actions.append(f"Noise lever to watch: “{noisiest['name']}” at {noisiest['fp_rate']:.0f}% FP.")
    if ranked_t and score(ranked_t[0], ["name", "use_case", "vendor"]) > 0:
        actions.append(f"Relevant telemetry: “{ranked_t[0]['name']}” ({ranked_t[0]['status']}, {ranked_t[0]['coverage']}% coverage).")
    blocked = [r for r in tel if r["status"] == "Blocked"]
    risks = [f"Blocked telemetry: {r['name']} ({r['coverage']}% coverage) — {r['use_case']}." for r in blocked]
    if noisiest and noisiest["fp_rate"] >= 20:
        risks.append(f"High false-positive rate on “{noisiest['name']}” risks analyst fatigue and missed true positives.")
    k = kpis(conn)
    summary = (f"Grounded in the live workspace (fidelity {k['fidelity']}%, coverage {k['coverage']}%, "
               f"MTTR {k['mttr_mins']}m, {k['open_backlog']} open backlog items): "
               f"prioritise signal quality before breadth — fix the noisiest detection and unblock telemetry before adding new coverage.")
    return {"summary": summary, "actions": actions,
            "referenced_titles": [r["title"] for r in ranked_b[:3]] + [r["name"] for r in ranked_d[:2]],
            "risks": risks,
            "next_steps": ["Assign an owner and a success criterion to the top action above.",
                           "Re-run the daily brief after any status change to see KPIs move."]}


def draft_doc(conn, kind, backlog_id, topic):
    if kind not in ("prd", "runbook", "launch-plan"):
        return None
    item = None
    if backlog_id:
        got = rows(conn, "SELECT * FROM backlog WHERE id=?", (backlog_id,))
        item = got[0] if got else None
        if not item:
            return None
    title = item["title"] if item else (topic or "Untitled SecOps capability")
    base = f"# {title}\n\nSimulated data for demonstration. "
    if item:
        base += f"Backlog item: {item['title']} ({item['team']}, {item['priority']}, RICE {item['rice']}, status {item['status']}).\n\nValue: {item['value_statement']}\n\nAcceptance criteria: {item['acceptance']}\n"
    else:
        base += f"Topic: {topic}.\n"
    if kind == "prd":
        doc = base + "\n## Problem\nState the analyst pain and the evidence in one paragraph.\n\n## Requirements\n1. Meet the acceptance criteria above.\n2. Ship with a runbook, owner, and success criteria before launch.\n\n## Success measures\nFidelity, false-positive rate, MTTR, and analyst adoption — measured for 30 days post-launch.\n"
    elif kind == "runbook":
        doc = base + "\n## When to use this\nWhen this alert fires in the SOC queue.\n\n## Steps\n1. Enrich the alert (asset, identity, owner).\n2. Confirm true vs false positive against the acceptance criteria.\n3. Contain only through an approved playbook — high-impact actions need human approval.\n4. Log the outcome and feed it back into tuning.\n\n## Rollback\nRevert the rule/playbook change and notify the SOC Lead.\n"
    else:
        doc = base + "\n## Rollout phases\n1. Staging / backtest.\n2. Pilot cohort with analyst validation.\n3. Full rollout only after training, runbook, and rollback are done.\n\n## Launch gates (all must pass)\nRunbook, owner + support model, success criteria, analyst training, test evidence, rollback plan, documentation.\n\n## Rollback\nStop the rollout and revert within the documented window; notify stakeholders.\n"
    return {"kind": kind, "title": title, "document": doc}


# ---------------------------------------------------------------- HTTP layer
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception:
            return None

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/dashboard":
            conn = db()
            out = {"kpis": kpis(conn), "brief": make_brief(conn),
                   "backlog": rows(conn, "SELECT * FROM backlog ORDER BY rice DESC"),
                   "detections": rows(conn, "SELECT * FROM detections"),
                   "telemetry": rows(conn, "SELECT * FROM telemetry"),
                   "playbooks": rows(conn, "SELECT * FROM playbooks"),
                   "roadmap": rows(conn, "SELECT * FROM roadmap"),
                   "decisions": rows(conn, "SELECT * FROM decisions"),
                   "launches": [], "briefs": rows(conn, "SELECT * FROM briefs ORDER BY id DESC LIMIT 5")}
            for la in rows(conn, "SELECT * FROM launches"):
                checks = json.loads(la["checks"])
                la["checks"] = checks
                la["launch_status"] = launch_status(checks)
                out["launches"].append(la)
            conn.close()
            return self.send_json(200, out)
        # static files
        path = parsed.path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(PUBLIC, path))
        if not full.startswith(PUBLIC) or not os.path.isfile(full):
            return self.send_json(404, {"error": "not found"})
        mime = {"index.html": "text/html", "app.js": "text/javascript", "styles.css": "text/css"}.get(
            os.path.basename(full), "application/octet-stream")
        data = open(full, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.route(write=True)

    def do_PATCH(self):
        self.route(write=True)

    def do_DELETE(self):
        self.route(write=True)

    def route(self, write=False):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        body = self.read_json()
        if body is None:
            return self.send_json(400, {"error": "invalid JSON"})
        if len(parts) < 2 or parts[0] != "api":
            return self.send_json(404, {"error": "not found"})
        conn = db()
        try:
            return self.dispatch(conn, parts[1:], body)
        finally:
            conn.close()

    def dispatch(self, conn, parts, body):
        # parts[0] is the resource
        def get_one(table, rid):
            got = rows(conn, f"SELECT * FROM {table} WHERE id=?", (rid,))
            return got[0] if got else None

        def patch(table, rid, allowed):
            if not get_one(table, rid):
                return self.send_json(404, {"error": "not found"})
            sets, vals = [], []
            for k in allowed:
                if k in body:
                    sets.append(f"{k}=?")
                    vals.append(body[k])
            if not sets:
                return self.send_json(400, {"error": "nothing to update"})
            conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", (*vals, rid))
            conn.commit()
            return self.send_json(200, get_one(table, rid))

        # ---- backlog
        if parts == ["backlog"] and self.command == "POST":
            for f in ("title", "reach", "impact", "confidence", "effort"):
                if f not in body:
                    return self.send_json(400, {"error": f"missing {f}"})
            r = rice(body["reach"], body["impact"], body["confidence"], body["effort"])
            cur = conn.execute(
                "INSERT INTO backlog (title, description, type, team, requester, priority, status, reach, impact, confidence, effort, rice, value_statement, acceptance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (body["title"], body.get("description", ""), body.get("type", "detection"), body.get("team", ""),
                 body.get("requester", ""), body.get("priority", "P2"), body.get("status", "intake"),
                 body["reach"], body["impact"], body["confidence"], body["effort"], r,
                 body.get("value_statement", ""), body.get("acceptance", "")))
            conn.commit()
            return self.send_json(201, get_one("backlog", cur.lastrowid))
        if len(parts) == 3 and parts[0] == "backlog" and parts[1].isdigit() and parts[2] == "x":
            pass  # placeholder, real routes below use parts[1] as id
        if len(parts) == 2 and parts[0] == "backlog" and parts[1].isdigit():
            rid = int(parts[1])
            if self.command == "PATCH":
                return patch("backlog", rid, ["status", "priority", "title"])
            if self.command == "DELETE":
                if not get_one("backlog", rid):
                    return self.send_json(404, {"error": "not found"})
                conn.execute("DELETE FROM backlog WHERE id=?", (rid,))
                conn.commit()
                return self.send_json(200, {"deleted": rid})
        # ---- triage / ask / draft / refresh
        if parts == ["triage"] and self.command == "POST":
            if not body.get("text"):
                return self.send_json(400, {"error": "missing text"})
            rec = triage_recommendation(body["text"])
            if body.get("save"):
                cur = conn.execute(
                    "INSERT INTO backlog (title, description, type, team, requester, priority, status, reach, impact, confidence, effort, rice, value_statement, acceptance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rec["title"], rec["description"], rec["type"], rec["team"], "", rec["priority"], "intake",
                     rec["reach"], rec["impact"], rec["confidence"], rec["effort"], rec["rice"], "", rec["acceptance"]))
                conn.commit()
                rec["saved_id"] = cur.lastrowid
            return self.send_json(200, rec)
        if parts == ["ask"] and self.command == "POST":
            if not body.get("question"):
                return self.send_json(400, {"error": "missing question"})
            return self.send_json(200, ask_answer(conn, body["question"]))
        if parts == ["draft"] and self.command == "POST":
            doc = draft_doc(conn, body.get("kind", ""), body.get("backlog_id"), body.get("topic", ""))
            if doc is None:
                return self.send_json(400, {"error": "kind must be prd|runbook|launch-plan, and backlog_id (if given) must exist"})
            return self.send_json(200, doc)
        if parts == ["refresh-brief"] and self.command == "POST":
            brief = make_brief(conn)
            k = kpis(conn)
            cur = conn.execute("INSERT INTO briefs (created_at, text, kpis) VALUES (?,?,?)",
                               (date.today().isoformat(), brief, json.dumps(k)))
            conn.commit()
            return self.send_json(200, {"brief": brief, "kpis": k, "snapshot_id": cur.lastrowid})
        # ---- detections
        if parts == ["detections"] and self.command == "POST":
            if not body.get("name"):
                return self.send_json(400, {"error": "missing name"})
            cur = conn.execute("INSERT INTO detections (name, stage, fidelity, fp_rate, mitre, log_source, owner, severity, description) VALUES (?,?,?,?,?,?,?,?,?)",
                               (body["name"], body.get("stage", "Idea"), body.get("fidelity", 0), body.get("fp_rate", 0),
                                body.get("mitre", ""), body.get("log_source", ""), body.get("owner", ""), body.get("severity", "Medium"), body.get("description", "")))
            conn.commit()
            return self.send_json(201, get_one("detections", cur.lastrowid))
        if len(parts) == 2 and parts[0] == "detections" and parts[1].isdigit() and self.command == "PATCH":
            return patch("detections", int(parts[1]), ["stage", "fidelity", "fp_rate"])
        # ---- telemetry
        if parts == ["telemetry"] and self.command == "POST":
            if not body.get("name"):
                return self.send_json(400, {"error": "missing name"})
            cur = conn.execute("INSERT INTO telemetry (name, vendor, status, coverage, effort, risk, priority_score, use_case) VALUES (?,?,?,?,?,?,?,?)",
                               (body["name"], body.get("vendor", ""), body.get("status", "Queued"), body.get("coverage", 0),
                                body.get("effort", "M"), body.get("risk", "Medium"), body.get("priority_score", 0), body.get("use_case", "")))
            conn.commit()
            return self.send_json(201, get_one("telemetry", cur.lastrowid))
        if len(parts) == 2 and parts[0] == "telemetry" and parts[1].isdigit() and self.command == "PATCH":
            return patch("telemetry", int(parts[1]), ["status", "coverage"])
        # ---- playbooks
        if parts == ["playbooks"] and self.command == "POST":
            if not body.get("name"):
                return self.send_json(400, {"error": "missing name"})
            cur = conn.execute("INSERT INTO playbooks (name, category, risk_level, status, approval_required, approved_by, runs, success_rate, time_saved_hrs, description) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (body["name"], body.get("category", ""), body.get("risk_level", "Low"), body.get("status", "In Review"),
                                1 if body.get("approval_required") else 0, body.get("approved_by", ""), body.get("runs", 0),
                                body.get("success_rate", 0), body.get("time_saved_hrs", 0), body.get("description", "")))
            conn.commit()
            return self.send_json(201, get_one("playbooks", cur.lastrowid))
        if len(parts) == 3 and parts[0] == "playbooks" and parts[1].isdigit() and parts[2] == "approve" and self.command == "POST":
            pb = get_one("playbooks", int(parts[1]))
            if not pb:
                return self.send_json(404, {"error": "not found"})
            approver = (body.get("approver") or "").strip()
            if pb["risk_level"] in ("High", "Critical") and not approver:
                return self.send_json(400, {"error": "High/Critical playbooks require a non-empty approver; status unchanged"})
            conn.execute("UPDATE playbooks SET status='Approved', approved_by=? WHERE id=?", (approver or pb["approved_by"], pb["id"]))
            conn.commit()
            return self.send_json(200, get_one("playbooks", pb["id"]))
        if len(parts) == 2 and parts[0] == "playbooks" and parts[1].isdigit() and self.command == "PATCH":
            return patch("playbooks", int(parts[1]), ["status"])
        # ---- roadmap / decisions / launches
        if parts == ["roadmap"] and self.command == "POST":
            if not body.get("title"):
                return self.send_json(400, {"error": "missing title"})
            cur = conn.execute("INSERT INTO roadmap (title, quarter, status, theme, owner, outcome) VALUES (?,?,?,?,?,?)",
                               (body["title"], body.get("quarter", "Next"), body.get("status", "Planned"), body.get("theme", ""), body.get("owner", ""), body.get("outcome", "")))
            conn.commit()
            return self.send_json(201, get_one("roadmap", cur.lastrowid))
        if parts == ["decisions"] and self.command == "POST":
            if not body.get("title"):
                return self.send_json(400, {"error": "missing title"})
            cur = conn.execute("INSERT INTO decisions (title, context, owner, status) VALUES (?,?,?,?)",
                               (body["title"], body.get("context", ""), body.get("owner", ""), body.get("status", "Decided")))
            conn.commit()
            return self.send_json(201, get_one("decisions", cur.lastrowid))
        if parts == ["launches"] and self.command == "POST":
            if not body.get("name"):
                return self.send_json(400, {"error": "missing name"})
            checks = body.get("checks") or [{"label": l, "done": False} for l in
                                            ["Runbook written and linked", "Owner + support model named", "Success criteria + KPI defined",
                                             "Analyst training / enablement done", "Test evidence (backtest / staging) attached",
                                             "Rollback plan documented", "Documentation published"]]
            cur = conn.execute("INSERT INTO launches (name, type, owner, target_date, checks) VALUES (?,?,?,?,?)",
                               (body["name"], body.get("type", "Detection"), body.get("owner", ""), body.get("target_date", ""), json.dumps(checks)))
            conn.commit()
            row = get_one("launches", cur.lastrowid)
            row["checks"] = json.loads(row["checks"])
            row["launch_status"] = launch_status(row["checks"])
            return self.send_json(201, row)
        if len(parts) == 5 and parts[0] == "launches" and parts[1].isdigit() and parts[2] == "checks" and parts[3].isdigit() and parts[4] == "toggle" and self.command == "POST":
            la = get_one("launches", int(parts[1]))
            if not la:
                return self.send_json(404, {"error": "not found"})
            checks = json.loads(la["checks"])
            idx = int(parts[3])
            if idx < 0 or idx >= len(checks):
                return self.send_json(400, {"error": "check index out of range"})
            checks[idx]["done"] = not checks[idx]["done"]
            conn.execute("UPDATE launches SET checks=? WHERE id=?", (json.dumps(checks), la["id"]))
            conn.commit()
            la["checks"] = checks
            la["launch_status"] = launch_status(checks)
            return self.send_json(200, la)
        return self.send_json(404, {"error": "not found"})


def main():
    if "--reset" in sys.argv:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        print("reset: database removed, will reseed on start")
    init_db()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"OmniBrain running at http://localhost:{PORT}  (Ctrl+C to stop)")
    print(f"Database: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
