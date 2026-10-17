# Hardware setup (IDF-B §7.8 parts)

**Status: untested on hardware. Firmware not yet compiled for ESP32.** Check every item against your modules before applying power.

## Pin budget: changes from the IDF-B

The IDF-B parts list connected directly needs about 33 GPIOs. An ESP32-WROOM-32 has about 25 usable, and some of those are boot-strapping pins. This build makes these changes:

1. **PCA9685 16-channel PWM board** (I2C, address 0x40) drives the six servos. Add it to the parts list (~₹250).
2. **Six TCRT5000 beam outputs wired-OR into one GPIO** (open-collector outputs tied together, or one diode each). Only one compartment opens at a time, so one input is enough.
3. **AHT20 temperature/humidity sensor** (I2C 0x38, ~₹150) added for storage monitoring. It shares the I2C bus, so it needs no extra pin (a DHT22 would need one).
4. **RC522 RFID reader** (patient card, ~₹120) shares the SD card's SPI bus (SCK 18, MISO 19, MOSI 23). CS goes on GPIO 0, the only free pin. GPIO 0 is a boot-strapping pin that must be HIGH at boot; a chip-select idles HIGH, so this works, but if the board will not boot, unplug the RC522 CS wire and re-check. Tie RC522 RST to 3V3.

## Wiring (proposed)

| Signal | GPIO | Notes |
|---|---|---|
| I2C SDA / SCL | 21 / 22 | MAX30102 0x57, DS3231 0x68, OLED 0x3C, PCA9685 0x40, AHT20 0x38 (storage temp/humidity, mount inside the medicine box) |
| Servos Box 1–6 | PCA9685 ch 0–5 | servo V+ from separate 5 V ≥ 3 A supply |
| Micro SD SCK / MISO / MOSI / CS | 18 / 19 / 23 / 5 | `/medgate_log.csv` |
| L298N IN1 / IN2 / ENA | 25 / 26 / 27 | 12 V motor supply on L298N |
| HX711 DT / SCK | 34 / 4 | 1 kg load cell under collector tray |
| IR beams (wired-OR ×6) | 35 | external 10 kΩ pull-up; LOW = beam broken |
| IR obstacle sensor (tray) | 36 | external pull-up; LOW = hand present |
| Limit switch (belt end-stop) | 39 | external pull-up; stops the motor. It does not detect the tablet: the load cell does |
| Buzzer | 32 | via transistor if > 20 mA |
| SIM800L RX / TX | 16 / 17 | own 3.7–4.2 V, 2 A burst supply; 2G only: confirm local 2G coverage |
| BP module RX / TX | 13 / 14 | UART1, 9600 baud default; parser accepts "SYS DIA [PULSE]" lines; check your module's output |
| MAX98357A BCLK / LRC / DIN | 33 / 2 / 15 | voice clips `/voice/<lang>/<KEY>.wav` (16-bit PCM mono) from SD, see VOICE_CLIPS.md |
| RC522 SDA(CS) | 0 | on VSPI with SD; RST → 3V3; 3.3 V only |

## Power

| Load | Supply |
|---|---|
| ESP32 + 3.3 V sensors | 5 V regulated (LM2596 from 12 V, or MT3608 from 18650 per IDF-B) |
| Servos via PCA9685 | separate 5 V ≥ 3 A |
| Conveyor motor | 12 V via L298N |
| SIM800L | 18650 cell directly (3.7–4.2 V) or dedicated buck; never 5 V |

## Bring-up order

1. USB only: serial monitor shows boot; OLED status; I2C scan finds 0x3C, 0x40, 0x57, 0x68.
2. SD card: `/medgate_log.csv` gets a BOOT line.
3. BP module: send a reading and check the serial log shows a "BP" line. Adapt `parseBpLine` if not.
4. Calibrate HX711 (`HX711_SCALE`): tare, place a known mass. Weigh one dummy tablet per box and enter it in the dashboard (Medicines → tablet g).
5. Servos one box at a time with dummy tablets; tune `SERVO_OPEN_US` / `SERVO_CLOSED_US`.
6. Belt: limit switch stops motor; pull its wire and expect `jam` after 15 s.
7. Drop a tablet by hand through each beam; confirm GPIO 35 goes LOW long enough for the 50 ms loop. If not, use an interrupt latch.
8. Tap a patient card: the OLED shows `card <UID>`. Enter that UID in Dashboard → Devices → Patient. Copy voice clips to the SD card.
9. Full cycle from the dashboard (DEMO_GUIDE.md, ESP32 section of README).

## Build

```
cd firmware
copy include\secrets.example.h include\secrets.h   (then edit)
pio run -e esp32dev
pio run -e esp32dev -t upload
pio device monitor
```
Start the server with `start_lan.bat`; set `MEDGATE_DEVICE_KEY` to the same value as `DEVICE_KEY`. Register the device in Dashboard → Devices.
