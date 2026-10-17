"""FastAPI application: dashboard API, device API, simulator control."""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core.fsm import IllegalTransition
from .core.drugs import DRUG_CLASSES
from .core.gate import IDF_RULES, MAX_TIGHTEN_STEPS, TIGHTEN_STEP
from .core.prompts import LANGS, PROMPTS
from .core.trends import LIMITS as TREND_LIMITS
from .db import DB, SCHEMA_VERSION
from .engine import Config, Engine, EngineError
from .report import render_report
from .simulator import SCENARIOS, SIM_ID, SIM_TAG, execute_pending, start_scenario_thread

APP_VERSION = "0.4.0"   # the dashboard checks this to warn about an old server still running
DB_PATH = os.environ.get("MEDGATE_DB", str(Path(__file__).resolve().parents[1] / "medgate.db"))
DEVICE_KEY = os.environ.get("MEDGATE_DEVICE_KEY", "dev-key-change-me")
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def build_engine(path=DB_PATH) -> Engine:
    e = Engine(DB(path), time.time, Config())
    if not e.db.one("SELECT 1 FROM devices WHERE id=?", (SIM_ID,)):
        e.ensure_device(SIM_ID, "SIMULATOR", "Simulated dispenser")
        from datetime import date, timedelta
        exp = lambda days: (date.today() + timedelta(days=days)).isoformat()   # demo expiry dates
        e.set_patient(SIM_ID, "Demo patient (simulated)", SIM_TAG, "en", actor="system")
        e.add_medication(SIM_ID, "Demo tablet A (dummy)", 1, 0.5, 0.15, notes="non-ingestible demo object",
                         expiry_date=exp(180))
        e.add_medication(SIM_ID, "Demo tablet B (dummy)", 2, 0.8, 0.2, notes="non-ingestible demo object",
                         expiry_date=exp(10))
        e.add_medication(SIM_ID, "Demo BP tablet (dummy)", 3, 0.6, 0.15, drug_class="ANTIHYPERTENSIVE",
                         notes="non-ingestible; stands in for an amlodipine-type drug", expiry_date=exp(365))
        e.add_medication(SIM_ID, "Demo beta-blocker (dummy)", 4, 0.7, 0.15, drug_class="BETA_BLOCKER",
                         notes="non-ingestible; stands in for a metoprolol-type drug", expiry_date=exp(365))
        e.add_medication(SIM_ID, "Demo diabetes tablet (dummy)", 5, 0.9, 0.2, drug_class="SULFONYLUREA",
                         notes="non-ingestible; stands in for a glimepiride-type drug", expiry_date=exp(90))
        e.check_expiry()
    return e


engine: Engine = None
sim_status: dict = {"running": False, "log": []}
_stop = threading.Event()


def _ticker():
    while not _stop.wait(1.0):
        try:
            engine.tick()
            if not sim_status.get("running") and engine.db.one(
                    "SELECT 1 FROM commands WHERE device_id=? AND status='PENDING'", (SIM_ID,)):
                sim_status.clear()
                sim_status.update(running=True, log=[])
                threading.Thread(target=execute_pending, args=(engine, sim_status), daemon=True).start()
        except Exception as ex:  # keep scheduler alive; error is visible in logs
            print("tick error:", ex)


@asynccontextmanager
async def lifespan(app):
    global engine
    engine = build_engine(os.environ.get("MEDGATE_DB", DB_PATH))
    _stop.clear()
    if os.environ.get("MEDGATE_NO_TICKER") != "1":
        threading.Thread(target=_ticker, daemon=True).start()
    yield
    _stop.set()


app = FastAPI(title="MedGate API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(EngineError)
def _ee(_, exc: EngineError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.code)


@app.exception_handler(IllegalTransition)
def _it(_, exc):
    return JSONResponse({"detail": f"illegal state change: {exc}"}, status_code=409)


def _rows(sql, args=(), json_cols=()):
    rows = engine.db.q(sql, args)
    for r in rows:
        for c in json_cols:
            if r.get(c):
                r[c] = json.loads(r[c])
    return rows


# ---------------------------------------------------------------- models
class DeviceIn(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{3,32}$")
    name: Optional[str] = None
    kind: Literal["SIMULATOR", "HARDWARE"]


class Rule(BaseModel):
    field: Literal["hr", "spo2", "bp_sys", "bp_dia"]
    op: Literal["<", "<=", ">", ">="]
    value: float
    label: str = Field(min_length=1, max_length=80)


class MedicationIn(BaseModel):
    device_id: str
    name: str = Field(min_length=1, max_length=80)
    compartment: int = Field(ge=1, le=6)
    unit_weight_g: float = Field(gt=0, le=5)
    weight_tol_g: float = Field(gt=0, le=2)
    rules: Optional[list[Rule]] = None
    notes: str = ""
    stock: int = Field(default=30, ge=0, le=500)
    refill_threshold: int = Field(default=5, ge=0, le=100)
    drug_class: Literal["GENERAL", "ANTIHYPERTENSIVE", "BETA_BLOCKER", "SULFONYLUREA"] = "GENERAL"
    requires_meal: Optional[bool] = None
    min_interval_h: Optional[float] = Field(default=None, ge=0, le=72)
    late_window_min: float = Field(default=50, ge=5, le=240)
    expiry_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ExpiryIn(BaseModel):
    expiry_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class SosIn(BaseModel):
    source: str = Field(default="dashboard", max_length=40)
    note: str = Field(default="", max_length=120)


class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tag: Optional[str] = Field(default=None, max_length=32)
    language: Literal["en", "hi", "mni"] = "en"


class RefillIn(BaseModel):
    count: int = Field(ge=0, le=500)
    expiry_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ResolveIn(BaseModel):
    by: str = Field(min_length=1, max_length=60)


class ScheduleIn(BaseModel):
    medication_id: int
    time_hhmm: str = Field(pattern=r"^\d{2}:\d{2}$")
    food: Literal["BEFORE_FOOD", "AFTER_FOOD", "ANY"] = "ANY"
    days: Optional[list[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]]] = None


class ToggleIn(BaseModel):
    active: bool


class DoseIn(BaseModel):
    medication_id: int
    delay_s: float = Field(default=0, ge=0, le=86400)


class ReviewIn(BaseModel):
    decision: Literal["approve", "withhold"]
    reviewer: str = Field(min_length=1, max_length=60)
    note: str = ""


class AckIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=60)
    note: str = ""


class Event(BaseModel):
    seq: int = Field(ge=0)
    type: Literal["HEARTBEAT", "VITALS", "CMD_ACK", "EJECT", "CONVEYOR", "COLLECT", "FAULT", "ENV", "SOS"]
    ts: Optional[float] = None
    data: dict = {}


class EventBatch(BaseModel):
    events: list[Event] = Field(max_length=200)


# ---------------------------------------------------------------- dashboard API
@app.get("/api/health")
def health():
    return {"ok": True, "time": time.time(), "version": APP_VERSION, "schema": SCHEMA_VERSION}


@app.get("/api/overview")
def overview():
    now = time.time()
    devs = _rows("SELECT * FROM devices ORDER BY kind, id", json_cols=("status_json",))
    for d in devs:
        d["online"] = bool(d["last_seen"] and now - d["last_seen"] < 60)
    return {"stats": engine.stats(), "devices": devs, "server_time": now,
            "audit": engine.verify_audit()}


@app.get("/api/devices")
def devices():
    return overview()["devices"]


@app.post("/api/devices")
def add_device(d: DeviceIn):
    return engine.ensure_device(d.id, d.kind, d.name)


@app.get("/api/medications")
def medications():
    rows = _rows("SELECT * FROM medications ORDER BY device_id, compartment", json_cols=("rules_json",))
    for r in rows:
        r["days_of_supply"] = engine.days_of_supply(r["id"], r["stock"])
    return rows


@app.post("/api/medications")
def add_med(m: MedicationIn):
    rules = None if m.rules is None else [r.model_dump() for r in m.rules]
    return {"id": engine.add_medication(m.device_id, m.name, m.compartment, m.unit_weight_g,
                                        m.weight_tol_g, rules, m.notes, stock=m.stock,
                                        refill_threshold=m.refill_threshold, drug_class=m.drug_class,
                                        requires_meal=m.requires_meal, min_interval_h=m.min_interval_h,
                                        late_window_min=m.late_window_min, expiry_date=m.expiry_date)}


@app.post("/api/medications/{medication_id}/expiry")
def set_expiry(medication_id: int, x: ExpiryIn):
    engine.set_expiry(medication_id, x.expiry_date)
    return {"ok": True}


@app.post("/api/devices/{device_id}/sos")
def sos(device_id: str, s: SosIn):
    return engine.sos(device_id, s.source, s.note)


@app.get("/api/patient/{device_id}")
def patient_screen(device_id: str):
    return engine.patient_screen(device_id)


@app.get("/api/adherence/{device_id}")
def adherence(device_id: str, days: int = 28):
    return engine.adherence_calendar(device_id, max(7, min(days, 90)))


@app.get("/api/digest/{device_id}")
def digest(device_id: str):
    return engine.family_digest(device_id)


@app.get("/api/drug-classes")
def drug_classes():
    return {"classes": DRUG_CLASSES, "trend_limits": TREND_LIMITS,
            "warning": "Example hold parameters for a demonstration with dummy tablets, not clinical guidance. "
                       "In real use the prescriber sets them per patient."}


@app.post("/api/devices/{device_id}/patient")
def set_patient(device_id: str, p: PatientIn):
    engine.set_patient(device_id, p.name, p.tag, p.language)
    return {"ok": True}


@app.get("/api/prompts")
def prompts(limit: int = 30):
    return {"recent": _rows("SELECT * FROM prompts ORDER BY id DESC LIMIT ?", (limit,)), "languages": LANGS,
            "catalogue": PROMPTS,
            "note": "Manipuri (mni) clips must be written and recorded by a native speaker; English text is shown until then."}


@app.get("/api/report/{device_id}")
def report_json(device_id: str, days: int = 30):
    return engine.doctor_report(device_id, max(1, min(days, 365)))


@app.get("/report/{device_id}", response_class=HTMLResponse)
def report_html(device_id: str, days: int = 30):
    return render_report(engine.doctor_report(device_id, max(1, min(days, 365))))


@app.post("/api/medications/{medication_id}/refill")
def refill(medication_id: int, r: RefillIn):
    engine.refill(medication_id, r.count, expiry_date=r.expiry_date)
    return {"ok": True}


@app.get("/api/alerts")
def alerts(limit: int = 50):
    return {"open": _rows("SELECT a.*, m.name medication, m.compartment FROM alerts a LEFT JOIN medications m ON m.id=a.medication_id "
                          "WHERE a.resolved_at IS NULL ORDER BY a.id DESC"),
            "recent": _rows("SELECT a.*, m.name medication FROM alerts a LEFT JOIN medications m ON m.id=a.medication_id "
                            "WHERE a.resolved_at IS NOT NULL ORDER BY a.id DESC LIMIT ?", (limit,)),
            "limits": {"max_temp_c": engine.cfg.storage_max_temp_c, "max_humidity": engine.cfg.storage_max_humidity}}


@app.post("/api/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, r: ResolveIn):
    if not engine.resolve_alerts(alert_id=alert_id, by=f"caregiver:{r.by}"):
        raise HTTPException(404, "no open alert with that id")
    return {"ok": True}


@app.get("/api/env")
def env(limit: int = 50):
    return _rows("SELECT * FROM env_readings ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/api/rules/demo")
def demo_rules():
    return {"rules": IDF_RULES, "tighten_step": TIGHTEN_STEP, "max_tighten_steps": MAX_TIGHTEN_STEPS,
            "warning": "IDF-B example thresholds (SpO2 >= 94 %, systolic <= 160 mmHg) for demonstration, not clinical guidance."}


@app.get("/api/schedules")
def schedules():
    rows = _rows("SELECT s.*, m.name medication, m.compartment, m.device_id FROM schedules s JOIN medications m ON m.id=s.medication_id ORDER BY time_hhmm")
    for r in rows:
        r["tighten_steps"] = engine.tighten_steps(r["medication_id"], r["session"])
        r["days"] = r["days"].split(",")
    return rows


@app.post("/api/schedules")
def add_schedule(s: ScheduleIn):
    return {"id": engine.add_schedule(s.medication_id, s.time_hhmm, s.food, days=s.days)}


@app.post("/api/schedules/{schedule_id}/active")
def toggle_schedule(schedule_id: int, t: ToggleIn):
    engine.set_schedule_active(schedule_id, t.active)
    return {"ok": True}


@app.get("/api/compliance/{device_id}")
def compliance(device_id: str):
    if not engine.db.one("SELECT 1 FROM devices WHERE id=?", (device_id,)):
        raise HTTPException(404, "unknown device")
    return engine.compliance_plan(device_id, time.time())


@app.get("/api/tightening")
def tightening():
    return _rows("SELECT t.*, m.name medication FROM gate_tightening t JOIN medications m ON m.id=t.medication_id ORDER BY m.id")


@app.get("/api/portal/doctor")
def portal_doctor():
    return engine.doctor_portal()


@app.get("/api/portal/caretaker")
def portal_caretaker():
    return engine.caretaker_portal()


@app.post("/api/doses")
def add_dose(d: DoseIn):
    return {"id": engine.create_dose_now(d.medication_id, delay_s=d.delay_s)}


DOSE_SQL = ("SELECT d.*, m.name medication, m.compartment, dv.kind device_kind FROM doses d "
            "JOIN medications m ON m.id=d.medication_id JOIN devices dv ON dv.id=d.device_id ")


@app.get("/api/doses")
def doses(limit: int = 50, state: Optional[str] = None):
    if state:
        return _rows(DOSE_SQL + "WHERE d.state=? ORDER BY d.due_at DESC LIMIT ?", (state, limit), ("gate_reasons",))
    return _rows(DOSE_SQL + "ORDER BY d.due_at DESC LIMIT ?", (limit,), ("gate_reasons",))


@app.get("/api/doses/{dose_id}")
def dose_detail(dose_id: int):
    d = _rows(DOSE_SQL + "WHERE d.id=?", (dose_id,), ("gate_reasons",))
    if not d:
        raise HTTPException(404, "dose not found")
    return {"dose": d[0],
            "audit": _rows("SELECT * FROM audit WHERE dose_id=? ORDER BY id", (dose_id,), ("detail",)),
            "escalations": _rows("SELECT * FROM escalations WHERE dose_id=? ORDER BY id", (dose_id,)),
            "commands": _rows("SELECT * FROM commands WHERE dose_id=?", (dose_id,), ("payload",)),
            "prompts": _rows("SELECT * FROM prompts WHERE dose_id=? ORDER BY id", (dose_id,))}


@app.post("/api/doses/{dose_id}/review")
def review(dose_id: int, r: ReviewIn):
    return engine.review(dose_id, r.decision, r.reviewer, r.note)


@app.post("/api/doses/{dose_id}/acknowledge")
def acknowledge(dose_id: int, a: AckIn):
    d = engine.db.one("SELECT state FROM doses WHERE id=?", (dose_id,))
    if not d:
        raise HTTPException(404, "dose not found")
    if d["state"] not in ("REVIEW_REQUIRED", "FAULT", "MISSED"):
        raise HTTPException(409, "only REVIEW_REQUIRED, FAULT or MISSED doses can be acknowledged")
    engine.db.x("UPDATE doses SET reviewer=?, review_note=? WHERE id=?", (a.reviewer, a.note, dose_id))
    engine.audit(f"reviewer:{a.reviewer}", "ACKNOWLEDGED", dose_id, {"note": a.note, "state": d["state"]})
    return {"ok": True}


@app.get("/api/vitals")
def vitals(limit: int = 50):
    return _rows("SELECT * FROM vitals ORDER BY id DESC LIMIT ?", (limit,), ("reasons",))


@app.get("/api/escalations")
def escalations(limit: int = 50):
    return _rows("SELECT e.*, m.name medication, d.state dose_state FROM escalations e JOIN doses d ON d.id=e.dose_id "
                 "JOIN medications m ON m.id=d.medication_id ORDER BY e.id DESC LIMIT ?", (limit,))


@app.get("/api/events")
def events(limit: int = 100):
    return _rows("SELECT * FROM device_events ORDER BY id DESC LIMIT ?", (limit,), ("payload",))


@app.get("/api/audit")
def audit(limit: int = 200):
    return _rows("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,), ("detail",))


@app.get("/api/audit/verify")
def audit_verify():
    return engine.verify_audit()


# ---------------------------------------------------------------- device API
def _auth(key: Optional[str]):
    if not key or not hmac.compare_digest(key, DEVICE_KEY):
        raise HTTPException(401, "invalid device key")


@app.post("/api/device/{device_id}/events")
def device_events(device_id: str, batch: EventBatch, x_device_key: Optional[str] = Header(None)):
    _auth(x_device_key)
    return {"results": engine.ingest(device_id, [e.model_dump() for e in batch.events]),
            "server_time": time.time()}


@app.get("/api/device/{device_id}/commands")
def device_commands(device_id: str, x_device_key: Optional[str] = Header(None)):
    _auth(x_device_key)
    if not engine.db.one("SELECT 1 FROM devices WHERE id=?", (device_id,)):
        raise HTTPException(404, "unknown device")
    engine.db.x("UPDATE devices SET last_seen=? WHERE id=?", (time.time(), device_id))
    return {"commands": engine.pending_commands(device_id), "prompts": engine.pending_prompts(device_id),
            "server_time": time.time()}


# ---------------------------------------------------------------- simulator
@app.get("/api/sim")
def sim():
    return {"device_id": SIM_ID, "scenarios": SCENARIOS, "status": sim_status}


@app.post("/api/sim/run/{name}")
def sim_run(name: str):
    if name not in SCENARIOS:
        raise HTTPException(404, "unknown scenario")
    if sim_status.get("running"):
        raise HTTPException(409, "a scenario is already running")
    sim_status.clear()
    sim_status.update(running=True, name=name, log=[])
    start_scenario_thread(engine, name, sim_status)
    return {"started": name}


# ---------------------------------------------------------------- frontend
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND / "index.html")
