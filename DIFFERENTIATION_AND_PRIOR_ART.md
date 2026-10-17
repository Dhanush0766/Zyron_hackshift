# Differentiation and prior art

**Short version:** the IDF-B's strongest point is that a vital-sign check *gates* the physical release of the tablet. That is real code here, and it is tested. Its novelty claims ("no prior art discloses…") are the team's own assertions from a patent search we have not reproduced. Treat them as claims to verify, not facts to present.

## Prior art the IDF-B itself lists (§3)

| Reference | What it does (per IDF-B) | Gap the IDF-B claims |
|---|---|---|
| US20140207278A1 | Monitors BP/HR before/after dispensing; alerts supervisor if thresholds exceeded | Advisory only: does not block dispensing |
| US11160730 (2021) | Vitals + dispensing + cloud compliance tracking | Vitals logged, no conditional release |
| US20080077440A1 | Implantable/IV dosing adjusted by physiology | Not oral home dispensing |
| WO2013025520A2 | Multi-compartment servo lids, alerts, web monitoring | No health gating, no conveyor |
| US20100100237A1 | Dual conveyors for bulk pill counting | Pharmacy scale, not home |
| US4573606A (1986) | Rotating multi-compartment dispenser with photoelectric tray detector | Fixed schedule, no physiology |

We did not read these patents. **Action for the team:** read US20140207278A1's claims in full. It is the IDF-B's own "closest prior art". If any claim lets out-of-range vitals stop or delay dispensing, the core novelty claim weakens.

## Additional prior art found by us

| Source | Overlap with IDF-B | Not covered by it |
|---|---|---|
| Dangre et al., *Smart Portable Medico – AI-Powered Medicine Reminder System*, JAAFR vol. 4 issue 4, Apr 2026 (uploaded by the team) | ESP32 + DS3231 multi-compartment box; IR intake detection per compartment; pre-alert → main alert → missed-dose escalation with 3 retry cycles; web dashboard with dose log. Its abstract also claims ML for missed-dose prediction and SMS reminders | No vital-sign gate, no conveyor, no load cell, no LSTM described in the body |
| IJERT 2026, *Smart Patient Monitoring and Assistive Pill Dispenser* | Measures HR, SpO₂, ECG, temperature alongside a dispenser | Dispenses on schedule; vitals do not gate release (per the paper's text) |
| GitHub: Shaztech/Pilldispenser, MelvinMo/DoseBuddy, AIoT-Medicine-Reminder and others (see RESEARCH_AND_REUSE.md) | Servo compartments, reminders, single "taken" sensors, caregiver alerts | No vitals gate, no two-sensor agreement |

**Consequence for the pitch:** multi-level reminder escalation and "ML predicts missed doses" are already published (JAAFR). What remains distinctive is the *physiological conditioning*: escalation windows that shorten after gate failures, and gate thresholds that tighten after missed pickups.

## What this prototype demonstrates (verifiable)

1. **Gate = Boolean AND, fail-safe.** Missing BP, missing SpO₂, implausible, stale or poor-contact readings never pass. 26 vectors run in both Python and C++.
2. **Closed loop both ways.** Gate failures raise the LSTM's gate-failure feature and contract L3/L4. Missed pickups tighten the next same-session gate. Each direction has its own test.
3. **Exactly-once dispensing** across FSM, database constraint and the device's NVS guard.
4. **Two-sensor delivery + two-sensor pickup** with no automatic re-dispense on disagreement.
5. **One model, three runtimes:** NumPy trainer, pure-Python server, C++ device agree within 0.00001 min on shared vectors.
6. **Tamper-evident audit** (SHA-256 chain).

## Problems found in the IDF-B text (fix before filing or presenting)

| Issue | Where | Suggested fix |
|---|---|---|
| A mechanical limit switch cannot detect a ~0.5 g tablet arriving | §6 Stage 3, §7.3, Claim 2 | Say the switch is the belt end-stop; arrival is confirmed by the load cell (as built here) |
| Urgency score = rate × missed × level is 0 whenever gate-failure rate is 0, however many doses are missed | §7.7 | Use (1 + rate) × missed × level, or a weighted sum |
| "Halved if failure rate > 40 %" vs example "2 of last 5" (= 40 %, not > 40 %) | §6 vs §4.3 | Use "≥ 40 %" |
| Parts list needs ~33 GPIOs; ESP32-WROOM-32 has ~25 usable | §7.8 | Add a PCA9685 servo driver and wire-OR the six IR beams |
| "Incremental weight updates on ESP32" for an LSTM is not demonstrated and is hard to do reliably | §7.5, Claim 4 | Claim on-device *inference*; train offline or on the server |
| §8.5 LSTM ablation used a synthetic dataset | §8.5 | A synthetic set in which the effect was built in cannot show that the co-variate helps in real patients; say so |
| "Clinically safe thresholds" | §4.3, Claim 1 | Thresholds must be prescriber-set per patient; 94 % / 160 mmHg are examples |
| 30 human test subjects for SpO₂/BP comparison | §8.1 | Confirm ethics approval/consent if real people were measured |

## Not established

- Global novelty of any claim (no independent patent search done by us).
- Clinical benefit of gating.
- Any IDF-B §8 result.
