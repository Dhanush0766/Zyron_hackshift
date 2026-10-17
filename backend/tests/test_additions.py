"""Tests for the MedGate additions beyond the IDF-B: drug-specific gate, meal condition,
patient card check, double-dose protection, per-medicine late window, early-warning trends,
voice prompts and the doctor report."""
import pytest

from medgate.core.drugs import DRUG_CLASSES, preset
from medgate.core.gate import GateDecision, health_gate
from medgate.core.prompts import PROMPTS, prompt
from medgate.core.trends import detect
from medgate.core.vitals import VitalsReading
from medgate.engine import EngineError

DEV = "SIM-001"
TAG = "MG-CARD-0001"


def reading(now, hr=72, spo2=98, bp_sys=120, bp_dia=80):
    return VitalsReading(hr=hr, spo2=spo2, bp_sys=bp_sys, bp_dia=bp_dia, quality=0.9, measured_at=now - 5)


# ---------------------------------------------------------------- core: drug-specific gate
def test_bp_drug_holds_low_bp_that_general_gate_passes():
    now = 1000.0
    r = reading(now, bp_sys=95, bp_dia=60)
    assert health_gate(r, now).decision == GateDecision.PROCEED                       # IDF-B general gate
    g = health_gate(r, now, rules=preset("ANTIHYPERTENSIVE")["rules"])
    assert g.decision == GateDecision.HOLD_FOR_REVIEW and "hypotension" in g.reasons[0]


def test_beta_blocker_holds_slow_pulse_and_requires_hr():
    now = 1000.0
    rules = preset("BETA_BLOCKER")["rules"]
    assert health_gate(reading(now, hr=52), now, rules=rules).decision == GateDecision.HOLD_FOR_REVIEW
    assert health_gate(reading(now, hr=70), now, rules=rules).decision == GateDecision.PROCEED
    g = health_gate(reading(now, hr=None), now, rules=rules)                           # missing HR never passes
    assert g.decision == GateDecision.RETAKE and "hr" in g.reasons[0]
    g = health_gate(reading(now, hr=None), now, rules=rules, attempts_so_far=2)
    assert g.decision == GateDecision.HOLD_FOR_REVIEW


def test_drug_rules_tighten_in_the_safe_direction():
    now = 1000.0
    rules = preset("ANTIHYPERTENSIVE")["rules"]
    r = reading(now, bp_sys=103, bp_dia=60)
    assert health_gate(r, now, rules=rules).decision == GateDecision.PROCEED
    # one tightening step raises the low-BP limit 100 -> 105: stricter, never looser
    assert health_gate(r, now, rules=rules, tighten_steps=1).decision == GateDecision.HOLD_FOR_REVIEW


@pytest.mark.parametrize("meal,decision", [(True, GateDecision.PROCEED), (False, GateDecision.HOLD_FOR_REVIEW),
                                           (None, GateDecision.RETAKE)])
def test_meal_condition(meal, decision):
    now = 1000.0
    assert health_gate(reading(now), now, requires_meal=True, meal_taken=meal).decision == decision


def test_precheck_failure_is_unusable_not_pass():
    now = 1000.0
    g = health_gate(reading(now), now, precheck_failures=["patient card not scanned"])
    assert g.decision == GateDecision.RETAKE
    g = health_gate(reading(now), now, precheck_failures=["x"], attempts_so_far=2)
    assert g.decision == GateDecision.HOLD_FOR_REVIEW


def test_every_drug_class_preset_is_valid():
    for name in DRUG_CLASSES:
        p = preset(name)
        assert p["rules"] and all(r["op"] in ("<", "<=", ">", ">=") for r in p["rules"])
    with pytest.raises(ValueError):
        preset("NOPE")


# ---------------------------------------------------------------- core: trends
def test_trend_flags_sustained_drift_only():
    spo2 = [98, 98, 97.5, 97, 96, 95.5, 95, 94.6]
    t = detect({"spo2": spo2, "bp_sys": [120] * 8})
    assert [x["field"] for x in t] == ["spo2"]
    assert detect({"spo2": [98] * 7 + [91]})[:1] == []                 # one spike is not a trend
    assert detect({"spo2": [98, 96, 95]}) == []                        # too few readings
    bp = detect({"bp_sys": [118, 121, 124, 127, 133, 138, 142, 146]})
    assert bp and bp[0]["field"] == "bp_sys" and bp[0]["change"] >= 15


# ---------------------------------------------------------------- core: prompts
def test_prompts_have_english_and_hindi_and_honest_manipuri_fallback():
    for key, t in PROMPTS.items():
        assert t["en"] and t["hi"], key
    p = prompt("HELD", "mni")
    assert p["fallback"] and p["text"] == PROMPTS["HELD"]["en"] and p["clip"] == "/voice/mni/HELD.wav"
    assert prompt("HELD", "hi")["fallback"] is False


# ---------------------------------------------------------------- engine
class Dev:
    def __init__(self, engine, clock):
        self.e, self.c, self.seq = engine, clock, 0

    def send(self, etype, **data):
        self.seq += 1
        return self.e.ingest(DEV, [{"seq": self.seq, "type": etype, "ts": self.c(), "data": data}])[0]["result"]

    def vitals(self, hr=72, spo2=98, bp_sys=120, bp_dia=80, **kw):
        return self.send("VITALS", hr=hr, spo2=spo2, bp_sys=bp_sys, bp_dia=bp_dia, quality=0.9,
                         measured_at=self.c() - 5, **kw)

    def deliver_and_collect(self, did):
        self.send("EJECT", dose_id=did, ir_beam=True)
        self.send("CONVEYOR", dose_id=did, status="arrived", tray_delta_g=0.6)
        self.send("COLLECT", dose_id=did, weight_removed=True, presence=True)


@pytest.fixture
def dev(engine, clock):
    return Dev(engine, clock)


def state(e, did):
    return e.db.one("SELECT state FROM doses WHERE id=?", (did,))["state"]


def due(e, mid):
    did = e.create_dose_now(mid)
    e.tick()
    return did


def test_engine_uses_each_medicines_own_rules(engine, dev):
    bp = engine.add_medication(DEV, "BP drug", 3, 0.6, 0.15, drug_class="ANTIHYPERTENSIVE")
    d_general, d_bp = due(engine, 1), None
    dev.vitals(bp_sys=95, bp_dia=60)
    assert state(engine, d_general) == "DISPENSE_SENT"           # general medicine: passes
    d_bp = due(engine, bp)
    dev.vitals(bp_sys=95, bp_dia=60)
    assert state(engine, d_bp) == "HELD"                          # BP drug: held by its own rule
    m = engine.db.one("SELECT * FROM medications WHERE id=?", (bp,))
    assert m["drug_class"] == "ANTIHYPERTENSIVE" and m["min_interval_h"] == 20


def test_meal_flow_retake_then_dispense(engine, dev):
    sulf = engine.add_medication(DEV, "Diabetes", 3, 0.9, 0.2, drug_class="SULFONYLUREA")
    did = due(engine, sulf)
    assert "RETAKE" in dev.vitals()
    assert engine.db.one("SELECT key FROM prompts ORDER BY id DESC LIMIT 1")["key"] == "CONFIRM_MEAL"
    assert "PROCEED" in dev.vitals(meal_taken=True)
    assert state(engine, did) == "DISPENSE_SENT"
    did2 = due(engine, sulf)
    dev.vitals(meal_taken=False)
    assert state(engine, did2) == "HELD"


def test_patient_card_required_once_registered(engine, dev):
    engine.set_patient(DEV, "Ama", TAG, "hi")
    did = due(engine, 1)
    assert "RETAKE" in dev.vitals()                                # no card
    assert engine.db.one("SELECT key, lang FROM prompts ORDER BY id DESC LIMIT 1") == {"key": "SCAN_CARD", "lang": "hi"}
    dev.vitals(patient_tag="MG-CARD-9999")                         # wrong card
    assert engine.db.one("SELECT COUNT(*) n FROM alerts WHERE kind='PATIENT_MISMATCH' AND resolved_at IS NULL")["n"] == 1
    assert state(engine, did) == "RETAKE"
    assert "PROCEED" in dev.vitals(patient_tag=TAG.lower())       # right card (case-insensitive UID)
    v = engine.db.one("SELECT status FROM vitals WHERE patient_tag='MG-CARD-9999'")
    assert v["status"] == "WRONG_PATIENT"


def test_wrong_card_three_times_holds(engine, dev):
    engine.set_patient(DEV, "Ama", TAG)
    did = due(engine, 1)
    for _ in range(3):
        dev.vitals(patient_tag="OTHER")
    assert state(engine, did) == "HELD"


def test_double_dose_protection_and_named_override(engine, dev):
    bp = engine.add_medication(DEV, "BP drug", 3, 0.6, 0.15, drug_class="ANTIHYPERTENSIVE")
    d1 = due(engine, bp)
    dev.vitals()
    dev.deliver_and_collect(d1)
    assert state(engine, d1) == "COLLECTED"
    d2 = due(engine, bp)
    dev.vitals()
    assert state(engine, d2) == "HELD"
    reasons = engine.db.one("SELECT gate_reasons FROM doses WHERE id=?", (d2,))
    assert engine.db.one("SELECT COUNT(*) n FROM commands WHERE dose_id=?", (d2,))["n"] == 0
    assert "double-dose" in reasons["gate_reasons"]
    assert any(h["id"] == d2 for h in engine.doctor_report(DEV)["holds"])     # shows up in the doctor report
    engine.review(d2, "approve", "Dr Singh", "patient vomited first dose")
    assert state(engine, d2) == "DISPENSE_SENT"


def test_double_dose_allows_after_interval(engine, dev, clock):
    m = engine.add_medication(DEV, "Twice daily", 3, 0.6, 0.15, min_interval_h=1)
    d1 = due(engine, m)
    dev.vitals()
    dev.deliver_and_collect(d1)
    clock.advance(60 * engine.cfg.escalation.minute_s + 1)          # 1 h on the demo time scale
    d2 = due(engine, m)
    dev.vitals()
    assert state(engine, d2) == "DISPENSE_SENT"


def test_per_medicine_late_window(engine, clock):
    m = engine.add_medication(DEV, "Strict timing", 3, 0.6, 0.15, late_window_min=10)
    did = due(engine, m)
    clock.advance(10 * engine.cfg.escalation.minute_s + 1)
    engine.tick()
    assert state(engine, did) == "MISSED"
    with pytest.raises(EngineError):
        engine.add_medication(DEV, "bad", 4, 0.6, 0.15, late_window_min=1)


def test_trend_alert_raised_and_auto_resolved(engine, dev):
    for spo2 in (98, 98, 97.5, 97, 96, 95.5, 95, 94.6):
        dev.vitals(spo2=spo2)
    a = engine.db.q("SELECT * FROM alerts WHERE kind='TREND_SPO2' AND resolved_at IS NULL")
    assert len(a) == 1 and "trending down" in a[0]["message"]
    for _ in range(8):
        dev.vitals(spo2=98)
    assert not engine.db.q("SELECT * FROM alerts WHERE kind='TREND_SPO2' AND resolved_at IS NULL")


def test_prompts_follow_the_dose_and_are_delivered_once(engine, dev):
    did = due(engine, 1)
    dev.vitals()
    dev.send("EJECT", dose_id=did, ir_beam=True)
    dev.send("CONVEYOR", dose_id=did, status="arrived", tray_delta_g=0.5)
    dev.send("COLLECT", dose_id=did, weight_removed=True, presence=True)
    keys = [r["key"] for r in engine.db.q("SELECT key FROM prompts WHERE dose_id=? ORDER BY id", (did,))]
    assert keys == ["DUE", "DELIVERED", "COLLECTED"]
    first = engine.pending_prompts(DEV)
    assert [p["key"] for p in first] == keys and engine.pending_prompts(DEV) == []


def test_set_patient_validates(engine):
    with pytest.raises(EngineError):
        engine.set_patient(DEV, "x", None, "fr")
    with pytest.raises(EngineError):
        engine.set_patient("NOPE", "x", None)


def test_doctor_report_numbers_come_from_records(engine, dev):
    did = due(engine, 1)
    dev.vitals()
    dev.deliver_and_collect(did)
    d2 = due(engine, 1)
    dev.vitals(bp_sys=175, bp_dia=95)
    r = engine.doctor_report(DEV, 30)
    assert r["adherence_pct"] == 100.0 and r["by_state"]["COLLECTED"] == 1 and r["by_state"]["HELD"] == 1
    assert r["holds"][0]["id"] == d2 and r["vitals"]["bp_sys"]["max"] == 175
    assert r["audit"]["ok"]
    with pytest.raises(EngineError):
        engine.doctor_report("NOPE")


# ---------------------------------------------------------------- round 2: expiry, SOS, patient screen, calendar, digest
def test_expired_medicine_is_never_dispensed(engine, dev, clock):
    from datetime import date, timedelta
    today = date.fromtimestamp(clock())
    engine.set_expiry(1, (today - timedelta(days=1)).isoformat())
    assert engine.db.one("SELECT COUNT(*) n FROM alerts WHERE kind='MEDICINE_EXPIRED' AND resolved_at IS NULL")["n"] == 1
    did = due(engine, 1)
    dev.vitals()
    d = engine.db.one("SELECT state, fault FROM doses WHERE id=?", (did,))
    assert d == {"state": "FAULT", "fault": "EXPIRED_MEDICINE"}
    assert engine.db.one("SELECT COUNT(*) n FROM commands WHERE dose_id=?", (did,))["n"] == 0
    engine.refill(1, 30, expiry_date=(today + timedelta(days=200)).isoformat())     # fresh strip clears it
    assert not engine.db.q("SELECT 1 FROM alerts WHERE kind IN ('MEDICINE_EXPIRED','EXPIRY_SOON') AND resolved_at IS NULL")


def test_expiry_soon_warning_and_validation(engine, clock):
    from datetime import date, timedelta
    engine.set_expiry(1, (date.fromtimestamp(clock()) + timedelta(days=5)).isoformat())
    a = engine.db.one("SELECT message FROM alerts WHERE kind='EXPIRY_SOON' AND resolved_at IS NULL")
    assert a and "5 day(s) left" in a["message"]
    with pytest.raises(EngineError):
        engine.set_expiry(1, "31-12-2026")


def test_sos_from_device_event_and_dashboard(engine, dev):
    engine.set_patient(DEV, "Ima Tombi", None, "hi")
    dev.send("SOS", note="hand held over tray")
    a = engine.db.q("SELECT * FROM alerts WHERE kind='SOS' AND resolved_at IS NULL")
    assert len(a) == 1 and "Ima Tombi" in a[0]["message"]
    p = engine.db.one("SELECT key, lang FROM prompts ORDER BY id DESC LIMIT 1")
    assert p == {"key": "SOS_ACK", "lang": "hi"}
    assert engine.stats()["open_sos"] == 1
    assert engine.caretaker_portal()["patients"][0]["sos_open"] is True
    with pytest.raises(EngineError):
        engine.sos("NOPE")


def test_patient_screen_calendar_and_digest(engine, dev):
    engine.set_patient(DEV, "Ima Tombi", None)
    did = due(engine, 1)
    dev.vitals()
    dev.deliver_and_collect(did)
    d2 = due(engine, 1)
    dev.vitals(bp_sys=175, bp_dia=95)
    p = engine.patient_screen(DEV)
    assert p["taken_today"] == 1 and p["total_today"] == 2 and p["active"]["id"] == d2
    assert p["prompt"]["key"] == "HELD"
    cal = engine.adherence_calendar(DEV, 7)
    assert len(cal["days"]) == 7 and sum(c["taken"] for c in cal["cells"]) == 1 and sum(c["held"] for c in cal["cells"]) == 1
    text = engine.family_digest(DEV)["text"]
    assert "Ima Tombi" in text and "taken today: 1 of 2" in text and "HELD for doctor check" in text
