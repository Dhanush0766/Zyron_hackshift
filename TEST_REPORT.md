# Test report

Run date: 17 Oct 2026. Build environment: Linux container, Python 3.13, FastAPI 0.143.3, Starlette 1.7.0, Uvicorn 0.54.0, Pydantic 2.14.1 (the same pinned versions shipped as Windows wheels in `backend/wheels`), g++ 13.3, Playwright Chromium.

## Summary

| Suite | Command | Result |
|---|---|---|
| Backend: core rules, engine, API, all 24 simulator scenarios | `cd backend && python -m pytest -q` | **140 passed** (102 original + 38 for additions A5–A18) |
| Firmware portable core (native, ASan + UBSan, `-Werror`) | `firmware/test/run_native_tests.sh` | **71 passed**: 26 gate vectors, drug-specific HR rule (5), LSTM parity, BP parser, verification, queue, executor, ENV defaults |
| Browser end-to-end against running server | `python backend/tests/e2e_ui.py http://127.0.0.1:8000` | **Pass**: gate held a dose, a named reviewer approved it in the browser, exactly one command issued; 11 views rendered with no JS errors |
| Full click-through audit (every form and button, both themes, desktop + 390 px phone) | `audit.py` against a live server | **18/18 checks pass, 0 JS errors, no sideways scrolling** |
| New scenarios on a live server + browser | 9 new scenarios run back to back, then Medicines, Devices, Doctor, Alerts, Simulator, dose drawer, report and a 390 px phone view | **Pass**: expected end states, no JS errors, no stuck or faulted doses |
| LSTM gradient check | `python backend/tools/lstm_train.py --gradcheck` | max relative error 3.6e-9 |
| Python ↔ trainer ↔ C++ LSTM parity | in both suites above | max difference 0.00001 min |
| ESP32 firmware compile | `pio run -e esp32dev` | **Not run.** Toolchain downloads are blocked in the build environment |
| Physical hardware | — | **Not run.** No hardware available |
| Windows start-up | `start.bat` | First attempt failed: pip could not resolve pypi.org on the team PC (`getaddrinfo failed`). Fixed by shipping offline wheels. **Re-run on the team PC is pending** |

## LSTM evaluation (SYNTHETIC data only)

Mean absolute error of next-session pickup latency, 40 held-out synthetic users (users split, no leakage), after 5 warm-up sessions:

| Predictor | MAE (min) |
|---|---|
| Static mean (IDF-B's "static threshold baseline" equivalent) | 5.31 |
| Rolling mean of same session, previous 3 days | 3.96 |
| LSTM without gate-failure features | 3.48 |
| LSTM with gate-failure features | **3.16** |

973 parameters, 3.9 KB as float32. **Caveat:** the generator builds in a "recent gate failures → slower pickup" effect. The ~9 % gain from gate-failure features only shows that the model learns an effect we put there. It is not evidence that the effect exists in patients. The same caveat applies to IDF-B §8.5.

## IDF-B behaviour covered by tests

| Behaviour | Test |
|---|---|
| BP missing → no dispense (AND gate) | `test_bp_module_missing_blocks_dispense`, vectors `missing_bp_*` |
| Systolic 172 / SpO₂ 90 → suppressed | `test_high_bp_suppresses_dispense`, vectors |
| Pickup needs weight AND hand | `test_tray_emptied_without_hand_is_not_compliance`, happy path |
| Missed pickup tightens next same-session gate; pickup resets | `test_missed_pickup_tightens_next_same_session_gate_then_resets` |
| Gate failures ≥ 40 % of last 5 halve L3/L4 | `test_escalation_levels_window_and_contraction`, `test_recent_gate_failures_contract_l3_l4` |
| L1→L4 then MISSED on non-collection | `test_uncollected_escalates_then_missed_then_tightens` |
| Doctor/caretaker portal data | `test_portals`, `test_portal_and_schedule_endpoints` |
| Food condition, session, enable/disable | `test_schedule_food_session_and_toggle` |

| Stock: 1 or 2 tablets consumed, refill alert once, refill clears it, empty box never dispenses | `test_stock_decrements_and_refill_alert`, `test_two_tablets_consume_two_and_nothing_dispensed_consumes_none`, `test_empty_compartment_never_dispenses` |
| Storage: hot/humid alerts deduplicated and auto-cleared; invalid sensor not treated as normal | `test_storage_alerts_raise_dedupe_and_auto_resolve`, `test_storage_sensor_invalid_is_not_normal` |
| Day-of-week schedules and days-of-supply | `test_weekday_schedule_skips_other_days`, `test_days_of_supply` |

Earlier coverage remains: duplicate-dispense prevention, idempotent offline replay, IR/weight disagreement, watchdogs, audit tampering, device-key auth, input validation.

## Failures found and fixed

| Problem | Fix |
|---|---|
| Dashboard unreachable on team PC: packages never installed (no DNS to pypi.org); window closed so the error was invisible; half-made `.venv` then skipped install forever | `start.bat` checks imports, installs from bundled wheels offline, logs to `backend\setup.log`, pauses on error, opens the browser |
| `memset` on a non-trivial struct (`-Werror`) | value-initialise |
| Approved doses not executed by the simulator | simulator polls pending commands |
| Blocking HTTP during conveyor motion | network I/O only in safe executor states |
| `String + int8_t` would append a character, not a number | explicit casts |
| LSTM under-trained at 6 epochs (gate features did not help) | 40 epochs |

## Not covered

ESP32 compile and on-device behaviour, sensor calibration, SIM800L delivery, SD logging, BP module wiring, audio amplifier, power backup, multi-device load, dashboard login.
