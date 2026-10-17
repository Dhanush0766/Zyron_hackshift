import os
import time

import pytest
from fastapi.testclient import TestClient

KEY = {"X-Device-Key": "dev-key-change-me"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDGATE_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MEDGATE_NO_TICKER", "1")
    from medgate import api
    with TestClient(api.app) as c:
        yield c, api


def test_seed_and_overview(client):
    c, _ = client
    o = c.get("/api/overview").json()
    assert o["devices"][0]["kind"] == "SIMULATOR"
    assert o["audit"]["ok"] and o["stats"]["adherence_pct"] is None  # no fake numbers
    assert len(c.get("/api/medications").json()) == 5


def test_device_api_requires_key(client):
    c, _ = client
    assert c.get("/api/device/SIM-001/commands").status_code == 401
    assert c.get("/api/device/SIM-001/commands", headers={"X-Device-Key": "wrong"}).status_code == 401
    assert c.get("/api/device/SIM-001/commands", headers=KEY).status_code == 200


def test_validation_errors(client):
    c, _ = client
    assert c.post("/api/medications", json={"device_id": "SIM-001", "name": "x", "compartment": 9,
                                            "unit_weight_g": 1, "weight_tol_g": 0.1}).status_code == 422
    assert c.post("/api/schedules", json={"medication_id": 1, "time_hhmm": "8am"}).status_code == 422
    assert c.post("/api/device/SIM-001/events", headers=KEY,
                  json={"events": [{"seq": 1, "type": "BOGUS"}]}).status_code == 422
    assert c.post("/api/doses/999/review", json={"decision": "approve", "reviewer": "a"}).status_code == 404


def test_full_flow_over_http_device_protocol(client):
    c, api = client
    did = c.post("/api/doses", json={"medication_id": 1}).json()["id"]
    api.engine.tick()
    now = time.time()
    r = c.post("/api/device/SIM-001/events", headers=KEY, json={"events": [
        {"seq": 1, "type": "VITALS", "data": {"hr": 70, "spo2": 98, "bp_sys": 120, "bp_dia": 80, "quality": 0.9, "measured_at": now, "patient_tag": "MG-CARD-0001"}}]}).json()
    assert "PROCEED" in r["results"][0]["result"]
    cmd = c.get("/api/device/SIM-001/commands", headers=KEY).json()["commands"][0]
    evs = [{"seq": 2, "type": "CMD_ACK", "data": {"command_id": cmd["command_id"]}},
           {"seq": 3, "type": "EJECT", "data": {"dose_id": did, "ir_beam": True}},
           {"seq": 4, "type": "CONVEYOR", "data": {"dose_id": did, "status": "arrived", "tray_delta_g": 0.5}},
           {"seq": 5, "type": "COLLECT", "data": {"dose_id": did, "weight_removed": True, "presence": True}}]
    c.post("/api/device/SIM-001/events", headers=KEY, json={"events": evs})
    det = c.get(f"/api/doses/{did}").json()
    assert det["dose"]["state"] == "COLLECTED"
    actions = [a["action"] for a in det["audit"]]
    assert actions[:2] == ["DOSE_CREATED", "DOSE_DUE"] and "COMMAND_ISSUED" in actions
    assert c.get("/api/audit/verify").json()["ok"]


def test_review_endpoint_and_illegal_state(client):
    c, api = client
    did = c.post("/api/doses", json={"medication_id": 1}).json()["id"]
    api.engine.tick()
    c.post("/api/device/SIM-001/events", headers=KEY, json={"events": [
        {"seq": 1, "type": "VITALS", "data": {"hr": 70, "spo2": 86, "bp_sys": 120, "bp_dia": 80, "quality": 0.9, "measured_at": time.time(), "patient_tag": "MG-CARD-0001"}}]})
    assert c.get(f"/api/doses/{did}").json()["dose"]["state"] == "HELD"
    assert c.post(f"/api/doses/{did}/review", json={"decision": "withhold", "reviewer": "Dr A"}).status_code == 200
    assert c.post(f"/api/doses/{did}/review", json={"decision": "approve", "reviewer": "Dr A"}).status_code == 409


@pytest.mark.parametrize("name,final", [
    ("normal", "COLLECTED"), ("abnormal_vitals", "HELD"), ("low_spo2", "HELD"), ("missing_bp", "RETAKE"),
    ("borderline", "COLLECTED"), ("removed_no_hand", "REVIEW_REQUIRED"), ("bad_readings", "HELD"),
    ("low_stock", "FAULT"),
    ("sensor_disagreement", "REVIEW_REQUIRED"), ("two_tablets", "REVIEW_REQUIRED"),
    ("conveyor_jam", "FAULT"), ("uncollected", "DELIVERED"), ("offline_replay", "COLLECTED"),
    ("bp_drug_low_bp", "HELD"), ("beta_blocker_slow_pulse", "HELD"), ("beta_blocker_no_hr", "RETAKE"),
    ("diabetes_no_meal", "HELD"), ("diabetes_meal_ok", "COLLECTED"), ("double_dose", "HELD"),
    ("no_card", "COLLECTED"), ("wrong_patient", "RETAKE"), ("trend_decline", "COLLECTED"),
    ("expired_medicine", "FAULT"),
])
def test_every_simulator_scenario(client, name, final):
    c, api = client
    from medgate.simulator import run_scenario
    st = run_scenario(api.engine, name, step_s=0)
    assert not any(l.startswith("ERROR") for l in st["log"]), st["log"]
    assert c.get(f"/api/doses/{st['dose_id']}").json()["dose"]["state"] == final
    if name == "offline_replay":
        assert any("DUPLICATE_IGNORED" in l for l in st["log"])
    ev = c.get("/api/events").json()
    assert ev and all(e["device_id"] == "SIM-001" for e in ev)


def test_simulator_executes_human_approved_command(client):
    c, api = client
    from medgate.simulator import execute_pending, run_scenario
    st = run_scenario(api.engine, "abnormal_vitals", step_s=0)
    did = st["dose_id"]
    c.post(f"/api/doses/{did}/review", json={"decision": "approve", "reviewer": "Dr A"})
    status = {}
    execute_pending(api.engine, status, step_s=0)
    assert status["dose_id"] == did
    assert c.get(f"/api/doses/{did}").json()["dose"]["state"] == "COLLECTED"


def test_portal_and_schedule_endpoints(client):
    c, _ = client
    assert c.post("/api/schedules", json={"medication_id": 1, "time_hhmm": "08:00", "food": "BEFORE_FOOD"}).status_code == 200
    assert c.post("/api/schedules", json={"medication_id": 1, "time_hhmm": "08:00", "food": "NOPE"}).status_code == 422
    sid = c.get("/api/schedules").json()[0]["id"]
    assert c.post(f"/api/schedules/{sid}/active", json={"active": False}).status_code == 200
    assert c.get("/api/schedules").json()[0]["active"] == 0
    assert "events" in c.get("/api/portal/doctor").json()
    assert c.get("/api/portal/caretaker").json()["patients"][0]["device"]["id"] == "SIM-001"
    assert c.get("/api/compliance/SIM-001").json()["model"] in ("DEFAULT", "LSTM")
    assert c.get("/api/rules/demo").json()["rules"][0]["value"] == 94


def test_storage_scenarios_and_alert_endpoints(client):
    c, api = client
    from medgate.simulator import run_scenario
    run_scenario(api.engine, "hot_storage", step_s=0)
    a = c.get("/api/alerts").json()
    store = [x for x in a["open"] if x["kind"].startswith("STORAGE")]
    assert {x["kind"] for x in store} == {"STORAGE_TEMP", "STORAGE_HUMIDITY"} and a["limits"]["max_temp_c"] == 30
    assert {x["kind"] for x in a["open"]} - {x["kind"] for x in store} == {"EXPIRY_SOON"}   # seeded demo strip
    aid = store[0]["id"]
    assert c.post(f"/api/alerts/{aid}/resolve", json={"by": "Asha"}).status_code == 200
    assert c.post(f"/api/alerts/{aid}/resolve", json={"by": "Asha"}).status_code == 404
    run_scenario(api.engine, "storage_ok", step_s=0)
    assert [x["kind"] for x in c.get("/api/alerts").json()["open"]] == ["EXPIRY_SOON"]
    assert len(c.get("/api/env").json()) == 2
    assert c.get("/api/doses").json() == []          # storage checks create no doses


def test_refill_and_days_endpoints(client):
    c, _ = client
    assert c.post("/api/medications/1/refill", json={"count": 12}).status_code == 200
    assert c.post("/api/medications/1/refill", json={"count": -1}).status_code == 422
    assert c.post("/api/schedules", json={"medication_id": 1, "time_hhmm": "08:00", "days": ["MON", "THU"]}).status_code == 200
    assert c.post("/api/schedules", json={"medication_id": 1, "time_hhmm": "08:00", "days": ["XYZ"]}).status_code == 422
    m = c.get("/api/medications").json()[0]
    assert m["stock"] == 12 and m["days_of_supply"] == round(12 / (2 / 7), 1)
    assert c.get("/api/schedules").json()[0]["days"] == ["MON", "THU"]


def test_frontend_served(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "MedGate" in r.text
    assert c.get("/static/app.js").status_code == 200


def test_additions_endpoints(client):
    c, api = client
    from medgate.simulator import run_scenario
    assert set(c.get("/api/drug-classes").json()["classes"]) == {"GENERAL", "ANTIHYPERTENSIVE", "BETA_BLOCKER", "SULFONYLUREA"}
    assert c.post("/api/devices/SIM-001/patient", json={"name": "Ama", "tag": "MG-CARD-0001", "language": "hi"}).status_code == 200
    assert c.post("/api/devices/SIM-001/patient", json={"name": "Ama", "language": "fr"}).status_code == 422
    r = c.post("/api/medications", json={"device_id": "SIM-001", "name": "BB", "compartment": 6, "unit_weight_g": 0.7,
                                         "weight_tol_g": 0.15, "drug_class": "BETA_BLOCKER"})
    assert r.status_code == 200
    run_scenario(api.engine, "trend_decline", step_s=0)
    assert any(a["kind"] == "TREND_SPO2" for a in c.get("/api/alerts").json()["open"])
    p = c.get("/api/prompts").json()
    assert p["recent"] and p["recent"][0]["lang"] == "hi"
    cmds = c.get("/api/device/SIM-001/commands", headers=KEY).json()
    assert "prompts" in cmds
    rep = c.get("/api/report/SIM-001").json()
    assert rep["device"]["patient_name"] == "Ama" and rep["trends"]
    html = c.get("/report/SIM-001").text
    assert "MedGate adherence" in html and "SIMULATOR DATA" in html and "<svg" in html
    assert c.get("/report/NOPE").status_code == 404



def test_round2_endpoints(client):
    c, api = client
    from medgate.simulator import run_scenario
    h = c.get("/api/health").json()
    assert h["version"] == api.APP_VERSION and h["schema"] >= 5
    run_scenario(api.engine, "sos", step_s=0)
    assert any(a["kind"] == "SOS" for a in c.get("/api/alerts").json()["open"])
    assert c.post("/api/devices/SIM-001/sos", json={"source": "patient screen"}).status_code == 200
    assert c.post("/api/devices/NOPE/sos", json={}).status_code == 404
    assert c.post("/api/medications/1/expiry", json={"expiry_date": "2030-01-01"}).status_code == 200
    assert c.post("/api/medications/1/expiry", json={"expiry_date": "01-01-2030"}).status_code == 422
    assert c.post("/api/medications/1/refill", json={"count": 30, "expiry_date": "2031-05-01"}).status_code == 200
    assert next(m for m in c.get("/api/medications").json() if m["id"] == 1)["expiry_date"] == "2031-05-01"
    p = c.get("/api/patient/SIM-001").json()
    assert p["device"]["id"] == "SIM-001" and p["sos"]
    assert len(c.get("/api/adherence/SIM-001?days=14").json()["days"]) == 14
    assert "MedGate update" in c.get("/api/digest/SIM-001").json()["text"]


def test_full_demo_story(client):
    c, api = client
    from medgate.simulator import run_scenario
    st = run_scenario(api.engine, "full_demo", step_s=0)
    log = "\n".join(st["log"])
    assert "STEP 8/8" in log and "DEMO FINISHED" in log and "ERROR" not in log
    kinds = {a["kind"] for a in c.get("/api/alerts").json()["open"]}
    assert {"SOS", "PATIENT_MISMATCH"} <= kinds
    states = [d["state"] for d in c.get("/api/doses?limit=100").json()]
    assert "COLLECTED" in states and "HELD" in states
