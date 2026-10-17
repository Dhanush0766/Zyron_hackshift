import pytest

from medgate.engine import EngineError

DEV = "SIM-001"


class Dev:
    """Minimal scripted device for engine tests."""
    def __init__(self, engine, clock):
        self.e, self.c, self.seq = engine, clock, 0

    def send(self, etype, **data):
        self.seq += 1
        return self.e.ingest(DEV, [{"seq": self.seq, "type": etype, "ts": self.c(), "data": data}])[0]["result"]

    def vitals(self, hr=72, spo2=98, bp_sys=120, bp_dia=80, quality=0.9, age=5, **kw):
        return self.send("VITALS", hr=hr, spo2=spo2, bp_sys=bp_sys, bp_dia=bp_dia, quality=quality,
                         measured_at=self.c() - age, **kw)


def dose(e, did):
    return e.db.one("SELECT * FROM doses WHERE id=?", (did,))


@pytest.fixture
def dev(engine, clock):
    return Dev(engine, clock)


def due_dose(engine):
    did = engine.create_dose_now(1)
    engine.tick()
    assert dose(engine, did)["state"] == "DUE"
    return did


def test_happy_path_end_to_end(engine, dev, clock):
    did = due_dose(engine)
    assert "PROCEED" in dev.vitals()
    assert dose(engine, did)["state"] == "DISPENSE_SENT"
    cmds = engine.pending_commands(DEV)
    assert cmds[0]["command_id"] == 1 and cmds[0]["dose_id"] == did and cmds[0]["compartment"] == 1
    dev.send("CMD_ACK", command_id=1)
    dev.send("EJECT", dose_id=did, ir_beam=True)
    dev.send("CONVEYOR", dose_id=did, status="started")
    assert dev.send("CONVEYOR", dose_id=did, status="arrived", tray_delta_g=0.48) == "CONFIRMED"
    assert dose(engine, did)["state"] == "DELIVERED"
    assert dev.send("COLLECT", dose_id=did, weight_removed=False, presence=True) == "NOT_COLLECTED"
    assert dev.send("COLLECT", dose_id=did, weight_removed=True, presence=True) == "COLLECTED"
    d = dose(engine, did)
    assert d["state"] == "COLLECTED" and d["collection_verdict"] == "COLLECTED"
    assert engine.stats()["adherence_pct"] == 100.0
    assert engine.verify_audit()["ok"]


def test_no_duplicate_dispense_on_second_reading(engine, dev):
    did = due_dose(engine)
    dev.vitals()
    dev.vitals()  # second valid reading must not create a second command
    assert len(engine.db.q("SELECT * FROM commands WHERE dose_id=?", (did,))) == 1


def test_commands_redelivered_until_acked_but_not_duplicated(engine, dev):
    due_dose(engine)
    dev.vitals()
    assert len(engine.pending_commands(DEV)) == 1
    assert len(engine.pending_commands(DEV)) == 1  # device rebooted before ack: same command id
    dev.send("CMD_ACK", command_id=1)
    assert engine.pending_commands(DEV) == []


def test_replayed_events_are_idempotent(engine, dev, clock):
    did = due_dose(engine)
    batch = [{"seq": 10, "type": "VITALS", "ts": clock(),
              "data": {"hr": 72, "spo2": 98, "bp_sys": 120, "bp_dia": 80, "quality": 0.9, "measured_at": clock()}},
             {"seq": 11, "type": "EJECT", "ts": clock(), "data": {"dose_id": did, "ir_beam": True}}]
    first = engine.ingest(DEV, batch)
    again = engine.ingest(DEV, batch)  # offline buffer replayed after reconnect
    assert [r["result"] for r in again] == ["DUPLICATE_IGNORED"] * 2
    assert "PROCEED" in first[0]["result"]
    assert dose(engine, did)["state"] == "EJECTED"


def test_retake_then_hold_after_three_bad_readings(engine, dev):
    did = due_dose(engine)
    dev.vitals(quality=0.2)
    assert dose(engine, did)["state"] == "RETAKE"
    dev.vitals(spo2=0)  # sensor error value
    assert dose(engine, did)["state"] == "RETAKE"
    dev.vitals(age=900)  # stale
    d = dose(engine, did)
    assert d["state"] == "HELD" and d["physio_hold"] == 0
    assert engine.db.q("SELECT * FROM commands") == []


def test_physiological_hold_escalates_to_caregiver_immediately(engine, dev):
    did = due_dose(engine)
    dev.vitals(spo2=88)
    d = dose(engine, did)
    assert d["state"] == "HELD" and d["physio_hold"] == 1 and d["escalation_level"] == 3
    assert engine.db.q("SELECT * FROM commands") == []
    lv = [e["level"] for e in engine.db.q("SELECT level FROM escalations WHERE dose_id=?", (did,))]
    assert lv == [1, 3]


def test_human_review_approve_and_withhold(engine, dev):
    did = due_dose(engine)
    dev.vitals(spo2=88)
    with pytest.raises(EngineError):
        engine.review(did, "approve", "")  # reviewer identity required
    engine.review(did, "approve", "Dr Demo", "checked by phone")
    assert dose(engine, did)["state"] == "DISPENSE_SENT"
    with pytest.raises(EngineError):
        engine.review(did, "approve", "Dr Demo")  # cannot approve twice
    did2 = due_dose(engine)
    dev.vitals(spo2=88)
    engine.review(did2, "withhold", "Dr Demo")
    assert dose(engine, did2)["state"] == "WITHHELD"
    assert len(engine.db.q("SELECT * FROM commands")) == 1


@pytest.mark.parametrize("ir,delta,state,verdict", [
    (True, 0.0, "REVIEW_REQUIRED", "DISAGREEMENT_IR_ONLY"),
    (False, 0.5, "REVIEW_REQUIRED", "DISAGREEMENT_WEIGHT_ONLY"),
    (True, 1.0, "REVIEW_REQUIRED", "WEIGHT_OUT_OF_RANGE"),
    (False, 0.01, "FAULT", "NOT_DISPENSED"),
])
def test_sensor_disagreement_never_counts_as_delivered(engine, dev, ir, delta, state, verdict):
    did = due_dose(engine)
    dev.vitals()
    dev.send("EJECT", dose_id=did, ir_beam=ir)
    assert dev.send("CONVEYOR", dose_id=did, status="arrived", tray_delta_g=delta) == verdict
    d = dose(engine, did)
    assert d["state"] == state and d["escalation_level"] >= 3
    assert len(engine.db.q("SELECT * FROM commands")) == 1  # no automatic re-dispense


def test_watchdogs(engine, dev, clock):
    did = due_dose(engine)
    dev.vitals()
    clock.advance(31)
    engine.tick()
    assert dose(engine, did)["fault"] == "DISPENSE_TIMEOUT"
    did2 = due_dose(engine)
    dev.vitals()
    dev.send("EJECT", dose_id=did2, ir_beam=True)
    dev.send("CONVEYOR", dose_id=did2, status="started")
    clock.advance(31)
    engine.tick()
    assert dose(engine, did2)["fault"] == "CONVEYOR_TIMEOUT"


def test_conveyor_jam_event(engine, dev):
    did = due_dose(engine)
    dev.vitals()
    dev.send("EJECT", dose_id=did, ir_beam=True)
    dev.send("CONVEYOR", dose_id=did, status="jam")
    assert dose(engine, did)["fault"] == "CONVEYOR_JAM"


def deliver(engine, dev, did):
    dev.send("EJECT", dose_id=did, ir_beam=True)
    dev.send("CONVEYOR", dose_id=did, status="arrived", tray_delta_g=0.5)
    assert dose(engine, did)["state"] == "DELIVERED"


def test_uncollected_escalates_then_missed_then_tightens(engine, dev, clock):
    # demo scale: window 30 s (default plan), L2 +30 s, L3 +60 s, L4 +90 s, MISSED after +30 s grace
    did = due_dose(engine)
    dev.vitals()
    deliver(engine, dev, did)
    clock.advance(65); engine.tick()
    assert dose(engine, did)["escalation_level"] == 2
    clock.advance(30); engine.tick()
    assert dose(engine, did)["escalation_level"] == 3
    clock.advance(30); engine.tick()
    assert dose(engine, did)["escalation_level"] == 4
    clock.advance(30); engine.tick()
    d = dose(engine, did)
    assert d["state"] == "MISSED"
    assert engine.tighten_steps(1, d["session"]) == 1


def test_no_vitals_within_window_is_missed_not_dispensed(engine, clock):
    did = due_dose(engine)
    clock.advance(301)
    engine.tick()
    assert dose(engine, did)["state"] == "MISSED"
    assert engine.db.q("SELECT * FROM commands") == []


def test_events_for_wrong_or_late_dose_rejected_and_logged(engine, dev):
    did = due_dose(engine)
    assert dev.send("EJECT", dose_id=did, ir_beam=True).startswith("REJECTED")  # not yet dispensed
    assert dev.send("EJECT", dose_id=999, ir_beam=True).startswith("REJECTED")
    assert engine.db.one("SELECT COUNT(*) n FROM audit WHERE action='EVENT_REJECTED'")["n"] == 2


def test_simulator_and_hardware_cannot_be_confused(engine):
    with pytest.raises(EngineError):
        engine.ensure_device("SIM-001", "HARDWARE")
    with pytest.raises(EngineError):
        engine.ingest("SIM-001", [], kind_expected="HARDWARE")


def test_medication_validation(engine):
    with pytest.raises(EngineError):
        engine.add_medication("SIM-001", "dup", 1, 0.5, 0.15)  # compartment taken
    with pytest.raises(EngineError):
        engine.add_medication("SIM-001", "light", 2, 0.2, 0.15)  # indistinguishable weights
    with pytest.raises(EngineError):
        engine.add_medication("SIM-001", "bad", 3, 0.5, 0.1, rules=[{"field": "temp", "op": "<", "value": 1}])


def test_audit_chain_detects_tampering(engine, dev):
    due_dose(engine)
    dev.vitals()
    assert engine.verify_audit()["ok"]
    engine.db.x("UPDATE audit SET detail='{\"forged\":1}' WHERE id=3")
    res = engine.verify_audit()
    assert res["ok"] is False and res["broken_at_id"] == 3


def test_schedule_generates_doses_idempotently(engine, clock):
    from datetime import datetime
    hhmm = datetime.fromtimestamp(clock() + 600).strftime("%H:%M")
    engine.add_schedule(1, hhmm)
    engine.generate_doses()
    engine.generate_doses()
    assert engine.db.one("SELECT COUNT(*) n FROM doses WHERE schedule_id=1")["n"] == 1


def test_bp_module_missing_blocks_dispense(engine, dev):
    did = due_dose(engine)
    assert "RETAKE" in dev.vitals(bp_sys=None, bp_dia=None)
    assert engine.db.q("SELECT * FROM commands") == []


def test_high_bp_suppresses_dispense(engine, dev):
    did = due_dose(engine)
    dev.vitals(bp_sys=172, bp_dia=96)
    d = dose(engine, did)
    assert d["state"] == "HELD" and d["physio_hold"] == 1
    assert engine.db.q("SELECT * FROM commands") == []


def test_tray_emptied_without_hand_is_not_compliance(engine, dev):
    did = due_dose(engine)
    dev.vitals()
    deliver(engine, dev, did)
    assert dev.send("COLLECT", dose_id=did, weight_removed=True, presence=False) == "TRAY_EMPTIED_NO_PRESENCE"
    d = dose(engine, did)
    assert d["state"] == "REVIEW_REQUIRED" and d["escalation_level"] >= 3


def test_missed_pickup_tightens_next_same_session_gate_then_resets(engine, dev, clock):
    did = due_dose(engine)
    dev.vitals()
    deliver(engine, dev, did)
    clock.advance(200); engine.tick()
    assert dose(engine, did)["state"] == "MISSED"
    # borderline reading passes normally (94.5 >= 94) but not after one tightening step (needs >= 95)
    did2 = due_dose(engine)
    dev.vitals(spo2=94.5)
    d2 = dose(engine, did2)
    assert d2["state"] == "HELD" and d2["tighten_steps"] == 1
    engine.review(did2, "approve", "Dr Demo")
    deliver(engine, dev, did2)
    dev.send("COLLECT", dose_id=did2, weight_removed=True, presence=True)
    assert engine.tighten_steps(1, d2["session"]) == 0       # successful pickup resets
    did3 = due_dose(engine)
    dev.vitals(spo2=94.5)
    assert dose(engine, did3)["state"] == "DISPENSE_SENT"


def test_recent_gate_failures_contract_l3_l4(engine, dev, clock):
    for _ in range(2):                          # two gate failures in recent sessions
        d = due_dose(engine)
        dev.vitals(spo2=88)
        engine.review(d, "withhold", "Dr Demo")
        clock.advance(10)
    did = due_dose(engine)
    assert dose(engine, did)["gate_fail_rate"] >= 0.4
    dev.vitals()
    deliver(engine, dev, did)
    clock.advance(30 + 31); engine.tick()       # window 30 s; contracted L3 offset = 30 s (normally 60 s)
    assert dose(engine, did)["escalation_level"] == 3
    esc = engine.db.q("SELECT reason FROM escalations WHERE dose_id=? AND level=3", (did,))
    assert "halved" in esc[0]["reason"]


def test_schedule_food_session_and_toggle(engine, clock):
    from datetime import datetime
    hhmm = datetime.fromtimestamp(clock() + 600).strftime("%H:%M")
    sid = engine.add_schedule(1, hhmm, food="AFTER_FOOD")
    s = engine.db.one("SELECT * FROM schedules WHERE id=?", (sid,))
    assert s["food"] == "AFTER_FOOD" and s["session"] in ("MORNING", "AFTERNOON", "NIGHT")
    d = engine.db.one("SELECT * FROM doses WHERE schedule_id=?", (sid,))
    assert d["food"] == "AFTER_FOOD" and d["session"] == s["session"]
    with pytest.raises(EngineError):
        engine.add_schedule(1, hhmm, food="WITH_TEA")
    engine.set_schedule_active(sid, False)
    assert engine.db.one("SELECT active FROM schedules WHERE id=?", (sid,))["active"] == 0


def test_portals(engine, dev, clock):
    d = due_dose(engine)
    dev.vitals(bp_sys=172, bp_dia=96)
    engine.review(d, "withhold", "Dr Demo")
    did = due_dose(engine)
    dev.vitals()
    deliver(engine, dev, did)
    clock.advance(200); engine.tick()
    doc = engine.doctor_portal()
    ev = {e["id"]: e for e in doc["events"]}
    assert ev[d]["gate_reasons"]["readings"]["bp_sys"] == 172
    assert ev[d]["gate_reasons"]["rules"][1]["value"] == 160
    assert doc["daily_trend"][0]["gate_failure_rate"] == 0.5
    p = engine.caretaker_portal()["patients"][0]
    assert p["missed_7d"] == 1 and p["gate_failure_rate_last5"] == 0.5
    assert p["urgency_score"] == round(1.5 * 1 * p["max_escalation_level_7d"], 2)
    assert p["urgency_score_idf_formula"] == round(0.5 * 1 * p["max_escalation_level_7d"], 2)


# ---------------------------------------------------------------- stock, storage, weekdays
def med(e):
    return e.db.one("SELECT * FROM medications WHERE id=1")


def open_alerts(e, kind):
    return e.db.q("SELECT * FROM alerts WHERE kind=? AND resolved_at IS NULL", (kind,))


def test_stock_decrements_and_refill_alert(engine, dev):
    engine.refill(1, 6)                       # threshold is 5
    did = due_dose(engine)
    dev.vitals()
    deliver(engine, dev, did)
    assert med(engine)["stock"] == 5
    a = open_alerts(engine, "REFILL_NEEDED")
    assert len(a) == 1 and "5 tablet" in a[0]["message"]
    did2 = due_dose(engine)                   # second low-stock dose updates, not duplicates, the alert
    dev.vitals()
    deliver(engine, dev, did2)
    assert med(engine)["stock"] == 4 and len(open_alerts(engine, "REFILL_NEEDED")) == 1
    engine.refill(1, 30)
    assert open_alerts(engine, "REFILL_NEEDED") == []


def test_two_tablets_consume_two_and_nothing_dispensed_consumes_none(engine, dev):
    did = due_dose(engine)
    dev.vitals()
    dev.send("EJECT", dose_id=did, ir_beam=True)
    dev.send("CONVEYOR", dose_id=did, status="arrived", tray_delta_g=1.0)
    assert med(engine)["stock"] == 28
    did2 = due_dose(engine)
    dev.vitals()
    dev.send("EJECT", dose_id=did2, ir_beam=False)
    dev.send("CONVEYOR", dose_id=did2, status="arrived", tray_delta_g=0.0)
    assert med(engine)["stock"] == 28


def test_empty_compartment_never_dispenses(engine, dev):
    engine.refill(1, 0)
    did = due_dose(engine)
    dev.vitals()
    d = dose(engine, did)
    assert d["state"] == "FAULT" and d["fault"] == "EMPTY_COMPARTMENT"
    assert engine.db.q("SELECT * FROM commands") == []
    assert len(open_alerts(engine, "REFILL_NEEDED")) == 1


def test_days_of_supply(engine, clock):
    engine.add_schedule(1, "08:00", days=["MON", "WED", "FRI"])
    engine.add_schedule(1, "20:00")
    assert engine.days_of_supply(1, 30) == round(30 / (3 / 7 + 1), 1)
    with pytest.raises(EngineError):
        engine.add_schedule(1, "09:00", days=["FUNDAY"])


def test_weekday_schedule_skips_other_days(engine, clock):
    from datetime import datetime, timedelta
    now = datetime.fromtimestamp(clock())
    later = now + timedelta(minutes=10)
    today = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[later.weekday()]
    other = [d for d in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN") if d != today][0]
    hhmm = later.strftime("%H:%M")
    s_today = engine.add_schedule(1, hhmm, days=[today])
    s_other = engine.add_schedule(1, hhmm, days=[other])
    n = lambda sid: engine.db.one("SELECT COUNT(*) n FROM doses WHERE schedule_id=?", (sid,))["n"]
    assert n(s_today) == 1
    tomorrow = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[(later + timedelta(days=1)).weekday()]
    assert n(s_other) == (1 if other == tomorrow and (later + timedelta(days=1)).timestamp() <= clock() + 86400 else 0)


def test_storage_alerts_raise_dedupe_and_auto_resolve(engine, dev):
    assert dev.send("ENV", temp_c=34.5, humidity=78) == "OUT_OF_RANGE"
    dev.send("ENV", temp_c=35.0, humidity=79)
    assert len(open_alerts(engine, "STORAGE_TEMP")) == 1 and len(open_alerts(engine, "STORAGE_HUMIDITY")) == 1
    assert "35.0" in open_alerts(engine, "STORAGE_TEMP")[0]["message"]
    assert dev.send("ENV", temp_c=25.0, humidity=50) == "OK"
    assert open_alerts(engine, "STORAGE_TEMP") == [] and open_alerts(engine, "STORAGE_HUMIDITY") == []


def test_storage_sensor_invalid_is_not_normal(engine, dev):
    assert dev.send("ENV", temp_c=None, humidity=50) == "INVALID"
    assert len(open_alerts(engine, "STORAGE_SENSOR")) == 1
    assert open_alerts(engine, "STORAGE_TEMP") == []
