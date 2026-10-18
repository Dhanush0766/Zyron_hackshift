# MedGate logins (demo accounts)

Created automatically the first time the server starts with a new database.
**Change them on the Users & logins page (admin) before showing the system to anyone outside the team.**
This file is kept out of Git on purpose. Share it only with the team.

| Username | Password | Role | Sees | Opens on |
|---|---|---|---|---|
| `admin` | `MedGate@Admin26` | Administrator | everything, both patients, users, audit, simulator | Overview |
| `doctor1` | `Doctor1@MG26` | Doctor | Patient 1 and Patient 2 | Doctor |
| `caretaker1` | `Care1@MG26` | Caretaker | Patient 1 only (SIM-001) | Caretaker |
| `caretaker2` | `Care2@MG26` | Caretaker | Patient 2 only (SIM-002) | Caretaker |
| `patient1` | `Patient1@MG26` | Patient | own screen + HELP (SIM-001) | Patient screen |
| `patient2` | `Patient2@MG26` | Patient | own screen + HELP (SIM-002) | Patient screen |

## What each role can do
- **Admin:** every page, every patient. Adds and edits users, links them to patients, resets passwords, registers devices, runs the simulator, reads the audit log.
- **Doctor:** Inventory & weights (view, calibrate), Medicine analytics, Doctor portal, Review (approve or withhold held doses; recorded under the doctor's own name), Vitals, Doses, Alerts, Medicines (add medicines, drug rules, schedules), the 30-day report.
- **Caretaker:** Caretaker portal with the live camera, 28-day calendar, family update, Inventory & weights (move to box, calibrate, daily restock), Search & buy (simulated purchasing), Medicine analytics, Alerts (resolve), Doses, Medicines (refill, expiry, schedule on/off only). Cannot approve doses or change prescriptions.
- **Patient:** only their own Patient screen and the HELP button.

## Rules the server enforces
- Every request is checked on the server: a caretaker of Patient 1 gets "403 not linked to your account" for any Patient 2 address, even typed by hand.
- 5 wrong passwords for one username → that username is locked for 5 minutes.
- Sessions last 12 hours. Log out ends the session at once. Disabling a user or resetting their password logs them out everywhere.
- Passwords are stored only as salted PBKDF2 hashes. They never appear in the audit log. Logins, failed logins, logouts and user changes do appear in the audit log.
- The ESP32 does not log in; it still uses its device key, unchanged.

## Adding another patient (e.g. Patient 3 on a real ESP32 box)
1. Admin → Devices → register the dispenser (e.g. `ESP32-001`), then set its patient name and card.
2. Admin → Users & logins → create `patient3` (role Patient, tick that dispenser) and `caretaker3` (role Caretaker, tick the same dispenser).
3. Edit `doctor1` and tick the new dispenser too, if the same doctor treats them.
