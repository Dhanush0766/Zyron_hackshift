#include "medgate_core.h"
#include "lstm_weights.h"

#include <math.h>
#include <string.h>

namespace mg {

// IDF-B §7.2 example thresholds (same as backend IDF_RULES): a rule is the FAILING condition.
// Demonstration values, NOT clinical guidance.
const Rule DEMO_RULES[] = {{Field::SPO2, Op::LT, 94}, {Field::BP_SYS, Op::GT, 160}};
const size_t DEMO_RULES_N = sizeof(DEMO_RULES) / sizeof(DEMO_RULES[0]);

static bool inRange(float v, float lo, float hi) { return v >= lo && v <= hi; }

ReadingStatus validateReading(const Vitals& v, double now, float max_age_s, float min_quality) {
  // IDF-B AND gate: SpO2 and BP are both required; HR is optional.
  if (!v.present || !v.has_spo2 || !v.has_bp || !v.has_time) return ReadingStatus::MISSING;
  // sensor sanity ranges, not clinical limits
  if ((v.has_hr && !inRange(v.hr, 25, 250)) || !inRange(v.spo2, 50, 100)) return ReadingStatus::INVALID;
  if (v.has_bp) {
    if (!inRange(v.bp_sys, 60, 260) || !inRange(v.bp_dia, 30, 160) || v.bp_dia >= v.bp_sys)
      return ReadingStatus::INVALID;
  }
  double age = now - v.measured_at;
  if (age < -30) return ReadingStatus::INVALID;  // clock fault
  if (age > max_age_s) return ReadingStatus::STALE;
  if (!v.has_quality || v.quality < min_quality) return ReadingStatus::LOW_QUALITY;
  return ReadingStatus::VALID;
}

static float tightenStep(Field f) { return f == Field::SPO2 ? 1.0f : 5.0f; }

static bool ruleHits(const Vitals& v, const Rule& r, int k) {
  float x;
  switch (r.field) {
    case Field::HR: if (!v.has_hr) return false; x = v.hr; break;
    case Field::SPO2: x = v.spo2; break;
    case Field::BP_SYS: if (!v.has_bp) return false; x = v.bp_sys; break;
    case Field::BP_DIA: if (!v.has_bp) return false; x = v.bp_dia; break;
    default: return false;
  }
  float d = tightenStep(r.field) * k;
  switch (r.op) {
    case Op::LT: return x < r.value + d;
    case Op::LE: return x <= r.value + d;
    case Op::GT: return x > r.value - d;
    case Op::GE: return x >= r.value - d;
  }
  return false;
}

GateDecision healthGate(const Vitals& v, double now, const Rule* rules, size_t n, int attempts, int max_attempts,
                        ReadingStatus* status_out, int tighten_steps) {
  int k = tighten_steps < 0 ? 0 : (tighten_steps > MAX_TIGHTEN_STEPS ? MAX_TIGHTEN_STEPS : tighten_steps);
  ReadingStatus s = validateReading(v, now);
  if (status_out) *status_out = s;
  if (s == ReadingStatus::VALID)
    for (size_t i = 0; i < n; i++)
      if (rules[i].field == Field::HR && !v.has_hr) { s = ReadingStatus::MISSING; break; }
  if (status_out) *status_out = s;
  if (s != ReadingStatus::VALID)
    return (attempts + 1 >= max_attempts) ? GateDecision::HOLD_FOR_REVIEW : GateDecision::RETAKE;
  for (size_t i = 0; i < n; i++)
    if (ruleHits(v, rules[i], k)) return GateDecision::HOLD_FOR_REVIEW;
  return GateDecision::PROCEED;
}

const char* toString(ReadingStatus s) {
  static const char* n[] = {"VALID", "MISSING", "INVALID", "STALE", "LOW_QUALITY"};
  return n[(int)s];
}
const char* toString(GateDecision d) {
  static const char* n[] = {"PROCEED", "RETAKE", "HOLD_FOR_REVIEW"};
  return n[(int)d];
}

// ------------------------------------------------------------------ LSTM inference
static float sigm(float x) { return 1.0f / (1.0f + expf(-x)); }

int lstmInputs() { return MG_LSTM_IN; }

float lstmPredict(const float* seq, int n_steps) {
  const int H = MG_LSTM_H, I = MG_LSTM_IN, Z = MG_LSTM_IN + MG_LSTM_H;
  float h[MG_LSTM_H] = {0}, c[MG_LSTM_H] = {0}, a[4 * MG_LSTM_H], z[MG_LSTM_IN + MG_LSTM_H];
  float y = MG_LSTM_BY;
  for (int t = 0; t < n_steps; t++) {
    for (int j = 0; j < I; j++) z[j] = seq[t * I + j];
    for (int j = 0; j < H; j++) z[I + j] = h[j];
    for (int r = 0; r < 4 * H; r++) {
      float s = MG_LSTM_B[r];
      for (int j = 0; j < Z; j++) s += MG_LSTM_W[r * Z + j] * z[j];
      a[r] = s;
    }
    y = MG_LSTM_BY;
    for (int j = 0; j < H; j++) {
      float i = sigm(a[j]), f = sigm(a[H + j]), o = sigm(a[2 * H + j]), g = tanhf(a[3 * H + j]);
      c[j] = f * c[j] + i * g;
      h[j] = o * tanhf(c[j]);
      y += MG_LSTM_WY[j] * h[j];
    }
  }
  if (y < 0) y = 0;
  if (y > 1) y = 1;
  return y * MG_LSTM_CAP_MIN;
}

// ------------------------------------------------------------------ BP parser
bool parseBpLine(const char* s, int* sys, int* dia, int* pulse) {
  int vals[3] = {0, 0, -1}, n = 0;
  while (*s && n < 3) {
    while (*s && (*s < '0' || *s > '9')) s++;
    if (!*s) break;
    long v = 0;
    while (*s >= '0' && *s <= '9') { if (v < 100000) v = v * 10 + (*s - '0'); s++; }
    vals[n++] = (int)v;
  }
  if (n < 2 || vals[0] < 40 || vals[0] > 300 || vals[1] < 20 || vals[1] > 200) return false;
  *sys = vals[0]; *dia = vals[1]; *pulse = n > 2 ? vals[2] : -1;
  return true;
}

// ------------------------------------------------------------------ verification
Delivery verifyDelivery(bool ir, float delta, float expected, float tol) {
  int w;  // 1 = one unit, 0 = nothing, -1 = out of range
  if (isnan(delta) || fabsf(delta) <= tol) w = 0;
  else if (fabsf(delta - expected) <= tol) w = 1;
  else w = -1;
  if (w < 0) return Delivery::WEIGHT_OUT_OF_RANGE;
  if (ir && w) return Delivery::CONFIRMED;
  if (!ir && !w) return Delivery::NOT_DISPENSED;
  return ir ? Delivery::DISAGREEMENT_IR_ONLY : Delivery::DISAGREEMENT_WEIGHT_ONLY;
}
const char* toString(Delivery d) {
  static const char* n[] = {"CONFIRMED", "NOT_DISPENSED", "DISAGREEMENT_IR_ONLY", "DISAGREEMENT_WEIGHT_ONLY",
                            "WEIGHT_OUT_OF_RANGE"};
  return n[(int)d];
}

// ------------------------------------------------------------------ queue
Event& EventQueue::push(EvType t, double ts) {
  if (n_ == CAP) { head_ = (head_ + 1) % CAP; n_--; dropped_++; }
  Event& e = buf_[(head_ + n_) % CAP];
  n_++;
  e = Event{};
  e.seq = next_seq_++;
  e.type = t;
  e.ts = ts;
  e.dose_id = -1;
  e.command_id = -1;
  e.value = NAN;
  e.value2 = NAN;
  return e;
}
void EventQueue::popFront(size_t k) {
  if (k > n_) k = n_;
  head_ = (head_ + k) % CAP;
  n_ -= k;
}

// ------------------------------------------------------------------ executor
const char* toString(ExecState s) {
  static const char* n[] = {"IDLE", "SERVO_OPEN", "SERVO_CLOSE", "CONVEYOR", "SETTLE", "AWAIT_PICKUP", "FAULT"};
  return n[(int)s];
}

bool DispenseExecutor::alreadyExecuted(int32_t id) const {
  for (size_t i = 0; i < DONE_CAP; i++)
    if (done_[i] == id && id != 0) return true;
  return false;
}
void DispenseExecutor::rememberExecuted(int32_t id) {
  if (alreadyExecuted(id)) return;
  done_[done_n_ % DONE_CAP] = id;
  done_n_++;
}

Event& DispenseExecutor::ev(EvType t, const Inputs& in) {
  Event& e = q_.push(t, in.epoch);
  e.dose_id = dose_;
  return e;
}

bool DispenseExecutor::accept(int32_t command_id, int32_t dose_id, int8_t compartment, const Inputs& in,
                              float unit_weight_g, float tol_g) {
  if (compartment < 1 || compartment > 6) return false;
  if (alreadyExecuted(command_id)) {           // e.g. server re-sent after reboot: ack again, never re-run
    Event& a = q_.push(EvType::CMD_ACK, in.epoch);
    a.command_id = command_id;
    return false;
  }
  if (busy() && st_ != ExecState::AWAIT_PICKUP && st_ != ExecState::FAULT) return false;
  // Remember BEFORE moving the servo, so a crash mid-cycle cannot cause a second ejection.
  rememberExecuted(command_id);
  cmd_ = command_id; dose_ = dose_id; comp_ = compartment;
  if (unit_weight_g > 0 && tol_g > 0) { cfg_.unit_weight_g = unit_weight_g; cfg_.tol_g = tol_g; }
  ir_latched_ = presence_latched_ = false;
  baseline_g_ = in.weight_valid ? in.tray_g : 0;
  Event& a = ev(EvType::CMD_ACK, in);
  a.command_id = command_id;
  enter(ExecState::SERVO_OPEN, in.now_ms);
  return true;
}

Outputs DispenseExecutor::step(const Inputs& in) {
  Outputs o;
  uint32_t el = in.now_ms - since_;
  switch (st_) {
    case ExecState::IDLE:
    case ExecState::FAULT:
      break;
    case ExecState::SERVO_OPEN:
      o.servo_compartment = comp_; o.servo_open = true;
      if (in.ir_broken) ir_latched_ = true;
      if (el >= cfg_.servo_open_ms) enter(ExecState::SERVO_CLOSE, in.now_ms);
      break;
    case ExecState::SERVO_CLOSE:
      o.servo_compartment = comp_; o.servo_open = false;
      if (in.ir_broken) ir_latched_ = true;     // tablet may still be falling
      if (el >= cfg_.servo_close_ms) {
        Event& e = ev(EvType::EJECT, in); e.flag_a = ir_latched_;
        Event& c = ev(EvType::CONVEYOR, in); strncpy(c.text, "started", sizeof(c.text) - 1);
        enter(ExecState::CONVEYOR, in.now_ms);
      }
      break;
    case ExecState::CONVEYOR:
      o.motor_on = true;
      if (in.limit_switch) {
        enter(ExecState::SETTLE, in.now_ms);
        o.motor_on = false;
      } else if (el >= cfg_.conveyor_timeout_ms) {
        Event& c = ev(EvType::CONVEYOR, in); strncpy(c.text, "jam", sizeof(c.text) - 1);
        o.motor_on = false;
        enter(ExecState::FAULT, in.now_ms);
      }
      break;
    case ExecState::SETTLE:
      if (el >= cfg_.settle_ms && in.weight_valid) {
        delivered_g_ = in.tray_g - baseline_g_;
        Event& c = ev(EvType::CONVEYOR, in);
        strncpy(c.text, "arrived", sizeof(c.text) - 1);
        c.value = delivered_g_;
        Delivery d = verifyDelivery(ir_latched_, delivered_g_, cfg_.unit_weight_g, cfg_.tol_g);
        enter(d == Delivery::CONFIRMED ? ExecState::AWAIT_PICKUP : ExecState::FAULT, in.now_ms);
      }
      break;
    case ExecState::AWAIT_PICKUP:
      o.buzzer = (el / 1000) % 10 < 1;          // short chirp every 10 s
      if (in.presence) presence_latched_ = true;
      if (in.weight_valid && (in.tray_g - baseline_g_) < cfg_.tol_g) {   // tray back to baseline
        Event& c = ev(EvType::COLLECT, in);
        c.flag_a = true; c.flag_b = presence_latched_;
        enter(ExecState::IDLE, in.now_ms);
        o.buzzer = false;
      } else if (el >= cfg_.pickup_timeout_ms) {
        enter(ExecState::IDLE, in.now_ms);      // server escalates and marks MISSED
        o.buzzer = false;
      }
      break;
  }
  return o;
}

}  // namespace mg
