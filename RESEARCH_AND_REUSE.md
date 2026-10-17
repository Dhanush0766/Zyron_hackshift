# Research and reuse

Research date: 17 Oct 2026. Method: GitHub repository search via the public search API (`/search/repositories`), README inspection of the most relevant hits, license checks via the repository API, and web search for published work.

## Coverage, stated honestly

- **Discovered:** 802 repositories match "pill dispenser", 263 "medication dispenser", 246 "medicine esp32" (overlapping). About 45 were listed with name, stars, license and description.
- **Inspected at README level:** 5 (`Shaztech/Pilldispenser`, `MelvinMo/DoseBuddy`, `Shiv07ansh/AIoT-Medicine-Reminder`, plus licenses of the library repos below).
- **Inspected at source level:** 0 dispenser repositories. No candidate repository was cloned or executed. Shell access to GitHub was blocked in our build environment, so pages were read through a web fetcher only.
- The brief's target of 50 discovered / 15–20 deeply inspected was **not met for deep inspection**. Time went to building and testing instead.

## Documents reviewed
- **IDF-B** (team's invention disclosure, 34 pages): requirements source; see FEATURE_TRACEABILITY.md.
- **Dangre et al., "Smart Portable Medico", JAAFR 4(4), Apr 2026** (uploaded by the team): prior art; see DIFFERENTIATION_AND_PRIOR_ART.md.

## Notable candidates

| Repository | Stars | License | What it has | What it lacks for us |
|---|---|---|---|---|
| [Shaztech/Pilldispenser](https://github.com/Shaztech/Pilldispenser) | 203 | none on API | ESP32 CYD, up to 10 servo trays, PCA9685, Telegram, OTA | No sensor confirming a pill dropped; no vitals; no license |
| [MelvinMo/DoseBuddy](https://github.com/MelvinMo/DoseBuddy) | ~2 | MIT | Weight sensors, stepper, caregiver alerts | Arduino R4; no vitals; verification method not described |
| [Shiv07ansh/AIoT-Medicine-Reminder](https://github.com/Shiv07ansh/AIoT-Medicine-Reminder) | ~3 | none shown | ESP32-S3, tinyML keyword spotting, Hall + ultrasonic "taken" check, email | Not a dispenser; single-speaker models |
| [quibueno/med_pill](https://github.com/quibueno/med_pill) | 7 | GPL-3.0 | Arduino dispenser, Wi-Fi, Telegram/Alexa alerts | GPL (copyleft); no vitals |
| [himikajain15/Smart-Pill-Dispenser](https://github.com/himikajain15/Smart-Pill-Dispenser) | 1 | none | Raspberry Pi, load cell + camera intake check | No license; Pi, not ESP32 |

## Features adopted (17 Oct 2026, re-implemented, no code copied)

| Feature | Where we saw it | Why adopted |
|---|---|---|
| Tablet stock + refill alert | JAAFR "Smart Portable Medico" (refill alerts), IJERT 2026 dispenser (low stock) | An empty box must never silently skip a dose |
| Storage temperature/humidity alert | Smart-Medibox (chathura-de-silva, eshansurendra; MIT) | Medicines degrade in hot, humid rooms (common in Indian summers and monsoon) |
| Day-of-week schedules | JAAFR paper | Many prescriptions are not daily |

Rejected: Telegram alerts (team chose not to add), bulk import of whole repositories (licence risk, conflicting logic, no IDF-B value).

## Decision: what we reused

No dispenser repository's code was copied. Reasons: the strongest one has no license (no right to reuse), the MIT ones are small and use different hardware, and none implements the safety logic that is our contribution. They informed design only (for example, Shaztech confirms one servo per compartment is standard practice).

| Component | Source | Class | How |
|---|---|---|---|
| MAX30102 driver + Maxim SpO2/HR algorithm | [SparkFun MAX3010x library](https://github.com/sparkfun/SparkFun_MAX3010x_Sensor_Library) | A. Reuse (dependency) | `lib_deps`; license not confirmed via API (fetch failed), so check before redistributing |
| HX711 load cell | [bogde/HX711](https://github.com/bogde/HX711) | A. Reuse (dependency) | MIT (verified via API) |
| DS3231 RTC | [adafruit/RTClib](https://github.com/adafruit/RTClib) | A. Reuse (dependency) | MIT (verified via API) |
| OLED | Adafruit SSD1306 + GFX | A. Reuse (dependency) | BSD (from memory, not verified) |
| JSON | ArduinoJson v7 | A. Reuse (dependency) | MIT (from memory, not verified) |
| Servo PWM | ESP32 LEDC peripheral directly | C. Build | Avoided `madhephaestus/ESP32Servo` (API shows no license field) |
| SIM800L | Raw AT commands | C. Build | Avoided TinyGSM (LGPL) for a ~20-line SMS routine |
| Gate, FSM, verification, escalation, audit, offline idempotency | Our code | C. Build | This is the contribution |
| LSTM (TFLite Micro) | — | D. Defer | No adherence data to train or evaluate |
| MQTT broker | — | D. Defer | HTTP polling is enough for one device and removes a service to run at the demo |

Firmware libraries are fetched by PlatformIO at build time. None are vendored in this repository.
