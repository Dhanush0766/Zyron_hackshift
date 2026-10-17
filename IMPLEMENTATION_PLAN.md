# Implementation plan and working process

Last updated 17 Oct 2026.

## How to work on this project (process)

1. **Source of truth for requirements:** IDF-B (Doc. 02-IPR-R003). Every change cites its IDF-B section in FEATURE_TRACEABILITY.md.
2. **Safety logic lives in one place per runtime:** `backend/medgate/core/*.py` and `firmware/lib/medgate_core`. Anything that changes a gate rule changes `shared/gate_vectors.csv` first; both test suites must pass.
3. **Change → test → commit:** `cd backend && python -m pytest -q`, `firmware/test/run_native_tests.sh`. No feature is "done" without a test listed in TEST_REPORT.md.
4. **LSTM changes:** edit `tools/lstm_train.py`, run it (needs numpy), commit the regenerated weights, vectors and `shared/lstm_eval.json` together. The parity tests catch a stale export.
5. **Hardware:** follow HARDWARE_SETUP.md bring-up order; record each verified step in TEST_REPORT.md under "Physically verified".
6. **Claims:** anything shown to judges must be in TEST_REPORT.md or marked as an IDF-B claim we have not reproduced.

## Done
- [x] IDF-B AND gate, example thresholds, tightening feedback, AND pickup gate
- [x] Escalation window + contraction; sessions, food condition, schedule toggle
- [x] LSTM: synthetic data, NumPy training with gradient check, server and C++ inference with parity tests
- [x] Doctor and caretaker portals
- [x] Firmware: PCA9685 servos, BP UART parser, SD log, on-device LSTM inference
- [x] Offline Windows install (`backend/wheels`) and fixed `start.bat`

## Next, in priority order
1. **Confirm `start.bat` runs on the team PC.** Accept: dashboard loads, `python -m pytest -q` passes in `backend\.venv`.
2. **Compile firmware** (`pio run`). Accept: zero errors; fixes noted in TEST_REPORT.md.
3. **Hardware bring-up** (HARDWARE_SETUP.md). Accept: one dummy tablet goes schedule → COLLECTED with both sensor pairs agreeing; one forced disagreement lands in REVIEW_REQUIRED.
4. Decide with the team: keep or remove the reviewer override (IDF-B cancels outright).
5. Read US20140207278A1 claims; update DIFFERENTIATION_AND_PRIOR_ART.md.
6. Server → device `NOTIFY` command so L3/L4 actually send SMS/call through the SIM800L.
7. MAX98357A audio prompts; GSM data fallback.
8. Real adherence data (with consent) before any LSTM accuracy claim.
