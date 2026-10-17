"use strict";
// MedGate dashboard. Every number shown here is read from the API, which reads persisted events.

/* ================================================================
   Utilities
   ================================================================ */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtT = (t) => t ? new Date(t * 1000).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"}) : "—";
const fmtDT = (t) => t ? new Date(t * 1000).toLocaleString([], {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "—";
const ago = (t) => { if (!t) return "never"; const s = Date.now()/1000 - t; return s < 60 ? `${s|0}s ago` : s < 3600 ? `${(s/60)|0}m ago` : `${(s/3600)|0}h ago`; };

async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {"Content-Type": "application/json"}, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j));
  return j;
}

/* ================================================================
   Toast notifications
   ================================================================ */
function toast(message, type = "success") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.classList.add("removing"); setTimeout(() => el.remove(), 200); }, 3000);
}

/* ================================================================
   Theme management
   ================================================================ */
function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("medgate-theme"); } catch(e) {}
  if (saved) {
    document.documentElement.setAttribute("data-theme", saved);
  } else if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
}
initTheme();

$("#themeToggle").onclick = () => {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  const next = isDark ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("medgate-theme", next); } catch(e) {}
};

/* ================================================================
   Sidebar
   ================================================================ */
const sidebarCollapsedKey = "medgate-sidebar-collapsed";
function initSidebar() {
  let collapsed = false;
  try { collapsed = localStorage.getItem(sidebarCollapsedKey) === "1"; } catch(e) {}
  if (collapsed) document.body.classList.add("sidebar-collapsed");
}
initSidebar();

$("#sidebarToggle").onclick = () => {
  document.body.classList.toggle("sidebar-collapsed");
  try {
    localStorage.setItem(sidebarCollapsedKey, document.body.classList.contains("sidebar-collapsed") ? "1" : "0");
  } catch(e) {}
};

$("#menuBtn").onclick = () => {
  const sb = $("#sidebar");
  sb.classList.toggle("open");
};

// Close sidebar on mobile when clicking a nav link
document.addEventListener("click", (e) => {
  if (e.target.closest(".sidebar nav a") && window.innerWidth <= 900) {
    $("#sidebar").classList.remove("open");
  }
});

/* ================================================================
   State classes & chips
   ================================================================ */
const STATE_CLASS = {
  SCHEDULED:"c-idle", DUE:"c-info", RETAKE:"c-warn", HELD:"c-warn", DISPENSE_SENT:"c-info", EJECTED:"c-info",
  IN_TRANSIT:"c-info", DELIVERED:"c-info", COLLECTED:"c-ok", MISSED:"c-bad", WITHHELD:"c-idle",
  REVIEW_REQUIRED:"c-bad", FAULT:"c-bad", VALID:"c-ok", MISSING:"c-bad", INVALID:"c-bad", STALE:"c-warn", LOW_QUALITY:"c-warn", OUT_OF_RANGE:"c-bad",
  PROCEED:"c-ok", HOLD_FOR_REVIEW:"c-warn", CONFIRMED:"c-ok", NOT_DISPENSED:"c-bad", DISAGREEMENT_IR_ONLY:"c-bad",
  DISAGREEMENT_WEIGHT_ONLY:"c-bad", WEIGHT_OUT_OF_RANGE:"c-bad", TRAY_EMPTIED_NO_PRESENCE:"c-warn",
  LOW:"c-ok", MEDIUM:"c-warn", HIGH:"c-bad", UNKNOWN:"c-idle", WRONG_PATIENT:"c-bad",
};
const DRUG_LABEL = {GENERAL:"General", ANTIHYPERTENSIVE:"BP-lowering", BETA_BLOCKER:"Beta-blocker", SULFONYLUREA:"Diabetes (with food)"};
const ALERT_CLASS = (k) => (k === "REFILL_NEEDED" || k === "EXPIRY_SOON") ? "c-warn" : k.startsWith("TREND_") ? "c-info" : "c-bad";
const EXPECTED_API = "0.4.0";
const fmtHM = (t) => t ? new Date(t * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : "—";
function daysLeft(iso) { if (!iso) return null; const d = new Date(iso + "T00:00:00"); const t = new Date(); t.setHours(0, 0, 0, 0); return Math.round((d - t) / 864e5); }
function expiryChip(iso) {
  const n = daysLeft(iso);
  if (n == null) return `<span class="muted">no expiry set</span>`;
  if (n < 0) return `<span class="chip c-bad">EXPIRED ${esc(iso)}</span>`;
  if (n <= 14) return `<span class="chip c-warn">expires in ${n} d</span>`;
  return `<span class="muted">exp ${esc(iso)}</span>`;
}

/* ================================================================
   Old-server check: the most common setup problem is an old server window
   still running after an update. Then new pages call addresses it does not have.
   ================================================================ */
async function checkServerVersion() {
  let h = {};
  try { h = await api("/api/health"); } catch (e) { return; }
  if (h.version === EXPECTED_API) return;
  const b = document.createElement("div");
  b.className = "stale-banner";
  b.setAttribute("role", "alert");
  b.innerHTML = `<b>An older MedGate server is still running</b> (server ${esc(h.version || "0.3 or older")}, dashboard ${EXPECTED_API}). New features will not work.
    Close every black "MedGate server" window, then double-click <b>start.bat</b> again. The new start.bat also stops old servers by itself.`;
  $("#mainWrapper").prepend(b);
}
const ruleText = (rules, meal) => (rules || []).map((r) => `${r.field} ${r.op} ${r.value}`).join(", ") + (meal ? ", no meal" : "");

/* ================================================================
   Voice prompts: the device plays recorded clips; the dashboard can read the same
   text aloud with the browser's own voices (offline only if the OS has that language).
   ================================================================ */
const VOICE_LANG = {en: "en-IN", hi: "hi-IN", mni: "en-IN"};
function speak(text, lang) {
  try {
    if (!("speechSynthesis" in window)) return false;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = VOICE_LANG[lang] || "en-IN";
    const v = speechSynthesis.getVoices().find((x) => x.lang && x.lang.toLowerCase().startsWith(u.lang.slice(0, 2)));
    if (v) u.voice = v;
    speechSynthesis.speak(u);
    return true;
  } catch (e) { return false; }
}
function promptList(rows) {
  return rows.length ? `<div class="table-scroll"><table><tr><th>Time</th><th>Prompt</th><th>Lang</th><th>Text the patient hears</th><th></th></tr>
  ${rows.map((p) => `<tr><td>${fmtT(p.created_at)}</td><td class="mono">${esc(p.key)}</td><td>${esc(p.lang)}</td>
    <td>${esc(p.text)}${p.fallback ? ` <span class="chip c-warn">English fallback: record Manipuri clip</span>` : ""}<div class="muted mono">${esc(p.clip)}</div></td>
    <td><button data-say="${p.id}" aria-label="Play prompt">▶ Play</button></td></tr>`).join("")}</table></div>` : `<div class="empty">No voice prompts yet.</div>`;
}
function bindSay(el, rows) {
  el.querySelectorAll("[data-say]").forEach((b) => b.onclick = () => {
    const p = rows.find((x) => String(x.id) === b.dataset.say);
    if (p && !speak(p.text, p.lang)) toast("This browser has no voice for that language", "error");
  });
}
const chip = (s) => `<span class="chip ${STATE_CLASS[s] || "c-idle"}">${esc(s)}</span>`;
const kindChip = (k) => k === "SIMULATOR" ? `<span class="chip c-sim">SIMULATOR</span>` : `<span class="chip c-ok">HARDWARE</span>`;
const gr = (d) => Array.isArray(d.gate_reasons) ? d.gate_reasons : (d.gate_reasons?.reasons || []);
const lvlChip = (l) => l ? `<span class="chip ${l >= 3 ? "c-bad" : "c-warn"}">L${l}</span>` : `<span class="muted">—</span>`;

/* ================================================================
   Nav icons (inline SVG)
   ================================================================ */
const ICONS = {
  overview: `<svg viewBox="0 0 20 20" fill="none"><rect x="2" y="2" width="7" height="7" rx="2" stroke="currentColor" stroke-width="1.5"/><rect x="11" y="2" width="7" height="7" rx="2" stroke="currentColor" stroke-width="1.5"/><rect x="2" y="11" width="7" height="7" rx="2" stroke="currentColor" stroke-width="1.5"/><rect x="11" y="11" width="7" height="7" rx="2" stroke="currentColor" stroke-width="1.5"/></svg>`,
  doctor: `<svg viewBox="0 0 20 20" fill="none"><circle cx="10" cy="7" r="3.5" stroke="currentColor" stroke-width="1.5"/><path d="M3 17.5c0-3.5 3-5.5 7-5.5s7 2 7 5.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M13 7v2M12 8h2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>`,
  caretaker: `<svg viewBox="0 0 20 20" fill="none"><path d="M10 18s-6-4.35-6-8.5A4 4 0 0110 5.5a4 4 0 016 4c0 4.15-6 8.5-6 8.5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
  doses: `<svg viewBox="0 0 20 20" fill="none"><rect x="4" y="2" width="12" height="16" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M4 8h12" stroke="currentColor" stroke-width="1.5"/><circle cx="8" cy="13" r="1" fill="currentColor"/><circle cx="12" cy="13" r="1" fill="currentColor"/></svg>`,
  review: `<svg viewBox="0 0 20 20" fill="none"><path d="M6 10l3 3 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><rect x="2" y="2" width="16" height="16" rx="4" stroke="currentColor" stroke-width="1.5"/></svg>`,
  vitals: `<svg viewBox="0 0 20 20" fill="none"><path d="M2 10h3l2-5 3 10 2-5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  alerts: `<svg viewBox="0 0 20 20" fill="none"><path d="M10 2l8 14H2L10 2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M10 8v3M10 13.5v.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
  meds: `<svg viewBox="0 0 20 20" fill="none"><rect x="3" y="6" width="14" height="10" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M7 6V4a3 3 0 016 0v2" stroke="currentColor" stroke-width="1.5"/><path d="M10 10v3M8.5 11.5h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
  devices: `<svg viewBox="0 0 20 20" fill="none"><rect x="3" y="3" width="14" height="11" rx="2" stroke="currentColor" stroke-width="1.5"/><path d="M7 17h6M10 14v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`,
  audit: `<svg viewBox="0 0 20 20" fill="none"><path d="M10 2l6 3v5c0 3.5-2.5 6-6 8-3.5-2-6-4.5-6-8V5l6-3z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M7 10l2 2 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  sim: `<svg viewBox="0 0 20 20" fill="none"><rect x="3" y="3" width="14" height="14" rx="3" stroke="currentColor" stroke-width="1.5"/><path d="M8 7v6l5-3-5-3z" fill="currentColor"/></svg>`,
  patient: `<svg viewBox="0 0 20 20" fill="none"><rect x="4" y="2" width="12" height="16" rx="2.5" stroke="currentColor" stroke-width="1.5"/><path d="M8 15h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M7.5 8.5c0-1.4 1.1-2.5 2.5-2.5s2.5 1.1 2.5 2.5S11.4 11 10 11 7.5 9.9 7.5 8.5z" stroke="currentColor" stroke-width="1.3"/></svg>`,
};

/* ================================================================
   Lifecycle stepper
   ================================================================ */
const STEPS = [["DUE","Due"],["GATE","Health gate"],["DISPENSE_SENT","Dispense"],["EJECTED","Ejected"],["IN_TRANSIT","Conveyor"],["DELIVERED","Tray verified"],["COLLECTED","Collected"]];
function lifecycle(d) {
  const order = {SCHEDULED:-1, DUE:0, RETAKE:1, HELD:1, DISPENSE_SENT:2, EJECTED:3, IN_TRANSIT:4, DELIVERED:5, COLLECTED:6};
  let idx = order[d.state], mode = "cur";
  if (d.state === "RETAKE" || d.state === "HELD") mode = "hold";
  if (d.state === "WITHHELD") { idx = 1; mode = "stop"; }
  if (d.state === "REVIEW_REQUIRED") { idx = 5; mode = "stop"; }
  if (d.state === "FAULT") { idx = d.fault === "DISPENSE_TIMEOUT" ? 2 : d.fault === "NOTHING_DISPENSED" ? 5 : 4; mode = "stop"; }
  if (d.state === "MISSED") { idx = d.delivered_at ? 6 : 1; mode = "stop"; }
  if (d.state === "COLLECTED") mode = "done";
  return `<div class="life">${STEPS.map(([, label], i) => {
    let c = i < idx ? "done" : i === idx ? mode : "";
    return `<div class="step ${c}"><div class="dot"></div>${label}</div>`; }).join("")}</div>`;
}

/* ================================================================
   Router
   ================================================================ */
const VIEWS = {
  overview: ["Overview", viewOverview],
  patient: ["Patient screen", viewPatient],
  doctor: ["Doctor", viewDoctor],
  caretaker: ["Caretaker", viewCaretaker],
  doses: ["Doses", viewDoses],
  review: ["Review", viewReview],
  vitals: ["Vitals", viewVitals],
  alerts: ["Alerts", viewAlerts],
  meds: ["Medicines", viewMeds],
  devices: ["Devices", viewDevices],
  audit: ["Audit", viewAudit],
  sim: ["Simulator", viewSim],
};
let current = null, timer = null, reviewCount = 0, alertCount = 0;

function nav() {
  $("#nav").innerHTML = Object.entries(VIEWS).map(([k, [label]]) => {
    let badge = "";
    if (k === "review" && reviewCount) badge = `<span class="badge">${reviewCount}</span>`;
    if (k === "alerts" && alertCount) badge = `<span class="badge">${alertCount}</span>`;
    return `<a href="#${k}" class="${k === current ? "on" : ""}">
      <span class="nav-icon">${ICONS[k] || ""}</span>
      <span class="nav-label">${label}</span>${badge}</a>`;
  }).join("");
}

async function render() {
  const fn = VIEWS[current][1];
  try {
    await fn($("#view"));
    $("#connText").textContent = "live · " + new Date().toLocaleTimeString();
    $("#statusDot").className = "status-dot";
    $("#pageTitle").textContent = VIEWS[current][0];
  } catch (e) {
    $("#connText").textContent = "API error: " + e.message;
    $("#statusDot").className = "status-dot err";
  }
}

function route() {
  current = (location.hash.slice(1) || "overview");
  if (!VIEWS[current]) current = "overview";
  nav(); render();
  clearInterval(timer);
  timer = setInterval(() => { if (!document.activeElement.matches("input,select,textarea")) render(); }, 2000);
}
window.addEventListener("hashchange", route);

/* ================================================================
   Mini donut SVG
   ================================================================ */
function miniDonut(pct, size = 48, color = "var(--primary)") {
  const r = (size - 6) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (Math.min(Math.max(pct, 0), 100) / 100) * circ;
  return `<div class="donut-wrap"><svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="var(--border)" stroke-width="5"/>
    <circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="${color}" stroke-width="5"
      stroke-dasharray="${circ}" stroke-dashoffset="${offset}" stroke-linecap="round"
      transform="rotate(-90 ${size/2} ${size/2})" style="transition:stroke-dashoffset .5s ease"/>
  </svg><div class="donut-label">${pct != null ? pct + "%" : "—"}</div></div>`;
}

/* ================================================================
   Overview
   ================================================================ */
async function viewOverview(el) {
  const [o, doses] = await Promise.all([api("/api/overview"), api("/api/doses?limit=8")]);
  const s = o.stats; reviewCount = s.awaiting_review; nav();
  // Attention items
  let attentionItems = [];
  if (s.open_sos > 0) attentionItems.push(`<div class="attention-item sos-item"><span>🆘 <b>HELP requested</b> by a patient</span><a href="#alerts">Respond →</a></div>`);
  if (s.awaiting_review > 0) attentionItems.push(`<div class="attention-item"><span>🔍 <b>${s.awaiting_review}</b> dose(s) held for review</span><a href="#review">Go to Review →</a></div>`);
  if (s.open_alerts > 0) attentionItems.push(`<div class="attention-item"><span>⚠️ <b>${s.open_alerts}</b> open alert(s)</span><a href="#alerts">Go to Alerts →</a></div>`);
  if (!o.audit.ok) attentionItems.push(`<div class="attention-item"><span>🔒 Audit chain is <b>broken</b></span><a href="#audit">Go to Audit →</a></div>`);

  el.innerHTML = `
  <h1>Overview</h1><p class="sub">Closed loop: schedule → vital-sign check → dispense → dual-sensor verification → pickup → escalation.</p>
  <div class="grid g5">
    <div class="card kpi">
      <div class="kpi-icon" style="background:var(--primary-soft)">${miniDonut(s.adherence_pct, 40)}</div>
      <div class="l">Adherence</div>
      <div class="v">${s.adherence_pct == null ? "—" : s.adherence_pct + "%"}</div>
      <div class="h">${esc(s.adherence_basis)}</div>
    </div>
    <div class="card kpi">
      <div class="l">Doses recorded</div>
      <div class="v">${s.total_doses}</div>
      <div class="h">${Object.entries(s.by_state).map(([k,v]) => `${k.toLowerCase()} ${v}`).join(" · ") || "none yet"}</div>
    </div>
    <div class="card kpi">
      <div class="l">Awaiting review</div>
      <div class="v" style="color:${s.awaiting_review ? "var(--warning-text)" : "var(--success-text)"}">${s.awaiting_review}</div>
      <div class="h">held by gate or sensor disagreement</div>
    </div>
    <div class="card kpi">
      <div class="l">Open alerts</div>
      <div class="v" style="color:${s.open_alerts ? "var(--danger-text)" : "var(--success-text)"}">${s.open_alerts}</div>
      <div class="h">refill, storage, patient card, early warning</div>
    </div>
    <div class="card kpi">
      <div class="l">Audit chain</div>
      <div class="v" style="color:${o.audit.ok ? "var(--success-text)" : "var(--danger-text)"}">${o.audit.ok ? "Intact" : "BROKEN"}</div>
      <div class="h">${o.audit.records} hash-linked records</div>
    </div>
  </div>
  ${attentionItems.length ? `<div class="attention-panel section-gap"><h2 style="margin-bottom:var(--sp-2)">⚡ Needs attention now</h2>${attentionItems.join("")}</div>` : ""}
  <div class="grid g2 section-gap">
    <div class="card"><h2>Recent doses</h2>${doseTable(doses, true)}</div>
    <div class="card"><h2>Devices</h2>${deviceTable(o.devices)}</div>
  </div>`;
  bindDoseRows(el);
}

/* ================================================================
   Dose table & helpers
   ================================================================ */
function doseTable(doses, compact) {
  if (!doses.length) return `<div class="empty">No doses yet. Add a schedule under Medications, or run a scenario in Simulator.</div>`;
  return `<table><tr><th>#</th><th>Medication</th><th>Due</th><th>State</th>${compact ? "" : "<th>Lifecycle</th><th>Gate</th>"}<th>Esc.</th></tr>
  ${doses.map((d) => `<tr class="click" data-dose="${d.id}"><td class="mono">${d.id}</td>
    <td>${esc(d.medication)} <span class="muted">· C${d.compartment}</span>${d.device_kind === "SIMULATOR" ? ` <span class="chip c-sim">SIM</span>` : ""}</td>
    <td>${fmtDT(d.due_at)}</td><td>${chip(d.state)}</td>
    ${compact ? "" : `<td style="min-width:330px">${lifecycle(d)}</td><td>${d.gate_decision ? chip(d.gate_decision) : "—"}</td>`}
    <td>${lvlChip(d.escalation_level)}</td></tr>`).join("")}</table>`;
}
function bindDoseRows(el) { el.querySelectorAll("[data-dose]").forEach((tr) => tr.onclick = () => openDose(+tr.dataset.dose)); }

/* ================================================================
   Doses page
   ================================================================ */
async function viewDoses(el) {
  const doses = await api("/api/doses?limit=100");
  el.innerHTML = `<h1>Doses</h1><p class="sub">Every dose and the point in the loop it reached. Click a row for its full audit trail.</p>
    <div class="card">${doseTable(doses, false)}</div>`;
  bindDoseRows(el);
}

/* ================================================================
   Review page
   ================================================================ */
async function viewReview(el) {
  const [held, rr, fault] = await Promise.all([api("/api/doses?state=HELD"), api("/api/doses?state=REVIEW_REQUIRED"), api("/api/doses?state=FAULT&limit=20")]);
  reviewCount = held.length + rr.length; nav();
  let name = "";
  try { name = sessionStorage.getItem("reviewer") || ""; } catch(e) {}
  el.innerHTML = `<h1>Human review</h1>
  <p class="sub">The health gate suppressed these doses (SpO₂ AND BP must both pass). They stay withheld unless a named reviewer overrides. Every decision is logged.</p>
  <div class="card" style="margin-bottom:var(--sp-4)"><div class="row"><label style="margin:0;font-weight:600;color:var(--ink)">Reviewer name</label><input id="rv" value="${esc(name)}" placeholder="e.g. Dr. Sharma" style="flex:1;max-width:300px"></div></div>
  <div class="card"><h2>Held by health gate (${held.length})</h2>${held.length ? held.map((d) => `
    <div class="scn"><div><b>Dose ${d.id}</b> · ${esc(d.medication)} ${chip(d.state)} ${lvlChip(d.escalation_level)}
      <ul class="reasons">${gr(d).map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>
      <div class="row"><button class="primary" data-act="approve" data-id="${d.id}">Approve dispense</button><button class="danger" data-act="withhold" data-id="${d.id}">Withhold</button></div></div>`).join("") : `<div class="empty">Nothing held.</div>`}
  </div>
  <div class="card section-gap"><h2>Sensor disagreements & faults</h2>${[...rr, ...fault].length ? [...rr, ...fault].map((d) => `
    <div class="scn"><div><b>Dose ${d.id}</b> · ${esc(d.medication)} ${chip(d.state)} <span class="mono">${esc(d.delivery_verdict || d.fault || "")}</span>
      <div class="muted">IR beam: ${d.ir_beam == null ? "—" : d.ir_beam ? "broken" : "clear"} · tray Δ: ${d.tray_delta_g ?? "—"} g ${d.reviewer ? `· acknowledged by ${esc(d.reviewer)}` : ""}</div>
      <div class="muted">Not counted as delivered. No automatic re-dispense; check the tray and compartment by hand.</div></div>
      ${d.reviewer ? "" : `<button data-act="ack" data-id="${d.id}">Acknowledge</button>`}</div>`).join("") : `<div class="empty">None.</div>`}
  </div><div id="rverr" class="err"></div>`;
  el.querySelectorAll("[data-act]").forEach((b) => b.onclick = async () => {
    const reviewer = $("#rv").value.trim();
    if (!reviewer) { $("#rverr").textContent = "Enter a reviewer name first: every decision is attributed."; return; }
    try { sessionStorage.setItem("reviewer", reviewer); } catch(e) {}
    b.disabled = true;
    const origText = b.textContent;
    b.innerHTML = `<span class="spinner"></span> ${esc(origText)}`;
    try {
      if (b.dataset.act === "ack") await api(`/api/doses/${b.dataset.id}/acknowledge`, {method: "POST", body: {reviewer}});
      else await api(`/api/doses/${b.dataset.id}/review`, {method: "POST", body: {decision: b.dataset.act, reviewer}});
      toast(b.dataset.act === "ack" ? "Acknowledged" : `Dose ${b.dataset.act}d`, "success");
      render();
    } catch (e) { $("#rverr").textContent = e.message; b.disabled = false; b.textContent = origText; }
  });
}

/* ================================================================
   Vitals page
   ================================================================ */
async function viewVitals(el) {
  const v = await api("/api/vitals?limit=60");
  el.innerHTML = `<h1>Vital-sign readings</h1><p class="sub">Each reading is classified before any gate decision. Missing, implausible, stale or poor-contact readings are never treated as normal.</p>
  <div class="card">${v.length ? `<table><tr><th>Received</th><th>Source</th><th>HR</th><th>SpO₂</th><th>BP</th><th>Quality</th><th>Status</th><th>Why</th></tr>
  ${v.map((r) => `<tr><td>${fmtT(r.received_at)}</td><td>${kindChip(r.source)}</td><td>${r.hr ?? "—"}</td><td>${r.spo2 ?? "—"}</td>
    <td>${r.bp_sys ? `${r.bp_sys}/${r.bp_dia}` : "—"}</td><td>${r.quality ?? "—"}</td><td>${chip(r.status)}</td>
    <td class="muted">${(r.reasons || []).map(esc).join("; ")}</td></tr>`).join("")}</table>` : `<div class="empty">No readings yet.</div>`}</div>`;
}

/* ================================================================
   Alerts page
   ================================================================ */
async function viewAlerts(el) {
  const [e, al] = await Promise.all([api("/api/escalations?limit=80"), api("/api/alerts")]);
  alertCount = al.open.length; nav();
  let who = "";
  try { who = sessionStorage.getItem("reviewer") || ""; } catch(e2) {}
  el.innerHTML = `<h1>Alerts</h1><p class="sub">Refill, medicine-storage, patient-card and early-warning trend alerts (storage limits: ${al.limits.max_temp_c} °C, ${al.limits.max_humidity} % humidity).</p>
  <div class="card" style="margin-bottom:var(--sp-4)">${al.open.length ? al.open.map((a) => `<div class="scn"><div><span class="chip ${ALERT_CLASS(a.kind)}">${esc(a.kind.replaceAll("_", " "))}</span> ${esc(a.message)}
      <div class="muted">since ${fmtDT(a.created_at)}</div></div><div class="row"><input data-who="${a.id}" value="${esc(who)}" placeholder="Your name" style="width:130px"><button data-resolve="${a.id}">Resolve</button></div></div>`).join("")
    : `<div class="empty">No open alerts.</div>`}<div class="err" id="alerr"></div></div>
  <h1 class="section-gap">Escalations</h1><p class="sub">L1 audio prompt + OLED → L2 buzzer → L3 caregiver SMS → L4 caregiver call. Gate failures and sensor faults start at L3.</p>
  <div class="card">${e.length ? `<table><tr><th>Time</th><th>Dose</th><th>Level</th><th>Channel</th><th>Delivery</th><th>Reason</th></tr>
  ${e.map((x) => `<tr class="click" data-dose="${x.dose_id}"><td>${fmtT(x.created_at)}</td><td class="mono">${x.dose_id} · ${esc(x.medication)}</td><td>${lvlChip(x.level)}</td>
    <td class="mono">${esc(x.channel)}</td><td class="muted">${esc(x.delivery)}</td><td>${esc(x.reason)}</td></tr>`).join("")}</table>` : `<div class="empty">No escalations.</div>`}</div>`;
  bindDoseRows(el);
  el.querySelectorAll("[data-resolve]").forEach((b) => b.onclick = async () => {
    const by = el.querySelector(`[data-who="${b.dataset.resolve}"]`).value.trim();
    if (!by) { $("#alerr").textContent = "Enter your name: every resolution is attributed."; return; }
    try { sessionStorage.setItem("reviewer", by); } catch(e2) {}
    b.disabled = true;
    b.innerHTML = `<span class="spinner"></span> Resolving`;
    try { await api(`/api/alerts/${b.dataset.resolve}/resolve`, {method: "POST", body: {by}}); toast("Alert resolved", "success"); render(); } catch (err) { $("#alerr").textContent = err.message; b.disabled = false; b.textContent = "Resolve"; }
  });
}

/* ================================================================
   Medicines page
   ================================================================ */
async function viewMeds(el) {
  const [meds, sch, devs, rules, dc] = await Promise.all([api("/api/medications"), api("/api/schedules"), api("/api/devices"), api("/api/rules/demo"), api("/api/drug-classes")]);
  const ALL_DAYS = ["MON","TUE","WED","THU","FRI","SAT","SUN"];

  function stockBar(stock, threshold) {
    const maxStock = Math.max(stock, threshold * 3, 30);
    const pct = Math.min(100, (stock / maxStock) * 100);
    const cls = stock <= threshold ? "low" : pct < 40 ? "mid" : "";
    return `<div class="stock-bar-bg"><div class="stock-bar-fill ${cls}" style="width:${pct}%"></div></div>`;
  }

  el.innerHTML = `<h1>Medications & schedules</h1><p class="sub">One medication per compartment (1–6). Tablet weight lets the tray load cell tell 0, 1 and 2 tablets apart.</p>
  <div class="grid g2">
  <div class="card"><h2>Compartments</h2>${meds.length ? `<div style="overflow-x:auto"><table><tr><th>C</th><th>Medication</th><th>Device</th><th>Weight</th><th>Stock</th><th>Expiry</th><th>Drug-specific gate: held if</th><th></th></tr>
    ${meds.map((m) => `<tr><td class="mono">${m.compartment}</td><td>${esc(m.name)}<div class="muted">${esc(m.notes)}</div></td><td class="mono">${esc(m.device_id)}</td>
    <td>${m.unit_weight_g} ± ${m.weight_tol_g} g</td>
    <td><b style="color:${m.stock <= m.refill_threshold ? "var(--danger-text)" : "inherit"}">${m.stock}</b> <span class="muted">${m.days_of_supply != null ? `≈${m.days_of_supply} d` : ""}</span>
      ${stockBar(m.stock, m.refill_threshold)}
      <div class="row" style="margin-top:var(--sp-1)"><input type="number" min="0" max="500" value="30" data-refill-n="${m.id}" style="width:64px" aria-label="Tablets after refill"><button data-refill="${m.id}">Refill</button></div></td>
      <td>${expiryChip(m.expiry_date)}<div class="row" style="margin-top:var(--sp-1)"><input type="date" value="${esc(m.expiry_date || "")}" data-exp-v="${m.id}" style="width:140px" aria-label="Expiry date"><button data-exp="${m.id}">Save</button></div></td><td><span class="chip ${m.drug_class === "GENERAL" ? "c-idle" : "c-info"}">${esc(DRUG_LABEL[m.drug_class] || m.drug_class)}</span>
      <div class="muted">${esc(ruleText(m.rules_json, m.requires_meal)) || "none"}</div>
      <div class="muted">${m.min_interval_h ? `min gap ${m.min_interval_h} h (double-dose guard)` : "no double-dose guard"} · late window ${m.late_window_min} min</div></td>
    <td><button data-now="${m.id}">Dose now</button></td></tr>`).join("")}</table></div>` : `<div class="empty">No medications.</div>`}
    <div class="muted" style="margin-top:var(--sp-2)">Default rules: ${esc(rules.warning)}</div></div>
  <div class="card form"><h2>Add medication</h2>
    <label>Device</label><select id="mdev">${devs.map((d) => `<option>${esc(d.id)}</option>`).join("")}</select>
    <label>Name</label><input id="mname" placeholder="Demo tablet C (dummy)">
    <div class="row"><div><label>Compartment</label><input id="mcomp" type="number" min="1" max="6" value="3" style="width:90px"></div>
    <div><label>Tablet g</label><input id="mw" type="number" step="0.05" value="0.6" style="width:90px"></div>
    <div><label>± g</label><input id="mt" type="number" step="0.05" value="0.15" style="width:90px"></div>
    <div><label>Tablets loaded</label><input id="mstock" type="number" min="0" max="500" value="30" style="width:90px"></div></div>
    <label>Expiry date of the strip</label><input id="mexp" type="date" style="width:170px">
    <label>Drug class (sets its own gate rules)</label><select id="mclass" style="width:100%;max-width:100%">${Object.entries(dc.classes).map(([k, c]) => `<option value="${esc(k)}">${esc(c.label)}: held if ${esc(ruleText(c.rules, c.requires_meal))}</option>`).join("")}</select>
    <div class="row"><div><label>Late-dose window (min)</label><input id="mlate" type="number" min="5" max="240" value="50" style="width:110px"></div>
    <div><label>Min gap (h, blank = class default)</label><input id="mgap" type="number" min="0" max="72" step="0.5" placeholder="default" style="width:110px"></div></div>
    <div class="muted" style="margin-top:var(--sp-2)">${esc(dc.warning)}</div>
    <div class="row" style="margin-top:10px"><button class="primary" id="madd">Add</button></div><div class="err" id="merr"></div></div>
  </div>
  <div class="grid g2 section-gap">
  <div class="card"><h2>Daily schedules</h2>${sch.length ? `<table><tr><th>Time</th><th>Medication</th><th>Days</th><th>Session</th><th>Food</th><th>Gate tightening</th><th>Enabled</th></tr>
    ${sch.map((s) => `<tr><td class="mono">${esc(s.time_hhmm)}</td><td>${esc(s.medication)} <span class="muted">· Box ${s.compartment}</span></td>
    <td><div class="day-chips">${ALL_DAYS.map(d => `<span class="day-chip ${s.days.includes(d) ? "" : "off"}">${d.slice(0,2)}</span>`).join("")}</div></td>
    <td>${esc(s.session.toLowerCase())}</td>
      <td>${esc(s.food.replace("_", " ").toLowerCase())}</td><td>${s.tighten_steps ? `<span class="chip c-warn">+${s.tighten_steps} step</span>` : `<span class="muted">none</span>`}</td>
      <td><button class="toggle-switch ${s.active ? "on" : ""}" data-toggle="${s.id}" data-active="${s.active}" aria-label="${s.active ? "Disable" : "Enable"} schedule"></button></td></tr>`).join("")}</table>` : `<div class="empty">No schedules.</div>`}
    <div class="muted" style="margin-top:var(--sp-2)">A missed pickup tightens the next same-session gate (SpO₂ +1 %, BP −5 mmHg per step, max 3). A successful pickup resets it.</div></div>
  <div class="card form"><h2>Add schedule</h2>
    <label>Medication</label><select id="smed">${meds.map((m) => `<option value="${m.id}">C${m.compartment} · ${esc(m.name)}</option>`).join("")}</select>
    <label>Time (daily)</label><input id="stime" type="time" value="08:00">
    <label>Days</label><div class="row">${ALL_DAYS.map((d) => `<label style="margin:0;color:var(--ink)"><input type="checkbox" class="sday" value="${d}" checked> ${d.slice(0, 2)}</label>`).join("")}</div>
    <label>Food condition</label><select id="sfood"><option value="ANY">Any time</option><option value="BEFORE_FOOD">Before food</option><option value="AFTER_FOOD">After food</option></select>
    <div class="row" style="margin-top:10px"><button class="primary" id="sadd">Add schedule</button></div><div class="err" id="serr"></div></div>
  </div>`;
  el.querySelectorAll("[data-refill]").forEach((b) => b.onclick = async () => { try { await api(`/api/medications/${b.dataset.refill}/refill`, {method: "POST", body: {count: +el.querySelector(`[data-refill-n="${b.dataset.refill}"]`).value}}); toast("Refilled", "success"); render(); } catch(e2) { toast(e2.message, "error"); } });
  el.querySelectorAll("[data-exp]").forEach((b) => b.onclick = async () => {
    const v = el.querySelector(`[data-exp-v="${b.dataset.exp}"]`).value;
    try { await api(`/api/medications/${b.dataset.exp}/expiry`, {method: "POST", body: {expiry_date: v || null}}); toast(v ? "Expiry saved" : "Expiry cleared", "success"); render(); }
    catch (e2) { toast(e2.message, "error"); }
  });
  el.querySelectorAll("[data-toggle]").forEach((b) => b.onclick = async () => { await api(`/api/schedules/${b.dataset.toggle}/active`, {method: "POST", body: {active: b.dataset.active !== "1"}}); render(); });
  el.querySelectorAll("[data-now]").forEach((b) => b.onclick = async () => { await api("/api/doses", {method: "POST", body: {medication_id: +b.dataset.now}}); location.hash = "doses"; });
  $("#madd").onclick = async () => {
    try { await api("/api/medications", {method: "POST", body: {device_id: $("#mdev").value, name: $("#mname").value, compartment: +$("#mcomp").value, unit_weight_g: +$("#mw").value, weight_tol_g: +$("#mt").value, stock: +$("#mstock").value,
      drug_class: $("#mclass").value, late_window_min: +$("#mlate").value, ...($("#mexp").value ? {expiry_date: $("#mexp").value} : {}), ...($("#mgap").value !== "" ? {min_interval_h: +$("#mgap").value} : {})}}); toast("Medication added", "success"); render(); }
    catch (e2) { $("#merr").textContent = e2.message; }
  };
  $("#sadd").onclick = async () => {
    try { await api("/api/schedules", {method: "POST", body: {medication_id: +$("#smed").value, time_hhmm: $("#stime").value, food: $("#sfood").value, days: [...el.querySelectorAll(".sday:checked")].map((x) => x.value)}}); toast("Schedule added", "success"); render(); }
    catch (e2) { $("#serr").textContent = e2.message; }
  };
}

/* ================================================================
   Device table & Devices page
   ================================================================ */
function deviceTable(devs) {
  return devs.length ? `<table><tr><th>Device</th><th>Patient</th><th>Type</th><th>Last seen</th><th>Firmware</th></tr>
  ${devs.map((d) => `<tr><td class="mono">${esc(d.id)}<div class="muted">${esc(d.name)}</div></td>
    <td>${esc(d.patient_name || "—")}<div class="muted">${d.patient_tag ? "card required" : "no card check"} · voice ${esc(d.language || "en")}</div></td><td>${kindChip(d.kind)}</td>
    <td>${d.online ? `<span class="chip c-ok">online</span>` : `<span class="chip c-idle">offline</span>`} <span class="muted">${ago(d.last_seen)}</span></td>
    <td class="mono">${esc(d.fw || "—")}${d.rssi ? ` · ${d.rssi} dBm` : ""}</td></tr>`).join("")}</table>` : `<div class="empty">No devices.</div>`;
}

async function viewDevices(el) {
  const [devs, ev, envr, pr] = await Promise.all([api("/api/devices"), api("/api/events?limit=60"), api("/api/env?limit=10"), api("/api/prompts?limit=15")]);
  el.innerHTML = `<h1>Devices</h1><p class="sub">SIMULATOR devices produce synthetic events. HARDWARE devices are ESP32 dispensers authenticated with a device key.</p>
  <div class="card">${deviceTable(devs)}</div>
  <div class="card section-gap"><h2>Medicine storage (AHT20)</h2>${envr.length ? `<table><tr><th>Received</th><th>Device</th><th>Temp</th><th>Humidity</th><th>Status</th></tr>
    ${envr.map((r) => `<tr><td>${fmtT(r.received_at)}</td><td class="mono">${esc(r.device_id)}</td><td>${r.temp_c ?? "—"} °C</td><td>${r.humidity ?? "—"} %</td>
    <td><span class="chip ${r.status === "OK" ? "c-ok" : "c-bad"}">${esc(r.status)}</span></td></tr>`).join("")}</table>` : `<div class="empty">No storage readings yet.</div>`}</div>
  <div class="grid g2 section-gap">
  <div class="card form"><h2>Patient, card &amp; voice language</h2>
    <label>Device</label><select id="pdev">${devs.map((d) => `<option>${esc(d.id)}</option>`).join("")}</select>
    <label>Patient name</label><input id="pname" placeholder="e.g. Ima Tombi">
    <label>Patient card UID (RFID). Leave blank to switch the card check off</label><input id="ptag" placeholder="MG-CARD-0001">
    <label>Voice prompt language</label><select id="plang">${Object.entries(pr.languages).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("")}</select>
    <div class="row" style="margin-top:10px"><button class="primary" id="psave">Save patient</button></div><div class="err" id="perr"></div>
    <div class="muted" style="margin-top:var(--sp-2)">With a card registered, readings without that card are not used and a wrong card raises an alert. ${esc(pr.note)}</div></div>
  <div class="card"><h2>Voice prompts sent to the device</h2>${promptList(pr.recent)}</div></div>
  <div class="card form section-gap"><h2>Register hardware dispenser</h2><div class="row">
    <input id="did" placeholder="ESP32-001"><input id="dname" placeholder="Name"><button class="primary" id="dadd">Register</button></div><div class="err" id="derr"></div></div>
  <div class="card section-gap"><h2>Raw device events (idempotent by device + sequence)</h2>${ev.length ? `<table><tr><th>Received</th><th>Device</th><th>Seq</th><th>Type</th><th>Data</th><th>Result</th></tr>
    ${ev.map((e2) => `<tr><td>${fmtT(e2.received_at)}</td><td class="mono">${esc(e2.device_id)}</td><td class="mono">${e2.seq}</td><td class="mono">${esc(e2.type)}</td>
    <td class="mono muted">${esc(JSON.stringify(e2.payload))}</td><td class="mono">${esc(e2.result)}</td></tr>`).join("")}</table>` : `<div class="empty">No events.</div>`}</div>`;
  bindSay(el, pr.recent);
  $("#psave").onclick = async () => {
    try { await api(`/api/devices/${encodeURIComponent($("#pdev").value)}/patient`, {method: "POST", body: {name: $("#pname").value, tag: $("#ptag").value || null, language: $("#plang").value}}); toast("Patient saved", "success"); render(); }
    catch (e2) { $("#perr").textContent = e2.message; }
  };
  $("#dadd").onclick = async () => {
    try { await api("/api/devices", {method: "POST", body: {id: $("#did").value, name: $("#dname").value, kind: "HARDWARE"}}); toast("Device registered", "success"); render(); }
    catch (e2) { $("#derr").textContent = e2.message; }
  };
}

/* ================================================================
   Audit page
   ================================================================ */
async function viewAudit(el) {
  const [a, v] = await Promise.all([api("/api/audit?limit=150"), api("/api/audit/verify")]);
  el.innerHTML = `<h1>Audit log</h1><p class="sub">Append-only. Each record stores the SHA-256 of the previous one, so editing any past record breaks the chain.</p>
  <div class="card" style="margin-bottom:var(--sp-4)">${v.ok ? `<span class="chip c-ok">Chain intact</span> ${v.records} records · head <span class="hash">${esc(v.head.slice(0, 16))}…</span>`
    : `<span class="chip c-bad">Chain broken at record ${v.broken_at_id}</span>`}</div>
  <div class="card"><table><tr><th>#</th><th>Time</th><th>Actor</th><th>Action</th><th>Dose</th><th>Detail</th><th>Hash</th></tr>
  ${a.map((r) => `<tr><td class="mono">${r.id}</td><td>${fmtT(r.ts)}</td><td class="mono">${esc(r.actor)}</td><td class="mono">${esc(r.action)}</td>
    <td class="mono">${r.dose_id ?? ""}</td><td class="muted" style="max-width:360px">${esc(r.detail?.reason || JSON.stringify(r.detail))}</td><td class="hash">${esc(r.hash.slice(0, 10))}</td></tr>`).join("")}</table></div>`;
}

/* ================================================================
   Simulator page
   ================================================================ */
async function viewSim(el) {
  const s = await api("/api/sim");
  const st = s.status || {};
  el.innerHTML = `<h1>Simulator <span class="chip c-sim">SYNTHETIC DATA</span></h1>
  <p class="sub">Device ${esc(s.device_id)} is a software stand-in for the ESP32 dispenser. It sends the same event protocol the firmware sends. Its records are labelled SIMULATOR everywhere.</p>
  <div class="card demo-hero" style="margin-bottom:var(--sp-4)"><div><h2 style="margin:0">One-click demo story</h2><div class="muted">Runs 8 scenarios in order with narration (about 1–2 minutes). Then open Review, Alerts, Doctor → report and Patient screen.</div></div>
    <button class="primary" data-run="full_demo" ${st.running ? "disabled" : ""}>▶ Run full demo</button></div>
  <div class="grid g2"><div class="card"><h2>Scenarios</h2><div class="muted" style="margin-bottom:var(--sp-2)">The last 9 show the drug-specific gate, patient card, double-dose protection and early warnings.</div>${Object.entries(s.scenarios).map(([k, d]) => `
    <div class="scn"><div><b class="mono">${esc(k)}</b><div class="muted">${esc(d)}</div></div><button class="primary" data-run="${esc(k)}" ${st.running ? "disabled" : ""}>Run</button></div>`).join("")}</div>
  <div class="card"><h2>${st.name ? `Scenario: ${esc(st.name)} ${st.running ? `<span class="chip c-info">running</span>` : `<span class="chip c-ok">finished</span>`}` : "Device log"}</h2>
    ${st.dose_id ? `<p><button data-dose="${st.dose_id}">Open dose ${st.dose_id}</button></p>` : ""}
    <div class="log">${esc((st.log || []).join("\n")) || "Run a scenario to see device activity."}</div><div class="err" id="simerr"></div></div></div>`;
  el.querySelectorAll("[data-run]").forEach((b) => b.onclick = async () => {
    b.disabled = true;
    b.innerHTML = `<span class="spinner"></span> Running`;
    try { await api(`/api/sim/run/${b.dataset.run}`, {method: "POST"}); toast(`Scenario ${b.dataset.run} started`, "success"); render(); } catch (e2) { $("#simerr").textContent = e2.message; b.disabled = false; b.textContent = "Run"; }
  });
  bindDoseRows(el);
}

/* ================================================================
   Dose drawer (slide-in panel)
   ================================================================ */
async function openDose(id) {
  const d = await api(`/api/doses/${id}`);
  const x = d.dose;
  const dr = $("#drawer");
  const ov = $("#drawerOverlay");
  dr.hidden = false;
  ov.hidden = false;
  requestAnimationFrame(() => { dr.classList.add("show"); ov.classList.add("show"); });

  dr.innerHTML = `<button class="close-btn" id="dx" aria-label="Close drawer">
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none"><path d="M4 4l10 10M14 4L4 14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
  </button>
  <h1>Dose ${x.id}</h1><p class="sub">${esc(x.medication)} · compartment ${x.compartment} · ${kindChip(x.device_kind)}</p>
  <div class="card">${chip(x.state)} ${lvlChip(x.escalation_level)} ${x.fault ? `<span class="chip c-bad">${esc(x.fault)}</span>` : ""}${lifecycle(x)}
    <table style="margin-top:var(--sp-2)">
      <tr><td class="muted">Due</td><td>${fmtDT(x.due_at)}</td></tr>
      <tr><td class="muted">Gate</td><td>${x.gate_decision ? chip(x.gate_decision) : "—"} after ${x.gate_attempts} reading(s)<ul class="reasons">${gr(x).map((r) => `<li>${esc(r)}</li>`).join("")}</ul></td></tr>
      <tr><td class="muted">Readings</td><td>${x.gate_reasons?.readings ? `SpO₂ ${x.gate_reasons.readings.spo2 ?? "—"} % · BP ${x.gate_reasons.readings.bp_sys ?? "—"}/${x.gate_reasons.readings.bp_dia ?? "—"} · HR ${x.gate_reasons.readings.hr ?? "—"}` : "—"}
        ${x.gate_reasons?.rules ? `<div class="muted">fails if ${x.gate_reasons.rules.map((r) => esc(`${r.field} ${r.op} ${r.value}`)).join(" or ")}${x.tighten_steps ? ` (tightened ${x.tighten_steps})` : ""}</div>` : ""}</td></tr>
      ${x.gate_reasons?.drug_class ? `<tr><td class="muted">Medicine gate</td><td>${esc(DRUG_LABEL[x.gate_reasons.drug_class] || x.gate_reasons.drug_class)}${x.gate_reasons.requires_meal ? ` · meal ${x.gate_reasons.meal_taken === true ? "confirmed" : x.gate_reasons.meal_taken === false ? "NOT eaten" : "not confirmed"}` : ""} · patient card ${x.gate_reasons.patient_verified ? "verified" : "not checked/failed"}</td></tr>` : ""}
      <tr><td class="muted">Session</td><td>${esc(x.session?.toLowerCase())} · ${esc((x.food || "").replace("_", " ").toLowerCase())}</td></tr>
      ${x.plan_json ? (() => { const p = JSON.parse(x.plan_json); return `<tr><td class="muted">Compliance model</td><td>${chip(p.model)} <span class="muted">trained on synthetic data</span> predicted pickup ${p.predicted_latency_min} min · remind ${p.reminder_lead_min} min early · volume ${p.alert_volume_pct}% · L1 window ${p.escalation_window_min} min · gate failures ${Math.round(p.gate_fail_rate * 100)}%</td></tr>`; })() : ""}
      <tr><td class="muted">IR beam</td><td>${x.ir_beam == null ? "—" : x.ir_beam ? "broken (tablet passed chute)" : "not broken"}</td></tr>
      <tr><td class="muted">Tray weight Δ</td><td>${x.tray_delta_g ?? "—"} g ${x.delivery_verdict ? chip(x.delivery_verdict) : ""}</td></tr>
      <tr><td class="muted">Pickup</td><td>${x.collection_verdict ? `${chip(x.collection_verdict)} <span class="muted">tray emptied; ingestion not verified</span>` : "—"}</td></tr>
      ${x.reviewer ? `<tr><td class="muted">Reviewer</td><td>${esc(x.reviewer)} ${esc(x.review_note || "")}</td></tr>` : ""}
    </table></div>
  ${d.prompts?.length ? `<h2 style="margin-top:var(--sp-5)">Voice prompts</h2><div class="card">${promptList(d.prompts)}</div>` : ""}
  <h2 style="margin-top:var(--sp-5)">Audit trail</h2>
  <ul class="tl">${d.audit.map((a) => `<li><div class="t">${fmtT(a.ts)} · ${esc(a.actor)}</div><b class="mono">${esc(a.action)}</b>
    <div class="muted">${esc(a.detail?.reason || (a.detail?.level ? `L${a.detail.level} ${a.detail.channel}: ${a.detail.reason}` : ""))}</div></li>`).join("")}</ul>`;
  $("#dx").onclick = closeDrawer;
  bindSay(dr, d.prompts || []);
}

function closeDrawer() {
  const dr = $("#drawer");
  const ov = $("#drawerOverlay");
  dr.classList.remove("show");
  ov.classList.remove("show");
  setTimeout(() => { dr.hidden = true; ov.hidden = true; }, 300);
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
$("#drawerOverlay").onclick = closeDrawer;

/* ================================================================
   Doctor portal
   ================================================================ */
async function viewDoctor(el) {
  const [p, devs, al] = await Promise.all([api("/api/portal/doctor"), api("/api/devices"), api("/api/alerts")]);
  const trends = al.open.filter((a) => a.kind.startsWith("TREND_"));
  // Build SVG bar chart for daily trend
  let chartSvg = "";
  if (p.daily_trend.length) {
    const maxVal = Math.max(...p.daily_trend.map(t => Math.max(t.gate_failure_rate || 0, t.non_adherence_rate || 0)), 0.01);
    const w = 400, h = 120, barW = Math.min(30, (w - 40) / (p.daily_trend.length * 2.5));
    chartSvg = `<svg viewBox="0 0 ${w} ${h + 30}" width="100%" style="max-width:${w}px;display:block;margin:var(--sp-3) 0">`;
    p.daily_trend.forEach((t, i) => {
      const x = 30 + i * (barW * 2.5);
      const gfH = ((t.gate_failure_rate || 0) / maxVal) * h;
      const naH = ((t.non_adherence_rate || 0) / maxVal) * h;
      chartSvg += `<rect x="${x}" y="${h - gfH}" width="${barW}" height="${gfH}" rx="2" fill="var(--warning)" opacity="0.8"/>`;
      chartSvg += `<rect x="${x + barW + 2}" y="${h - naH}" width="${barW}" height="${naH}" rx="2" fill="var(--danger)" opacity="0.8"/>`;
      chartSvg += `<text x="${x + barW}" y="${h + 14}" text-anchor="middle" fill="var(--ink-tertiary)" font-size="9" font-family="var(--font-sans)">${esc(t.day.slice(5))}</text>`;
    });
    chartSvg += `<line x1="28" y1="0" x2="28" y2="${h}" stroke="var(--border)" stroke-width="1"/>`;
    chartSvg += `<line x1="28" y1="${h}" x2="${w}" y2="${h}" stroke="var(--border)" stroke-width="1"/>`;
    chartSvg += `</svg>`;
    chartSvg += `<div class="row" style="gap:var(--sp-4);margin-bottom:var(--sp-2)"><span style="font-size:11px;color:var(--warning)">■ Gate failure rate</span><span style="font-size:11px;color:var(--danger)">■ Non-adherence rate</span></div>`;
  }

  el.innerHTML = `<h1>Doctor portal</h1><p class="sub">Health-gate decision for every dispensing event (readings, outcome, thresholds applied), and how gate failures track missed doses.</p>
  <div class="grid g2" style="margin-bottom:var(--sp-4)">
    <div class="card"><h2>Printable 30-day report</h2>${devs.map((d) => `<div class="scn"><div><b>${esc(d.patient_name || d.id)}</b> <span class="muted mono">${esc(d.id)}</span></div>
      <a class="btn primary" href="/report/${encodeURIComponent(d.id)}?days=30" target="_blank" rel="noopener" style="text-decoration:none">Open report</a></div>`).join("")}
      <div class="muted">Adherence, held doses, overrides, vitals charts and early warnings. Use the browser's Print to save it as a PDF for the clinic visit.</div></div>
    <div class="card"><h2>Early warnings</h2>${trends.length ? trends.map((t) => `<div class="scn"><div><span class="chip c-info">${esc(t.kind.replaceAll("_", " "))}</span> ${esc(t.message)}<div class="muted">since ${fmtDT(t.created_at)}</div></div></div>`).join("")
      : `<div class="empty">No sustained SpO₂ or BP drift in recent readings.</div>`}</div>
  </div>
  <div class="grid g2">
    <div class="card"><h2>Gate failures vs missed doses, by day</h2>${p.daily_trend.length ? `${chartSvg}
      <table><tr><th>Day</th><th>Gate failure rate</th><th>Non-adherence rate</th><th>Gated</th><th>Outcomes</th></tr>
      ${p.daily_trend.map((t) => `<tr><td class="mono">${esc(t.day)}</td><td>${t.gate_failure_rate == null ? "—" : Math.round(t.gate_failure_rate * 100) + "%"}</td>
      <td>${t.non_adherence_rate == null ? "—" : Math.round(t.non_adherence_rate * 100) + "%"}</td><td>${t.gated}</td><td>${t.outcomes}</td></tr>`).join("")}</table>
      <div class="muted" style="margin-top:var(--sp-2)">A correlation needs many days of data. This table shows the inputs; it draws no conclusion.</div>` : `<div class="empty">No data yet.</div>`}</div>
    <div class="card"><h2>By session</h2>${p.per_session.length ? `<table><tr><th>Session</th><th>Gate failures</th><th>Avg SpO₂</th><th>Avg systolic</th></tr>
      ${p.per_session.map((s) => `<tr><td>${esc((s.session || "").toLowerCase())}</td><td>${s.gate_failures ?? 0} of ${s.gated ?? 0}</td><td>${s.avg_spo2 ? s.avg_spo2.toFixed(1) : "—"}</td><td>${s.avg_bp_sys ? s.avg_bp_sys.toFixed(0) : "—"}</td></tr>`).join("")}</table>` : `<div class="empty">No data yet.</div>`}</div>
  </div>
  <div class="card section-gap"><h2>Gate decision audit trail</h2>${p.events.length ? `<table><tr><th>Dose</th><th>When</th><th>Medication</th><th>SpO₂</th><th>BP</th><th>Thresholds</th><th>Gate</th><th>Outcome</th><th>Esc.</th></tr>
    ${p.events.map((e2) => { const g = e2.gate_reasons || {}; const r = g.readings || {}; return `<tr class="click" data-dose="${e2.id}"><td class="mono">${e2.id}</td><td>${fmtDT(e2.due_at)}<div class="muted">${esc((e2.session || "").toLowerCase())}</div></td><td>${esc(e2.medication)}</td>
      <td>${r.spo2 ?? "—"}</td><td>${r.bp_sys ? `${r.bp_sys}/${r.bp_dia}` : "—"}</td><td class="muted">${(g.rules || []).map((x) => esc(`${x.field} ${x.op} ${x.value}`)).join(", ")}${e2.tighten_steps ? ` <span class="chip c-warn">tightened ${e2.tighten_steps}</span>` : ""}</td>
      <td>${chip(e2.gate_decision)}</td><td>${chip(e2.state)}${e2.reviewer ? `<div class="muted">by ${esc(e2.reviewer)}</div>` : ""}</td><td>${lvlChip(e2.escalation_level)}</td></tr>`; }).join("")}</table>` : `<div class="empty">No gate decisions yet. Run a scenario in Simulator.</div>`}</div>`;
  bindDoseRows(el);
}

/* ================================================================
   Caretaker portal
   ================================================================ */
async function viewCaretaker(el) {
  const p = await api("/api/portal/caretaker");
  const extra = {};
  await Promise.all(p.patients.map(async (x) => {
    const [cal, dig] = await Promise.all([api(`/api/adherence/${encodeURIComponent(x.device.id)}?days=28`), api(`/api/digest/${encodeURIComponent(x.device.id)}`)]);
    extra[x.device.id] = {cal, dig};
  }));

  function urgencyGauge(score) {
    const maxScore = 10;
    const pct = Math.min(100, (score / maxScore) * 100);
    const color = score >= 6 ? "var(--danger)" : score > 0 ? "var(--warning)" : "var(--success)";
    const r = 32, circ = Math.PI * r; // semicircle
    const offset = circ - (pct / 100) * circ;
    return `<div class="urgency-gauge">
      <svg viewBox="0 0 80 50" width="80">
        <path d="M8 44 A32 32 0 0 1 72 44" fill="none" stroke="var(--border)" stroke-width="6" stroke-linecap="round"/>
        <path d="M8 44 A32 32 0 0 1 72 44" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round"
          stroke-dasharray="${circ}" stroke-dashoffset="${offset}" style="transition:stroke-dashoffset .5s ease"/>
        <text x="40" y="42" text-anchor="middle" fill="${color}" font-size="18" font-weight="700" font-family="var(--font-sans)">${score}</text>
      </svg>
    </div>`;
  }

  el.innerHTML = `<h1>Caretaker portal</h1><p class="sub">Escalation-weighted urgency per patient, not just a compliance percentage.</p>
  ${p.patients.map((x) => `<div class="card" style="margin-bottom:var(--sp-4)">
    <div class="row" style="justify-content:space-between">
      <div><b>${esc(x.device.patient_name || x.device.id)}</b> <span class="mono muted">${esc(x.device.id)}</span> ${kindChip(x.device.kind)} ${x.sos_open ? `<span class="chip c-bad">🆘 HELP REQUESTED</span>` : ""}</div>
      <div style="display:flex;align-items:center;gap:var(--sp-3)">
        ${urgencyGauge(x.urgency_score)}
        <div><div class="muted" style="font-size:11px">Urgency</div><div style="font-size:24px;font-weight:750;color:${x.urgency_score >= 6 ? "var(--danger)" : x.urgency_score > 0 ? "var(--warning)" : "var(--success)"}">${x.urgency_score}</div></div>
      </div>
    </div>
    <div class="grid g4" style="margin-top:var(--sp-3)">
      <div><div class="muted">Gate failure rate (last 5)</div><b>${Math.round(x.gate_failure_rate_last5 * 100)}%</b></div>
      <div><div class="muted">Missed doses (7 days)</div><b>${x.missed_7d}</b></div>
      <div><div class="muted">Highest escalation (7 days)</div><b>${x.max_escalation_level_7d ? "L" + x.max_escalation_level_7d : "—"}</b></div>
      <div><div class="muted">IDF-B formula score</div><b>${x.urgency_score_idf_formula}</b></div>
    </div>
    <div class="muted" style="margin-top:var(--sp-2)">${esc(x.urgency_note)}</div>
    <div class="grid g2" style="margin-top:var(--sp-4)">
      <div><h2>Last 28 days</h2>${calendarGrid(extra[x.device.id].cal)}</div>
      <div><h2>Family update (copy and send)</h2><pre class="digest" data-digest="${esc(x.device.id)}">${esc(extra[x.device.id].dig.text)}</pre>
        <div class="row"><button data-copy="${esc(x.device.id)}">Copy text</button>
        <a class="btn" href="https://wa.me/?text=${encodeURIComponent(extra[x.device.id].dig.text)}" target="_blank" rel="noopener">Share on WhatsApp</a></div>
        <div class="muted" style="margin-top:var(--sp-1)">WhatsApp needs internet; Copy works offline.</div></div>
    </div>
    <h2 style="margin-top:var(--sp-4)">Latest doses</h2>${x.latest.length ? `<table><tr><th>Dose</th><th>Due</th><th>Medication</th><th>Status</th><th>Esc.</th></tr>
      ${x.latest.map((d) => `<tr class="click" data-dose="${d.id}"><td class="mono">${d.id}</td><td>${fmtDT(d.due_at)}</td><td>${esc(d.medication)}</td><td>${chip(d.state)}</td><td>${lvlChip(d.escalation_level)}</td></tr>`).join("")}</table>` : `<div class="empty">No doses yet.</div>`}
  </div>`).join("")}`;
  bindDoseRows(el);
  el.querySelectorAll("[data-copy]").forEach((b) => b.onclick = async () => {
    const t = el.querySelector(`[data-digest="${b.dataset.copy}"]`).textContent;
    try { await navigator.clipboard.writeText(t); toast("Copied: paste it in SMS or WhatsApp", "success"); }
    catch (e2) { toast("Copy blocked by the browser: select the text and press Ctrl+C", "error"); }
  });
}

/* Adherence calendar: a cell per day x session, coloured by what happened. */
function calendarGrid(cal) {
  const cell = {};
  cal.cells.forEach((c) => { cell[c.day + "|" + c.session] = c; });
  const kind = (c) => !c ? "none" : c.missed ? "missed" : c.held || c.problem ? "held" : c.taken === c.total ? "taken" : "pending";
  const label = {none: "no dose", missed: "missed", held: "held / problem", taken: "all taken", pending: "pending"};
  const sessions = cal.sessions;
  return `<div class="cal" style="grid-template-columns:72px repeat(${cal.days.length},1fr)">
    <div></div>${cal.days.map((d, i) => `<div class="cal-h">${i % 7 === 0 || i === cal.days.length - 1 ? esc(d.slice(8)) : ""}</div>`).join("")}
    ${sessions.map((s) => `<div class="cal-s">${esc(s.toLowerCase())}</div>${cal.days.map((d) => { const c = cell[d + "|" + s]; const k = kind(c);
      return `<div class="cal-c cal-${k}" title="${esc(d)} ${esc(s.toLowerCase())}: ${label[k]}${c ? ` (${c.taken}/${c.total} taken)` : ""}"></div>`; }).join("")}`).join("")}
  </div><div class="cal-key"><span><i class="cal-c cal-taken"></i>all taken</span><span><i class="cal-c cal-missed"></i>missed</span><span><i class="cal-c cal-held"></i>held / problem</span><span><i class="cal-c cal-pending"></i>pending</span><span><i class="cal-c cal-none"></i>no dose</span></div>`;
}

/* ================================================================
   Patient screen: what the patient (or a tablet next to the box) shows.
   Big text, voice in the patient's language, one big HELP button.
   ================================================================ */
let spokenPromptId = null;
function autoVoice() { try { return localStorage.getItem("medgate-autovoice") === "1"; } catch (e) { return false; } }
const FRIENDLY = {
  DUE: ["Time for your medicine", "Tap your card, put your finger on the sensor and measure your BP."],
  RETAKE: ["Please measure again", "Sit still and try once more."],
  HELD: ["Please wait: doctor is checking", "This dose is on hold. Your doctor and family have been told."],
  DISPENSE_SENT: ["Getting your tablet…", "The box is opening."], EJECTED: ["Getting your tablet…", "Tablet is on its way."],
  IN_TRANSIT: ["Getting your tablet…", "Tablet is on the belt."], DELIVERED: ["Your tablet is in the tray", "Please take it now."],
};
async function viewPatient(el) {
  const devs = await api("/api/devices");
  if (!devs.length) { el.innerHTML = `<div class="empty">No devices.</div>`; return; }
  // default: the box that has a patient registered (a new hardware box may have none yet)
  let devId = (devs.find((d) => d.patient_name) || devs[0]).id;
  try { devId = localStorage.getItem("medgate-patient-dev") || devId; } catch (e) {}
  if (!devs.find((d) => d.id === devId)) devId = devs[0].id;
  const p = await api(`/api/patient/${encodeURIComponent(devId)}`);
  const a = p.active, f = a ? (FRIENDLY[a.state] || [a.state, ""]) : null;
  const pr = p.prompt;
  if (pr && pr.id !== spokenPromptId) {
    if (spokenPromptId !== null && autoVoice() && Date.now() / 1000 - pr.created_at < 30) speak(pr.text, pr.lang);
    spokenPromptId = pr.id;
  }
  el.innerHTML = `<div class="pt">
    <div class="pt-top"><div><div class="pt-hello">Hello, ${esc(p.device.patient_name || "patient")}</div>
      <div class="muted">${new Date(p.now * 1000).toLocaleDateString([], {weekday: "long", day: "numeric", month: "long"})} · ${fmtHM(p.now)}</div></div>
      <div class="row">${devs.length > 1 ? `<select id="ptdev" aria-label="Device">${devs.map((d) => `<option ${d.id === devId ? "selected" : ""}>${esc(d.id)}</option>`).join("")}</select>` : ""}
      <label class="pt-toggle"><input type="checkbox" id="ptvoice" ${autoVoice() ? "checked" : ""}> Speak new messages</label></div></div>
    ${p.sos ? `<div class="pt-sos-on" role="alert">🆘 Help has been requested. Your family and doctor have been alerted.</div>` : ""}
    <div class="pt-main ${a ? "pt-" + a.state.toLowerCase() : "pt-idle"}">
      <div class="pt-big">${a ? esc(f[0]) : p.next ? "Next medicine at " + fmtHM(p.next.due_at) : "No medicine due right now"}</div>
      <div class="pt-sub">${a ? esc(f[1]) + ` <span class="muted">(${esc(a.medication)}, box ${a.compartment})</span>` : p.next ? esc(p.next.medication) + ", box " + p.next.compartment : "Rest well."}</div>
    </div>
    ${pr ? `<div class="pt-say"><div><div class="muted">The box says (${esc(pr.lang)}):</div><div class="pt-say-text">${esc(pr.text)}</div></div><button id="ptsay" class="pt-play" aria-label="Play message">▶</button></div>` : ""}
    <div class="grid g2">
      <div class="card"><h2>Today: ${p.taken_today} of ${p.total_today} taken</h2>${p.today.length ? `<ul class="pt-list">${p.today.map((d) =>
        `<li><span class="pt-ico">${d.state === "COLLECTED" ? "✅" : d.state === "MISSED" ? "❌" : ["HELD", "WITHHELD"].includes(d.state) ? "⏸️" : "🕒"}</span><b>${fmtHM(d.due_at)}</b> ${esc(d.medication)}</li>`).join("")}</ul>` : `<div class="empty">No doses today yet.</div>`}</div>
      <div class="card pt-help-card"><button class="pt-help" id="pthelp">🆘 HELP</button><div class="muted">Press if you feel unwell. Family and doctor are alerted at once. On the real box: hold your hand over the tray for 5 seconds.</div></div>
    </div></div>`;
  const dsel = $("#ptdev"); if (dsel) dsel.onchange = () => { try { localStorage.setItem("medgate-patient-dev", dsel.value); } catch (e) {} render(); };
  $("#ptvoice").onchange = (e) => { try { localStorage.setItem("medgate-autovoice", e.target.checked ? "1" : "0"); } catch (e2) {} if (e.target.checked && pr) speak(pr.text, pr.lang); };
  if ($("#ptsay")) $("#ptsay").onclick = () => { if (!speak(pr.text, pr.lang)) toast("This browser has no voice for that language", "error"); };
  $("#pthelp").onclick = async () => {
    const b = $("#pthelp"); b.disabled = true;
    try { await api(`/api/devices/${encodeURIComponent(devId)}/sos`, {method: "POST", body: {source: "patient screen"}}); toast("Help requested: family and doctor alerted", "success"); render(); }
    catch (e) { toast(e.message, "error"); b.disabled = false; }
  };
}

/* ================================================================
   Boot
   ================================================================ */
route();
checkServerVersion();
