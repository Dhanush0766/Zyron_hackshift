# Known limitations

## Safety and clinical
- Thresholds (SpO₂ ≥ 94 %, systolic ≤ 160) are the IDF-B example values, not clinical guidance. Real use needs prescriber-set per-patient rules and regulatory review (software that controls medication release may be a regulated medical device in India).
- A normal SpO₂/BP reading does not mean a medicine is safe to take.
- Weight removed + hand detected shows the tray was emptied, not that the tablet was swallowed.
- MAX30102 accuracy is affected by motion, skin tone, nail polish and light. The device's quality value is a proxy from the algorithm's validity flags.
- Demo with dummy, non-ingestible tablets only.

## Medicines and purchasing
- Medicine data is a dated snapshot of an open dataset (MRP as listed then); it is not a live price or availability feed.
- Published tablet weights exist for only 4 compositions, and they come from other brands than the Indian ones in the boxes (amlodipine 10 mg ranges 162–323 mg between brands). Calibrate each box by weighing 10 tablets before trusting the weight.
- Simulator boxes without a published weight use a simulated weight, labelled as such.
- Pharmacy platforms, their stock and prices, and payments are simulated. Nothing is bought; no card or UPI details are collected.

## Logins
- Demo accounts are created with fixed passwords (LOGINS.md); anyone with the source code knows them until they are changed on the Users page.
- The dashboard runs over plain HTTP on the LAN. Passwords cross the network unencrypted; real use needs HTTPS.
- No "forgot password" e-mail: the admin resets passwords. Sessions are stored in the same SQLite file.
- All patients share one database file; separation is enforced by the server on every request (per-patient links), not by separate files.

## Deviations from the IDF-B (deliberate, documented in FEATURE_TRACEABILITY.md)
- A named reviewer can override a gate suppression (IDF-B cancels the cycle).
- Limit switch is the belt end-stop; the load cell confirms tablet arrival.
- Servos on a PCA9685; IR beams wired-OR (pin budget).
- LSTM trained offline; device runs inference only.
- Urgency score uses (1 + rate) instead of rate (IDF-B formula shown alongside).

## Not implemented
- Server-requested SMS/voice escalation to the device; GSM data fallback.
- MAX98357A voice prompts.
- BP module driver beyond a generic line parser.
- Dashboard login/roles (reviewer name is free text).
- HTTPS device link; one shared device key.
- Autonomous offline dispensing (deliberately not allowed).

## Added features
- Storage limits (30 °C, 70 %) are defaults; use the strictest label condition of the medicines actually loaded.
- Stock is only as accurate as the refill count typed in; the load cell confirms deliveries, it does not count the box.

- Drug hold parameters (systolic < 100, pulse < 60, meal) are demonstration values; the prescriber must set real ones. Only 4 drug classes are built in.
- Meal confirmation is the patient's own report (hand over tray); it cannot verify that food was eaten.
- The RFID card proves a card was present, not who is holding it.
- Manipuri voice clips do not exist yet; Hindi text was written by us and should be checked by a native speaker.
- Trend detection uses the last 10 valid readings, not calendar days; limits (2 % SpO₂, 15 mmHg) are demonstration values.
- Component prices in COST_ANALYSIS.md are estimates.

- SOS by holding a hand over the tray can be triggered by accident (e.g. cleaning); it is limited to once a minute and every alert is attributed and resolvable. A dedicated button needs a free pin.
- `start.bat` stops whatever program is listening on port 8000 before starting, assuming it is an old MedGate server.
- Expiry dates are typed in by the caregiver; the box cannot read a strip's date.

## Engineering
- RC522 on GPIO 0 (strapping pin) and the I2S WAV player are untested on hardware.
- ESP32 firmware not compiled for the target yet.
- LSTM trained on synthetic data; its numbers say nothing about patients.
- Firmware event buffer is RAM-only (64 events); SD log is write-only.
- On-device session history for the LSTM is RAM-only and records pickup timing only; the server's history is authoritative.
- SQLite, single process.
