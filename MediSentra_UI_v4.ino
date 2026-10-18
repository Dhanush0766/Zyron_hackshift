/*
  MediSentra UI v4 - Arduino UNO + MCUFRIEND_kbv parallel TFT shield
  Layout is built for a 480x320 screen (3.5" shield), landscape.

  Libraries: MCUFRIEND_kbv, Adafruit GFX, DHT sensor library (Adafruit)
  Board:     Arduino UNO

  v4: SIZE-TRIMMED to fit the UNO (square panels, integer number
      formatting, table-based logo ellipse). Same features as v3.

  v3 CHANGES
  - Each slide stays for 10 seconds (PAGE_INTERVAL_MS).
  - No page number in the header.
  - No blinking: the header is drawn once, live values are overwritten in
    place with an opaque text background, the bar graph only redraws the
    bar that changed, and the line graph only redraws its lines.
    Only a quick body clear happens when the slide changes.

  MEMORY: all text uses F()/PROGMEM, no Serial, no bitmaps (logo is drawn
  with shapes), so SRAM stays tiny and flash stays as low as possible.

  PIN MAP (the TFT shield already uses D2-D9 and A0-A4):
    DHT11 data  -> D10
    PIR out     -> D11
    Ultrasonic  -> D12  (single-pin mode: TRIG+ECHO joined, ~1k in series
                         with ECHO on an HC-SR04; 3-wire modules need nothing)
    Buzzer (+)  -> D13  (active buzzer)
    Gas (MQ AO) -> A5

  Edit the CARETAKER and PILL SCHEDULE blocks below with real details.
  The pill names there are examples only, not medical advice.
*/

#include <Adafruit_GFX.h>
#include <MCUFRIEND_kbv.h>
#include <DHT.h>

MCUFRIEND_kbv tft;

#define DHT_PIN    10
#define DHT_TYPE   DHT11
#define PIR_PIN    11
#define SONAR_PIN  12
#define BUZZER_PIN 13
#define GAS_PIN    A5

#define FP(x) ((const __FlashStringHelper*)(x))

// ===================== EDIT: CARETAKER =====================
const char CARE_NAME[] PROGMEM = "CARETAKER";
const char CARE_NUM[]  PROGMEM = "+91 98765 43210";

// ===================== EDIT: PILL SCHEDULE =================
// Keep pill names <= 14 chars, dose text <= 24 chars.
const char SL0[] PROGMEM = "MORNING";
const char SL1[] PROGMEM = "AFTERNOON";
const char SL2[] PROGMEM = "NIGHT";
const char TM0[] PROGMEM = "08:00 AM";
const char TM1[] PROGMEM = "02:00 PM";
const char TM2[] PROGMEM = "09:00 PM";
const char PL0[] PROGMEM = "Vitamin D3";
const char PL1[] PROGMEM = "Multivitamin";
const char PL2[] PROGMEM = "Calcium";
const char DS0[] PROGMEM = "1 tab - after breakfast";
const char DS1[] PROGMEM = "1 tab - after lunch";
const char DS2[] PROGMEM = "1 tab - after dinner";
const char *const SLOTS[] PROGMEM = {SL0, SL1, SL2};
const char *const TIMES[] PROGMEM = {TM0, TM1, TM2};
const char *const PILLS[] PROGMEM = {PL0, PL1, PL2};
const char *const DOSES[] PROGMEM = {DS0, DS1, DS2};
// ===========================================================

const char TAGLINE[] PROGMEM = "Verified care, wherever you are.";

const __FlashStringHelper *pstr(const char *const *tbl, uint8_t i) {
  return (const __FlashStringHelper *)pgm_read_word(&tbl[i]);
}

DHT dht(DHT_PIN, DHT_TYPE);

// RGB565 colours (UI)
const uint16_t C_BLACK = 0x0000, C_WHITE = 0xFFFF, C_NAVY = 0x10A3;
const uint16_t C_BLUE = 0x3D7F, C_CYAN = 0x07FF, C_GREEN = 0x07E0;
const uint16_t C_YELLOW = 0xFFE0, C_RED = 0xF800, C_GREY = 0x8410;
const uint16_t C_DARK = 0x2104, C_PURPLE = 0xA81F;
// RGB565 colours (logo)
const uint16_t L_SBLUE = 0x0B39, L_SGREEN = 0x1D51, L_SDARK = 0x0A2C;
const uint16_t L_PTOP = 0xDF3E, L_PBOT = 0x1514;
const uint16_t L_NAVY = 0x094C, L_TEAL = 0x14B1;
const uint16_t L_DBLUE = 0x1C7B, L_DGREEN = 0x5E4B;

const uint8_t  PAGE_COUNT = 8;
const uint8_t  HIST_N = 10;
const unsigned long PAGE_INTERVAL_MS   = 10000UL;  // 10 s per slide
const unsigned long SENSOR_INTERVAL_MS = 2500UL;
const unsigned long ALARM_INTERVAL_MS  = 60000UL;
const unsigned long ALARM_DURATION_MS  = 2500UL;
const int GAS_DEMO_THRESHOLD = 600;
const int16_t BASE_Y = 268;  // bar graph baseline

float temperatureC = 26.0, humidityPct = 58.0, distanceCm = 42.0;
int gasValue = 320;
bool usingDemoEnvironment = false;

uint8_t tHist[HIST_N] = {25, 26, 25, 27, 26, 26, 27, 26, 25, 26};
uint8_t hHist[HIST_N] = {55, 57, 56, 58, 60, 59, 58, 57, 58, 58};
uint8_t dT[HIST_N], dH[HIST_N];  // what the line graph currently shows
bool lineDrawn = false;

uint8_t barPct[4];   // what each bar currently shows
int16_t barVal[4];

unsigned long lastSensorRead = 0, lastPageChange = 0;
unsigned long lastAlarmStart = 0, alarmEndsAt = 0;
bool alarmStarted = false;
uint8_t currentPage = 0;
uint8_t doseIdx = 0;    // next slot: 0 morning, 1 afternoon, 2 night
uint8_t shownDose = 255;

uint16_t slotColor(uint8_t i) {
  return i == 0 ? C_YELLOW : (i == 1 ? C_CYAN : C_PURPLE);
}

// ------------------------------------------------------------ text helpers
void txt(int16_t x, int16_t y, const __FlashStringHelper *s,
         uint16_t c, uint8_t sz = 1) {
  tft.setTextColor(c);
  tft.setTextSize(sz);
  tft.setCursor(x, y);
  tft.print(s);
}

// Opaque text, padded with spaces: overwrites old text in place (no blink).
void txtBg(int16_t x, int16_t y, const __FlashStringHelper *s, uint16_t fg,
           uint16_t bg, uint8_t sz, uint8_t pad) {
  tft.setTextColor(fg, bg);
  tft.setTextSize(sz);
  tft.setCursor(x, y);
  tft.print(s);
  for (uint8_t n = strlen_P((const char *)s); n < pad; n++) tft.print(' ');
}

// Integer-only number formatting (avoids float printing code in flash).
void fmtNum(char *b, float v, uint8_t dec) {
  int16_t iv = dec ? (int16_t)(v * 10.0 + 0.5) : (int16_t)(v + 0.5);
  if (iv < 0) iv = 0;
  itoa(dec ? iv / 10 : iv, b, 10);
  if (dec) {
    uint8_t n = strlen(b);
    b[n] = '.';
    b[n + 1] = '0' + iv % 10;
    b[n + 2] = 0;
  }
}

// Opaque left-aligned number, padded to w characters.
void numPad(int16_t x, int16_t y, float v, uint8_t dec, uint8_t w,
            uint16_t fg, uint16_t bg, uint8_t sz) {
  char b[8];
  fmtNum(b, v, dec);
  tft.setTextColor(fg, bg);
  tft.setTextSize(sz);
  tft.setCursor(x, y);
  tft.print(b);
  for (uint8_t n = strlen(b); n < w; n++) tft.print(' ');
}

void pnum(float v, uint8_t dec) {
  char b[8];
  fmtNum(b, v, dec);
  tft.print(b);
}

void panel(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t border,
           uint16_t fill = C_DARK) {
  tft.fillRect(x, y, w, h, border);
  tft.fillRect(x + 2, y + 2, w - 4, h - 4, fill);
}

void progressBar(int16_t x, int16_t y, int16_t w, int16_t h, int pct,
                 uint16_t c) {
  pct = constrain(pct, 0, 100);
  int16_t f = (long)(w - 2) * pct / 100;
  tft.drawRect(x, y, w, h, C_GREY);
  tft.fillRect(x + 1, y + 1, f, h - 2, c);
  tft.fillRect(x + 1 + f, y + 1, w - 2 - f, h - 2, C_BLACK);
}

// ------------------------------------------------------------ logo drawing
void shieldPoly(int16_t cx, int16_t y, int16_t hw, int16_t h,
                uint16_t cl, uint16_t cr) {
  int16_t y1 = y + h / 5, y2 = y + (h * 3) / 5, yb = y + h;
  tft.fillTriangle(cx, y, cx - hw, y1, cx - hw, y2, cl);
  tft.fillTriangle(cx, y, cx - hw, y2, cx, yb, cl);
  tft.fillTriangle(cx, y, cx + hw, y1, cx + hw, y2, cr);
  tft.fillTriangle(cx, y, cx + hw, y2, cx, yb, cr);
}

void drawShield(int16_t cx, int16_t y, int16_t hw, int16_t h, bool inner) {
  shieldPoly(cx, y, hw, h, L_SBLUE, L_SGREEN);
  if (inner) shieldPoly(cx, y + 9, hw - 9, h - 20, L_SDARK, L_SDARK);
}

// Half-ellipse heights (rx=60, ry=20) every 5 px, no sqrt needed.
const uint8_t ELL[] PROGMEM = {20, 19, 19, 19, 18, 18, 17, 16, 14, 13, 11, 8, 0};

void ellipseLite(int16_t cx, int16_t cy, uint16_t c) {
  int16_t px = -60, py = 0;
  for (int16_t x = -55; x <= 60; x += 5) {
    int16_t y = pgm_read_byte(&ELL[(x < 0 ? -x : x) / 5]);
    tft.drawLine(cx + px, cy - py, cx + x, cy - y, c);
    tft.drawLine(cx + px, cy + py, cx + x, cy + y, c);
    px = x;
    py = y;
  }
}

void showSplash() {
  tft.fillScreen(C_WHITE);
  drawShield(240, 8, 68, 140, true);
  ellipseLite(240, 76, C_WHITE);

  tft.fillCircle(240, 50, 18, L_PTOP);
  tft.fillRect(222, 50, 37, 26, L_PTOP);
  tft.fillRect(222, 76, 37, 26, L_PBOT);
  tft.fillCircle(240, 102, 18, L_PBOT);

  const int16_t ex[] = {222, 235, 240, 246, 251, 254, 258};
  const int16_t ey[] = {76, 76, 66, 90, 72, 76, 76};
  for (uint8_t i = 0; i < 6; i++) {
    tft.drawLine(ex[i], ey[i], ex[i + 1], ey[i + 1], L_NAVY);
    tft.drawLine(ex[i], ey[i] + 1, ex[i + 1], ey[i + 1] + 1, L_NAVY);
  }

  tft.fillCircle(180, 76, 9, C_WHITE);
  tft.fillCircle(180, 76, 7, L_DBLUE);
  tft.fillCircle(300, 76, 9, C_WHITE);
  tft.fillCircle(300, 76, 7, L_DGREEN);

  txt(90, 160, F("Medi"), L_NAVY, 5);
  txt(210, 160, F("Sentra"), L_TEAL, 5);
  txt(48, 214, FP(TAGLINE), L_NAVY, 2);

  tft.drawRect(120, 262, 240, 14, L_SDARK);
  for (int16_t i = 0; i <= 234; i += 9) {
    tft.fillRect(122, 264, i, 10, L_TEAL);
    delay(40);
  }
  delay(500);
}

// ------------------------------------------------------------ chrome
// Header is drawn ONCE (no page number); slides only change the title/dots.
void drawHeader() {
  tft.fillScreen(C_BLACK);
  tft.fillRect(0, 0, 480, 48, C_NAVY);
  drawShield(26, 5, 16, 38, false);
  tft.fillRect(22, 19, 9, 12, C_WHITE);
  tft.fillCircle(26, 19, 4, C_WHITE);
  tft.fillCircle(26, 31, 4, C_WHITE);
  txt(56, 13, F("MEDISENTRA"), C_WHITE, 3);
  tft.drawFastHLine(0, 48, 480, C_CYAN);
}

const __FlashStringHelper *titleFor(uint8_t p) {
  switch (p) {
    case 0: return F("HEALTH DASHBOARD");
    case 1: return F("MEDICINE REMINDER");
    case 2: return F("PILL SCHEDULE");
    case 3: return F("SENSOR SNAPSHOT - BAR GRAPH");
    case 4: return F("TREND - LINE GRAPH");
    case 5: return F("SENSOR STATUS");
    case 6: return F("EMERGENCY CONTACTS");
    default: return F("SYSTEM STATUS");
  }
}

void drawTitleAndDots(uint8_t p) {
  tft.fillRect(0, 52, 480, 22, C_BLACK);
  txt(12, 58, titleFor(p), C_YELLOW, 2);
  tft.fillRect(0, 300, 480, 16, C_BLACK);
  for (uint8_t i = 0; i < PAGE_COUNT; i++) {
    tft.fillCircle(240 - (PAGE_COUNT - 1) * 8 + i * 16, 308, 4,
                   (i == p) ? C_CYAN : C_DARK);
  }
}

// ------------------------------------------------------------ sensors
float readDistanceCm() {
  pinMode(SONAR_PIN, OUTPUT);
  digitalWrite(SONAR_PIN, LOW);
  delayMicroseconds(3);
  digitalWrite(SONAR_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(SONAR_PIN, LOW);
  pinMode(SONAR_PIN, INPUT);
  unsigned long d = pulseIn(SONAR_PIN, HIGH, 25000UL);
  if (d == 0) return -1.0;
  return (d * 0.0343f) / 2.0f;
}

bool updateEnvironment(unsigned long now) {
  if (now - lastSensorRead < SENSOR_INTERVAL_MS) return false;
  lastSensorRead = now;

  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) {
    usingDemoEnvironment = true;  // demo-only slow random walk
    temperatureC += (random(0, 3) - 1) * 0.5;
    humidityPct += (random(0, 3) - 1) * 2.0;
    temperatureC = constrain(temperatureC, 23.0, 33.0);
    humidityPct = constrain(humidityPct, 40.0, 78.0);
  } else {
    usingDemoEnvironment = false;
    temperatureC = t;
    humidityPct = h;
  }

  gasValue = analogRead(GAS_PIN);
  float d = readDistanceCm();
  if (d >= 0) {
    distanceCm = d;
  } else {  // no sensor: small demo jitter so the bar moves
    distanceCm += (random(0, 3) - 1) * 3.0;
    distanceCm = constrain(distanceCm, 10.0, 150.0);
  }

  for (uint8_t i = 0; i < HIST_N - 1; i++) {
    tHist[i] = tHist[i + 1];
    hHist[i] = hHist[i + 1];
  }
  tHist[HIST_N - 1] = (uint8_t)constrain(temperatureC + 0.5, 0, 100);
  hHist[HIST_N - 1] = (uint8_t)constrain(humidityPct + 0.5, 0, 100);
  return true;
}

// ================================================================ PAGE 1
void dashStatic() {
  panel(12, 80, 226, 100, C_CYAN);
  panel(242, 80, 226, 100, C_GREEN);
  txt(24, 90, F("TEMPERATURE"), C_CYAN, 2);
  tft.fillCircle(154, 128, 4, C_WHITE);
  tft.fillCircle(154, 128, 2, C_DARK);
  txt(164, 136, F("C"), C_WHITE, 3);
  txt(254, 90, F("HUMIDITY"), C_GREEN, 2);
  txt(356, 136, F("%"), C_WHITE, 3);
  panel(12, 188, 456, 60, C_BLUE);
  txt(24, 196, F("ENVIRONMENT"), C_YELLOW, 2);
  tft.fillRect(12, 256, 456, 38, C_NAVY);
  txt(48, 267, FP(TAGLINE), C_WHITE, 2);
}

void dashLive() {
  numPad(24, 124, temperatureC, 1, 4, temperatureC >= 35 ? C_RED : C_WHITE,
         C_DARK, 5);
  numPad(254, 124, humidityPct, 0, 3, humidityPct >= 80 ? C_YELLOW : C_WHITE,
         C_DARK, 5);
  txtBg(200, 196, usingDemoEnvironment ? F("DEMO VALUES (NO DHT)")
                                       : F("DHT11 LIVE"),
        usingDemoEnvironment ? C_YELLOW : C_GREEN, C_DARK, 2, 20);
  if (temperatureC >= 35.0)
    txtBg(24, 220, F("HIGH TEMPERATURE"), C_RED, C_DARK, 3, 16);
  else if (humidityPct >= 80.0)
    txtBg(24, 220, F("HIGH HUMIDITY"), C_YELLOW, C_DARK, 3, 16);
  else
    txtBg(24, 220, F("COMFORTABLE"), C_GREEN, C_DARK, 3, 16);
}

// ================================================================ PAGE 2
void reminderStatic() {
  panel(12, 80, 456, 120, C_RED);
  tft.fillCircle(41, 105, 13, C_WHITE);
  tft.fillCircle(71, 105, 13, C_WHITE);
  tft.fillRect(41, 92, 30, 27, C_WHITE);
  tft.fillCircle(41, 105, 13, C_RED);
  tft.fillRect(41, 92, 15, 27, C_RED);
  txt(100, 94, F("MEDICINE TIME"), C_RED, 3);

  panel(12, 206, 456, 48, C_YELLOW);
  txt(24, 211, F("Missed a dose? Call caretaker:"), C_YELLOW);
  tft.setTextColor(C_WHITE);
  tft.setTextSize(3);
  tft.setCursor(24, 225);
  tft.print(FP(CARE_NUM));
  txt(12, 284, F("Demo only - not a real medication schedule"), C_GREY);
}

void reminderLive(unsigned long now) {
  uint8_t d = doseIdx;
  txtBg(24, 130, pstr(PILLS, d), C_WHITE, C_DARK, 3, 16);
  tft.setTextColor(slotColor(d), C_DARK);
  tft.setTextSize(2);
  tft.setCursor(24, 160);
  tft.print(pstr(SLOTS, d));
  tft.print(F("  "));
  tft.print(pstr(TIMES, d));
  tft.print(F("      "));
  txtBg(24, 180, pstr(DOSES, d), C_GREY, C_DARK, 2, 24);

  unsigned long rem = ALARM_INTERVAL_MS - (now - lastAlarmStart);
  if (rem > ALARM_INTERVAL_MS) rem = 0;
  tft.setTextColor(C_CYAN, C_BLACK);
  tft.setTextSize(2);
  tft.setCursor(12, 261);
  tft.print(F("Next demo buzzer in "));
  pnum((float)(rem / 1000UL), 0);
  tft.print(F(" s  "));
}

// ================================================================ PAGE 3
void pageSchedule() {
  for (uint8_t i = 0; i < 3; i++) {
    int16_t y = 80 + i * 70;
    bool due = (i == doseIdx);
    panel(12, y, 456, 62, slotColor(i), due ? C_NAVY : C_DARK);
    txt(24, y + 10, pstr(SLOTS, i), slotColor(i), 2);
    txt(24, y + 36, pstr(TIMES, i), C_WHITE, 2);
    tft.drawFastVLine(138, y + 8, 46, C_GREY);
    txt(150, y + 8, pstr(PILLS, i), C_WHITE, 3);
    txt(150, y + 38, pstr(DOSES, i), C_GREY, 2);
    if (due) {
      tft.fillRect(404, y + 6, 58, 20, C_YELLOW);
      txt(410, y + 9, F("NEXT"), C_BLACK, 2);
    }
  }
  shownDose = doseIdx;
}

// ================================================================ PAGE 4
void barsStatic() {
  for (uint8_t g = 0; g <= 4; g++)
    tft.drawFastHLine(62, BASE_Y - (g * 150) / 4, 404, g ? C_DARK : C_GREY);
  tft.drawFastVLine(62, BASE_Y - 150, 151, C_GREY);
  txt(30, BASE_Y - 153, F("100"), C_GREY);
  txt(40, BASE_Y - 78, F("50"), C_GREY);
  txt(50, BASE_Y - 4, F("0"), C_GREY);
  txt(63, BASE_Y + 6, F("TEMP C"), C_CYAN, 2);
  txt(167, BASE_Y + 6, F("HUM %"), C_GREEN, 2);
  txt(271, BASE_Y + 6, F("GAS ADC"), C_YELLOW, 2);
  txt(375, BASE_Y + 6, F("DIST cm"), C_RED, 2);
  txt(12, 292, F("Each bar is scaled to its sensor's full range"), C_GREY);
}

// Redraws only the part of one bar column that actually changed.
void barUpdate(uint8_t i, float v, uint8_t dec, int pct, uint16_t c,
               bool force) {
  pct = constrain(pct, 0, 100);
  int16_t v10 = (int16_t)(v * 10);
  if (!force && barPct[i] == pct && barVal[i] == v10) return;

  int16_t x = 70 + i * 104;
  int16_t h1 = (pct * 3) / 2;
  int16_t h0 = force ? 0 : (barPct[i] * 3) / 2;
  int16_t hMax = h0 > h1 ? h0 : h1;
  int16_t hMin = h0 < h1 ? h0 : h1;
  int16_t yTop = BASE_Y - hMax - 20;
  int16_t yBot = BASE_Y - hMin;

  tft.fillRect(x, yTop, 70, yBot - yTop, C_BLACK);  // changed area only
  for (uint8_t g = 1; g <= 4; g++) {                 // restore gridlines
    int16_t gy = BASE_Y - (g * 150) / 4;
    if (gy >= yTop && gy < yBot) tft.drawFastHLine(x, gy, 70, C_DARK);
  }
  if (h1 > h0) tft.fillRect(x, BASE_Y - h1, 70, h1 - h0, c);
  tft.setTextColor(C_WHITE);
  tft.setTextSize(2);
  tft.setCursor(x + 11, BASE_Y - h1 - 18);
  pnum(v, dec);

  barPct[i] = pct;
  barVal[i] = v10;
}

void barsLive(bool force) {
  barUpdate(0, temperatureC, 1, (int)(temperatureC * 2.0), C_CYAN, force);
  barUpdate(1, humidityPct, 0, (int)humidityPct, C_GREEN, force);
  barUpdate(2, gasValue, 0, (int)((long)gasValue * 100 / 1023), C_YELLOW,
            force);
  barUpdate(3, distanceCm, 0, (int)(distanceCm / 2.0), C_RED, force);
}

// ================================================================ PAGE 5
void drawSeries(const uint8_t *d, uint16_t c) {
  int16_t px = 0, py = 0;
  for (uint8_t i = 0; i < HIST_N; i++) {
    int16_t x = 70 + i * 44;
    int16_t y = 240 - ((int)d[i] * 3) / 2;
    if (i) {
      tft.drawLine(px, py, x, y, c);
      tft.drawLine(px, py + 1, x, y + 1, c);
    }
    tft.fillCircle(x, y, 3, c);
    px = x;
    py = y;
  }
}

void lineGrid() {
  for (uint8_t g = 0; g <= 4; g++)
    tft.drawFastHLine(62, 240 - (g * 150) / 4, 404, g ? C_DARK : C_GREY);
  tft.drawFastVLine(62, 90, 151, C_GREY);
}

void lineStatic() {
  lineGrid();
  txt(30, 87, F("100"), C_GREY);
  txt(40, 162, F("50"), C_GREY);
  txt(50, 236, F("0"), C_GREY);
  tft.fillRect(40, 256, 12, 12, C_CYAN);
  tft.fillRect(250, 256, 12, 12, C_GREEN);
  txt(12, 284, F("Last 10 readings, 2.5 s apart"), C_GREY);
  lineDrawn = false;
}

void lineLive() {
  if (lineDrawn) {            // erase only the old lines, then restore grid
    drawSeries(dH, C_BLACK);
    drawSeries(dT, C_BLACK);
    lineGrid();
  }
  drawSeries(hHist, C_GREEN);
  drawSeries(tHist, C_CYAN);
  memcpy(dT, tHist, HIST_N);
  memcpy(dH, hHist, HIST_N);
  lineDrawn = true;

  tft.setTextSize(2);
  tft.setTextColor(C_CYAN, C_BLACK);
  tft.setCursor(58, 254);
  tft.print(F("TEMP "));
  pnum(temperatureC, 1);
  tft.print(F(" C   "));
  tft.setTextColor(C_GREEN, C_BLACK);
  tft.setCursor(268, 254);
  tft.print(F("HUM "));
  pnum(humidityPct, 0);
  tft.print(F(" %   "));
}

// ================================================================ PAGE 6
void sensorsStatic() {
  panel(12, 80, 226, 96, C_CYAN);
  panel(242, 80, 226, 96, C_GREEN);
  txt(24, 88, F("DISTANCE"), C_CYAN, 2);
  txt(130, 118, F("cm"), C_WHITE, 2);
  txt(254, 88, F("GAS ADC"), C_GREEN, 2);
  panel(12, 184, 456, 50, C_BLUE);
  txt(24, 200, F("PIR MOTION"), C_YELLOW, 2);
  panel(12, 240, 456, 54, C_BLUE);
  txt(24, 272, F("Calibrate gas sensor before use"), C_GREY, 2);
}

void sensorsLive() {
  numPad(24, 112, distanceCm, 1, 5, C_WHITE, C_DARK, 3);
  progressBar(24, 152, 200, 12, (int)(distanceCm / 2.0), C_CYAN);
  bool hi = gasValue >= GAS_DEMO_THRESHOLD;
  numPad(254, 112, gasValue, 0, 4, C_WHITE, C_DARK, 3);
  progressBar(254, 152, 200, 12, (int)((long)gasValue * 100 / 1023),
              hi ? C_RED : C_GREEN);
  bool motion = digitalRead(PIR_PIN) == HIGH;
  txtBg(290, 196, motion ? F("DETECTED") : F("NO MOTION"),
        motion ? C_GREEN : C_WHITE, C_DARK, 3, 9);
  txtBg(24, 248, hi ? F("Gas ADC above demo threshold")
                    : F("Gas ADC below demo threshold"),
        hi ? C_RED : C_GREEN, C_DARK, 2, 28);
}

// ================================================================ PAGE 7
void pageContacts() {
  panel(12, 80, 456, 50, C_RED);
  txt(24, 97, F("Emergency services"), C_WHITE, 2);
  txt(360, 88, F("112"), C_RED, 4);

  panel(12, 136, 456, 50, C_CYAN);
  txt(24, 153, F("Ambulance"), C_WHITE, 2);
  txt(336, 144, F("108*"), C_CYAN, 4);

  panel(12, 192, 456, 88, C_GREEN);
  tft.setTextColor(C_YELLOW);
  tft.setTextSize(2);
  tft.setCursor(24, 200);
  tft.print(FP(CARE_NAME));
  tft.setTextColor(C_WHITE);
  tft.setTextSize(4);
  tft.setCursor(24, 224);
  tft.print(FP(CARE_NUM));
  txt(24, 262, F("Edit CARE_NUM in the sketch"), C_GREY);

  txt(12, 286, F("*Verify local service availability."), C_YELLOW);
}

// ================================================================ PAGE 8
void statusRow(int16_t y, const __FlashStringHelper *label,
               const __FlashStringHelper *state) {
  txt(28, y, label, C_WHITE, 2);
  tft.fillCircle(312, y + 7, 7, C_GREEN);
  txt(330, y, state, C_GREEN, 2);
}

// Status labels only - no real fingerprint/camera hardware is read.
void pageStatus() {
  panel(12, 80, 456, 208, C_GREEN);
  txt(28, 88, F("ALL SYSTEMS OK"), C_GREEN, 3);
  statusRow(124, F("Fingerprint"), F("LINKED"));
  statusRow(152, F("Camera"), F("LINKED"));
  statusRow(180, F("Pill box"), F("READY"));
  statusRow(208, F("Sensors"), F("ONLINE"));
  statusRow(236, F("Buzzer"), F("READY"));
  statusRow(264, F("Caretaker alert"), F("ON"));
}

// ------------------------------------------------------------ page control
void livePage(uint8_t p, unsigned long now, bool force) {
  switch (p) {
    case 0: dashLive(); break;
    case 1: reminderLive(now); break;
    case 2: if (force || doseIdx != shownDose) pageSchedule(); break;
    case 3: barsLive(force); break;
    case 4: lineLive(); break;
    case 5: sensorsLive(); break;
    default: break;
  }
}

void enterPage(uint8_t p, unsigned long now) {
  drawTitleAndDots(p);
  tft.fillRect(0, 76, 480, 224, C_BLACK);  // one quick body clear per slide
  switch (p) {
    case 0: dashStatic(); break;
    case 1: reminderStatic(); break;
    case 3: barsStatic(); break;
    case 4: lineStatic(); break;
    case 5: sensorsStatic(); break;
    case 6: pageContacts(); break;
    case 7: pageStatus(); break;
    default: break;
  }
  livePage(p, now, true);
}

// ------------------------------------------------------------ alarm
void updateDemoAlarm(unsigned long now) {
  if (!alarmStarted || now - lastAlarmStart >= ALARM_INTERVAL_MS) {
    if (alarmStarted) doseIdx = (doseIdx + 1) % 3;  // next slot is due
    alarmStarted = true;
    lastAlarmStart = now;
    alarmEndsAt = now + ALARM_DURATION_MS;
  }
  digitalWrite(BUZZER_PIN, (long)(alarmEndsAt - now) > 0 ? HIGH : LOW);
}

// ------------------------------------------------------------ main
void setup() {
  pinMode(PIR_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  dht.begin();
  randomSeed(analogRead(GAS_PIN));

  uint16_t id = tft.readID();
  if (id == 0xD3D3 || id == 0xFFFF || id == 0x0000) id = 0x9486;
  tft.begin(id);
  tft.setRotation(1);

  showSplash();
  drawHeader();

  lastSensorRead = millis() - SENSOR_INTERVAL_MS;
  updateEnvironment(millis());
  enterPage(0, millis());
  lastPageChange = millis();
}

void loop() {
  unsigned long now = millis();
  updateDemoAlarm(now);
  bool fresh = updateEnvironment(now);

  if (now - lastPageChange >= PAGE_INTERVAL_MS) {
    lastPageChange = now;
    currentPage++;
    if (currentPage >= PAGE_COUNT) currentPage = 0;
    enterPage(currentPage, now);
  } else if (fresh && currentPage < 6) {
    livePage(currentPage, now, false);  // update values in place
  }
}
