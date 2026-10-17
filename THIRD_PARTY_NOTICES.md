# Third-party notices

No third-party source code is copied into this repository. The components below are installed by package managers at build or run time and remain under their own licenses.

## Backend (pip, `backend/requirements.txt`)
| Package | License (as published by the project) |
|---|---|
| FastAPI | MIT |
| Uvicorn | BSD-3-Clause |
| Pydantic | MIT |
| HTTPX | BSD-3-Clause |
| pytest | MIT |

## Firmware (PlatformIO `lib_deps`)
| Library | License | Verified |
|---|---|---|
| bogde/HX711 | MIT | yes, GitHub API |
| adafruit/RTClib | MIT | yes, GitHub API |
| Adafruit PWM Servo Driver Library (PCA9685) | BSD | no, from project documentation memory |
| Adafruit AHTX0 (AHT20) | BSD | no, from project documentation memory |
| SparkFun MAX3010x (incl. Maxim `spo2_algorithm`) | check repository LICENSE; Maxim algorithm files carry their own notice | **no**, fetch failed |
| Adafruit SSD1306, Adafruit GFX | BSD | no, from project documentation memory |
| ArduinoJson | MIT | no, from project documentation memory |
| miguelbalboa/MFRC522 (RC522 RFID) | Unlicense (public domain) | no, from memory |
| Arduino-ESP32 core (via espressif32 platform) | LGPL-2.1 | no |

Confirm the unverified entries before distributing a firmware binary.

## Development-only tools
| Tool | Use |
|---|---|
| NumPy | `backend/tools/lstm_train.py` (training only; not needed to run the app) |
| Playwright | `backend/tests/e2e_ui.py` |

The Windows wheels in `backend/wheels` are unmodified copies of the PyPI packages listed above plus their dependencies (anyio, starlette, click, colorama, h11, idna, certifi, httpcore, annotated-types, typing-extensions, typing-inspection, opentelemetry-api, pluggy, iniconfig, packaging, pygments, exceptiongroup, tomli), each under its own license.

## Design references (no code used)
Repositories listed in RESEARCH_AND_REUSE.md were read for design context only.
