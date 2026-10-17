# Demo guide (about 8 minutes)

## Start

1. Double-click **`start.bat`**. First run installs from `backend\wheels` (no internet needed) and takes about a minute. The browser opens http://127.0.0.1:8000 by itself. **Keep the black window open.**
2. If it fails, the window stays open with the error; the full log is in `backend\setup.log`.
3. Clean demo: close the window, delete `backend\medgate.db`, start again.
4. For the ESP32 on the same Wi-Fi use `start_lan.bat` (Windows will ask to allow network access).

Demo time scale: **1 IDF-B minute = 6 seconds** (`EscalationPolicy.minute_s`). So L2/L3/L4 come 30/60/90 s after the reminder window, and an uncollected tablet becomes MISSED about 2.5 minutes after delivery.

## Fastest demo

Simulator → **▶ Run full demo** (8 steps with narration, about 1–2 minutes). Then show: **Patient screen** (big text, voice, HELP), **Review** (approve with a name), **Alerts** (SOS, wrong card, early warnings), **Caretaker** (28-day calendar, family update), **Doctor → Open report** (print to PDF).

If the dashboard shows a red "older MedGate server is still running" bar: close every black server window and run `start.bat` again.

## Script

| # | Do | Say |
|---|---|---|
| 1 | Simulator → **normal** | SpO₂ 97 AND BP 122/78 pass → servo → IR beam → conveyor → tray weight +0.5 g → weight removed AND hand detected → COLLECTED. Open the dose: lifecycle, readings, thresholds, LSTM plan |
| 2 | Simulator → **abnormal_vitals** | Systolic 172 > 160 → servo suppressed, health alert straight to L3. Doctor tab shows readings + thresholds |
| 3 | Simulator → **missing_bp** | BP module returned nothing → the AND gate cannot pass on SpO₂ alone |
| 4 | Review → name → **Withhold** (or Approve) | Gate never releases by itself; override is attributed and logged |
| 5 | Simulator → **removed_no_hand** | Weight dropped but no hand → compliance AND gate fails → REVIEW_REQUIRED |
| 6 | Simulator → **uncollected**, wait ~2.5 min (do steps 7–8 meanwhile) | Alerts tab: L1 → L2 → L3 SMS → L4 call → MISSED. Medicines tab: schedule shows "+1 step" tightening |
| 7 | Simulator → **sensor_disagreement** | Beam broke, no weight → not counted, never auto re-dispensed |
| 8 | Simulator → **offline_replay** | Buffered events replayed twice → `DUPLICATE_IGNORED` |
| 9 | After step 6: Simulator → **borderline** | SpO₂ 94.5 passes normally but is HELD now, because the missed pickup tightened the threshold to 95 |
| 10 | Caretaker tab | Urgency score and why we changed the IDF formula |
| 10b | Simulator → **low_stock**, then Medicines → Refill | Box 1 down to 0 → refill alert; next dose refused as EMPTY_COMPARTMENT, nothing dispensed |
| 10c | Simulator → **hot_storage**, then **storage_ok** | 34.5 °C / 78 % → storage alerts; back to normal → alerts clear automatically |
| 10d | Simulator → **bp_drug_low_bp**, then **beta_blocker_slow_pulse** | Same gate idea, but each medicine has its own rule: a BP tablet is held when BP is LOW (95/62), a beta-blocker when the pulse is slow (52). The general IDF-B gate would have let both through |
| 10e | Simulator → **diabetes_meal_ok**, then **diabetes_no_meal** | Device asks "have you eaten?"; hand over tray confirms. "Not eaten" → held (hypoglycaemia risk) |
| 10f | Simulator → **no_card**, then **wrong_patient** | Card check: no card → asked to tap it; someone else's card → PATIENT_MISMATCH alert, nothing released |
| 10g | Simulator → **double_dose** | Second dose of the same BP tablet right after the first → held by double-dose protection |
| 10h | Simulator → **trend_decline**, then Doctor tab → **Open report** | Every reading passed, but SpO₂ drifted down and BP up → early-warning alerts. The report is a printable PDF for the clinic visit |
| 10i | Devices → set voice language to Hindi, run **normal**, open the dose → ▶ Play | Prompts in the patient's language (device plays recorded clips) |
| 10j | Simulator → **expired_medicine**, then Medicines → set a new expiry | Expired strip never dispensed; alert clears when a fresh date is saved |
| 10k | Patient screen → tick "Speak new messages" → press **HELP** | SOS to family and doctor; the box answers in the patient's language |
| 11 | Audit tab (optional tamper: `backend\.venv\Scripts\python backend\tools\tamper_demo.py 5`) | Chain breaks at record 5 |

## Questions to expect

- *"Is 94 % / 160 mmHg safe?"* They are the IDF-B example values. Real thresholds are set per patient by the prescriber.
- *"Does the LSTM work?"* It runs on server and device and matches the trainer exactly. It was trained on synthetic data, so we claim the pipeline works, not clinical accuracy (TEST_REPORT.md).
- *"Does it prove the patient swallowed the tablet?"* No. It proves the tablet left the tray while a hand was present.
- *"Are the drug hold values clinical?"* No. They mirror common nursing hold parameters and are examples. The prescriber sets them per patient.
- *"Is it affordable?"* About ₹6–10 K in parts (our estimate, COST_ANALYSIS.md), compared with ₹24–40 K a month for a 12-hour home nurse (one market source). It does not replace a nurse; it covers safe daily medicine-taking.

## Fallback

Everything runs on the simulator. If hardware fails on stage, say so and continue.
