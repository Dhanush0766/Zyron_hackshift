ROLE
You are a senior full-stack engineer and product designer. Add secure role-based logins (Patient, Caretaker, Doctor, Admin) to the MedGate project in this folder. Give each role its own professional dashboard. Make the UI more attractive and user-friendly. Keep every existing backend feature working, and verify everything yourself before you finish.

WORK STYLE (follow strictly)
- Read before you write. First read: README.md, FEATURE_TRACEABILITY.md, backend/medgate/api.py, backend/medgate/engine.py, backend/medgate/db.py, backend/tests/conftest.py, backend/tests/e2e_ui.py, frontend/index.html, frontend/app.js, frontend/styles.css. Read other files only when a step needs them.
- Then write a short plan (max 25 lines) and follow it phase by phase: 1 backend auth, 2 role enforcement, 3 tests, 4 frontend login + role dashboards, 5 design polish, 6 verification.
- Use targeted edits. Do not rewrite whole files unless a file is mostly new. Do not paste big files back to me.
- If a check fails, fix the smallest cause and re-run. Never delete or weaken a test to make it pass; adjust a test only when the behaviour change is intended (e.g. it now needs a login).

WHAT THIS PROJECT IS (do not break any of it)
MedGate is a health-gated, closed-loop medication dispenser (hackathon prototype, IDF-B invention disclosure). An ESP32 box with 6 compartments checks the patient's SpO2 and blood pressure before releasing a tablet. A servo drops the tablet, an IR beam sees it fall, a conveyor carries it to a tray, and a load cell confirms one tablet arrived. Pickup counts only when the weight is removed AND a hand is seen. If the patient forgets, reminders escalate in 4 levels (sound, buzzer, SMS, call). Everything is written to a SHA-256 hash-chained audit log.

Existing features (all tested, 140 backend tests + browser E2E):
- Health gate: SpO2 >= 94 AND systolic <= 160, BP required, readings validated. Gate tightens after a missed pickup. Doses are held for a named human review (approve / withhold).
- Drug-specific gate: drug classes GENERAL, ANTIHYPERTENSIVE (held if systolic < 100), BETA_BLOCKER (held if pulse < 60; HR required), SULFONYLUREA (held if the patient has not eaten).
- Patient RFID card check, double-dose protection (min gap per medicine), per-medicine late-dose window.
- Dual-sensor delivery check, compliance AND gate, 4-level escalation, LSTM reminder planning (synthetic data, clearly labelled).
- Stock + refill alerts, medicine expiry (an expired strip is never dispensed), storage temperature/humidity alerts, early-warning trends (SpO2 drift down, BP drift up).
- Voice prompts in en/hi (Manipuri fallback), patient screen with big text and a HELP (SOS) button, SOS from the device.
- 28-day adherence calendar, family daily digest (copy / WhatsApp), printable 30-day doctor report at /report/{device_id}.
- Simulator with 27 scenarios including full_demo. Device API authenticated by the X-Device-Key header.
- Old-server warning: /api/health returns {"version", "schema"}; frontend constant EXPECTED_API must equal backend APP_VERSION.

HARD CONSTRAINTS (breaking these breaks the demo)
1. No new Python packages. The venue PC installs offline from backend/wheels (FastAPI, Starlette, Uvicorn, Pydantic, httpx, pytest only). Use only the standard library for security: hashlib.pbkdf2_hmac, secrets, hmac.compare_digest.
2. No npm, no build step, no CDN, no web fonts, no external images. Plain HTML/CSS/vanilla JS in frontend/, served by FastAPI at / and /static/. Inline SVG icons only.
3. Python 3.10 syntax: no f-strings that reuse the same quote type inside {} (that needs Python 3.12).
4. Keep the device API (/api/device/{id}/events and /commands) on X-Device-Key, unchanged. The ESP32 firmware must not need changes.
5. Keep every existing endpoint, scenario, feature and safety rule. The gate, FSM, verification, escalation, audit chain and simulator logic in core/ and engine.py must not change behaviour.
6. Keep these UI hooks (the browser test uses them): [data-run="<scenario>"], #rv, #rverr, [data-act="approve|withhold|ack"][data-id], [data-dose].
7. Keep the honesty text: prototype / dummy tablets / thresholds are IDF-B example values, not clinical guidance; "ingestion not verified" on pickup; "trained on synthetic data" on the LSTM; SIMULATOR labels.
8. Escape all API text in HTML (keep esc()). Never put passwords or session tokens in URLs, logs, the audit log or localStorage.
9. Database: bump SCHEMA_VERSION in db.py (the existing auto-rebuild handles old demo DBs). Bump APP_VERSION in api.py to "0.5.0" and EXPECTED_API in app.js to "0.5.0".
10. start.bat / start_lan.bat must keep working exactly as now (CRLF line endings).

PHASE 1: BACKEND AUTH
- New tables: users(id, username UNIQUE, full_name, role CHECK IN ('ADMIN','DOCTOR','CARETAKER','PATIENT'), pw_salt, pw_hash, active, must_change_pw, created_at, last_login_at), user_devices(user_id, device_id) linking doctors/caretakers/patients to dispensers, sessions(token_hash, user_id, created_at, expires_at), login_attempts for rate limiting.
- Passwords: PBKDF2-HMAC-SHA256, 16-byte random salt, >= 200,000 iterations, compare with hmac.compare_digest. Minimum 8 characters.
- Sessions: secrets.token_urlsafe(32) in an HttpOnly, SameSite=Strict cookie named medgate_session, path=/. Store only a SHA-256 of the token. Expiry 12 h. Logout deletes it.
- Lockout: 5 failed logins for a username within 5 minutes → refuse for 5 minutes. Use the same error message for wrong user and wrong password.
- Endpoints: POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me (user, role, linked devices, permissions list), POST /api/auth/change-password (needs the old password).
- Admin-only user endpoints: GET/POST /api/users, POST /api/users/{id} (name, role, active, linked devices), POST /api/users/{id}/reset-password. The last active admin can never be disabled or demoted.
- Seed demo accounts on a fresh DB, linked to SIM-001. Write them in README under "Demo logins (change before any real use)":
  admin / admin@123 (ADMIN)
  doctor / doctor@123 (DOCTOR, "Dr. Demo")
  caretaker / care@123 (CARETAKER, "Asha (daughter)")
  patient / patient@123 (PATIENT, linked to SIM-001, the simulated patient)
  Show these on the login page only while env MEDGATE_DEMO is not "0" (default: shown, with a "demo only" label).
- Audit every login, logout, failed login, user change and password change (never the password). Every human action now records the logged-in user as actor. The review reviewer name comes from the session user's full name instead of free text. Keep the #rv input, prefilled and read-only, so the E2E hook stays.

PHASE 2: ROLE ENFORCEMENT ON THE SERVER (not only by hiding buttons)
One FastAPI dependency, e.g. require(roles=..., device_scope=True). Unauthenticated → 401. Wrong role → 403. A device outside the user's linked devices → 403. ADMIN passes everything. Public: /, /static/*, /api/health, /api/auth/login. Device endpoints keep X-Device-Key.
Permission matrix (implement exactly; list endpoints are filtered to the user's devices):
- PATIENT: own device only. GET /api/patient/{id}, POST /api/devices/{id}/sos, read own today's doses and voice prompts, change own password. Nothing else.
- CARETAKER: linked devices. Overview, caretaker portal, adherence calendar, family digest, doses (read), dose detail, alerts (read + resolve refill/storage/SOS/expiry/card alerts), escalations, medications (read, refill, set expiry; cannot change drug class, rules or add medicines), schedules (read, on/off), patient screen, acknowledge faults, set patient language/card.
- DOCTOR: linked devices. Doctor portal, report /report/{id}, review approve/withhold, vitals, doses, dose detail, alerts (read + resolve trend alerts), medications (add, drug class, rules, min gap, late window, expiry), schedules (add, on/off), compliance plan, patient screen (view only).
- ADMIN: everything, including devices (register), users, audit log + verify, simulator, events, env readings, rules/demo.
- /api/sim/* is ADMIN only (doctor and caretaker may see the Simulator page only if you also give them access; keep it admin-only).

PHASE 3: TESTS
- Keep all 140 existing tests passing. In conftest.py the API `client` fixture logs in as admin (session cookie), so old tests run unchanged in behaviour.
- Add tests/test_auth.py with at least: password hashing round-trip + salt uniqueness; login ok / wrong password / unknown user, with the same message; lockout after 5 failures; logout invalidates the session; expired session refused; /api/auth/me per role; a 403 matrix test looping over every role × a representative endpoint per permission row; device-scope test (a caretaker not linked to SIM-001 gets 403); the last admin cannot be disabled; change-password needs the old password; review records the logged-in doctor as reviewer; the device API still works with X-Device-Key and no session; the audit has LOGIN entries and no password text.
- Update backend/tests/e2e_ui.py: log in through the login form (admin for the existing flow, then doctor approving a held dose), keep its final "E2E UI OK" line, and check every page each role can see renders without JS errors.

PHASE 4: FRONTEND (login + one dashboard per role)
- Login screen: centred card, MedGate logo (inline SVG), username + password, show/hide password, Enter submits, clear inline errors, lockout message, demo-accounts panel (click to fill) when enabled. Subtle illustrated health background in pure CSS/SVG. Responsive.
- After login, GET /api/auth/me decides the nav and the home page. On any 401, return to the login screen. Top bar: user avatar (initials), name, role badge, change password, logout. Forced password change screen when must_change_pw.
- PATIENT dashboard: the existing Patient screen made even simpler. Huge text, high contrast, "what to do now", today's tick list, ▶ hear message (speak in their language), giant HELP button with a 3-second hold-to-confirm, next dose countdown. No other menus.
- CARETAKER dashboard: home "My patients" cards (photo-less avatar, urgency gauge, SOS banner, taken today x/y, next dose, low stock/expiry chips). Then the patient detail with the 28-day calendar, family digest (copy / WhatsApp), alerts to resolve, refill + expiry quick actions, recent doses.
- DOCTOR dashboard: home "Needs your decision" (held doses with readings, the drug rule that triggered and Approve/Withhold), early warnings, a patient list with adherence %, gate-failure rate, last vitals, 30-day report button. Plus the existing Doctor portal charts, Vitals, Doses, and Medicines with drug class / rules / schedules editing.
- ADMIN dashboard: everything that exists today (Overview, Patient screen, Doctor, Caretaker, Doses, Review, Vitals, Alerts, Medicines, Devices, Audit, Simulator) plus a new Users page: create user, role, link devices, enable/disable, reset password, last login.
- Each role sees only its own menu items, and the server enforces the same rules. Hidden is not secure on its own.

PHASE 5: DESIGN (professional, calm, trustworthy healthcare look)
- Keep the existing sidebar/top-bar layout and design tokens (CSS variables, light + dark). Refine them: an 8-px spacing scale, a clear type scale (system fonts: "Segoe UI Variable", "Segoe UI", system-ui), 12–16 px radii, soft elevation, one teal primary, semantic success/warning/danger/info, purple only for SIMULATOR.
- KPI cards with small inline-SVG sparklines/donuts where real data exists; skeleton loaders; empty states with a helpful line; toasts; 150–200 ms transitions; respect prefers-reduced-motion.
- Role-coloured accent on the badge and avatar only (not the whole UI).
- Accessibility: WCAG AA contrast in both themes, visible focus rings, real buttons, aria-labels on icon buttons, minimum 44 px touch targets (patient screen 64 px+), keyboard navigation, Esc closes drawers.
- Responsive from 360 px to 1920 px; no page may scroll sideways (wide tables scroll inside their card).
- Never invent numbers: every figure comes from the API; show "—" or an empty state when there is no data.

PHASE 6: VERIFY (all must pass; fix and repeat until clean)
1. cd backend && .venv\Scripts\python.exe -m pytest -q → all pass (140 old + your new ones). Report the count.
2. node --check frontend/app.js (and any new JS file), if Node is installed.
3. Close any running server, then start one: in backend, .venv\Scripts\python.exe -m uvicorn medgate.api:app --host 127.0.0.1 --port 8000. If .venv is missing, create it and pip install --no-index --find-links wheels -r requirements.txt.
4. In the browser, log in as each of the 4 demo users. For each, visit every page in its menu in light and dark mode at desktop and phone width: zero console errors, no sideways scroll. Then try a URL or API call outside its permissions and confirm 403.
5. As admin: Simulator → ▶ Run full demo; confirm the doctor sees the held doses in "Needs your decision" and can approve; the caretaker sees the SOS and resolves it; the patient sees today's list and HELP works; the doctor's report opens with charts.
6. If Playwright is installed: .venv\Scripts\python.exe tests\e2e_ui.py http://127.0.0.1:8000 → must print "E2E UI OK".
7. /api/health shows version 0.5.0, and the dashboard shows no "older server" warning.

ALSO UPDATE
README (demo logins, roles table, how to add users), FEATURE_TRACEABILITY (add A19 role-based access with tests), TEST_REPORT (new counts), DEMO_GUIDE (log in as doctor/caretaker/patient during the demo), KNOWN_LIMITATIONS (demo passwords, no HTTPS on the LAN, no password-reset email, sessions in SQLite).

FINAL REPORT (max 15 lines)
Files changed; the permission matrix as implemented; test counts; what you verified in the browser per role; anything you could not verify or left out, and why.
