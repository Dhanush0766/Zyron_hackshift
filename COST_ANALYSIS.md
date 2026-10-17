# Cost analysis (affordability)

**Status: component prices are our ESTIMATES of typical Indian hobby-electronics retail prices (2025–26). They were not checked against live listings. Before quoting them to judges, check current prices on Robu.in, Amazon.in or a local shop and replace the ranges with real quotes.**

## Bill of materials, one prototype dispenser

| Part | Qty | Estimated ₹ |
|---|---|---|
| ESP32 DevKit (WROOM-32) | 1 | 400–550 |
| MAX30102 SpO₂/HR sensor | 1 | 150–250 |
| BP module with UART output | 1 | 1,500–2,500 |
| PCA9685 16-ch servo driver | 1 | 200–300 |
| SG90/MG90S servos | 6 | 480–900 |
| TCRT5000 IR beam sensors | 6 | 150–240 |
| L298N motor driver | 1 | 120–180 |
| 12 V gear motor + belt/pulleys | 1 | 300–600 |
| HX711 + 1 kg load cell | 1 | 150–250 |
| IR obstacle sensor (hand at tray) | 1 | 40–60 |
| Limit switch | 1 | 20–40 |
| DS3231 RTC | 1 | 120–200 |
| 0.96" OLED (SSD1306) | 1 | 180–250 |
| Micro SD module + 8 GB card | 1 | 300–400 |
| SIM800L GSM module | 1 | 350–500 |
| MAX98357A I2S amp + small speaker (voice prompts) | 1 | 300–450 |
| AHT20 temperature/humidity | 1 | 120–200 |
| RC522 RFID reader + 2 cards (patient check) | 1 | 120–180 |
| Buzzer | 1 | 20–30 |
| Power: 12 V 2 A adapter, 2× LM2596, 18650 + holder | 1 | 550–750 |
| Enclosure / 3D-printed parts, wires, PCB | 1 | 600–1,500 |
| **Total (estimate)** | | **≈ ₹6,200 – ₹10,300** |

The BP module is the biggest and least certain item. Most cheap wrist/arm BP monitors have no serial output, so the team must confirm a module that outputs readings over UART before buying.

Running cost: electricity is negligible. A prepaid SIM for SMS alerts costs a small monthly recharge. The server runs on any laptop or a low-cost cloud VM.

## What it is compared with

| Option | Cost (source) |
|---|---|
| Home nurse visit | ₹500 – ₹1,300 per visit (Chennai market ranges, Samarth Elder Care, 2026) |
| Home nurse, 12-hour shift | ₹24,000 – ₹40,000 per month (same source) |
| MedGate prototype | ≈ ₹6,200 – ₹10,300 one-time (estimate above) |

Source: [Home nursing cost in Chennai 2026, Samarth Elder Care](https://care.samarth.community/medical-care/blog-medical-care-home-nursing-cost-chennai-2026/). Its figures are market ranges for one city, not national prices.

**What we can honestly claim:** the parts cost about as much as 5–20 nurse visits, or less than half a month of a 12-hour nurse. **What we cannot claim:** MedGate does not replace a nurse. It covers one task (safe, verified daily medicine-taking with alerts) so that a family or a visiting nurse is called when needed rather than every day.

**Not yet sourced:** how many hospital admissions missed doses cause in India. There are Indian adherence studies (e.g. on type 2 diabetes) but we could not open them to verify a number. Do not quote a figure on the slide until a team member has read the paper.

## Cost at scale (ideas, not measured)
- A custom PCB replaces the module boards (PCA9685, HX711, RTC and SD breakouts).
- An ESP32-S3 module with enough pins removes the PCA9685 and the wired-OR workaround.
- One shared server can serve many homes, e.g. through a primary health centre or a clinic.
