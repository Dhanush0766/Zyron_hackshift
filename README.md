# MedGate: health-gated, closed-loop medication dispenser (hackathon prototype)

Theme: **Healthcare & Well-being: Reimagining Healthcare Through Technology**.
Implements our IDF-B invention disclosure (VIT IPR&TT Cell, Doc. 02-IPR-R003, 29.04.2026): health-gated multi-compartment dispensing with conveyor delivery, LSTM compliance prediction, physiologically-conditioned 4-level escalation, closed-loop dual-sensor compliance verification, and doctor/caretaker portals.

> **Prototype for demonstration with dummy, non-ingestible tablets only.** Gate thresholds are the IDF-B example values, not clinical guidance. Tray sensors show a tablet was removed, not that it was swallowed.

## What it does

```
schedule (box, time, food, session) ─► LSTM plan (reminder lead, volume, L1 window) ─► dose DUE
   ─► SpO2 (MAX30102) AND BP (UART) ─► valid? ── no ──► RETAKE (3 strikes → suppressed)
                                       │
                                  thresholds (tightened after a missed same-session pickup)
                                       │ fail ─► servo SUPPRESSED, health alert L3, doctor portal; named override possible
                                       ▼ pass
   DISPENSE (exactly once) ─► servo ─► IR beam ─► conveyor ─► limit switch stops belt ─► tray weight +1 tablet
        both agree ─► DELIVERED ─► weight removed AND hand detected ─► COLLECTED (tightening reset)
        disagree   ─► REVIEW_REQUIRED (never auto re-dispensed)          weight only ─► REVIEW_REQUIRED
        not picked up ─► L1 audio/OLED ─► L2 buzzer ─► L3 SMS ─► L4 call ─► MISSED ─► tighten next same-session gate
                         (L3/L4 come twice as fast when ≥ 40 % of the last 5 sessions failed the gate)
```

Every step is persisted and written to a SHA-256 hash-chained audit log.

Added beyond the IDF-B (features seen in other projects, re-implemented here): **tablet stock + refill alerts**, **medicine-storage temperature/humidity watch** (AHT20), **day-of-week schedules**.

Our own additions (FEATURE_TRACEABILITY A5–A12):
- **Drug-specific health gate.** A BP tablet is held when BP is too *low*, a beta-blocker when the pulse is slow, and a diabetes tablet when the patient has not eaten.
- **Patient card check (RFID).**
- **Double-dose protection** and a per-medicine late-dose window.
- **Voice prompts** in English and Hindi, with Manipuri to be recorded by the team.
- **Early-warning trends** that alert the doctor before a dose is ever blocked.
- **Printable 30-day doctor report.**
- **Cost analysis** ([COST_ANALYSIS.md](COST_ANALYSIS.md)).
- **Patient screen** with large text, the box's voice and a big HELP button.
- **SOS**: from the screen, or a hand held over the tray for 5 s.
- **Medicine expiry**: an expired strip is never given.
- **28-day adherence calendar.**
- **Family update** to copy, or share on WhatsApp.
- **One-click demo story.**

Theme fit: quality of care (drug-specific gate, double-dose guard), remote monitoring (trends, portals, report), accessibility (voice in local language, card instead of reading a screen), affordability (≈ ₹6–10 K parts, estimate).

## Repository layout

| Path | What |
|---|---|
| `backend/medgate/core/` | Pure logic: vitals validation, AND health gate + tightening, dose FSM, dual-sensor verification, escalation with contraction, LSTM compliance inference |
| `backend/tools/lstm_train.py` | Offline LSTM training on synthetic data (numpy), exports weights for server and ESP32 |
| `backend/medgate/engine.py` | Orchestration: the only place dose state changes; audit chain; idempotent device ingest |
| `backend/medgate/api.py` | FastAPI: dashboard API, device API (key-authenticated), simulator control |
| `backend/medgate/simulator.py` | **SIMULATED** dispenser speaking the firmware's event protocol |
| `frontend/` | Dashboard (plain HTML/JS, served by the backend, no build step) |
| `firmware/` | ESP32 PlatformIO project; `lib/medgate_core` is portable C++ tested on the host |
| `shared/gate_vectors.csv` | Test vectors run by BOTH the Python and the C++ gate |

## Quick start

Windows: double-click **`start.bat`** (installs from the bundled `backend\wheels`, no internet needed; keep its window open). Linux/macOS: `./start.sh`. The browser opens http://127.0.0.1:8000. Use **Simulator** to run scenarios. Full script: [DEMO_GUIDE.md](DEMO_GUIDE.md).

Tests: `cd backend && .venv\Scripts\python -m pytest -q` (140 tests) and `firmware/test/run_native_tests.sh` (71 checks).

## Status at a glance

| Part | Status |
|---|---|
| Backend, dashboard, simulator | Implemented and tested (unit, API, browser E2E) |
| Firmware portable core | Implemented and tested natively with g++ (ASan + UBSan), 71 checks |
| Firmware ESP32 glue (`src/main.cpp`) | Written, **not yet compiled for ESP32** (toolchain download blocked in our build environment) |
| Physical hardware | **Not tested**. No hardware was available |
| LSTM compliance model | Implemented; trained on **synthetic** data; server + device inference match the trainer |
| IDF-B cross-check | Done: [FEATURE_TRACEABILITY.md](FEATURE_TRACEABILITY.md) maps every IDF-B section; deviations explained |
| Hackathon (Discord) rules | **Not provided yet** |

Documentation: [IMPLEMENTATION_PLAN](IMPLEMENTATION_PLAN.md) · [RESEARCH_AND_REUSE](RESEARCH_AND_REUSE.md) · [DIFFERENTIATION_AND_PRIOR_ART](DIFFERENTIATION_AND_PRIOR_ART.md) · [FEATURE_TRACEABILITY](FEATURE_TRACEABILITY.md) · [TEST_REPORT](TEST_REPORT.md) · [HARDWARE_SETUP](HARDWARE_SETUP.md) · [DEMO_GUIDE](DEMO_GUIDE.md) · [KNOWN_LIMITATIONS](KNOWN_LIMITATIONS.md) · [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md) · [COST_ANALYSIS](COST_ANALYSIS.md) · [VOICE_CLIPS](VOICE_CLIPS.md)
