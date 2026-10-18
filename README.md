# Zyron HackShift — IITD Hackathon

**Team:** Zyron  
**Event:** IITD HackShift Hackathon  
**Theme:** Healthcare & Well-being — Reimagining Healthcare Through Technology

---

## What is this?

**MediSentra** is a smart, health-gated medication dispenser built for the hackathon. It's a closed-loop system that checks a patient's vitals (SpO₂, Blood Pressure) before dispensing medicine — so the right pill is given only when the patient's body is ready for it.

Think of it as a robot pharmacist that's also a nurse.

---

## The Problem It Solves

- Patients forget to take medicine → poor adherence
- Wrong medicine taken when vitals are bad → dangerous outcomes
- No real-time monitoring for caregivers or doctors
- Medication errors in elderly or chronically ill patients

---

## How It Works

1. **Schedule set** — Doctor/admin sets medicine schedules (time, food condition, day of week)
2. **Vitals checked** — Before each dose, the device reads SpO₂ (MAX30102 sensor) and Blood Pressure (UART)
3. **Gate decides** — If vitals are within safe range, the medicine is dispensed. If not, it's suppressed and an alert is sent
4. **Dispensed** — A servo motor releases the pill onto a conveyor belt, which drops it into the tray
5. **Verified** — IR beam + weight sensor confirm the pill was picked up
6. **Escalation** — If the pill isn't taken, alerts go out: audio → buzzer → SMS → call

Everything is logged with a SHA-256 hash-chained audit trail.

---

## Key Features

- 🔒 **Health Gate** — Vitals must pass before dispensing (drug-specific: e.g., BP tablet held when BP is too low)
- 🤖 **LSTM Compliance Prediction** — ML model predicts if a patient is likely to miss a dose
- 🏥 **4-Level Escalation** — L1 audio → L2 buzzer → L3 SMS → L4 phone call
- 👆 **RFID Patient Card** — Patients tap their card instead of reading a screen
- 🗣️ **Voice Prompts** — English + Hindi (Manipuri planned)
- 📊 **Doctor & Caretaker Portals** — Real-time dashboards with trends and 30-day reports
- 💊 **168,000+ Indian Medicines** — Real medicine catalogue (name, dose, MRP)
- 🛒 **Simulated Pharmacy** — Caretaker can search, compare prices and "buy" medicine
- 📅 **28-Day Adherence Calendar** — Visual compliance tracking
- 🆘 **SOS Button** — On-screen or by holding hand over tray for 5 seconds
- 🌡️ **Storage Monitor** — AHT20 sensor watches temperature & humidity of the medicine box
- 🛡️ **Double-Dose Protection** — Can never dispense the same dose twice
- ⏰ **Expiry Check** — Expired medicine is never dispensed

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · FastAPI |
| Frontend | Plain HTML / CSS / JavaScript (no build step) |
| Firmware | C++ · ESP32 · PlatformIO |
| ML Model | LSTM (NumPy, custom, runs on both server and ESP32) |
| Database | SQLite (via SQLAlchemy) |
| Hardware | ESP32, MAX30102, AHT20, servo, IR sensor, weight sensor, conveyor, OLED |

---

## Project Structure

```
healthCare Hackthon Project/
├── backend/          # FastAPI server, business logic, LSTM model, simulator
├── frontend/         # Dashboard (HTML/CSS/JS), login screen, patient view
├── firmware/         # ESP32 C++ code + portable core tested natively
├── datasets/         # Indian medicine dataset (168K+ entries)
├── shared/           # Test vectors shared between Python and C++
├── start.bat         # One-click startup (Windows)
├── start.sh          # One-click startup (Linux/macOS)
└── DEMO_GUIDE.md     # Full demo walkthrough
```

---

## Quick Start

**Windows:** Double-click `start.bat` — it installs everything from bundled wheels (no internet needed) and opens the dashboard at `http://127.0.0.1:8000`

**Linux / macOS:** Run `./start.sh`

Then go to the **Simulator** tab to run demo scenarios without any physical hardware.

---

## User Roles

| Role | Access |
|---|---|
| Admin | Everything |
| Doctor | All patients' vitals, trends, reports |
| Caretaker | Their patient's schedule, inventory, purchase |
| Patient | Their own screen with voice + big text + SOS |

Demo credentials are in `LOGINS.md`.

---

## Hardware Status

| Component | Status |
|---|---|
| Backend + Dashboard + Simulator | ✅ Implemented & tested |
| Firmware (portable C++ core) | ✅ Tested natively (71 checks) |
| Firmware (ESP32 glue) | ⚠️ Written, not yet compiled for ESP32 |
| Physical hardware | ❌ Not tested — no hardware available |
| LSTM model | ✅ Trained on synthetic data; server & device match |

---

## Cost Estimate

Full parts cost: **≈ ₹6,000 – ₹10,000**  
See [`COST_ANALYSIS.md`](healthCare%20Hackthon%20Project/COST_ANALYSIS.md) for the breakdown.

---

## Docs

- [Demo Guide](healthCare%20Hackthon%20Project/DEMO_GUIDE.md)
- [Implementation Plan](healthCare%20Hackthon%20Project/IMPLEMENTATION_PLAN.md)
- [Feature Traceability](healthCare%20Hackthon%20Project/FEATURE_TRACEABILITY.md)
- [Test Report](healthCare%20Hackthon%20Project/TEST_REPORT.md)
- [Hardware Setup](healthCare%20Hackthon%20Project/HARDWARE_SETUP.md)
- [Known Limitations](healthCare%20Hackthon%20Project/KNOWN_LIMITATIONS.md)
- [Cost Analysis](healthCare%20Hackthon%20Project/COST_ANALYSIS.md)
- [Third-Party Notices](healthCare%20Hackthon%20Project/THIRD_PARTY_NOTICES.md)

---

> **Disclaimer:** This is a hackathon prototype for demonstration purposes only. All tablets used are dummy, non-ingestible. Gate thresholds are example values, not clinical guidance.
