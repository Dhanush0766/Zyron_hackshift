// MedGate portable core: no Arduino dependencies, compiled and unit-tested on the
// host (firmware/test/run_native_tests.sh) and linked into the ESP32 build.
// Behaviour mirrors backend/medgate/core/*.py; both run shared/gate_vectors.csv.
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace mg {

// ------------------------------------------------------------------ vitals + gate
enum class ReadingStatus : uint8_t { VALID, MISSING, INVALID, STALE, LOW_QUALITY };
enum class GateDecision : uint8_t { PROCEED, RETAKE, HOLD_FOR_REVIEW };

struct Vitals {
  bool present = false;            // false = no reading at all
  bool has_hr = false, has_spo2 = false, has_bp = false, has_quality = false, has_time = false;
  float hr = 0, spo2 = 0, bp_sys = 0, bp_dia = 0, quality = 0;
  double measured_at = 0;          // epoch seconds
  char patient_tag[24] = {0};      // RFID card UID tapped before measuring ("" = none)
  int8_t meal = -1;                // -1 unknown, 1 patient confirmed a meal
};

enum class Field : uint8_t { HR, SPO2, BP_SYS, BP_DIA };
enum class Op : uint8_t { LT, LE, GT, GE };
struct Rule { Field field; Op op; float value; };

extern const Rule DEMO_RULES[];
extern const size_t DEMO_RULES_N;

ReadingStatus validateReading(const Vitals& v, double now, float max_age_s = 120, float min_quality = 0.6f);
// IDF-B Boolean AND gate: SpO2 AND BP required. A rule on a field makes that field required
// (e.g. HR for a beta-blocker rule): a missing value is unusable, never a pass (matches gate.py). tighten_steps (0..3) makes every rule
// stricter after a missed same-session pickup (IDF-B §7.4); it can never loosen a rule.
GateDecision healthGate(const Vitals& v, double now, const Rule* rules, size_t n_rules,
                        int attempts_so_far, int max_attempts = 3, ReadingStatus* status_out = nullptr,
                        int tighten_steps = 0);
static const int MAX_TIGHTEN_STEPS = 3;

// ------------------------------------------------------------------ compliance LSTM (IDF-B §7.5)
// Inference only; weights trained offline on SYNTHETIC data (backend/tools/lstm_train.py).
// seq: n_steps x MG_LSTM_IN features, oldest first. Returns predicted pickup latency (minutes).
float lstmPredict(const float* seq, int n_steps);
int lstmInputs();
const char* toString(ReadingStatus s);
const char* toString(GateDecision d);

// ------------------------------------------------------------------ UART BP module line parser
// Reads the first integers of a text line as SYS DIA [PULSE] ("120,80,72", "S120 D80 P72").
// Returns false for fewer than two numbers or implausible values. Adapt to your module's format.
bool parseBpLine(const char* line, int* sys, int* dia, int* pulse);

// ------------------------------------------------------------------ dual-sensor verification
enum class Delivery : uint8_t { CONFIRMED, NOT_DISPENSED, DISAGREEMENT_IR_ONLY, DISAGREEMENT_WEIGHT_ONLY, WEIGHT_OUT_OF_RANGE };
Delivery verifyDelivery(bool ir_beam_broken, float tray_delta_g, float expected_g, float tol_g);
const char* toString(Delivery d);

// ------------------------------------------------------------------ device event queue
// Offline buffer. Every event gets a monotonically increasing sequence number; the
// server de-duplicates on (device_id, seq), so re-sending after a failed upload is safe.
enum class EvType : uint8_t { HEARTBEAT, VITALS, CMD_ACK, EJECT, CONVEYOR, COLLECT, FAULT, ENV, SOS };
struct Event {
  uint32_t seq;
  EvType type;
  double ts;
  int32_t dose_id;      // -1 if none
  int32_t command_id;   // CMD_ACK
  bool flag_a;          // EJECT: ir_beam; COLLECT: weight_removed
  bool flag_b;          // COLLECT: presence
  float value;          // CONVEYOR arrived: tray_delta_g; ENV: temperature (C)
  float value2;         // ENV: relative humidity (%)
  char text[24];        // CONVEYOR status / FAULT code
  Vitals vitals;        // VITALS
};

class EventQueue {
 public:
  static const size_t CAP = 64;
  explicit EventQueue(uint32_t first_seq = 1) : next_seq_(first_seq) {}
  Event& push(EvType t, double ts);     // drops oldest when full (counted in dropped())
  size_t size() const { return n_; }
  const Event& at(size_t i) const { return buf_[(head_ + i) % CAP]; }
  void popFront(size_t k);               // remove k events after a successful upload
  uint32_t dropped() const { return dropped_; }
  uint32_t nextSeq() const { return next_seq_; }
 private:
  Event buf_[CAP];
  size_t head_ = 0, n_ = 0;
  uint32_t next_seq_, dropped_ = 0;
};

// ------------------------------------------------------------------ dispense executor
// Non-blocking state machine for ONE dispense command. Call step() every loop with
// current sensor inputs; it returns actuator outputs and appends events to the queue.
struct ExecConfig {
  uint32_t servo_open_ms = 700, servo_close_ms = 500;
  uint32_t conveyor_timeout_ms = 15000, settle_ms = 1500, pickup_timeout_ms = 180000;
  float unit_weight_g = 0.5f, tol_g = 0.15f;
};

struct Inputs {
  uint32_t now_ms;
  double epoch;          // RTC time for event timestamps
  bool ir_broken;        // IR break-beam at chute (true while interrupted)
  bool limit_switch;     // conveyor end-stop reached
  bool presence;         // IR proximity at tray
  float tray_g;          // load cell (grams, tared)
  bool weight_valid;     // HX711 returned a fresh sample
};

struct Outputs {
  int8_t servo_compartment = -1;  // which compartment servo to drive (-1 = none)
  bool servo_open = false;
  bool motor_on = false;
  bool buzzer = false;
};

enum class ExecState : uint8_t { IDLE, SERVO_OPEN, SERVO_CLOSE, CONVEYOR, SETTLE, AWAIT_PICKUP, FAULT };
const char* toString(ExecState s);

class DispenseExecutor {
 public:
  DispenseExecutor(EventQueue& q, ExecConfig cfg = ExecConfig()) : q_(q), cfg_(cfg) {}
  // Returns false (and does nothing) if busy or this command id was already executed.
  bool accept(int32_t command_id, int32_t dose_id, int8_t compartment, const Inputs& in,
              float unit_weight_g = -1, float tol_g = -1);   // per-medication weights from the command
  Outputs step(const Inputs& in);
  ExecState state() const { return st_; }
  bool busy() const { return st_ != ExecState::IDLE; }
  bool alreadyExecuted(int32_t command_id) const;
  void rememberExecuted(int32_t command_id);   // also called when restoring from NVS
  int32_t lastExecuted() const { return done_[(done_n_ + DONE_CAP - 1) % DONE_CAP]; }
 private:
  void enter(ExecState s, uint32_t now) { st_ = s; since_ = now; }
  Event& ev(EvType t, const Inputs& in);
  EventQueue& q_;
  ExecConfig cfg_;
  ExecState st_ = ExecState::IDLE;
  uint32_t since_ = 0;
  int32_t dose_ = -1, cmd_ = -1;
  int8_t comp_ = -1;
  bool ir_latched_ = false, presence_latched_ = false;
  float baseline_g_ = 0, delivered_g_ = 0;
  static const size_t DONE_CAP = 16;
  int32_t done_[DONE_CAP] = {0};
  size_t done_n_ = 0;
};

}  // namespace mg
