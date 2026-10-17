# Cursor prompt: redesign the MedGate dashboard UI

Paste everything below the line into Cursor Agent (Agent mode, terminal allowed), with this project folder open.

---

ROLE: You are a senior product designer + frontend engineer. Redesign the MedGate dashboard into a beautiful, modern, professional healthcare UI, then verify it works and fix any errors yourself.

TOKEN RULES (follow strictly):
- Read only: `frontend/index.html`, `frontend/styles.css`, `frontend/app.js`, and `backend/medgate/api.py` (for endpoint shapes). Do not read docs, firmware, tests or other backend files unless a step fails.
- Plan once, then write each file in full once. Do not rewrite a file repeatedly; use targeted edits for fixes.
- Keep replies short: what changed, what you verified.

HARD CONSTRAINTS (breaking these breaks the project):
1. Stay buildless: plain HTML + CSS + vanilla JavaScript (ES modules allowed). NO npm, NO React/Vite/Tailwind build, NO CDN links, NO web fonts from the internet. This PC has no reliable internet; the demo venue may have none. Use a system font stack (e.g. "Segoe UI Variable", "Segoe UI", Inter if installed, system-ui). Icons: inline SVG you write yourself.
2. Files stay in `frontend/`, served by FastAPI at `/` and `/static/...`. `index.html` must contain the word "MedGate" and load `/static/app.js`. You may add more files under `frontend/` (e.g. `/static/views/*.js`, `/static/icons.js`).
3. Do NOT change any backend file or API. Use the existing endpoints only: `/api/overview`, `/api/doses`, `/api/doses/{id}`, `/api/doses/{id}/review`, `/api/doses/{id}/acknowledge`, `/api/vitals`, `/api/escalations`, `/api/alerts`, `/api/alerts/{id}/resolve`, `/api/env`, `/api/medications`, `/api/medications/{id}/refill`, `/api/schedules`, `/api/schedules/{id}/active`, `/api/devices`, `/api/events`, `/api/audit`, `/api/audit/verify`, `/api/portal/doctor`, `/api/portal/caretaker`, `/api/compliance/{device_id}`, `/api/rules/demo`, `/api/sim`, `/api/sim/run/{name}`, `/api/doses` (POST). Check `api.py` for request/response fields before using one.
4. Keep every feature and page that exists today: Overview, Doctor, Caretaker, Doses (with lifecycle stepper + click-to-open detail drawer with audit trail), Review (approve/withhold with required reviewer name; acknowledge faults), Vitals, Alerts (refill/storage alerts with resolve + escalation history), Medicines (compartments, stock + refill, add medication, schedules with days/food/session/tightening/on-off toggle, add schedule), Devices (devices, storage readings, register hardware, raw events), Audit (chain status + log), Simulator (scenario list + live device log). Hash routing (`#overview`, `#doctor`, …) must keep working.
5. Keep these exact hooks (an automated browser test uses them): buttons `[data-run="<scenario>"]` on the Simulator page; on Review page the reviewer input `#rv`, error element `#rverr`, buttons `[data-act="approve"][data-id="<dose id>"]`, `[data-act="withhold"]`, `[data-act="ack"]`; clickable dose rows/elements `[data-dose="<id>"]` that open the dose drawer.
6. Keep honesty/safety text visible: the prototype banner (dummy tablets, thresholds are IDF-B example values not clinical guidance, tray sensors do not prove swallowing), the purple SIMULATOR labels on simulated devices/data, "ingestion not verified" on pickup, and "trained on synthetic data" wherever the LSTM plan is shown. Never show invented numbers: every figure must come from the API; empty states when there is no data.
7. Escape all API text before inserting into HTML (keep an `esc()` helper). No `innerHTML` with raw API strings.

DESIGN DIRECTION:
- Calm, trustworthy clinical aesthetic. A left sidebar navigation with icons and labels (collapsible to icons on narrow screens), a top bar with page title, live connection status (pulsing dot), last-updated time, and a light/dark toggle (respect `prefers-color-scheme`, remember the choice in localStorage inside try/catch).
- Design tokens as CSS variables: one teal/emerald primary, semantic colours (success, warning, danger, info) plus purple for SIMULATOR, neutral surfaces with subtle elevation, 8 px spacing scale, 12–16 px radii, consistent type scale (page title 24–28 px, section 13 px uppercase labels, body 14 px). Both themes must meet WCAG AA contrast.
- Overview: hero KPI cards (adherence %, doses, awaiting review, open alerts, audit chain status) with small inline SVG sparklines or donut where data allows; a "what needs attention now" panel (held doses, open alerts, faults) with one-click navigation; recent doses as a visual timeline.
- Doses: each dose as a card or table row with a polished horizontal lifecycle stepper (Due → Health gate → Dispense → Ejected → Conveyor → Tray verified → Collected) showing done/current/held/stopped states with colour and icons.
- Dose detail: slide-in drawer with smooth transition, readings (SpO₂, BP, HR), thresholds applied (and tightening), LSTM plan, IR/tray verdicts, and a vertical audit timeline.
- Doctor portal: clean data tables plus simple hand-drawn SVG charts (gate-failure rate vs non-adherence by day; per-session averages). Caretaker portal: large urgency score gauge per patient, coloured by severity, with the explanation note.
- Review & Alerts: prominent action cards; destructive actions (Withhold) visually distinct; disabled + spinner state while a request is in flight; inline error messages.
- Medicines: compartment cards (Box 1–6) showing medicine, stock bar (red at/below refill threshold), days of supply, refill control; schedules as a tidy table with day chips and an on/off switch.
- Simulator: scenario cards with short description and Run button, a terminal-style live log, and a link to open the scenario's dose.
- Micro-interactions: hover/focus states, 150–200 ms transitions, skeleton loaders on first load, toast notifications for success/error, `prefers-reduced-motion` respected.
- Fully responsive (360 px phone to 1920 px desktop), keyboard accessible (visible focus rings, Esc closes drawer, buttons are real <button>s), aria-labels on icon buttons.

BEHAVIOUR TO PRESERVE:
- Auto-refresh the current page every 2 s, but never while the user is typing in an input/select/textarea, and never reset a form the user is filling in.
- Review count badge on the Review nav item; open-alert badge on Alerts.
- Reviewer name remembered in sessionStorage (in try/catch).

VERIFY AND FIX (do all of these, and repair anything that fails):
1. Start the server if it is not running: in `backend/`, `.venv\Scripts\python.exe -m uvicorn medgate.api:app --host 127.0.0.1 --port 8000` (if `.venv` is missing or broken, create it and install with `pip install --no-index --find-links wheels -r requirements.txt`).
2. `node --check` every JS file you wrote (if Node is installed).
3. Run backend tests: `cd backend && .venv\Scripts\python.exe -m pytest -q` → all must pass (they check the frontend is served).
4. Open http://127.0.0.1:8000 in the browser (use Cursor's browser tool if available). Visit every page, both themes, and a narrow window. Check the browser console has zero errors.
5. Simulator: run `normal`, `abnormal_vitals`, `low_stock`, `hot_storage`. Confirm: dose reaches COLLECTED; Review shows the held dose and Approve works with a name (and refuses without one); Alerts shows refill + storage alerts and Resolve works; dose drawer opens from Doses and from Doctor tables.
6. If Playwright is available: `cd backend && .venv\Scripts\python.exe tests\e2e_ui.py http://127.0.0.1:8000` must print `E2E UI OK`. If Playwright is not installed, skip this step and say so.
7. Fix every error you find, re-run the failing check, repeat until clean.

FINAL REPORT (max 10 lines): files changed, design highlights, checks run with results, anything you could not verify.
