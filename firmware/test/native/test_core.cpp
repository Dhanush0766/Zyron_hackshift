// Host-side tests for the portable firmware core. Build: firmware/test/run_native_tests.sh
#include <math.h>
#include <stdio.h>
#include <string.h>

#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "medgate_core.h"

using namespace mg;
static int fails = 0, passes = 0;
#define CHECK(cond, msg)                                                   \
  do {                                                                     \
    if (cond) passes++;                                                    \
    else { fails++; printf("FAIL %s:%d %s\n", __FILE__, __LINE__, msg); }  \
  } while (0)

static const double NOW = 1800000000.0;

static void gateVectors(const char* path) {
  std::ifstream f(path);
  CHECK(f.good(), "vector file opens");
  std::string line;
  std::getline(f, line);  // header
  int n = 0;
  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::vector<std::string> c;
    std::stringstream ss(line);
    std::string cell;
    while (std::getline(ss, cell, ',')) c.push_back(cell);
    while (c.size() < 11) c.push_back("");
    Vitals v;
    if (c[1] != "NONE") {
      v.present = true;
      if (!c[1].empty()) { v.has_hr = true; v.hr = std::stof(c[1]); }
      if (!c[2].empty()) { v.has_spo2 = true; v.spo2 = std::stof(c[2]); }
      if (!c[3].empty() && !c[4].empty()) { v.has_bp = true; v.bp_sys = std::stof(c[3]); v.bp_dia = std::stof(c[4]); }
      if (!c[5].empty()) { v.has_quality = true; v.quality = std::stof(c[5]); }
      if (!c[6].empty()) { v.has_time = true; v.measured_at = NOW - std::stod(c[6]); }
    }
    ReadingStatus st;
    GateDecision d = healthGate(v, NOW, DEMO_RULES, DEMO_RULES_N, std::stoi(c[7]), 3, &st, std::stoi(c[8]));
    char msg[160];
    snprintf(msg, sizeof msg, "vector %s: got %s/%s want %s/%s", c[0].c_str(), toString(st), toString(d), c[9].c_str(),
             c[10].c_str());
    CHECK(c[9] == toString(st) && c[10] == toString(d), msg);
    n++;
  }
  printf("gate vectors executed: %d\n", n);
  CHECK(n >= 25, "enough vectors");
}

static void lstmVectors(const char* path) {
  std::ifstream f(path);
  CHECK(f.good(), "lstm vector file opens");
  std::string line;
  std::getline(f, line);
  std::vector<float> seq;
  int curSeq = -1, nSeq = 0, nIn = lstmInputs();
  double worst = 0;
  while (std::getline(f, line)) {
    if (line.empty()) continue;
    std::vector<std::string> c;
    std::stringstream ss(line);
    std::string cell;
    while (std::getline(ss, cell, ',')) c.push_back(cell);
    while ((int)c.size() < nIn + 3) c.push_back("");
    int s = std::stoi(c[0]);
    if (s != curSeq) { seq.clear(); curSeq = s; }
    for (int j = 0; j < nIn; j++) seq.push_back(std::stof(c[2 + j]));
    if (!c[2 + nIn].empty()) {
      float got = lstmPredict(seq.data(), (int)seq.size() / nIn);
      double diff = fabs(got - std::stod(c[2 + nIn]));
      if (diff > worst) worst = diff;
      nSeq++;
    }
  }
  printf("lstm parity: %d sequences, max |C++ - trainer| = %.6f min\n", nSeq, worst);
  CHECK(nSeq == 5 && worst < 1e-3, "C++ LSTM matches trainer output");
}

static void verification() {
  {  // drug-specific rule on HR (beta-blocker): HR becomes required, slow pulse holds
    const Rule bb[] = {{Field::SPO2, Op::LT, 94}, {Field::BP_SYS, Op::GT, 160}, {Field::HR, Op::LT, 60}};
    Vitals v; v.present = v.has_spo2 = v.has_bp = v.has_quality = v.has_time = true;
    v.spo2 = 97; v.bp_sys = 120; v.bp_dia = 80; v.quality = 0.9f; v.measured_at = 1000;
    ReadingStatus st;
    CHECK(healthGate(v, 1005, bb, 3, 0, 3, &st) == GateDecision::RETAKE && st == ReadingStatus::MISSING, "HR required by rule");
    CHECK(healthGate(v, 1005, bb, 3, 2, 3) == GateDecision::HOLD_FOR_REVIEW, "missing HR exhausts attempts");
    v.has_hr = true; v.hr = 52;
    CHECK(healthGate(v, 1005, bb, 3, 0) == GateDecision::HOLD_FOR_REVIEW, "slow pulse holds");
    v.hr = 70;
    CHECK(healthGate(v, 1005, bb, 3, 0) == GateDecision::PROCEED, "normal pulse passes");
    CHECK(healthGate(v, 1005, DEMO_RULES, DEMO_RULES_N, 0) == GateDecision::PROCEED, "general gate unchanged");
  }
  CHECK(verifyDelivery(true, 0.5f, 0.5f, 0.15f) == Delivery::CONFIRMED, "confirmed");
  CHECK(verifyDelivery(false, 0.0f, 0.5f, 0.15f) == Delivery::NOT_DISPENSED, "none");
  CHECK(verifyDelivery(true, 0.0f, 0.5f, 0.15f) == Delivery::DISAGREEMENT_IR_ONLY, "ir only");
  CHECK(verifyDelivery(false, 0.52f, 0.5f, 0.15f) == Delivery::DISAGREEMENT_WEIGHT_ONLY, "weight only");
  CHECK(verifyDelivery(true, 1.0f, 0.5f, 0.15f) == Delivery::WEIGHT_OUT_OF_RANGE, "two tablets");
  CHECK(verifyDelivery(true, NAN, 0.5f, 0.15f) == Delivery::DISAGREEMENT_IR_ONLY, "no weight sample");
}

static void bpParser() {
  int s, d, p;
  CHECK(parseBpLine("120,80,72", &s, &d, &p) && s == 120 && d == 80 && p == 72, "csv bp");
  CHECK(parseBpLine("SYS:135 DIA:88 PUL:70\r", &s, &d, &p) && s == 135 && d == 88 && p == 70, "labelled bp");
  CHECK(parseBpLine("118/76", &s, &d, &p) && s == 118 && d == 76 && p == -1, "slash bp without pulse");
  CHECK(!parseBpLine("ERR 3", &s, &d, &p), "single number rejected");
  CHECK(!parseBpLine("", &s, &d, &p), "empty rejected");
  CHECK(!parseBpLine("999,80", &s, &d, &p), "implausible rejected");
}

static void queue() {
  EventQueue q(100);
  for (int i = 0; i < 70; i++) q.push(EvType::HEARTBEAT, 0);
  CHECK(q.size() == EventQueue::CAP, "queue capped");
  CHECK(q.dropped() == 6, "drops counted");
  CHECK(q.at(0).seq == 106, "oldest kept is seq 106");
  q.popFront(10);
  CHECK(q.at(0).seq == 116, "pop after upload");
  CHECK(q.nextSeq() == 170, "seq monotonic");
  Event& env = q.push(EvType::ENV, 0);
  CHECK(isnan(env.value) && isnan(env.value2), "ENV defaults to missing, never 0 C / 0 %");
}

struct Sim {
  Inputs in{};
  Sim() { in.weight_valid = true; in.epoch = NOW; }
  void run(DispenseExecutor& ex, uint32_t ms, Outputs* last = nullptr) {
    for (uint32_t t = 0; t < ms; t += 50) {
      in.now_ms += 50;
      Outputs o = ex.step(in);
      if (last) *last = o;
    }
  }
};

static int countType(EventQueue& q, EvType t, const char* text = nullptr) {
  int n = 0;
  for (size_t i = 0; i < q.size(); i++)
    if (q.at(i).type == t && (!text || strcmp(q.at(i).text, text) == 0)) n++;
  return n;
}

static void executorHappyPath() {
  EventQueue q;
  DispenseExecutor ex(q);
  Sim s;
  CHECK(ex.accept(7, 42, 3, s.in), "accept command");
  Outputs o;
  s.run(ex, 300, &o);
  CHECK(o.servo_open && o.servo_compartment == 3, "servo opens compartment 3");
  s.in.ir_broken = true; s.run(ex, 100); s.in.ir_broken = false;
  s.run(ex, 1000, &o);
  CHECK(ex.state() == ExecState::CONVEYOR && o.motor_on, "conveyor running");
  CHECK(countType(q, EvType::EJECT) == 1 && q.at(1).flag_a, "EJECT with ir latched");
  s.in.limit_switch = true; s.in.tray_g = 0.49f;
  s.run(ex, 2000, &o);
  CHECK(!o.motor_on, "motor stops at limit");
  CHECK(ex.state() == ExecState::AWAIT_PICKUP, "awaiting pickup");
  CHECK(countType(q, EvType::CONVEYOR, "arrived") == 1, "arrival reported");
  s.in.presence = true; s.run(ex, 200); s.in.tray_g = 0.0f;
  s.run(ex, 200);
  CHECK(ex.state() == ExecState::IDLE, "idle after pickup");
  CHECK(countType(q, EvType::COLLECT) == 1, "collect reported");
  // duplicate command after a reboot-style re-send: no second servo cycle
  size_t before = q.size();
  CHECK(!ex.accept(7, 42, 3, s.in), "duplicate command rejected");
  CHECK(q.size() == before + 1 && q.at(before).type == EvType::CMD_ACK, "duplicate is re-acked only");
  CHECK(ex.state() == ExecState::IDLE, "no movement on duplicate");
}

static void executorJamAndDisagreement() {
  EventQueue q;
  DispenseExecutor ex(q);
  Sim s;
  ex.accept(8, 43, 1, s.in);
  s.run(ex, 1300);                       // no IR break
  s.run(ex, 16000);                      // no limit switch -> jam
  CHECK(ex.state() == ExecState::FAULT, "fault on conveyor timeout");
  CHECK(countType(q, EvType::CONVEYOR, "jam") == 1, "jam reported");
  CHECK(!ex.accept(8, 43, 1, s.in), "jammed command not re-run");
  CHECK(ex.accept(9, 44, 2, s.in), "new command accepted after fault");
  s.in.ir_broken = true; s.run(ex, 1300); s.in.ir_broken = false;
  s.in.limit_switch = true; s.in.tray_g = 0.0f;   // beam broke but nothing arrived
  s.run(ex, 2000);
  CHECK(ex.state() == ExecState::FAULT, "disagreement holds device in FAULT (no auto retry)");
  CHECK(!ex.accept(0, 1, 9, s.in), "invalid compartment rejected");
}

int main(int argc, char** argv) {
  gateVectors(argc > 1 ? argv[1] : "../../shared/gate_vectors.csv");
  lstmVectors(argc > 2 ? argv[2] : "../../shared/lstm_vectors.csv");
  verification();
  bpParser();
  queue();
  executorHappyPath();
  executorJamAndDisagreement();
  printf("%d passed, %d failed\n", passes, fails);
  return fails ? 1 : 0;
}
