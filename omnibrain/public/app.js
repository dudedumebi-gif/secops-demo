const TABS = [["mission","Mission Control"],["backlog","Backlog"],["detections","Detections"],["telemetry","Telemetry"],["soar","SOAR"],["roadmap","Roadmap"],["launches","Launches"],["mitre","MITRE"],["ask","Ask OmniBrain"]];
let D = null;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function api(path, method, body) {
  const r = await fetch(path, {method: method || "GET", headers: {"Content-Type":"application/json"}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}
function nav() {
  $("#tabs").innerHTML = TABS.map(([k,l]) => `<button data-tab="${k}" class="${k===state.tab?'active':''}">${l}</button>`).join("");
  document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => show(b.dataset.tab));
}
const state = {tab: "mission"};
function show(tab) {
  state.tab = tab;
  document.querySelectorAll(".view").forEach(v => v.classList.add("hidden"));
  document.getElementById("view-" + tab).classList.remove("hidden");
  nav(); render();
}
function rowHtml(cells) { return "<tr>" + cells.map(c => `<td>${c}</td>`).join("") + "</tr>"; }
function render() {
  if (!D) return;
  const k = D.kpis;
  const mt = D.kpis.mttr_by_severity || {};
  const mh = D.mitre_heatmap || {};
  $("#view-mission").innerHTML = `<h2>Mission Control</h2><div class="brief">${esc(D.brief)}</div>
    <div class="cards">
      <div class="card"><b>${k.fidelity}%</b>Fidelity</div><div class="card"><b>${k.mttr_mins}m</b>MTTR (${esc(k.mttr_source || "")})</div>
      <div class="card"><b>${k.coverage}%</b>Coverage</div><div class="card"><b>${k.automation_rate}%</b>Automation</div>
      <div class="card"><b>${k.hours_saved}</b>Hours saved</div><div class="card"><b>${k.open_backlog}</b>Open backlog</div>
      <div class="card"><b>${k.prod_detections}</b>Prod detections</div><div class="card"><b>${k.blocked_items}</b>Blocked items</div>
    </div>
    <h3>MTTR by severity — industry benchmarks, not an official standard</h3>
    <p>Seed incident rows are simulated samples. Logging a real incident below moves MTTR and its pass/breach status.</p>
    <table><tr><th>Severity</th><th>Mean</th><th>Median</th><th>Count</th><th>Threshold</th><th>Status</th></tr>
    ${Object.entries(mt).map(([sev, v]) => rowHtml([esc(sev), v.mean == null ? "—" : v.mean + "m", v.median == null ? "—" : v.median + "m", v.count, v.threshold_mins + "m",
      `<span class="${v.status === "breach" ? "blocked" : v.status === "pass" ? "ready" : ""}">${v.status}</span>`])).join("")}</table>
    <form onsubmit="return logIncident(event)"><input name="title" placeholder="Incident title" required>
    <select name="severity"><option>Critical</option><option>High</option><option>Medium</option><option>Low</option></select>
    <input name="mttr_mins" type="number" step="any" placeholder="Response time (minutes)" required><button class="primary">Log incident response time</button></form>
    <h3>MITRE ATT&amp;CK coverage</h3>
    <p>${mh.unique_techniques || 0} unique techniques covered · ${mh.tactics_covered || 0} tactics covered, ${mh.tactics_partial || 0} partial, ${mh.tactics_gap || 0} with gaps.
    Gaps: ${(mh.gaps || []).join(", ") || "none"}. Prioritise: ${(mh.lowest_coverage_tactics || []).slice(0, 5).join(", ")}.
    See the MITRE tab for the full grid.</p>
    <h3>External threat context — public feeds only, not internal telemetry</h3>
    <p>Sources: CISA KEV, abuse.ch URLhaus, NVD CVE (MITRE enrichment is the local mapping; full STIX is not downloaded at runtime).
    Status: ${esc((D.threat_intel && D.threat_intel.status) || "empty")} · Fetched: ${esc((D.threat_intel && D.threat_intel.fetched_at) || "never")}</p>
    <div>${(D.threat_intel && D.threat_intel.sources || []).map(s => `<div class="card"><b>${esc(s.source)}</b> (${s.count} items, ${esc(s.fetched_at)})
      <ul>${(s.items || []).slice(0, 5).map(it => `<li>${esc(it.cveID || it.id || it.url || JSON.stringify(it))} ${esc(it.vendorProject || it.product || it.threat || "")}</li>`).join("")}</ul></div>`).join("") || "<p>No cached threat intel yet — use Refresh below. Dashboard still works offline.</p>"}</div>
    <p><button class="primary" onclick="refreshThreatIntel()">Refresh external threat context</button> <button class="primary" onclick="refreshBrief()">Refresh daily brief</button></p>
    <h3>Decisions</h3><ul>${D.decisions.map(d => `<li><b>${esc(d.title)}</b> — ${esc(d.context)} (${esc(d.owner)})</li>`).join("")}</ul>`;
  $("#view-backlog").innerHTML = `<h2>Backlog (RICE-ranked)</h2>
    <form onsubmit="return addBacklog(event)"><input name="title" placeholder="Title" required>
    <input name="reach" type="number" step="any" placeholder="Reach" required><input name="impact" type="number" step="any" placeholder="Impact" required>
    <input name="confidence" type="number" step="any" placeholder="Confidence %" required><input name="effort" type="number" step="any" placeholder="Effort" required>
    <input name="team" placeholder="Team"><input name="type" placeholder="Type"><button class="primary">Add (RICE auto-computed)</button></form>
    <form onsubmit="return triage(event)"><input name="text" placeholder="Paste a stakeholder request to triage…" style="width:60%" required>
    <label><input type="checkbox" name="save"> Save to backlog</label><button class="primary">Triage</button></form><div id="triageOut"></div>
    <table><tr><th>Title</th><th>Team</th><th>P</th><th>Status</th><th>RICE</th><th>Actions</th></tr>
    ${D.backlog.map(b => rowHtml([esc(b.title), esc(b.team), esc(b.priority), esc(b.status), b.rice,
      `<button onclick="setBacklog(${b.id},'in_progress')">Start</button> <button onclick="setBacklog(${b.id},'done')">Done</button> <button onclick="delBacklog(${b.id})">Delete</button>`])).join("")}</table>`;
  $("#view-detections").innerHTML = `<h2>Detections</h2><table><tr><th>Name</th><th>Stage</th><th>Fidelity</th><th>FP %</th><th>MITRE</th><th>Move</th></tr>
    ${D.detections.map(d => rowHtml([esc(d.name), esc(d.stage), d.fidelity + "%", d.fp_rate, esc(d.mitre),
      `<select onchange="setDet(${d.id},this.value)"><option>${esc(d.stage)}</option>${["Idea","Development","Testing","Tuning","Deployment","Production"].filter(s=>s!==d.stage).map(s=>`<option>${s}</option>`).join("")}</select>`])).join("")}</table>
    <form onsubmit="return addDet(event)"><input name="name" placeholder="Detection name" required><button class="primary">Add detection</button></form>`;
  $("#view-telemetry").innerHTML = `<h2>Telemetry</h2><table><tr><th>Source</th><th>Vendor</th><th>Status</th><th>Coverage</th><th>Actions</th></tr>
    ${D.telemetry.map(t => rowHtml([esc(t.name), esc(t.vendor), esc(t.status), t.coverage + "%",
      `<select onchange="setTel(${t.id},this.value)"><option>${esc(t.status)}</option>${["Live","Onboarding","Parser Fix","Queued","Blocked"].filter(s=>s!==t.status).map(s=>`<option>${s}</option>`).join("")}</select>`])).join("")}</table>`;
  $("#view-soar").innerHTML = `<h2>SOAR Playbooks</h2><p>High / Critical playbooks require a named approver. Low / Medium may auto-approve.</p>
    <table><tr><th>Playbook</th><th>Risk</th><th>Status</th><th>Success</th><th>Runs</th><th>Approve</th></tr>
    ${D.playbooks.map(p => rowHtml([esc(p.name), esc(p.risk_level), esc(p.status), p.success_rate + "%", p.runs,
      `<input id="ap-${p.id}" placeholder="Approver"><button onclick="approvePb(${p.id})">Approve</button>`])).join("")}</table>`;
  $("#view-roadmap").innerHTML = `<h2>Roadmap</h2><table><tr><th>Item</th><th>Quarter</th><th>Status</th><th>Theme</th><th>Outcome</th></tr>
    ${D.roadmap.map(r => rowHtml([esc(r.title), esc(r.quarter), esc(r.status), esc(r.theme), esc(r.outcome)])).join("")}</table>
    <form onsubmit="return addRoad(event)"><input name="title" placeholder="Roadmap item" required><input name="quarter" placeholder="Now/Next/Later"><button class="primary">Add</button></form>`;
  $("#view-launches").innerHTML = `<h2>Launches</h2>${D.launches.map(l => `<div class="card"><h3>${esc(l.name)}</h3>
    <p>Target ${esc(l.target_date)} — <span class="${l.launch_status === 'BLOCKED' ? 'blocked' : 'ready'}">${l.launch_status}</span> until all checks pass.</p>
    <ul>${l.checks.map((c,i) => `<li><label><input type="checkbox" ${c.done ? "checked" : ""} onchange="toggleCheck(${l.id},${i})"> ${esc(c.label)}</label></li>`).join("")}</ul></div>`).join("")}`;
  const mh2 = D.mitre_heatmap || {tactics: []};
  $("#view-mitre").innerHTML = `<h2>MITRE ATT&amp;CK Heat Map</h2><p>Derived live from detection MITRE codes — not a separate hardcoded copy.
    Cell colour: deep red = Production (score 3), lighter red/amber = Testing/Tuning/Deployment (2) or Idea/Development (1), grey = gap.
    Hover or click a cell for stage, fidelity and false-positive rate.</p>
    <p><b>${mh2.unique_techniques || 0} unique techniques</b> · ${mh2.tactics_covered || 0} covered / ${mh2.tactics_partial || 0} partial / ${mh2.tactics_gap || 0} gap tactics.</p>
    <div class="mitre-grid">${(mh2.tactics || []).map(t => `<div class="tactic ${t.tactic_status}">
      <h3>${esc(t.tactic)}</h3><p class="tactic-status">${t.tactic_status} · ${t.technique_count} technique(s)</p>
      ${t.techniques.length ? t.techniques.map(te => `<div class="tech-cell score-${te.coverage_score}" title="${esc(te.id)} ${esc(te.name)} — ${esc(te.stage)}, fidelity ${te.fidelity}%, FP ${te.fp_rate}% — detections: ${esc(te.detections.join(", "))}" onclick="alert('${esc(te.id)} ${esc(te.name)}\\nStage: ${esc(te.stage)}\\nFidelity: ${te.fidelity}%\\nFP: ${te.fp_rate}%\\nDetections: ${esc(te.detections.join(", "))}')">
        <b>${esc(te.id)}</b><span>${esc(te.name)}</span><span>${esc(te.stage)} · ${te.fidelity}%</span></div>`).join("") : `<div class="tech-cell gap">No detection — gap</div>`}
    </div>`).join("")}</div>
    <h3>Gaps to prioritise</h3><p>${(mh2.gaps || []).join(", ") || "No gaps."}</p>
    <p><i>${esc(mh2.primary_tactic_gap_note || "")}</i></p>
    <h3>Gap mitigations — P0 attacker progression first</h3>
    <div class="mitre-grid">${(mh2.mitigations || []).map(m => `<div class="tactic"><h3>${esc(m.tactic)}</h3>
      <p><b>${esc(m.recommended_detection)}</b> (${esc(m.technique)}${m.also_technique ? " / " + esc(m.also_technique) : ""})</p>
      <p>Log source: ${esc(m.log_source_needed)}</p><p>NIST: ${esc(m.nist_function)} · Effort ${esc(m.effort)} · Priority ${esc(m.priority)}</p>
      <p>${esc(m.suggested_backlog_title)}</p>
      <button class="primary" onclick="addMitigation('${esc(m.tactic)}')">Add to backlog</button></div>`).join("") || "<p>No mitigations.</p>"}</div>
    <p>Lowest coverage first: ${(mh2.lowest_coverage_tactics || []).join(", ")}</p>`;
  $("#view-ask").innerHTML = `<h2>Ask OmniBrain</h2><p>Offline, grounded in the live workspace above. No external service is called.</p>
    <form onsubmit="return ask(event)"><input name="q" placeholder="e.g. What should we prioritise this week?" style="width:60%" required><button class="primary">Ask</button></form><div id="askOut"></div>
    <h3>Draft a document</h3><form onsubmit="return draft(event)"><select name="kind"><option value="prd">PRD</option><option value="runbook">Runbook</option><option value="launch-plan">Launch plan</option></select>
    <input name="topic" placeholder="Topic, or leave blank and pick a backlog ID"><input name="bid" type="number" placeholder="Backlog ID (optional)"><button class="primary">Draft</button></form><div id="draftOut"></div>`;
}
async function load() { D = await api("/api/dashboard"); render(); }
async function refreshBrief() { const r = await api("/api/refresh-brief", "POST", {}); alert(r.brief); await load(); }
async function logIncident(e) { e.preventDefault(); const f = new FormData(e.target);
  try { await api("/api/incidents", "POST", {title: f.get("title"), severity: f.get("severity"), mttr_mins: +f.get("mttr_mins")}); }
  catch (err) { alert(err.message); return false; }
  await load(); return false; }
async function addBacklog(e) { e.preventDefault(); const f = new FormData(e.target);
  await api("/api/backlog", "POST", {title: f.get("title"), reach: +f.get("reach"), impact: +f.get("impact"), confidence: +f.get("confidence"), effort: +f.get("effort"), team: f.get("team"), type: f.get("type")}); await load(); return false; }
async function setBacklog(id, status) { await api("/api/backlog/" + id, "PATCH", {status}); await load(); }
async function delBacklog(id) { if (confirm("Delete this backlog item?")) { await api("/api/backlog/" + id, "DELETE"); await load(); } }
async function triage(e) { e.preventDefault(); const f = new FormData(e.target);
  const r = await api("/api/triage", "POST", {text: f.get("text"), save: !!f.get("save")});
  $("#triageOut").innerHTML = `<pre>${esc(JSON.stringify(r, null, 2))}</pre>`; await load(); return false; }
async function addDet(e) { e.preventDefault(); await api("/api/detections", "POST", {name: new FormData(e.target).get("name")}); await load(); return false; }
async function setDet(id, stage) { await api("/api/detections/" + id, "PATCH", {stage}); await load(); }
async function setTel(id, status) { await api("/api/telemetry/" + id, "PATCH", {status}); await load(); }
async function approvePb(id) { try { await api("/api/playbooks/" + id + "/approve", "POST", {approver: document.getElementById("ap-" + id).value}); } catch (err) { alert(err.message); } await load(); }
async function addRoad(e) { e.preventDefault(); const f = new FormData(e.target); await api("/api/roadmap", "POST", {title: f.get("title"), quarter: f.get("quarter")}); await load(); return false; }
async function toggleCheck(id, idx) { await api(`/api/launches/${id}/checks/${idx}/toggle`, "POST", {}); await load(); }
async function ask(e) { e.preventDefault(); const r = await api("/api/ask", "POST", {question: new FormData(e.target).get("q")});
  const rs = r.reasoning || r;
  const s1 = rs.step1_mitre || {}, s2 = rs.step2_nist || {}, s3 = rs.step3_decision || {};
  $("#askOut").innerHTML = `<div class="brief"><p>${esc(r.summary)}</p>
    <h3>Reasoning — Steps 1–3</h3>
    <p><b>Step 1 MITRE:</b> ${s1.technique ? esc(s1.technique + " " + (s1.technique_name || "") + " / " + s1.tactic + " (" + s1.confidence + ")") : esc(s1.note || "no MITRE technique matched — human review required")}</p>
    <p><b>Step 2 NIST CSF 2.0:</b> ${esc(s2.function || "")} — ${esc(s2.category || "")}: ${esc(s2.rationale || "")}</p>
    <p><b>Step 3 Decision:</b> priority ${esc(s3.priority || "")}, owner ${esc(s3.owner || "")}, approval required: ${s3.approval_required ? "yes" : "no"}, success: ${esc(s3.success_criteria || "")}</p>
    <ul>${r.actions.map(a => `<li>${esc(a)}</li>`).join("")}</ul><p><b>Risks:</b></p><ul>${r.risks.map(a => `<li>${esc(a)}</li>`).join("")}</ul></div>`; return false; }
async function draft(e) { e.preventDefault(); const f = new FormData(e.target);
  const r = await api("/api/draft", "POST", {kind: f.get("kind"), topic: f.get("topic"), backlog_id: f.get("bid") ? +f.get("bid") : null});
  $("#draftOut").innerHTML = `<pre>${esc(r.document)}</pre>`; return false; }
async function refreshThreatIntel() { try { const r = await api("/api/threat-intel/refresh", "POST", {}); alert("Threat intel refresh: " + r.status + " — " + r.sources.map(s => s.source + ": " + s.status).join(", ")); } catch (err) { alert(err.message); } await load(); }
async function addMitigation(tactic) { try { const r = await api("/api/mitre/mitigations/" + encodeURIComponent(tactic) + "/create-backlog", "POST", {}); alert("Added to backlog: " + r.title + " (RICE " + r.rice + ")"); } catch (err) { alert(err.message); } await load(); }
nav(); load();
