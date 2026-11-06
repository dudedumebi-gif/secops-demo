#!/usr/bin/env python3
"""OmniBrain — SecOps product-management app.

Run from omnibrain/:
    python3 server.py            # http://localhost:8765 (local SQLite)
    python3 server.py --reset    # delete the local SQLite DB and reseed
    PORT=9000 python3 server.py  # override the port
    HOST=0.0.0.0 python3 server.py  # Render / LAN bind (default 127.0.0.1 locally)
    DATABASE_URL=postgresql://... python3 server.py  # Postgres instead
    TURSO_DATABASE_URL=libsql://... TURSO_AUTH_TOKEN=... python3 server.py  # Turso

Database choice, decided at startup and nowhere else, in this order:
  1. TURSO_DATABASE_URL set (libsql:// form from `turso db show --url`)
     AND TURSO_AUTH_TOKEN set -> Turso remote, direct mode only
     (libsql.connect(database=..., auth_token=...)). No embedded replica:
     a local file on Render is ephemeral and would confuse the state story.
     Turso WINS over Postgres if both are set. If TURSO_DATABASE_URL is
     set but TURSO_AUTH_TOKEN is missing, startup fails fast — it must
     never silently fall back to SQLite on Render, or data would reseed.
     See TURSO.md for the user's dashboard steps.
  2. DATABASE_URL starting with postgres:// or postgresql:// -> Postgres,
     via psycopg[binary] from requirements.txt, imported lazily only on
     this path. Kept as a fallback for any postgres:// provider; it is
     not the chosen route (Neon was ruled out). See NEON.md.
  3. Anything else (including both unset) -> local SQLite file
     omnibrain.db, exactly as before, with no extra dependency installed.

Both libsql and psycopg are imported lazily, only on their own path,
so a local SQLite run needs nothing installed. requirements.txt lists
both because Render installs it for all paths.

The AI assistant (Ask / Triage / Draft) is rule-based and runs offline
over the live workspace; the rule-based Steps 1-3 result is always
computed first and stays authoritative. If OPENAI_API_KEY is set, one
optional Chat Completions call (gpt-4o-mini, stdlib urllib only, 15s
timeout, max 600 tokens) adds a natural-language layer on top, grounded
only in the rule-based result and real DB rows — its text is returned
in llm_answer alongside the unchanged rule-based fields, and any
failure falls back silently to the rule-based response. Without the
key the app is fully offline. All data is simulated for demonstration.
OMNIBRAIN_LLM_URL is an old placeholder and is not read.
Never print TURSO_DATABASE_URL, TURSO_AUTH_TOKEN, DATABASE_URL or
OPENAI_API_KEY: they are secrets.
"""
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, "public")
DB_PATH = os.path.join(BASE, "omnibrain.db")
PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "127.0.0.1")
# Engine priority (see docstring): Turso > Postgres > local SQLite.
# TURSO_DATABASE_URL must be the libsql:// URL from `turso db show --url`.
TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")
USE_TURSO = bool(TURSO_DATABASE_URL)
# Postgres is used only when DATABASE_URL is a postgres URL and Turso is
# not set. Any other value (including unset) keeps local SQLite behaviour.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = (not USE_TURSO) and DATABASE_URL.startswith(("postgres://", "postgresql://"))

# ---------------------------------------------------------------- optional LLM layer (gpt-4o-mini)
# Read from the environment only (Render dashboard env var, sync: false
# in render.yaml). The Muse-side OpenAI connection cannot be used here;
# no key value is ever stored in this repo, in logs, or in chat.
LLM_MODEL = "gpt-4o-mini"
LLM_URL = "https://api.openai.com/v1/chat/completions"
LLM_TIMEOUT_S = 15
LLM_MAX_TOKENS = 600
LLM_TEMPERATURE = 0.2
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()


# ---------------------------------------------------------------- DB adapter
# Handlers below are written once, in SQLite dialect: "?" placeholders,
# INTEGER PRIMARY KEY AUTOINCREMENT, cursor.lastrowid, fetchone()[0] for
# COUNT(*). The wrapper translates that dialect for Postgres so no
# handler needs a second copy. Audit (2026-11-03): the app has no
# INSERT OR / REPLACE INTO / PRAGMA / LIKE-with-"?" SQL, no "?" inside
# SQL string literals, and no "%" inside SQL strings, so the scanner
# below (which skips single-quoted literals anyway) covers every query.
def _pg_sql(sql):
    """Translate one SQLite-dialect statement to Postgres dialect."""
    out, in_quote, i = [], False, 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            if in_quote and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_quote = not in_quote
            out.append(ch)
        elif ch == "?" and not in_quote:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _pg_schema(sql):
    return sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


class _Cursor:
    """Cursor wrapper: tuple rows on both engines, .lastrowid on both."""

    def __init__(self, raw, is_pg=False, returning=False):
        self._raw = raw
        self._is_pg = is_pg
        self._returning = returning
        self._lastrowid = None
        self._lastrowid_fetched = False

    @property
    def description(self):
        return self._raw.description

    def fetchone(self):
        return self._raw.fetchone()

    def fetchall(self):
        return self._raw.fetchall()

    @property
    def lastrowid(self):
        if not self._is_pg:
            return self._raw.lastrowid
        if not self._returning:
            return None
        if not self._lastrowid_fetched:
            row = self._raw.fetchone()
            self._lastrowid = row[0] if row else None
            self._lastrowid_fetched = True
        return self._lastrowid


class _Connection:
    """Connection wrapper: same .execute/.executescript/.commit/.close.

    Modes: is_pg (Postgres translation) or is_turso (SQLite dialect passed
    through unchanged — libSQL speaks SQLite, so no ?->%s or SERIAL
    translation applies; its cursor natively exposes lastrowid and a
    DB-API description, verified against libsql 0.1.11). Plain SQLite
    is neither flag. For Turso, executescript is implemented as
    split-on-';' rather than relying on the remote executescript path:
    SCHEMA contains no ';' inside string literals, so the split is safe.
    """

    def __init__(self, raw, is_pg=False, is_turso=False):
        self._raw = raw
        self._is_pg = is_pg
        self._is_turso = is_turso

    def execute(self, sql, args=()):
        if self._is_pg:
            sql = _pg_sql(sql)
            returning = sql.strip().upper().startswith("INSERT ") and "RETURNING" not in sql.upper()
            if returning:
                sql = sql + " RETURNING id"
            return _Cursor(self._raw.execute(sql, args), is_pg=True, returning=returning)
        return _Cursor(self._raw.execute(sql, args))

    def executescript(self, sql):
        if self._is_pg:
            for stmt in _pg_schema(sql).split(";"):
                if stmt.strip():
                    self._raw.execute(stmt)
            return None
        if self._is_turso:
            for stmt in sql.split(";"):
                if stmt.strip():
                    self._raw.execute(stmt)
            return None
        return self._raw.executescript(sql)

    def commit(self):
        return self._raw.commit()

    def close(self):
        return self._raw.close()

# ---------------------------------------------------------------- MTTR thresholds
# Industry benchmarks, NOT a single official NIST/ISO MTTR standard.
# There is no official NIST/ISO MTTR threshold; the values below are
# commonly used industry benchmarks, configurable here. The overall
# 120-240 minute (2-4 hour) range is a reference range, not a hard
# threshold. Seed incident rows are simulated samples (see INCIDENTS).
MTTR_THRESHOLDS = {"Critical": 60, "High": 120, "Medium": 240, "Low": 480}
MTTR_BENCHMARK_RANGE_MINS = (120, 240)
MTTR_SEED_BASELINE_MINS = 38.0

# MITRE ATT&CK Enterprise tactics, in canonical order.
TACTICS = ["Reconnaissance", "Resource Development", "Initial Access", "Execution",
           "Persistence", "Privilege Escalation", "Defense Evasion", "Credential Access",
           "Discovery", "Lateral Movement", "Collection", "Command and Control",
           "Exfiltration", "Impact"]
# technique -> (name, primary_tactic, related_tactics)
TECHNIQUE_MAP = {
    "T1078": ("Valid Accounts", "Persistence", ["Initial Access", "Privilege Escalation", "Defense Evasion"]),
    "T1550": ("Use Alternate Authentication Material", "Defense Evasion", ["Lateral Movement", "Persistence"]),
    "T1059.001": ("PowerShell", "Execution", []),
    "T1003.001": ("LSASS Memory", "Credential Access", []),
    "T1110": ("Brute Force", "Credential Access", []),
    "T1071.004": ("DNS", "Command and Control", []),
    "T1098": ("Account Manipulation", "Persistence", []),
    "T1486": ("Data Encrypted for Impact", "Impact", []),
    "T1190": ("Exploit Public-Facing Application", "Initial Access", []),
    "T1133": ("External Remote Services", "Persistence", ["Initial Access"]),
    "T1566": ("Phishing", "Initial Access", []),
    "T1548": ("Abuse Elevation Control Mechanism", "Privilege Escalation", []),
    "T1068": ("Exploitation for Privilege Escalation", "Privilege Escalation", []),
    "T1083": ("File and Directory Discovery", "Discovery", []),
    "T1087": ("Account Discovery", "Discovery", []),
    "T1021": ("Remote Services", "Lateral Movement", []),
    "T1005": ("Data from Local System", "Collection", []),
    "T1048": ("Exfiltration Over Alternative Protocol", "Exfiltration", []),
    "T1595": ("Active Scanning", "Reconnaissance", []),
    "T1583": ("Acquire Infrastructure", "Resource Development", []),
    "T1588": ("Obtain Capabilities", "Resource Development", []),
}

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
CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  severity TEXT NOT NULL, mttr_mins REAL NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS threat_intel_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL, fetched_at TEXT NOT NULL, payload_json TEXT DEFAULT '[]');
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
    if USE_TURSO:
        # Fail fast, never silently fall back to SQLite: a missing token
        # on Render would otherwise reseed data instead of using Turso.
        if not TURSO_AUTH_TOKEN:
            raise RuntimeError(
                "TURSO_DATABASE_URL is set but TURSO_AUTH_TOKEN is missing. "
                "Set TURSO_AUTH_TOKEN in the Render dashboard (see TURSO.md) "
                "or unset TURSO_DATABASE_URL to use local SQLite.")
        # Lazy import: SQLite/Postgres runs must never require libsql.
        # Remote direct mode only — no sync_url / embedded replica.
        import libsql

        return _Connection(
            libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN),
            is_turso=True)
    if USE_POSTGRES:
        # Lazy import: a SQLite run must never require psycopg installed.
        import psycopg

        # The pooled Postgres string already carries sslmode=require; pass
        # it through untouched and never log it.
        return _Connection(psycopg.connect(DATABASE_URL), is_pg=True)
    return _Connection(sqlite3.connect(DB_PATH))


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
    # Seed incidents independently (also covers pre-existing DBs created
    # before the incidents table existed). Three Critical samples averaging
    # exactly 38 minutes: (32 + 38 + 44) / 3 = 38. Simulated samples only.
    if conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0:
        for title, mins in [("Sample incident — ransomware containment (simulated)", 32),
                            ("Sample incident — compromised account response (simulated)", 38),
                            ("Sample incident — phishing mailbox purge (simulated)", 44)]:
            conn.execute("INSERT INTO incidents (title, severity, mttr_mins, created_at) VALUES (?,?,?,?)",
                         (title, "Critical", mins, date.today().isoformat()))
        conn.commit()
    conn.close()


def rows(conn, sql, args=()):
    # Description-based dicts work on both engines (tuple rows). Do not
    # use dict(r) here: Postgres cursors return tuples, not sqlite Rows.
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _mean(vals):
    return round(sum(vals) / len(vals), 1) if vals else None


def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return round(s[n // 2], 1) if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 1)


def mttr_stats(conn):
    """MTTR computed from the incidents table, never a hardcoded constant
    when rows exist. Empty table -> seed baseline 38.0 (documented)."""
    inc = rows(conn, "SELECT * FROM incidents")
    if not inc:
        by_sev = {sev: {"mean": None, "median": None, "count": 0,
                         "threshold_mins": MTTR_THRESHOLDS[sev], "status": "no_data"}
                  for sev in MTTR_THRESHOLDS}
        return {"mttr_mins": MTTR_SEED_BASELINE_MINS, "mttr_by_severity": by_sev,
                "mttr_source": "seed baseline"}
    by_sev = {}
    for sev, thr in MTTR_THRESHOLDS.items():
        vals = [r["mttr_mins"] for r in inc if r["severity"] == sev]
        by_sev[sev] = {"mean": _mean(vals), "median": _median(vals), "count": len(vals),
                       "threshold_mins": thr,
                       "status": "no_data" if not vals else ("pass" if _mean(vals) <= thr else "breach")}
    return {"mttr_mins": _mean([r["mttr_mins"] for r in inc]),
            "mttr_by_severity": by_sev, "mttr_source": "measured from incident log"}


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
    out = {"fidelity": fidelity, "coverage": coverage, "automation_rate": automation,
           "hours_saved": hours, "open_backlog": backlog_n, "prod_detections": prod_n,
           "blocked_items": blocked, "noisy_detections": noisy}
    out.update(mttr_stats(conn))
    return out


def _stage_score(stage):
    if stage == "Production":
        return 3
    if stage in ("Testing", "Tuning", "Deployment"):
        return 2
    if stage in ("Idea", "Development"):
        return 1
    return 0


def mitre_heatmap(conn):
    """Derived live from detection.mitre — never a hardcoded second copy.
    Split on ' / ', map each code to its primary tactic, keep unknown
    codes under 'Unknown' rather than dropping them."""
    dets = rows(conn, "SELECT * FROM detections")
    by_code = {}
    for d in dets:
        for code in [c.strip() for c in (d.get("mitre") or "").split(" / ") if c.strip()]:
            by_code.setdefault(code, []).append(d)
    tech_by_tactic = {t: [] for t in TACTICS}
    tech_by_tactic["Unknown"] = []
    for code in sorted(by_code):
        ds = by_code[code]
        name, primary, related = TECHNIQUE_MAP.get(code, (code, "Unknown", []))
        best = max(ds, key=lambda r: _stage_score(r["stage"]))
        entry = {"id": code, "name": name, "primary_tactic": primary,
                 "related_tactics": related,
                 "coverage_score": _stage_score(best["stage"]),
                 "stage": best["stage"], "fidelity": best["fidelity"], "fp_rate": best["fp_rate"],
                 "detections": [r["name"] for r in ds],
                 "detection_details": [{"name": r["name"], "stage": r["stage"],
                                        "fidelity": r["fidelity"], "fp_rate": r["fp_rate"],
                                        "coverage_score": _stage_score(r["stage"])} for r in ds]}
        tech_by_tactic[primary].append(entry)
    tactics = []
    for tactic in TACTICS + ["Unknown"]:
        techs = sorted(tech_by_tactic[tactic], key=lambda t: t["id"])
        if not techs:
            status = "gap"
        elif any(t["coverage_score"] == 3 for t in techs):
            status = "covered"
        else:
            status = "partial"
        tactics.append({"tactic": tactic, "techniques": techs,
                        "technique_count": len(techs),
                        "max_coverage_score": max([t["coverage_score"] for t in techs], default=0),
                        "tactic_status": status})
    # Drop the Unknown tactic row if empty, keep it if it has techniques.
    tactics = [t for t in tactics if t["tactic"] != "Unknown" or t["technique_count"] > 0]
    gaps = [t["tactic"] for t in tactics if t["tactic_status"] == "gap"]
    covered = sum(1 for t in tactics if t["tactic_status"] == "covered")
    partial = sum(1 for t in tactics if t["tactic_status"] == "partial")
    # Prioritise gaps first, then partials by lowest max score, then name.
    lowest = sorted([t for t in tactics if t["tactic_status"] != "covered"],
                    key=lambda t: (0 if t["tactic_status"] == "gap" else 1, t["max_coverage_score"], t["tactic"]))
    return {"unique_techniques": len(by_code),
            "techniques_covered": sorted(by_code),
            "tactics": tactics,
            "tactics_covered": covered, "tactics_partial": partial, "tactics_gap": len(gaps),
            "gaps": gaps,
            "lowest_coverage_tactics": [t["tactic"] for t in lowest],
            "mitigations": mitigations_for_gaps(gaps),
            "primary_tactic_gap_note": "Some gaps are primary-tactic only — e.g. T1078 also covers Initial Access and T1550 also covers Lateral Movement as secondary tactics, so those tactics are not wholly blind, they just lack a primary-tactic detection.",
            "note": "Derived from live detections; technique coverage_score: 0=no detection, 1=Idea/Development, 2=Testing/Tuning/Deployment, 3=Production."}



# ---------------------------------------------------------------- MITRE gap mitigations
# Priority order (user-agreed): P0 attacker progression first, then data loss,
# then early warning, then threat-intel monitoring.
MITIGATION_PRIORITY = {"Initial Access": 0, "Privilege Escalation": 0, "Lateral Movement": 0,
                       "Exfiltration": 1, "Collection": 1,
                       "Discovery": 2, "Reconnaissance": 2, "Resource Development": 3}
MITRE_MITIGATIONS = {
    "Initial Access": {"tactic": "Initial Access", "recommended_detection": "Detect exploitation of public-facing applications and phishing",
                      "technique": "T1190", "also_technique": "T1566", "log_source_needed": "WAF + web server logs, email gateway logs",
                      "nist_function": "Detect", "effort": "M", "priority": "P0",
                      "suggested_backlog_title": "Add Initial Access detection: public-facing exploit (T1190) + phishing (T1566)"},
    "Privilege Escalation": {"tactic": "Privilege Escalation", "recommended_detection": "Detect elevation control abuse and privilege escalation exploits",
                      "technique": "T1548", "also_technique": "T1068", "log_source_needed": "EDR process + privilege-change logs, CloudTrail",
                      "nist_function": "Detect", "effort": "M", "priority": "P0",
                      "suggested_backlog_title": "Add Privilege Escalation detection (T1548/T1068)"},
    "Lateral Movement": {"tactic": "Lateral Movement", "recommended_detection": "Detect remote service abuse",
                      "technique": "T1021", "also_technique": "", "log_source_needed": "Network flow + EDR lateral movement logs, VPN/RDP logs",
                      "nist_function": "Detect", "effort": "M", "priority": "P0",
                      "suggested_backlog_title": "Add Lateral Movement detection via remote services (T1021)"},
    "Exfiltration": {"tactic": "Exfiltration", "recommended_detection": "Detect exfiltration over alternative protocol",
                      "technique": "T1048", "also_technique": "", "log_source_needed": "Network egress + DNS + proxy logs, DLP",
                      "nist_function": "Detect", "effort": "M", "priority": "P1",
                      "suggested_backlog_title": "Add Exfiltration detection (T1048)"},
    "Collection": {"tactic": "Collection", "recommended_detection": "Detect local system data collection",
                      "technique": "T1005", "also_technique": "", "log_source_needed": "EDR file access + DLP logs",
                      "nist_function": "Detect", "effort": "S", "priority": "P1",
                      "suggested_backlog_title": "Add Collection detection (T1005)"},
    "Discovery": {"tactic": "Discovery", "recommended_detection": "Detect file/directory and account discovery",
                      "technique": "T1083", "also_technique": "T1087", "log_source_needed": "EDR + audit logs (process, file access, account queries)",
                      "nist_function": "Detect", "effort": "S", "priority": "P1",
                      "suggested_backlog_title": "Add Discovery detection (T1083/T1087)"},
    "Reconnaissance": {"tactic": "Reconnaissance", "recommended_detection": "Detect active scanning and external enumeration",
                      "technique": "T1595", "also_technique": "", "log_source_needed": "Internet-facing asset / WAF + firewall logs, external attack surface feed",
                      "nist_function": "Detect", "effort": "M", "priority": "P2",
                      "suggested_backlog_title": "Add Active Scanning (T1595) detection"},
    "Resource Development": {"tactic": "Resource Development", "recommended_detection": "Monitor adversary infrastructure and capability acquisition",
                      "technique": "T1583", "also_technique": "T1588", "log_source_needed": "Threat-intel feeds (CISA/MITRE/abuse.ch) + domain registration monitoring",
                      "nist_function": "Identify", "effort": "M", "priority": "P2",
                      "suggested_backlog_title": "Add threat-intel monitoring for infrastructure/capability acquisition (T1583/T1588)"},
}

def mitigations_for_gaps(gaps):
    out = [dict(MITRE_MITIGATIONS[g]) for g in gaps if g in MITRE_MITIGATIONS]
    out.sort(key=lambda m: (MITIGATION_PRIORITY.get(m["tactic"], 9), m["tactic"]))
    return out

# ---------------------------------------------------------------- rule-based reasoning, steps 1-3
# Step 1 MITRE keyword rules: (keywords, technique, confidence)
MITRE_KEYWORDS = [
    (["powershell", "encodedcommand"], "T1059.001", "high"),
    (["lsass", "credential dumping", "credential dump"], "T1003.001", "high"),
    (["brute force", "password spraying"], "T1110", "high"),
    (["token replay", "impossible travel"], "T1078", "high"),
    (["token reuse", "alternate authentication"], "T1550", "high"),
    (["ransomware", "data encrypted"], "T1486", "high"),
    (["phishing", "phish"], "T1566", "high"),
    (["dns tunneling", "dns tunnel", "c2", "command and control"], "T1071.004", "high"),
    (["account manipulation", "policy attach", "new key creation"], "T1098", "high"),
    (["privilege escalation", "elevation control"], "T1548", "high"),
    (["public-facing", "public facing", "web exploit", "exploit public"], "T1190", "medium"),
    (["remote services", "rdp", "lateral movement", "lateral"], "T1021", "medium"),
    (["active scanning", "scanning", "reconnaissance", "enumeration"], "T1595", "medium"),
    (["file discovery", "directory discovery"], "T1083", "medium"),
    (["account discovery"], "T1087", "medium"),
    (["exfiltration", "exfil", "data loss"], "T1048", "medium"),
    (["local system", "data collection", "collection"], "T1005", "medium"),
    (["external remote", "vpn gateway"], "T1133", "medium"),
    (["infrastructure acquisition", "capability acquisition"], "T1583", "medium"),
]

def step1_mitre(text):
    low = (text or "").lower()
    for keywords, code, conf in MITRE_KEYWORDS:
        hits = [k for k in keywords if k in low]
        if hits:
            name, tactic, related = TECHNIQUE_MAP.get(code, (code, "Unknown", []))
            return {"technique": code, "technique_name": name, "tactic": tactic,
                    "related_tactics": related, "confidence": conf, "evidence_keywords": hits}
    return {"technique": None, "technique_name": None, "tactic": None,
            "related_tactics": [], "confidence": "low", "evidence_keywords": [],
            "note": "no MITRE technique matched — human review required"}

def step2_nist(text):
    low = (text or "").lower()
    if any(w in low for w in ["approval", "governance", "owner", "policy", "oversight", "role"]):
        return {"function": "Govern", "category": "Roles, Responsibilities, and Authorities (GV.RR)",
                "rationale": "Approval, ownership, policy or oversight is the governing control for this request."}
    if any(w in low for w in ["rollback", "recover", "restore", "lessons learned", "lessons"]):
        return {"function": "Recover", "category": "Incident Recovery Plan Execution (RC.RP)",
                "rationale": "Rollback, recovery and lessons learned sit in Recover."}
    if any(w in low for w in ["isolate", "contain", "disable", "block", "purge", "respond", "incident"]):
        return {"function": "Respond", "category": "Incident Mitigation (RS.MI)",
                "rationale": "Containment and response actions sit in Respond."}
    if any(w in low for w in ["asset", "inventory", "cmdb", "criticality"]):
        return {"function": "Identify", "category": "Asset Management (ID.AM)",
                "rationale": "Asset inventory and criticality sit in Identify."}
    if any(w in low for w in ["mfa", "allowlist", "access control", "authentication", "patch"]):
        return {"function": "Protect", "category": "Identity Management, Authentication and Access Control (PR.AA)",
                "rationale": "MFA, allowlisting and access control sit in Protect."}
    return {"function": "Detect", "category": "Continuous Monitoring (DE.CM)",
            "rationale": "Detection, alerting, telemetry and log coverage sit in Detect."}

def reason_about(text, triage_rec=None):
    s1 = step1_mitre(text)
    s2 = step2_nist(text)
    if triage_rec:
        priority, owner = triage_rec.get("priority", "P2"), triage_rec.get("team", "Platform")
        approval = bool(triage_rec.get("type") == "soar" and triage_rec.get("impact", 0) >= 9) or priority == "P0"
        success = triage_rec.get("acceptance", "Fidelity, FP rate, MTTR and adoption measured 30 days post-launch.")
    else:
        low = (text or "").lower()
        priority = "P0" if ("critical" in low or "ransomware" in low) else "P2"
        owner = "Detection Eng" if s1["tactic"] else "Platform"
        approval = any(w in low for w in ["isolate", "disable", "critical", "high-impact", "high impact"])
        success = "Fidelity, FP rate, MTTR and adoption measured 30 days post-launch."
    launch_gate = ["Runbook written and linked", "Owner + support model named", "Success criteria + KPI defined",
                   "Analyst training / enablement done", "Test evidence (backtest / staging) attached",
                   "Rollback plan documented", "Documentation published"]
    s3 = {"priority": priority, "owner": owner, "approval_required": approval,
          "launch_gate": launch_gate, "success_criteria": success}
    return {"step1_mitre": s1, "step2_nist": s2, "step3_decision": s3}


# ---------------------------------------------------------------- optional LLM layer (gpt-4o-mini)
# The rule-based engine above stays authoritative: the LLM is a
# natural-language layer on top of it, never a replacement for
# grounding. One Chat Completions call per ask/triage/draft, stdlib
# urllib only, no new dependency. No key -> no call, rule-based only.
# Any failure (timeout, network, non-200, bad JSON) -> silent fallback
# to the exact rule-based response with llm_used=false. Never retry,
# never 500 because of the LLM, never expose the key or raw API body.
def llm_status():
    return {"configured": bool(OPENAI_API_KEY), "model": LLM_MODEL}


def _workspace_context(conn):
    """Compact grounded context: real DB rows only, capped. Never a
    whole-table dump."""
    k = kpis(conn)
    return {
        "kpis": {"fidelity": k["fidelity"], "coverage": k["coverage"],
                 "mttr_mins": k["mttr_mins"], "open_backlog": k["open_backlog"]},
        "backlog_top5": [{"id": r["id"], "title": r["title"], "team": r["team"],
                          "priority": r["priority"], "status": r["status"], "rice": r["rice"]}
                         for r in rows(conn, "SELECT * FROM backlog ORDER BY rice DESC LIMIT 5")],
        "detections": [{"name": r["name"], "stage": r["stage"], "fidelity": r["fidelity"],
                        "fp_rate": r["fp_rate"], "mitre": r["mitre"]}
                       for r in rows(conn, "SELECT * FROM detections")],
        "telemetry": [{"name": r["name"], "status": r["status"], "coverage": r["coverage"]}
                      for r in rows(conn, "SELECT * FROM telemetry")],
        "playbooks": [{"name": r["name"], "risk": r["risk_level"], "status": r["status"]}
                      for r in rows(conn, "SELECT * FROM playbooks")],
        "roadmap": [{"title": r["title"], "quarter": r["quarter"], "status": r["status"]}
                    for r in rows(conn, "SELECT * FROM roadmap")],
    }


def _llm_chat(messages):
    """One Chat Completions call. Returns the model's text, or None on
    any failure. The key travels only in this request's header."""
    import urllib.request
    payload = json.dumps({"model": LLM_MODEL, "messages": messages,
                          "temperature": LLM_TEMPERATURE,
                          "max_tokens": LLM_MAX_TOKENS}).encode()
    req = urllib.request.Request(
        LLM_URL, data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + OPENAI_API_KEY},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    try:
        text = data["choices"][0]["message"]["content"]
    except Exception:
        return None
    return text.strip() if isinstance(text, str) and text.strip() else None


def llm_enhance(conn, kind, user_text, reasoning, rule_summary):
    """Returns (text_or_None, used_bool, error_or_None). Never raises."""
    if not OPENAI_API_KEY:
        return None, False, None
    s1 = reasoning.get("step1_mitre", {}) if isinstance(reasoning, dict) else {}
    system = (
        "You are the natural-language layer for OmniBrain, a SecOps "
        "product-management demo. All workspace data is simulated for "
        "demonstration, not live employer telemetry. "
        "Answer using ONLY the facts in the supplied rule-based reasoning "
        "and workspace context. Cite real backlog/detection/telemetry "
        "titles and IDs from the context. Keep the Steps 1-3 structure: "
        "restate Step 1 (MITRE technique/tactic), Step 2 (NIST CSF 2.0 "
        "function), Step 3 (priority, owner, approval gate). "
        "Never invent MITRE techniques, controls, detections, or "
        "workspace facts. If Step 1 technique is null, say exactly "
        "'no MITRE technique matched — human review required' and do not "
        "guess a technique. Be concise."
    )
    try:
        context = _workspace_context(conn)
    except Exception:
        # A DB/context error must never turn an ask/triage/draft into a
        # 500: fall back to the rule-based answer, same as an LLM failure.
        return None, False, "openai_unavailable"
    user = json.dumps({"kind": kind, "request": user_text,
                       "rule_based_reasoning_steps_1_to_3": reasoning,
                       "rule_based_summary": rule_summary,
                       "workspace_context": context}, default=str)
    text = _llm_chat([{"role": "system", "content": system},
                      {"role": "user", "content": user}])
    if text is None:
        return None, False, "openai_unavailable"
    return text, True, None


def _attach_llm(result, conn, kind, user_text, rule_summary):
    """Adds llm_answer / llm_used / llm_model / llm_error to a
    rule-based result without touching its rule-based fields."""
    reasoning = result.get("reasoning") or {
        "step1_mitre": result.get("step1_mitre", {}),
        "step2_nist": result.get("step2_nist", {}),
        "step3_decision": result.get("step3_decision", {})}
    text, used, err = llm_enhance(conn, kind, user_text, reasoning, rule_summary)
    result["llm_answer"] = text
    result["llm_used"] = used
    result["llm_model"] = LLM_MODEL if used else None
    result["llm_error"] = err
    return result

# ---------------------------------------------------------------- free public threat intel (external context only)
THREAT_INTEL_NOTE = "public external threat context, not internal telemetry"
MITRE_STIX_URL = "https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json"

def _fetch_json(url, timeout=10):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "omnibrain-demo/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def _fetch_text(url, timeout=10):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "omnibrain-demo/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="replace")

def fetch_threat_intel():
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    results = []
    # CISA KEV — free, no auth, most recent by dateAdded, cap 5.
    try:
        data = _fetch_json("https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json")
        vulns = sorted(data.get("vulnerabilities", []), key=lambda v: v.get("dateAdded", ""), reverse=True)[:5]
        items = [{"cveID": v.get("cveID"), "vendorProject": v.get("vendorProject"), "product": v.get("product"),
                  "dateAdded": v.get("dateAdded"), "vulnerabilityName": v.get("vulnerabilityName")} for v in vulns]
        results.append({"source": "CISA KEV", "status": "ok", "count": len(items), "items": items, "fetched_at": fetched_at})
    except Exception as e:
        results.append({"source": "CISA KEV", "status": "failed", "count": 0, "items": [], "error": str(e)[:200], "fetched_at": fetched_at})
    # abuse.ch URLhaus — NOT attempted: verified 2026-11-03 that its public
    # no-key downloads now return 410 Gone and its v1 recent endpoint returns
    # Unauthorized; abuse.ch now gates those exports behind an API key. This
    # app takes no keys, so the source is documented as key-gated only.
    results.append({"source": "abuse.ch URLhaus", "status": "unavailable", "count": 0, "items": [],
                    "error": "Public no-key downloads are key-gated as of Nov 2026 (410 Gone / Unauthorized); not attempted without a key.",
                    "fetched_at": fetched_at})
    # NVD — free, no key, rate-limited, best-effort only, cap 5. The default
    # listing is oldest-first, so filter to the last 7 days to get recent CVEs.
    try:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end = now.strftime("%Y-%m-%dT%H:%M:%S.999Z")
        data = _fetch_json(f"https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage=5&pubStartDate={start}&pubEndDate={end}")
        items = []
        for v in data.get("vulnerabilities", [])[:5]:
            cve = v.get("cve", {})
            descs = cve.get("descriptions", [])
            items.append({"id": cve.get("id"), "published": cve.get("published", ""), "description": (descs[0].get("value", "")[:200] if descs else "")})
        results.append({"source": "NVD CVE", "status": "ok", "count": len(items), "items": items, "fetched_at": fetched_at})
    except Exception as e:
        results.append({"source": "NVD CVE", "status": "failed", "count": 0, "items": [], "error": str(e)[:200], "fetched_at": fetched_at})
    return results

def threat_intel_summary(conn):
    cached = rows(conn, "SELECT * FROM threat_intel_cache ORDER BY id")
    sources = []
    for r in cached:
        try:
            items = json.loads(r["payload_json"] or "[]")
        except Exception:
            items = []
        sources.append({"source": r["source"], "fetched_at": r["fetched_at"], "count": len(items), "items": items})
    fetched = max([s["fetched_at"] for s in sources], default=None)
    return {"sources": sources, "fetched_at": fetched,
            "status": "cached" if sources else "empty",
            "note": THREAT_INTEL_NOTE,
            "mitre_note": "MITRE enrichment is the local technique/tactic mapping; full STIX bundle is NOT downloaded at runtime (too large). Future offline snapshot source: " + MITRE_STIX_URL}

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
    rec = {"title": title, "type": kind, "team": team, "reach": r, "impact": i, "confidence": c,
           "effort": e, "rice": rice(r, i, c, e), "priority": priority,
           "acceptance": acc, "risks": risks,
           "description": f"Triaged from stakeholder request: {text.strip()[:300]}"}
    reasoning = reason_about(text, triage_rec=rec)
    rec["reasoning"] = reasoning
    rec["step1_mitre"] = reasoning["step1_mitre"]
    rec["step2_nist"] = reasoning["step2_nist"]
    rec["step3_decision"] = reasoning["step3_decision"]
    return rec


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
               f"MTTR {k['mttr_mins']:g}m, {k['open_backlog']} open backlog items): "
               f"prioritise signal quality before breadth — fix the noisiest detection and unblock telemetry before adding new coverage.")
    reasoning = reason_about(question)
    return {"summary": summary, "actions": actions,
            "reasoning": reasoning,
            "step1_mitre": reasoning["step1_mitre"],
            "step2_nist": reasoning["step2_nist"],
            "step3_decision": reasoning["step3_decision"],
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
    reasoning = reason_about((item["title"] + " " + item["description"]) if item else (topic or ""))
    base = f"# {title}\n\nSimulated data for demonstration.\n\n## Reasoning — Steps 1-3\n\nStep 1 MITRE: {reasoning['step1_mitre']}\n\nStep 2 NIST CSF 2.0: {reasoning['step2_nist']}\n\nStep 3 Decision: {reasoning['step3_decision']}\n\n"
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
    return {"kind": kind, "title": title, "document": doc, "reasoning": reasoning,
            "step1_mitre": reasoning["step1_mitre"], "step2_nist": reasoning["step2_nist"],
            "step3_decision": reasoning["step3_decision"]}


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
        if parsed.path == "/api/threat-intel":
            conn = db()
            out = threat_intel_summary(conn)
            conn.close()
            return self.send_json(200, out)
        if parsed.path == "/api/llm-status":
            return self.send_json(200, llm_status())
        if parsed.path == "/api/mitre":
            conn = db()
            out = mitre_heatmap(conn)
            conn.close()
            return self.send_json(200, out)
        if parsed.path == "/api/incidents":
            conn = db()
            out = rows(conn, "SELECT * FROM incidents ORDER BY id")
            conn.close()
            return self.send_json(200, out)
        if parsed.path == "/api/dashboard":
            conn = db()
            out = {"kpis": kpis(conn), "brief": make_brief(conn), "mitre_heatmap": mitre_heatmap(conn),
                   "llm_available": bool(OPENAI_API_KEY),
                   "llm_model": LLM_MODEL if OPENAI_API_KEY else None,
                   "threat_intel": threat_intel_summary(conn),
                   "incidents": rows(conn, "SELECT * FROM incidents ORDER BY id"),
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
        parts = [unquote(p) for p in parsed.path.split("/") if p]
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
            _attach_llm(rec, conn, "triage", body["text"],
                        {"title": rec["title"], "type": rec["type"], "team": rec["team"],
                         "rice": rec["rice"], "priority": rec["priority"],
                         "acceptance": rec["acceptance"], "risks": rec["risks"]})
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
            ans = ask_answer(conn, body["question"])
            _attach_llm(ans, conn, "ask", body["question"],
                        {"summary": ans["summary"], "actions": ans["actions"]})
            return self.send_json(200, ans)
        if parts == ["draft"] and self.command == "POST":
            doc = draft_doc(conn, body.get("kind", ""), body.get("backlog_id"), body.get("topic", ""))
            if doc is None:
                return self.send_json(400, {"error": "kind must be prd|runbook|launch-plan, and backlog_id (if given) must exist"})
            _attach_llm(doc, conn, "draft", body.get("topic") or doc["title"],
                        {"document": doc["document"]})
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
        # ---- incidents (drives MTTR)
        if parts == ["incidents"] and self.command == "POST":
            if not (body.get("title") or "").strip():
                return self.send_json(400, {"error": "missing title"})
            if body.get("severity") not in MTTR_THRESHOLDS:
                return self.send_json(400, {"error": "severity must be Critical|High|Medium|Low"})
            try:
                mins = float(body.get("mttr_mins"))
            except (TypeError, ValueError):
                return self.send_json(400, {"error": "mttr_mins must be a number > 0"})
            if mins <= 0:
                return self.send_json(400, {"error": "mttr_mins must be > 0"})
            cur = conn.execute("INSERT INTO incidents (title, severity, mttr_mins, created_at) VALUES (?,?,?,?)",
                               (body["title"].strip(), body["severity"], mins, date.today().isoformat()))
            conn.commit()
            return self.send_json(201, get_one("incidents", cur.lastrowid))
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
        if parts == ["threat-intel", "refresh"] and self.command == "POST":
            results = fetch_threat_intel()
            for r in results:
                if r["status"] == "ok":
                    conn.execute("DELETE FROM threat_intel_cache WHERE source=?", (r["source"],))
                    conn.execute("INSERT INTO threat_intel_cache (source, fetched_at, payload_json) VALUES (?,?,?)",
                                 (r["source"], r["fetched_at"], json.dumps(r["items"])))
            conn.commit()
            ok = any(r["status"] == "ok" for r in results)
            return self.send_json(200, {"sources": results, "status": "ok" if ok else "offline",
                                        "note": THREAT_INTEL_NOTE})
        if len(parts) == 4 and parts[0] == "mitre" and parts[1] == "mitigations" and parts[3] == "create-backlog" and self.command == "POST":
            tactic = parts[2]
            mit = MITRE_MITIGATIONS.get(tactic)
            if not mit:
                return self.send_json(404, {"error": f"no mitigation for tactic {tactic}"})
            eff_map = {"S": 2, "M": 5, "L": 8}
            reach, impact = (9, 9) if mit["priority"] == "P0" else ((8, 8) if mit["priority"] == "P1" else (7, 6))
            effort = eff_map.get(mit["effort"], 5)
            r = rice(reach, impact, 80, effort)
            typ = "telemetry" if tactic == "Resource Development" else "detection"
            team = "Platform" if tactic == "Resource Development" else "Detection Eng"
            cur = conn.execute(
                "INSERT INTO backlog (title, description, type, team, requester, priority, status, reach, impact, confidence, effort, rice, value_statement, acceptance) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mit["suggested_backlog_title"], f"Mitigation for MITRE gap {tactic}: {mit['recommended_detection']} ({mit['technique']}). Log source needed: {mit['log_source_needed']}. NIST function: {mit['nist_function']}.",
                 typ, team, "MITRE gap review", mit["priority"], "intake", reach, impact, 80, effort, r,
                 f"Close MITRE {tactic} gap with {mit['technique']}.", "Backtest, FP <5%, runbook + analyst validation"))
            conn.commit()
            return self.send_json(201, get_one("backlog", cur.lastrowid))
        return self.send_json(404, {"error": "not found"})


def main():
    # Fail fast before serving if Turso is half-configured.
    if USE_TURSO and not TURSO_AUTH_TOKEN:
        print("ERROR: TURSO_DATABASE_URL is set but TURSO_AUTH_TOKEN is missing.", file=sys.stderr)
        print("Set TURSO_AUTH_TOKEN in the Render dashboard (see TURSO.md),", file=sys.stderr)
        print("or unset TURSO_DATABASE_URL to use local SQLite.", file=sys.stderr)
        sys.exit(2)
    if "--reset" in sys.argv:
        if USE_TURSO or USE_POSTGRES:
            # Never drop a shared remote database from a CLI flag; seed
            # only fills empty tables, so existing Turso/Postgres data
            # is preserved.
            print("reset ignored for remote database: existing data is kept, empty tables are seeded")
        elif os.path.exists(DB_PATH):
            os.remove(DB_PATH)
            print("reset: database removed, will reseed on start")
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"OmniBrain running at http://{HOST}:{PORT}  (Ctrl+C to stop)")
    if USE_TURSO:
        print("Database: Turso remote (URL and token not shown)")
    elif USE_POSTGRES:
        print("Database: Postgres (DATABASE_URL set)")
    else:
        print(f"Database: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
