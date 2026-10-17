// Pin map and timing for the IDF-B §7.8 parts list. PROPOSED pins; verify against your wiring.
//
// Pin budget: the IDF-B parts (6 servos, 6 IR beams, I2C x4 devices, HX711, L298N, limit switch,
// IR proximity, buzzer, SIM800L, UART BP module, I2S audio amp, Micro SD) need ~33 GPIOs if
// wired directly; an ESP32-WROOM-32 has ~25 usable. Two changes make it fit:
//   1. The 6 servos are driven by a PCA9685 16-channel PWM board on the shared I2C bus.
//   2. The 6 TCRT5000 beam outputs are wired-OR into ONE input (open-collector outputs, or a
//      diode each). Only one compartment opens at a time, so the firmware knows which beam it is.
// Strapping pins 0, 2, 12, 15 are left free or used only as outputs after boot.
#pragma once

// I2C bus: MAX30102 (0x57), DS3231 (0x68), SSD1306 OLED (0x3C), PCA9685 (0x40), AHT20 (0x38)
#define PIN_SDA 21
#define PIN_SCL 22
#define PCA9685_ADDR 0x40
#define SERVO_CHANNEL_FIRST 0      // compartment N uses PCA9685 channel (N - 1)
#define SERVO_CLOSED_US 600
#define SERVO_OPEN_US 1500
// Servos are powered from a separate 5 V supply (>= 3 A) into the PCA9685 V+ terminal.

// Micro SD (VSPI): gate decisions and events are appended to /medgate_log.csv (IDF-B §7.2)
#define PIN_SD_SCK 18
#define PIN_SD_MISO 19
#define PIN_SD_MOSI 23
#define PIN_SD_CS 5

// L298N conveyor motor (12 V gear motor on the L298N's own supply)
#define PIN_MOTOR_IN1 25
#define PIN_MOTOR_IN2 26
#define PIN_MOTOR_EN 27
#define MOTOR_DUTY 200  // 0-255

// HX711 tray load cell (1 kg)
#define PIN_HX711_DT 34   // input-only pin
#define PIN_HX711_SCK 4
#define HX711_SCALE 2280.0f  // PLACEHOLDER: calibrate with a known mass (see HARDWARE_SETUP.md)

// Digital sensors. GPIO 34-39 have NO internal pull-ups: fit external 10k pull-ups.
#define PIN_IR_BEAM 35        // wired-OR of all 6 TCRT5000 outputs; LOW when any beam interrupted
#define PIN_PROXIMITY 36      // IR obstacle sensor at tray; LOW when hand present (module dependent)
#define PIN_LIMIT_SWITCH 39   // belt/tray end-stop; LOW when pressed
#define PIN_BUZZER 32

// SIM800L on UART2. Needs its own 3.7-4.2 V supply able to deliver 2 A bursts.
#define PIN_SIM_RX 16
#define PIN_SIM_TX 17

// UART blood-pressure module on UART1 (remapped). Line format differs between modules:
// bpParseLine() accepts any line whose first numbers are SYS DIA [PULSE]. Adapt it to your module.
#define PIN_BP_RX 13
#define PIN_BP_TX 14
#define BP_BAUD 9600
#define BP_MAX_AGE_S 120      // a BP reading older than this is not combined with SpO2

// MAX98357A I2S amplifier: plays recorded voice clips /voice/<lang>/<KEY>.wav from the SD card
// (16-bit PCM mono WAV). Clips are recorded by the team; see backend/medgate/core/prompts.py.
#define PIN_I2S_BCLK 33
#define PIN_I2S_LRC 2
#define PIN_I2S_DIN 15

// RC522 RFID reader (patient card), on the SD card's VSPI bus (SCK 18 / MISO 19 / MOSI 23).
// Only free pin left is GPIO 0 (strapping: must be HIGH at boot). A chip-select idles HIGH, and
// the RC522 module has its own pull-up, so boot is unaffected. Tie RC522 RST to 3V3.
#define PIN_RFID_CS 0
#define CARD_VALID_MS 180000      // a tapped card counts for readings taken within 3 min
#define MEAL_VALID_MS 1800000     // "I have eaten" confirmation counts for 30 min
#define MEAL_HOLD_MS 2000         // hand held over the tray this long = meal confirmed
#define SOS_HOLD_MS 5000          // hand held over the EMPTY tray this long while idle = HELP (SOS)

// Timing (ms)
#define POLL_COMMANDS_MS 2000
#define UPLOAD_EVENTS_MS 1000
#define HEARTBEAT_MS 30000
#define VITALS_WINDOW_SAMPLES 100
#define FINGER_IR_THRESHOLD 50000  // IR level indicating a finger on the MAX30102
#define OFFLINE_SMS_AFTER_MS 120000
#define ENV_INTERVAL_MS 60000      // AHT20 storage temperature/humidity reading

// Fallback if a command does not carry the medication's tablet weight.
#define UNIT_WEIGHT_G 0.5f
#define WEIGHT_TOL_G 0.15f
