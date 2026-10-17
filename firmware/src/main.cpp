// MedGate ESP32 firmware: hardware glue around the portable core (lib/medgate_core).
// Single non-blocking loop; no delay() in the control path.
//
// Responsibilities on the device:
//   * measure vitals (MAX30102 [+ optional BP module]) and report them
//   * pre-check readings locally so the patient gets instant "retake" feedback
//   * execute DISPENSE commands from the server exactly once (NVS-backed guard)
//   * verify ejection (IR beam) and arrival (tray load cell); report both
//   * buffer every event while offline and replay it (server de-duplicates by seq)
//   * if offline for a long time while a fault is active, send one SMS via SIM800L
//
// The device never dispenses on its own initiative: dispensing requires a server
// command, which is only issued after the health gate (or a human reviewer) allows it.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <RTClib.h>
#include <WiFi.h>
#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include <Adafruit_PWMServoDriver.h>
#include <Adafruit_SSD1306.h>
#include <HX711.h>
#include <SD.h>
#include <SPI.h>
#include <MAX30105.h>
#include <spo2_algorithm.h>
#include <MFRC522.h>
#include <driver/i2s.h>

#include "config.h"
#include "medgate_core.h"
#include "lstm_weights.h"
#if __has_include("secrets.h")
#include "secrets.h"
#else
#include "secrets.example.h"
#warning "Using secrets.example.h - copy it to secrets.h and fill in your values"
#endif

using namespace mg;

static RTC_DS3231 rtc;
static Adafruit_SSD1306 oled(128, 64, &Wire, -1);
static HX711 scale;
static MAX30105 pulse;
static Preferences nvs;
static HardwareSerial simSerial(2);
static HardwareSerial bpSerial(1);
static Adafruit_PWMServoDriver pca(PCA9685_ADDR);
static Adafruit_AHTX0 aht;   // storage temperature/humidity, I2C 0x38
static MFRC522 rfid(PIN_RFID_CS, MFRC522::UNUSED_PIN);   // patient card
static bool rfidOk = false, i2sOk = false;
static char lastTag[24] = "";
static uint32_t lastTagAt = 0, mealAt = 0, handSince = 0;
static bool awaitingMeal = false;
static char pendingClip[48] = "";

static EventQueue *queue_;
static DispenseExecutor *exec_;

static bool rtcOk = false, oledOk = false, pulseOk = false, sdOk = false, ahtOk = false;
static float bpSys = NAN, bpDia = NAN, bpPulse = NAN;
static double bpAt = 0;
static float predictedLatencyMin = NAN;   // on-device LSTM output (IDF-B §7.5)
static uint32_t lastPoll = 0, lastUpload = 0, lastBeat = 0, offlineSince = 0;
static bool faultSmsSent = false;
static float trayG = NAN;
static bool trayValid = false;
static char statusLine[32] = "boot";
static int gateAttempts = 0;

// ------------------------------------------------------------------ helpers
static double epochNow() {
  if (rtcOk) return (double)rtc.now().unixtime();
  return millis() / 1000.0;  // no RTC: readings will be rejected as STALE/INVALID by the server
}

static void servoWriteUs(int idx, int us) {
  // PCA9685 at 50 Hz: compartment idx (0-5) on channel SERVO_CHANNEL_FIRST + idx.
  pca.writeMicroseconds(SERVO_CHANNEL_FIRST + idx, us);
}

// ------------------------------------------------------------------ SD audit log (IDF-B §7.2)
static void sdLog(const char *kind, const String &detail) {
  if (!sdOk) return;
  File f = SD.open("/medgate_log.csv", FILE_APPEND);
  if (!f) return;
  f.printf("%.0f,%s,%s\n", epochNow(), kind, detail.c_str());
  f.close();
}

// ------------------------------------------------------------------ UART BP module
// Accepts a line whose first integers are SYS DIA [PULSE], e.g. "120,80,72" or "S120 D80 P72".
static bool bpParseLine(const String &line) {
  int sys, dia, pulse;
  if (!parseBpLine(line.c_str(), &sys, &dia, &pulse)) return false;
  bpSys = sys; bpDia = dia; bpPulse = pulse >= 0 ? pulse : NAN;
  bpAt = epochNow();
  sdLog("BP", String(sys) + "/" + String(dia));
  return true;
}

static void pollBp() {
  static String line;
  while (bpSerial.available()) {
    char ch = (char)bpSerial.read();
    if (ch == '\n' || ch == '\r') { if (line.length()) bpParseLine(line); line = ""; }
    else if (line.length() < 64) line += ch;
  }
}

// ------------------------------------------------------------------ on-device compliance history
struct Session { float latencyMin; bool missed, gateFail; uint8_t hour; bool weekend; };
static Session hist[20];
static int histN = 0;

static void recordSession(float latencyMin, bool missed, bool gateFail) {
  DateTime now = rtcOk ? rtc.now() : DateTime((uint32_t)0);
  if (histN == 20) { memmove(hist, hist + 1, sizeof(Session) * 19); histN = 19; }
  hist[histN++] = {latencyMin, missed, gateFail, (uint8_t)now.hour(), now.dayOfTheWeek() == 0 || now.dayOfTheWeek() == 6};
  if (histN < 3) return;
  // Same feature layout as backend/medgate/core/compliance.py features()
  static float seq[20 * MG_LSTM_IN];
  float prev = 5.0f;
  for (int t = 0; t < histN; t++) {
    const Session &s = hist[t];
    uint8_t nh = t + 1 < histN ? hist[t + 1].hour : (uint8_t)((now.hour() + 6) % 24);
    bool nw = t + 1 < histN ? hist[t + 1].weekend : s.weekend;
    float lat = s.missed ? 30.0f : (isnan(s.latencyMin) ? prev : fminf(30.0f, s.latencyMin));
    prev = lat;
    int lo = t - 4 < 0 ? 0 : t - 4, cnt = 0, fails = 0;
    for (int k = lo; k <= t; k++) { cnt++; fails += hist[k].gateFail; }
    float *x = &seq[t * MG_LSTM_IN];
    x[0] = lat / 30.0f; x[1] = s.missed; x[2] = s.gateFail; x[3] = (float)fails / cnt;
    x[4] = sinf(2 * PI * nh / 24); x[5] = cosf(2 * PI * nh / 24); x[6] = nw;
  }
  predictedLatencyMin = lstmPredict(seq, histN);
  sdLog("LSTM", String(predictedLatencyMin, 1));
}

static void motor(bool on) {
  digitalWrite(PIN_MOTOR_IN1, on ? HIGH : LOW);
  digitalWrite(PIN_MOTOR_IN2, LOW);
  ledcWrite(7, on ? MOTOR_DUTY : 0);
}

static void showStatus() {
  if (!oledOk) return;
  oled.clearDisplay();
  oled.setTextSize(1);
  oled.setTextColor(SSD1306_WHITE);
  oled.setCursor(0, 0);
  oled.printf("MedGate %s\n", WiFi.isConnected() ? "online" : "OFFLINE");
  oled.printf("exec: %s\n", toString(exec_->state()));
  oled.printf("queue: %u drop:%u\n", (unsigned)queue_->size(), (unsigned)queue_->dropped());
  oled.printf("tray: %.2f g\n", trayValid ? trayG : 0.0f);
  oled.println(statusLine);
  oled.display();
}

// ------------------------------------------------------------------ vitals
// Collect 100 samples (~4 s at the library default rate) only while a finger is present.
static uint32_t irBuf[VITALS_WINDOW_SAMPLES], redBuf[VITALS_WINDOW_SAMPLES];
static int nSamples = 0;

static void sampleVitals() {
  if (!pulseOk) return;
  pulse.check();
  while (pulse.available()) {
    uint32_t ir = pulse.getFIFOIR(), red = pulse.getFIFORed();
    pulse.nextSample();
    if (ir < FINGER_IR_THRESHOLD) { nSamples = 0; continue; }  // no finger: restart window
    irBuf[nSamples] = ir;
    redBuf[nSamples] = red;
    if (++nSamples < VITALS_WINDOW_SAMPLES) continue;
    nSamples = 0;

    int32_t spo2, hr;
    int8_t spo2Ok, hrOk;
    maxim_heart_rate_and_oxygen_saturation(irBuf, VITALS_WINDOW_SAMPLES, redBuf, &spo2, &spo2Ok, &hr, &hrOk);
    Vitals v;
    v.present = true;
    v.has_time = rtcOk;
    v.measured_at = epochNow();
    v.has_hr = hrOk; v.hr = hr;
    v.has_spo2 = spo2Ok; v.spo2 = spo2;
    // IDF-B AND gate: combine with the most recent BP reading if it is fresh enough.
    if (!isnan(bpSys) && epochNow() - bpAt <= BP_MAX_AGE_S) { v.has_bp = true; v.bp_sys = bpSys; v.bp_dia = bpDia; }
    // Quality proxy: both algorithm validity flags + finger contact held for the whole window.
    v.has_quality = true;
    v.quality = (hrOk && spo2Ok) ? 0.9f : 0.3f;
    // Patient card and meal confirmation travel with the reading; the SERVER decides (identity
    // check, drug-specific rules). The device never dispenses on its own.
    if (lastTag[0] && millis() - lastTagAt < CARD_VALID_MS) strncpy(v.patient_tag, lastTag, sizeof v.patient_tag - 1);
    if (mealAt && millis() - mealAt < MEAL_VALID_MS) v.meal = 1;

    ReadingStatus st;
    GateDecision d = healthGate(v, epochNow(), DEMO_RULES, DEMO_RULES_N, gateAttempts, 3, &st);
    snprintf(statusLine, sizeof statusLine, "%s", st == ReadingStatus::VALID ? "reading sent" : "retake: keep still");
    if (d == GateDecision::RETAKE) gateAttempts++;
    else gateAttempts = 0;
    // Always report: the server makes the authoritative gate decision and logs it.
    Event &e = queue_->push(EvType::VITALS, epochNow());
    e.vitals = v;
    sdLog("GATE_PRECHECK", String(toString(st)) + "," + toString(d) + ",spo2=" + String(v.spo2, 0) +
                               ",bp=" + (v.has_bp ? String(v.bp_sys, 0) + "/" + String(v.bp_dia, 0) : String("none")));
  }
}

// ------------------------------------------------------------------ networking
static String eventsJson(size_t n) {
  JsonDocument doc;
  JsonArray arr = doc["events"].to<JsonArray>();
  static const char *names[] = {"HEARTBEAT", "VITALS", "CMD_ACK", "EJECT", "CONVEYOR", "COLLECT", "FAULT", "ENV", "SOS"};
  for (size_t i = 0; i < n; i++) {
    const Event &e = queue_->at(i);
    JsonObject o = arr.add<JsonObject>();
    o["seq"] = e.seq;
    o["type"] = names[(int)e.type];
    o["ts"] = e.ts;
    JsonObject d = o["data"].to<JsonObject>();
    if (e.dose_id >= 0) d["dose_id"] = e.dose_id;
    switch (e.type) {
      case EvType::HEARTBEAT:
        d["fw"] = "medgate-0.3.0"; d["rssi"] = WiFi.RSSI(); d["queue_dropped"] = queue_->dropped();
        d["rtc_ok"] = rtcOk; d["pulse_ok"] = pulseOk; d["sd_ok"] = sdOk; d["rfid_ok"] = rfidOk; d["audio_ok"] = i2sOk; d["exec_state"] = toString(exec_->state());
        if (!isnan(predictedLatencyMin)) d["lstm_predicted_latency_min"] = predictedLatencyMin;
        break;
      case EvType::VITALS:
        if (e.vitals.has_hr) d["hr"] = e.vitals.hr;
        if (e.vitals.has_spo2) d["spo2"] = e.vitals.spo2;
        if (e.vitals.has_bp) { d["bp_sys"] = e.vitals.bp_sys; d["bp_dia"] = e.vitals.bp_dia; }
        if (e.vitals.has_quality) d["quality"] = e.vitals.quality;
        if (e.vitals.has_time) d["measured_at"] = e.vitals.measured_at;
        if (e.vitals.patient_tag[0]) d["patient_tag"] = e.vitals.patient_tag;
        if (e.vitals.meal == 1) d["meal_taken"] = true;
        break;
      case EvType::CMD_ACK: d["command_id"] = e.command_id; break;
      case EvType::EJECT: d["ir_beam"] = e.flag_a; break;
      case EvType::CONVEYOR:
        d["status"] = e.text;
        if (!isnan(e.value)) d["tray_delta_g"] = e.value;
        break;
      case EvType::COLLECT: d["weight_removed"] = e.flag_a; d["presence"] = e.flag_b; break;
      case EvType::FAULT: d["code"] = e.text; break;
      case EvType::SOS: d["note"] = "hand held over tray"; break;
      case EvType::ENV:
        if (!isnan(e.value)) d["temp_c"] = e.value;
        if (!isnan(e.value2)) d["humidity"] = e.value2;
        d["measured_at"] = e.ts;
        break;
    }
  }
  String s;
  serializeJson(doc, s);
  return s;
}

static void syncClock(JsonDocument &doc) {
  // Keep the RTC within 5 s of the server so readings are not rejected as stale.
  if (!rtcOk || doc["server_time"].isNull()) return;
  uint32_t srv = (uint32_t)doc["server_time"].as<double>();
  if (abs((long)srv - (long)rtc.now().unixtime()) > 5) rtc.adjust(DateTime(srv));
}

static void uploadEvents() {
  if (!WiFi.isConnected() || queue_->size() == 0) return;
  size_t n = queue_->size() > 20 ? 20 : queue_->size();
  HTTPClient http;
  http.begin(String(SERVER_BASE) + "/api/device/" + DEVICE_ID + "/events");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.setTimeout(4000);
  int code = http.POST(eventsJson(n));
  if (code == 200) {
    queue_->popFront(n);  // server accepted (or ignored as duplicate) all of them
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) syncClock(doc);
  }
  http.end();
}

static Inputs readInputs() {
  Inputs in;
  in.now_ms = millis();
  in.epoch = epochNow();
  in.ir_broken = digitalRead(PIN_IR_BEAM) == LOW;
  in.limit_switch = digitalRead(PIN_LIMIT_SWITCH) == LOW;
  in.presence = digitalRead(PIN_PROXIMITY) == LOW;
  in.weight_valid = trayValid;
  in.tray_g = trayG;
  return in;
}

static void pollCommands() {
  if (!WiFi.isConnected()) return;
  HTTPClient http;
  http.begin(String(SERVER_BASE) + "/api/device/" + DEVICE_ID + "/commands");
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.setTimeout(4000);
  if (http.GET() == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      syncClock(doc);
      // Voice prompts (patient's language): show on OLED, log, queue the clip for playback.
      for (JsonObject p : doc["prompts"].as<JsonArray>()) {
        const char *key = p["key"] | "";
        snprintf(statusLine, sizeof statusLine, "say: %s", key);
        snprintf(pendingClip, sizeof pendingClip, "%s", (const char *)(p["clip"] | ""));
        if (!strcmp(key, "CONFIRM_MEAL")) awaitingMeal = true;
        sdLog("PROMPT", String(key) + "," + pendingClip);
      }
      for (JsonObject c : doc["commands"].as<JsonArray>()) {
        if (strcmp(c["kind"] | "", "DISPENSE") != 0) continue;
        int32_t cid = c["command_id"], did = c["dose_id"];
        int8_t comp = c["compartment"];
        float uw = c["unit_weight_g"] | -1.0f, tol = c["weight_tol_g"] | -1.0f;
        if (exec_->accept(cid, did, comp, readInputs(), uw, tol)) {
          nvs.putInt("last_cmd", cid);  // persisted before the servo moves
          snprintf(statusLine, sizeof statusLine, "dispensing C%d", comp);
          sdLog("DISPENSE", String((long)cid) + ",dose=" + String((long)did) + ",box=" + String((int)comp));
        }
      }
    }
  }
  http.end();
}

// ------------------------------------------------------------------ patient card (RC522)
static void pollCard() {
  if (!rfidOk || !rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;
  char uid[24] = "";
  for (byte i = 0; i < rfid.uid.size && i < 10; i++) snprintf(uid + 2 * i, sizeof uid - 2 * i, "%02X", rfid.uid.uidByte[i]);
  strncpy(lastTag, uid, sizeof lastTag - 1);
  lastTagAt = millis();
  rfid.PICC_HaltA();
  snprintf(statusLine, sizeof statusLine, "card %s", uid);   // register this UID in Dashboard > Devices
  sdLog("CARD", uid);
}

// Meal confirmation for "take with food" medicines: hand held over the tray for 2 s.
// Only while the server has asked (CONFIRM_MEAL prompt) and the tray is not in use.
static void pollMealConfirm(bool presence) {
  if (!awaitingMeal || exec_->busy()) { handSince = 0; return; }
  if (!presence) { handSince = 0; return; }
  if (!handSince) handSince = millis();
  if (millis() - handSince >= MEAL_HOLD_MS) {
    mealAt = millis(); awaitingMeal = false; handSince = 0;
    snprintf(statusLine, sizeof statusLine, "meal confirmed");
    sdLog("MEAL", "confirmed");
  }
}

// HELP / SOS: while nothing is happening and no meal answer is awaited, a hand held over the
// tray for 5 s sends an SOS event (server alerts family + doctor). Offline: SMS straight away.
static bool simSms(const char *number, const char *text);   // defined in the SIM800L section below
static uint32_t sosHandSince = 0, lastSosAt = 0;
static void pollSos(bool presence) {
  if (awaitingMeal || exec_->state() != ExecState::IDLE || !presence) { sosHandSince = 0; return; }
  if (!sosHandSince) sosHandSince = millis();
  if (millis() - sosHandSince < SOS_HOLD_MS || (lastSosAt && millis() - lastSosAt < 60000)) return;
  lastSosAt = millis(); sosHandSince = 0;
  queue_->push(EvType::SOS, epochNow());
  snprintf(statusLine, sizeof statusLine, "HELP sent");
  sdLog("SOS", "hand held over tray");
  digitalWrite(PIN_BUZZER, HIGH); delay(300); digitalWrite(PIN_BUZZER, LOW);
  if (!WiFi.isConnected()) simSms(CAREGIVER_PHONE, "MedGate: the patient pressed HELP (hand over tray). Please call or visit now.");
}

// ------------------------------------------------------------------ voice clips (MAX98357A, I2S)
static void i2sInit() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
  cfg.sample_rate = 16000;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.dma_buf_count = 6;
  cfg.dma_buf_len = 256;
  i2s_pin_config_t pins = {};
  pins.bck_io_num = PIN_I2S_BCLK; pins.ws_io_num = PIN_I2S_LRC; pins.data_out_num = PIN_I2S_DIN;
  pins.data_in_num = I2S_PIN_NO_CHANGE;
  i2sOk = i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr) == ESP_OK && i2s_set_pin(I2S_NUM_0, &pins) == ESP_OK;
}

// Blocking playback (max 8 s). Called only when nothing is moving. Expects 16-bit PCM mono WAV.
static void playClip(const char *path) {
  if (!i2sOk || !sdOk || !path[0]) return;
  File f = SD.open(path);
  if (!f) { sdLog("PROMPT_MISSING", path); return; }
  uint8_t hdr[44];
  if (f.read(hdr, 44) != 44 || memcmp(hdr, "RIFF", 4) || memcmp(hdr + 8, "WAVE", 4)) { f.close(); return; }
  uint32_t rate = hdr[24] | (hdr[25] << 8) | ((uint32_t)hdr[26] << 16) | ((uint32_t)hdr[27] << 24);
  i2s_set_clk(I2S_NUM_0, rate, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
  uint8_t buf[512];
  uint32_t t0 = millis();
  size_t n, written;
  while ((n = f.read(buf, sizeof buf)) > 0 && millis() - t0 < 8000) i2s_write(I2S_NUM_0, buf, n, &written, portMAX_DELAY);
  i2s_zero_dma_buffer(I2S_NUM_0);
  f.close();
}

// ------------------------------------------------------------------ SIM800L (offline fallback)
static bool simSms(const char *number, const char *text) {
  // Blocking for a few seconds; only used once per offline fault episode.
  simSerial.println("AT+CMGF=1");
  delay(300);
  simSerial.printf("AT+CMGS=\"%s\"\r", number);
  delay(300);
  simSerial.print(text);
  simSerial.write(26);  // Ctrl+Z sends
  uint32_t t0 = millis();
  String resp;
  while (millis() - t0 < 8000) {
    while (simSerial.available()) resp += (char)simSerial.read();
    if (resp.indexOf("+CMGS") >= 0) return true;
    if (resp.indexOf("ERROR") >= 0) return false;
  }
  return false;
}

// ------------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(115200);
  Wire.begin(PIN_SDA, PIN_SCL);

  pca.begin();
  pca.setOscillatorFrequency(25000000);
  pca.setPWMFreq(50);
  for (int i = 0; i < 6; i++) servoWriteUs(i, SERVO_CLOSED_US);
  pinMode(PIN_MOTOR_IN1, OUTPUT);
  pinMode(PIN_MOTOR_IN2, OUTPUT);
  ledcSetup(7, 1000, 8);
  ledcAttachPin(PIN_MOTOR_EN, 7);
  motor(false);
  pinMode(PIN_IR_BEAM, INPUT);
  pinMode(PIN_PROXIMITY, INPUT);
  pinMode(PIN_LIMIT_SWITCH, INPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  rtcOk = rtc.begin() && !rtc.lostPower();
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  pulseOk = pulse.begin(Wire, I2C_SPEED_FAST);
  if (pulseOk) pulse.setup(60, 4, 2, 100, 411, 4096);  // ledBrightness, avg, ledMode(red+IR), rate, width, range
  scale.begin(PIN_HX711_DT, PIN_HX711_SCK);
  scale.set_scale(HX711_SCALE);
  scale.tare();
  simSerial.begin(9600, SERIAL_8N1, PIN_SIM_RX, PIN_SIM_TX);
  bpSerial.begin(BP_BAUD, SERIAL_8N1, PIN_BP_RX, PIN_BP_TX);
  SPI.begin(PIN_SD_SCK, PIN_SD_MISO, PIN_SD_MOSI, PIN_SD_CS);
  pinMode(PIN_RFID_CS, OUTPUT);
  digitalWrite(PIN_RFID_CS, HIGH);   // keep the RFID reader off the shared SPI bus during SD init
  sdOk = SD.begin(PIN_SD_CS);
  rfid.PCD_Init();
  rfidOk = rfid.PCD_PerformSelfTest() || rfid.PCD_ReadRegister(MFRC522::VersionReg) != 0x00;
  rfid.PCD_Init();                   // self-test leaves the reader in a test state
  i2sInit();
  ahtOk = aht.begin();
  sdLog("BOOT", "medgate-0.3.0");

  nvs.begin("medgate", false);
  // Sequence numbers must never repeat across reboots, or the server would drop new events.
  uint32_t bootBlock = nvs.getUInt("seq_block", 0) + 1;
  nvs.putUInt("seq_block", bootBlock);
  queue_ = new EventQueue(bootBlock * 1000000UL);
  ExecConfig cfg;
  cfg.unit_weight_g = UNIT_WEIGHT_G;
  cfg.tol_g = WEIGHT_TOL_G;
  exec_ = new DispenseExecutor(*queue_, cfg);
  int32_t last = nvs.getInt("last_cmd", 0);
  if (last) exec_->rememberExecuted(last);  // never repeat the command we were running at power loss

  if (!rtcOk) { Event &f = queue_->push(EvType::FAULT, epochNow()); strncpy(f.text, "RTC_UNAVAILABLE", 23); }
  if (!pulseOk) { Event &f = queue_->push(EvType::FAULT, epochNow()); strncpy(f.text, "MAX30102_UNAVAILABLE", 23); }
  if (!sdOk) { Event &f = queue_->push(EvType::FAULT, epochNow()); strncpy(f.text, "SD_UNAVAILABLE", 23); }
  if (!ahtOk) { Event &f = queue_->push(EvType::FAULT, epochNow()); strncpy(f.text, "AHT20_UNAVAILABLE", 23); }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  queue_->push(EvType::HEARTBEAT, epochNow());
}

void loop() {
  uint32_t now = millis();

  if (scale.is_ready()) { trayG = scale.get_units(1); trayValid = true; }
  sampleVitals();
  pollBp();
  pollCard();
  pollMealConfirm(digitalRead(PIN_PROXIMITY) == LOW);
  pollSos(digitalRead(PIN_PROXIMITY) == LOW);

  // Medicine storage climate, once a minute. A failed read is sent as missing values,
  // which the server records as an INVALID reading and a STORAGE_SENSOR alert.
  static uint32_t lastEnv = 0;
  if (now - lastEnv > ENV_INTERVAL_MS) {
    lastEnv = now;
    sensors_event_t hum, temp;
    bool ok = ahtOk && aht.getEvent(&hum, &temp);
    Event &e = queue_->push(EvType::ENV, epochNow());
    e.value = ok ? temp.temperature : NAN;
    e.value2 = ok ? hum.relative_humidity : NAN;
    sdLog("ENV", ok ? String(temp.temperature, 1) + "C," + String(hum.relative_humidity, 0) + "%" : String("read failed"));
  }

  static ExecState prevState = ExecState::IDLE;
  static uint32_t deliveredAt = 0;
  Outputs o = exec_->step(readInputs());
  ExecState nowState = exec_->state();
  if (nowState != prevState) {
    if (nowState == ExecState::AWAIT_PICKUP) deliveredAt = now;
    if (prevState == ExecState::AWAIT_PICKUP && nowState == ExecState::IDLE) {
      float waitedMin = (now - deliveredAt) / 60000.0f;
      bool collected = queue_->size() && queue_->at(queue_->size() - 1).type == EvType::COLLECT;
      recordSession(waitedMin, !collected, false);
    }
    sdLog("EXEC", toString(nowState));
    prevState = nowState;
  }
  for (int i = 0; i < 6; i++)
    servoWriteUs(i, (o.servo_compartment == i + 1 && o.servo_open) ? SERVO_OPEN_US : SERVO_CLOSED_US);
  motor(o.motor_on);
  digitalWrite(PIN_BUZZER, o.buzzer ? HIGH : LOW);

  bool online = WiFi.isConnected();
  // HTTP calls block for up to 4 s. Never make them while a servo or the conveyor is
  // moving, or the motor could run past the end-stop before step() sees the switch.
  ExecState es = exec_->state();
  bool motionSafe = es == ExecState::IDLE || es == ExecState::AWAIT_PICKUP || es == ExecState::FAULT;
  if (online && motionSafe) {
    offlineSince = 0;
    faultSmsSent = false;
    if (now - lastUpload > UPLOAD_EVENTS_MS) { lastUpload = now; uploadEvents(); }
    if (now - lastPoll > POLL_COMMANDS_MS && !exec_->busy()) { lastPoll = now; pollCommands(); }
  } else if (!online) {
    if (!offlineSince) offlineSince = now;
    if (exec_->state() == ExecState::FAULT && !faultSmsSent && now - offlineSince > OFFLINE_SMS_AFTER_MS) {
      faultSmsSent = simSms(CAREGIVER_PHONE, "MedGate: dispenser fault while offline. Please check the device.");
    }
  }
  // Play queued voice clip only when no servo/belt is moving (playback blocks for a few seconds).
  ExecState ps = exec_->state();
  if (pendingClip[0] && (ps == ExecState::IDLE || ps == ExecState::AWAIT_PICKUP || ps == ExecState::FAULT)) {
    playClip(pendingClip); pendingClip[0] = 0;
  }
  if (now - lastBeat > HEARTBEAT_MS) { lastBeat = now; queue_->push(EvType::HEARTBEAT, epochNow()); showStatus(); }
  static uint32_t lastOled = 0;
  if (now - lastOled > 500) { lastOled = now; showStatus(); }
}
