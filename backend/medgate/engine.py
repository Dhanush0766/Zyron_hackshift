"""Dose orchestration engine: the single place where dose state changes.

Every state change goes through `_move`, which (1) checks the FSM, (2) writes
the dose row and (3) appends a hash-chained audit record under one lock.

IDF-B mapping: health gate (§7.2) in `_on_vitals`; conveyor delivery and
dual-sensor checks (§7.3) in `_on_arrival`; AND-gate pickup and threshold
tightening feedback (§7.4) in `_on_collect` / `_tighten`; LSTM plan (§7.5) in
`compliance_plan`; physiologically-conditioned escalation (§7.6) in `tick`;
portal data models (§7.7) in `doctor_portal` / `caretaker_portal`.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from .core.compliance import plan as compliance_plan_from_history
from .core.drugs import DRUG_CLASSES, preset as drug_preset
from .core.prompts import LANGS, prompt as make_prompt
from .core.trends import detect as detect_trends
from .core.escalation import CHANNEL, EscalationPolicy, contracted, level_for
from .core.fsm import AWAITING_VITALS, TERMINAL, DoseState as S, check_transition
from .core.gate import IDF_RULES, MAX_TIGHTEN_STEPS, GateDecision, health_gate
from .core.verification import CollectionVerdict, DeliveryVerdict, verify_collection, verify_delivery
from .core.vitals import VitalsReading, session_of, validate_reading
from .db import DB, dumps

GENESIS = "0" * 64
FOODS = ("BEFORE_FOOD", "AFTER_FOOD", "ANY")
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@dataclass
class Config:
    cmd_timeout_s: float = 30         # dispense command -> EJECT event
    conveyor_timeout_s: float = 30    # EJECTED/IN_TRANSIT -> arrival
    collect_grace_s: float = 30       # after L4 is reached, wait this long before MISSED
    max_gate_attempts: int = 3
    # Storage limits. "Store below 30 °C" is a common Indian label condition; humidity limit is our
    # choice. Both are configuration: use the strictest limit printed on the actual medicines.
    storage_max_temp_c: float = 30.0
    storage_max_humidity: float = 70.0
    vitals_max_age_s: float = 120
    min_quality: float = 0.6
    escalation: EscalationPolicy = None

    def __post_init__(self):
        self.escalation = self.escalation or EscalationPolicy()


class EngineError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


class Engine:
    def __init__(self, db: DB, clock: Callable[[], float] = time.time, config: Config = None):
        self.db, self.clock, self.cfg = db, clock, config or Config()
        self._expiry_checked_at = None

    # ------------------------------------------------------------ audit
    def audit(self, actor: str, action: str, dose_id: Optional[int] = None, detail=None):
        with self.db.lock:
            prev = self.db.one("SELECT hash FROM audit ORDER BY id DESC LIMIT 1")
            prev_hash = prev["hash"] if prev else GENESIS
            ts = self.clock()
            body = dumps({"ts": ts, "actor": actor, "action": action,
                          "dose_id": dose_id, "detail": detail, "prev": prev_hash})
            h = hashlib.sha256(body.encode()).hexdigest()
            self.db.x("INSERT INTO audit(ts,actor,action,dose_id,detail,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
                      (ts, actor, action, dose_id, dumps(detail), prev_hash, h))

    def verify_audit(self) -> dict:
        prev = GENESIS
        rows = self.db.q("SELECT * FROM audit ORDER BY id")
        for r in rows:
            body = dumps({"ts": r["ts"], "actor": r["actor"], "action": r["action"],
                          "dose_id": r["dose_id"], "detail": json.loads(r["detail"]), "prev": prev})
            if r["prev_hash"] != prev or hashlib.sha256(body.encode()).hexdigest() != r["hash"]:
                return {"ok": False, "records": len(rows), "broken_at_id": r["id"]}
            prev = r["hash"]
        return {"ok": True, "records": len(rows), "head": prev}

    # ------------------------------------------------------------ setup
    def ensure_device(self, device_id: str, kind: str, name: str = None):
        if kind not in ("SIMULATOR", "HARDWARE"):
            raise EngineError("kind must be SIMULATOR or HARDWARE")
        d = self.db.one("SELECT * FROM devices WHERE id=?", (device_id,))
        if d:
            if d["kind"] != kind:
                raise EngineError(f"device {device_id} is registered as {d['kind']}", 409)
            return d
        self.db.x("INSERT INTO devices(id,name,kind,created_at) VALUES(?,?,?,?)",
                  (device_id, name or device_id, kind, self.clock()))
        self.audit("system", "DEVICE_REGISTERED", None, {"device_id": device_id, "kind": kind})
        return self.db.one("SELECT * FROM devices WHERE id=?", (device_id,))

    def set_patient(self, device_id: str, name: str, tag: Optional[str], language: str = "en", actor="caregiver"):
        """Who this dispenser belongs to. With a tag set, every reading must come with that card."""
        if not self.db.one("SELECT 1 FROM devices WHERE id=?", (device_id,)):
            raise EngineError("unknown device", 404)
        if language not in LANGS:
            raise EngineError(f"language must be one of {sorted(LANGS)}")
        tag = (tag or "").strip().upper() or None
        self.db.x("UPDATE devices SET patient_name=?, patient_tag=?, language=? WHERE id=?",
                  (name, tag, language, device_id))
        self.audit(actor, "PATIENT_SET", None, {"device_id": device_id, "patient": name,
                                                "card_required": bool(tag), "language": language})

    def add_medication(self, device_id, name, compartment, unit_weight_g, weight_tol_g,
                       rules=None, notes="", actor="caregiver", stock: int = 30, refill_threshold: int = 5,
                       drug_class: str = "GENERAL", requires_meal: Optional[bool] = None,
                       min_interval_h: Optional[float] = None, late_window_min: float = 50,
                       expiry_date: Optional[str] = None):
        if not self.db.one("SELECT 1 FROM devices WHERE id=?", (device_id,)):
            raise EngineError("unknown device", 404)
        expiry_date = self._valid_date(expiry_date)
        try:
            pre = drug_preset(drug_class)
        except ValueError as e:
            raise EngineError(str(e))
        rules = pre["rules"] if rules is None else rules
        requires_meal = pre["requires_meal"] if requires_meal is None else bool(requires_meal)
        min_interval_h = pre["min_interval_h"] if min_interval_h is None else float(min_interval_h)
        if not (0 <= min_interval_h <= 72) or not (5 <= late_window_min <= 240):
            raise EngineError("min interval must be 0..72 h and late window 5..240 min")
        for r in rules:
            if r.get("op") not in ("<", "<=", ">", ">=") or r.get("field") not in ("hr", "spo2", "bp_sys", "bp_dia"):
                raise EngineError(f"invalid rule {r}")
        if weight_tol_g <= 0 or unit_weight_g <= weight_tol_g * 2:
            raise EngineError("unit weight must exceed 2x tolerance so 0/1/2 tablets are distinguishable")
        try:
            if stock < 0 or refill_threshold < 0:
                raise EngineError("stock and refill threshold must be >= 0")
            mid = self.db.x("INSERT INTO medications(device_id,name,compartment,unit_weight_g,weight_tol_g,rules_json,notes,created_at,"
                            "stock,refill_threshold,drug_class,requires_meal,min_interval_h,late_window_min,expiry_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (device_id, name, compartment, unit_weight_g, weight_tol_g, dumps(rules), notes, self.clock(),
                             stock, refill_threshold, drug_class, int(requires_meal), min_interval_h, late_window_min, expiry_date))
        except sqlite3.IntegrityError as e:
            raise EngineError(f"compartment {compartment} already used or invalid ({e})", 409)
        self.audit(actor, "MEDICATION_ADDED", None, {"id": mid, "name": name, "compartment": compartment, "rules": rules,
                                                     "drug_class": drug_class, "requires_meal": requires_meal,
                                                     "min_interval_h": min_interval_h, "late_window_min": late_window_min})
        return mid

    def add_schedule(self, medication_id, time_hhmm, food="ANY", actor="caregiver", days=None):
        try:
            hh = datetime.strptime(time_hhmm, "%H:%M").hour
        except ValueError:
            raise EngineError("time must be HH:MM")
        if food not in FOODS:
            raise EngineError(f"food must be one of {FOODS}")
        days = list(DAYS) if not days else [d.upper() for d in days]
        if any(d not in DAYS for d in days):
            raise EngineError(f"days must be from {DAYS}")
        days_s = ",".join(d for d in DAYS if d in days)
        if not self.db.one("SELECT 1 FROM medications WHERE id=?", (medication_id,)):
            raise EngineError("unknown medication", 404)
        session = session_of(hh)
        sid = self.db.x("INSERT INTO schedules(medication_id,time_hhmm,food,session,days,created_at) VALUES(?,?,?,?,?,?)",
                        (medication_id, time_hhmm, food, session, days_s, self.clock()))
        self.audit(actor, "SCHEDULE_ADDED", None, {"id": sid, "medication_id": medication_id, "time": time_hhmm,
                                                   "food": food, "session": session, "days": days_s})
        self.generate_doses()
        return sid

    def set_schedule_active(self, schedule_id: int, active: bool, actor="caregiver"):
        if not self.db.one("SELECT 1 FROM schedules WHERE id=?", (schedule_id,)):
            raise EngineError("unknown schedule", 404)
        self.db.x("UPDATE schedules SET active=? WHERE id=?", (int(active), schedule_id))
        self.audit(actor, "SCHEDULE_ENABLED" if active else "SCHEDULE_DISABLED", None, {"id": schedule_id})

    def create_dose_now(self, medication_id, actor="caregiver", delay_s: float = 0, food="ANY"):
        med = self.db.one("SELECT * FROM medications WHERE id=?", (medication_id,))
        if not med:
            raise EngineError("unknown medication", 404)
        now = self.clock()
        session = session_of(datetime.fromtimestamp(now + delay_s).hour)
        did = self.db.x("INSERT INTO doses(medication_id,device_id,due_at,state,state_since,session,food,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (medication_id, med["device_id"], now + delay_s, S.SCHEDULED.value, now, session, food, now))
        self.audit(actor, "DOSE_CREATED", did, {"medication": med["name"], "due_in_s": delay_s, "session": session})
        return did

    def generate_doses(self, horizon_h: int = 24):
        now = self.clock()
        base = datetime.fromtimestamp(now)
        for s in self.db.q("SELECT s.*, m.device_id FROM schedules s JOIN medications m ON m.id=s.medication_id WHERE s.active=1"):
            hh, mm = map(int, s["time_hhmm"].split(":"))
            for day in (0, 1):
                due_dt = base.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=day)
                if DAYS[due_dt.weekday()] not in s["days"].split(","):
                    continue  # not scheduled on this weekday
                due = due_dt.timestamp()
                if now - 60 <= due <= now + horizon_h * 3600:
                    try:
                        did = self.db.x("INSERT INTO doses(schedule_id,medication_id,device_id,due_at,state,state_since,session,food,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                        (s["id"], s["medication_id"], s["device_id"], due, S.SCHEDULED.value, now,
                                         s["session"], s["food"], now))
                        self.audit("scheduler", "DOSE_CREATED", did, {"schedule_id": s["id"], "due_at": due})
                    except sqlite3.IntegrityError:
                        pass  # already generated

    # ------------------------------------------------------------ state changes
    def _move(self, dose: dict, target: S, actor: str, reason, **fields):
        with self.db.lock:
            cur = self.db.one("SELECT state FROM doses WHERE id=?", (dose["id"],))
            check_transition(S(cur["state"]), target)  # raises IllegalTransition
            now = self.clock()
            sets = ["state=?", "state_since=?"]
            vals = [target.value, now]
            for k, v in fields.items():
                sets.append(f"{k}=?")
                vals.append(v)
            self.db.x(f"UPDATE doses SET {', '.join(sets)} WHERE id=?", (*vals, dose["id"]))
            self.audit(actor, f"DOSE_{target.value}", dose["id"],
                       {"from": cur["state"], "reason": reason,
                        **{k: v for k, v in fields.items() if k not in ("gate_reasons", "plan_json")}})
        return self.db.one("SELECT * FROM doses WHERE id=?", (dose["id"],))

    # ------------------------------------------------------------ voice prompts
    def _prompt(self, device_id: str, key: str, dose_id: Optional[int] = None):
        dev = self.db.one("SELECT language FROM devices WHERE id=?", (device_id,))
        p = make_prompt(key, dev["language"] if dev else "en")
        self.db.x("INSERT INTO prompts(device_id,dose_id,key,lang,text,clip,fallback,created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (device_id, dose_id, p["key"], p["lang"], p["text"], p["clip"], int(p["fallback"]), self.clock()))

    def pending_prompts(self, device_id: str) -> list:
        with self.db.lock:
            rows = self.db.q("SELECT * FROM prompts WHERE device_id=? AND status='PENDING' ORDER BY id", (device_id,))
            if rows:
                self.db.x("UPDATE prompts SET status='SENT', sent_at=? WHERE device_id=? AND status='PENDING'",
                          (self.clock(), device_id))
        return [{"prompt_id": r["id"], "key": r["key"], "lang": r["lang"], "clip": r["clip"], "text": r["text"],
                 "dose_id": r["dose_id"]} for r in rows]

    def _escalate(self, dose: dict, level: int, reason: str):
        if level <= dose["escalation_level"]:
            return
        if level >= 2 and dose["escalation_level"] < 2 and dose.get("state") == S.DELIVERED.value:
            self._prompt(dose["device_id"], "REMINDER", dose["id"])
        now = self.clock()
        dev = self.db.one("SELECT kind FROM devices WHERE id=?", (dose["device_id"],))
        delivery = "SIMULATED (no modem)" if dev and dev["kind"] == "SIMULATOR" else "REQUESTED"
        self.db.x("INSERT INTO escalations(dose_id,level,channel,reason,created_at,delivery) VALUES(?,?,?,?,?,?)",
                  (dose["id"], level, CHANNEL[level], reason, now, delivery))
        self.db.x("UPDATE doses SET escalation_level=?, escalation_since=COALESCE(escalation_since, ?) WHERE id=?",
                  (level, now, dose["id"]))
        self.audit("escalation", "ESCALATED", dose["id"], {"level": level, "channel": CHANNEL[level], "reason": reason})
        dose["escalation_level"] = level

    def _issue_dispense(self, dose: dict, actor: str, reason: str):
        med = self.db.one("SELECT * FROM medications WHERE id=?", (dose["medication_id"],))
        if med["expiry_date"] and date.fromisoformat(med["expiry_date"]) < date.fromtimestamp(self.clock()):
            dose = self._move(dose, S.FAULT, actor, f"box {med['compartment']} medicine expired on {med['expiry_date']}: never dispensed",
                              fault="EXPIRED_MEDICINE")
            self._escalate(dose, 3, f"box {med['compartment']} expired ({med['name']})")
            self.raise_alert("MEDICINE_EXPIRED", dose["device_id"], med["id"],
                             f"Box {med['compartment']} ({med['name']}) expired on {med['expiry_date']}; a dose was not given")
            self._prompt(dose["device_id"], "EXPIRED", dose["id"])
            return dose
        if med["stock"] <= 0:
            dose = self._move(dose, S.FAULT, actor, f"compartment {med['compartment']} is empty: refill before dispensing",
                              fault="EMPTY_COMPARTMENT")
            self._escalate(dose, 3, f"box {med['compartment']} empty ({med['name']})")
            self.raise_alert("REFILL_NEEDED", dose["device_id"], med["id"],
                             f"Box {med['compartment']} ({med['name']}) is empty; a dose could not be dispensed", 0)
            return dose
        dose = self._move(dose, S.DISPENSE_SENT, actor, reason, dispensed_at=self.clock())
        # UNIQUE(dose_id) makes a second command for the same dose impossible.
        cid = self.db.x("INSERT INTO commands(device_id,dose_id,kind,payload,status,created_at) VALUES(?,?,?,?,?,?)",
                        (dose["device_id"], dose["id"], "DISPENSE",
                         dumps({"dose_id": dose["id"], "compartment": med["compartment"],
                                "unit_weight_g": med["unit_weight_g"], "weight_tol_g": med["weight_tol_g"],
                                "alert_volume": dose["alert_volume"]}), "PENDING", self.clock()))
        self.audit(actor, "COMMAND_ISSUED", dose["id"], {"command_id": cid, "compartment": med["compartment"]})
        return dose

    # ------------------------------------------------------------ feedback loop (IDF-B §7.4)
    def tighten_steps(self, medication_id: int, session: str) -> int:
        r = self.db.one("SELECT steps FROM gate_tightening WHERE medication_id=? AND session=?", (medication_id, session))
        return r["steps"] if r else 0

    def _tighten(self, dose: dict, delta: int, reason: str):
        cur = self.tighten_steps(dose["medication_id"], dose["session"])
        new = max(0, min(MAX_TIGHTEN_STEPS, cur + delta))
        if new == cur:
            return
        self.db.x("INSERT INTO gate_tightening(medication_id,session,steps,updated_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(medication_id,session) DO UPDATE SET steps=excluded.steps, updated_at=excluded.updated_at",
                  (dose["medication_id"], dose["session"], new, self.clock()))
        self.audit("feedback", "GATE_TIGHTENED" if new > cur else "GATE_RELAXED", dose["id"],
                   {"medication_id": dose["medication_id"], "session": dose["session"], "steps": new, "reason": reason})

    # ------------------------------------------------------------ compliance model (IDF-B §7.5)
    def _history(self, device_id: str, before: float) -> list:
        rows = self.db.q("SELECT state, due_at, delivered_at, collected_at, gate_decision FROM doses "
                         "WHERE device_id=? AND due_at<? AND (state IN ('COLLECTED','MISSED','WITHHELD') OR gate_decision IS NOT NULL) "
                         "AND state NOT IN ('DUE','RETAKE','HELD','DISPENSE_SENT','EJECTED','IN_TRANSIT','DELIVERED') "
                         "ORDER BY due_at DESC LIMIT 20", (device_id, before))
        hist = []
        m = self.cfg.escalation.minute_s
        for r in reversed(rows):
            dt = datetime.fromtimestamp(r["due_at"])
            lat = (r["collected_at"] - r["delivered_at"]) / m if r["collected_at"] and r["delivered_at"] else None
            hist.append({"latency_min": lat, "missed": int(r["state"] == "MISSED"),
                         "gate_fail": int(r["gate_decision"] == GateDecision.HOLD_FOR_REVIEW.value),
                         "hour": dt.hour, "weekend": dt.weekday() >= 5})
        return hist

    def compliance_plan(self, device_id: str, at: float) -> dict:
        dt = datetime.fromtimestamp(at)
        return asdict(compliance_plan_from_history(self._history(device_id, at), dt.hour, dt.weekday() >= 5))

    # ------------------------------------------------------------ tick
    def tick(self):
        now = self.clock()
        cfg, pol = self.cfg, self.cfg.escalation
        self.generate_doses()
        if self._expiry_checked_at is None or now - self._expiry_checked_at >= 60:
            self._expiry_checked_at = now
            self.check_expiry()
        for d in self.db.q("SELECT * FROM doses WHERE state NOT IN (%s)" % ",".join("?" * len(TERMINAL)),
                           [t.value for t in TERMINAL]):
            st = S(d["state"])
            if st == S.SCHEDULED:
                p = self.compliance_plan(d["device_id"], d["due_at"])
                lead = p["reminder_lead_min"] * pol.minute_s
                if now >= d["due_at"] - lead:
                    d = self._move(d, S.DUE, "scheduler", "dose time reached",
                                   reminder_lead_s=int(lead), escalation_window_s=p["escalation_window_min"] * pol.minute_s,
                                   alert_volume=p["alert_volume_pct"], gate_fail_rate=p["gate_fail_rate"],
                                   plan_json=dumps(p))
                    self._escalate(d, 1, f"dose due: audio prompt + OLED at {p['alert_volume_pct']}% volume")
                    self._prompt(d["device_id"], "DUE", d["id"])
                continue
            window = d["escalation_window_s"] or 5 * pol.minute_s
            rate = d["gate_fail_rate"] or 0.0
            tag = " (L3/L4 windows halved: recent gate failures)" if contracted(rate, pol) else ""
            if st in AWAITING_VITALS or st == S.HELD:
                med = self.db.one("SELECT late_window_min FROM medications WHERE id=?", (d["medication_id"],))
                late_s = med["late_window_min"] * pol.minute_s
                if now > d["due_at"] + late_s:
                    # Per-medicine late-dose window. After it, the dose is skipped, never doubled up later.
                    self._move(d, S.MISSED, "scheduler",
                               f"not given within this medicine's late-dose window ({med['late_window_min']:g} min): "
                               "skipped, not doubled up later")
                    continue
                lvl = level_for(max(0, now - d["due_at"]), window, pol, gate_fail_rate=rate,
                                immediate_level=3 if st == S.HELD else 0)
                self._escalate(d, lvl, ("dispensing suppressed by health gate" if st == S.HELD else "awaiting vital-sign reading") + tag)
            elif st == S.DISPENSE_SENT and now - d["state_since"] > cfg.cmd_timeout_s:
                d = self._move(d, S.FAULT, "watchdog", "no ejection report from device", fault="DISPENSE_TIMEOUT")
                self._escalate(d, 3, "device did not report dispensing")
            elif st in (S.EJECTED, S.IN_TRANSIT) and now - d["state_since"] > cfg.conveyor_timeout_s:
                d = self._move(d, S.FAULT, "watchdog", "conveyor did not report arrival", fault="CONVEYOR_TIMEOUT")
                self._escalate(d, 3, "conveyor timeout: possible jam")
            elif st == S.DELIVERED:
                waited = now - d["delivered_at"]
                _, _, l4 = pol.offsets(rate)
                if waited > window + l4 + cfg.collect_grace_s:
                    d = self._move(d, S.MISSED, "scheduler", "tablet not collected from tray")
                    self._tighten(d, +1, "tablet not collected within escalation window")
                else:
                    self._escalate(d, level_for(waited, window, pol, gate_fail_rate=rate), "tablet waiting in tray" + tag)

    # ------------------------------------------------------------ device ingest
    def ingest(self, device_id: str, events: list, kind_expected: Optional[str] = None) -> list:
        dev = self.db.one("SELECT * FROM devices WHERE id=?", (device_id,))
        if not dev:
            raise EngineError("unknown device; register first", 404)
        if kind_expected and dev["kind"] != kind_expected:
            raise EngineError(f"device is {dev['kind']}", 409)
        results = []
        for ev in sorted(events, key=lambda e: e["seq"]):
            now = self.clock()
            try:
                eid = self.db.x("INSERT INTO device_events(device_id,seq,type,device_ts,payload,received_at) VALUES(?,?,?,?,?,?)",
                                (device_id, int(ev["seq"]), ev["type"], ev.get("ts"), dumps(ev.get("data", {})), now))
            except sqlite3.IntegrityError:
                results.append({"seq": ev["seq"], "result": "DUPLICATE_IGNORED"})
                continue
            self.db.x("UPDATE devices SET last_seen=? WHERE id=?", (now, device_id))
            try:
                res = self._handle(dev, ev["type"], ev.get("data", {}), ev.get("ts"))
            except Exception as e:  # recorded, never silently dropped
                res = f"REJECTED: {e}"
                self.audit(device_id, "EVENT_REJECTED", ev.get("data", {}).get("dose_id"),
                           {"seq": ev["seq"], "type": ev["type"], "error": str(e)})
            self.db.x("UPDATE device_events SET result=? WHERE id=?", (res, eid))
            results.append({"seq": ev["seq"], "result": res})
        return results

    def _dose_for(self, dev, data) -> dict:
        d = self.db.one("SELECT * FROM doses WHERE id=? AND device_id=?", (data.get("dose_id"), dev["id"]))
        if not d:
            raise EngineError("dose not found for this device", 404)
        return d

    def _handle(self, dev, etype, data, ts):
        if etype == "HEARTBEAT":
            self.db.x("UPDATE devices SET fw=?, rssi=?, status_json=? WHERE id=?",
                      (data.get("fw"), data.get("rssi"), dumps(data), dev["id"]))
            return "OK"
        if etype == "VITALS":
            return self._on_vitals(dev, data, dev["kind"])
        if etype == "CMD_ACK":
            self.db.x("UPDATE commands SET status='ACKED', acked_at=? WHERE id=? AND device_id=?",
                      (self.clock(), data.get("command_id"), dev["id"]))
            return "OK"
        if etype == "EJECT":
            d = self._dose_for(dev, data)
            self._move(d, S.EJECTED, dev["id"], "servo cycle complete", ir_beam=int(bool(data.get("ir_beam"))))
            return "OK"
        if etype == "CONVEYOR":
            d = self._dose_for(dev, data)
            status = data.get("status")
            if status == "started":
                self._move(d, S.IN_TRANSIT, dev["id"], "conveyor started")
                return "OK"
            if status == "jam":
                d = self._move(d, S.FAULT, dev["id"], "conveyor jam reported by device", fault="CONVEYOR_JAM")
                self._escalate(d, 3, "conveyor jam")
                return "OK"
            if status == "arrived":
                return self._on_arrival(d, dev, data)
            raise EngineError(f"unknown conveyor status {status}")
        if etype == "COLLECT":
            return self._on_collect(self._dose_for(dev, data), dev, data)
        if etype == "ENV":
            return self._on_env(dev, data)
        if etype == "SOS":
            self.sos(dev["id"], source=f"device:{dev['id']}", note=str(data.get("note", ""))[:120])
            return "OK"
        if etype == "FAULT":
            self.audit(dev["id"], "DEVICE_FAULT", data.get("dose_id"), data)
            if data.get("dose_id"):
                d = self._dose_for(dev, data)
                if S(d["state"]) in (S.DISPENSE_SENT, S.EJECTED, S.IN_TRANSIT):
                    d = self._move(d, S.FAULT, dev["id"], data.get("code", "FAULT"), fault=data.get("code"))
                    self._escalate(d, 3, f"device fault {data.get('code')}")
            return "OK"
        raise EngineError(f"unknown event type {etype}")

    def _identity_problem(self, dev, tag) -> Optional[tuple]:
        """Patient check (MedGate addition): with a card registered for this box, the reading must
        come with that card. Returns (prompt_key, reason) or None."""
        expected = dev.get("patient_tag")
        if not expected:
            return None
        if not tag:
            return "SCAN_CARD", "patient card not scanned"
        if str(tag).strip().upper() != expected:
            return "WRONG_PATIENT", "card does not match the patient registered for this box"
        return None

    def _on_vitals(self, dev, data, src):
        now = self.clock()
        r = VitalsReading(hr=data.get("hr"), spo2=data.get("spo2"), bp_sys=data.get("bp_sys"),
                          bp_dia=data.get("bp_dia"), quality=data.get("quality"),
                          measured_at=data.get("measured_at"))
        meal = data.get("meal_taken")
        meal = None if meal is None else bool(meal)
        tag = data.get("patient_tag")
        v = validate_reading(r, now, max_age_s=self.cfg.vitals_max_age_s, min_quality=self.cfg.min_quality)
        ident = self._identity_problem(dev, tag)
        status = "WRONG_PATIENT" if ident and ident[0] == "WRONG_PATIENT" else v.status.value
        reasons = v.reasons + ([ident[1]] if ident else [])
        self.db.x("INSERT INTO vitals(device_id,hr,spo2,bp_sys,bp_dia,quality,measured_at,received_at,status,reasons,source,patient_tag,meal_taken) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (dev["id"], r.hr, r.spo2, r.bp_sys, r.bp_dia, r.quality, r.measured_at, now, status, dumps(reasons), src,
                   tag, None if meal is None else int(meal)))
        if ident and ident[0] == "WRONG_PATIENT":
            self.raise_alert("PATIENT_MISMATCH", dev["id"], None,
                             f"Someone tapped a card that is not {dev.get('patient_name') or 'this patient'}'s at {dev['id']}")
        if v.status.value == "VALID" and not ident:
            self._check_trends(dev["id"])
        waiting = self.db.q("SELECT * FROM doses WHERE device_id=? AND state IN ('DUE','RETAKE') ORDER BY due_at",
                            (dev["id"],))
        if not waiting:
            if ident:
                self._prompt(dev["id"], ident[0])
            return f"STORED ({status}); no dose awaiting vitals"
        out = []
        for d in waiting:
            med = self.db.one("SELECT * FROM medications WHERE id=?", (d["medication_id"],))
            steps = self.tighten_steps(d["medication_id"], d["session"])
            g = health_gate(r, now, rules=json.loads(med["rules_json"]), attempts_so_far=d["gate_attempts"],
                            max_attempts=self.cfg.max_gate_attempts, tighten_steps=steps,
                            max_age_s=self.cfg.vitals_max_age_s, min_quality=self.cfg.min_quality,
                            precheck_failures=[ident[1]] if ident else None,
                            requires_meal=bool(med["requires_meal"]), meal_taken=meal)
            common = dict(gate_attempts=d["gate_attempts"] + 1, gate_decision=g.decision.value,
                          gate_reasons=dumps({"reasons": g.reasons, "rules": g.effective_rules,
                                              "drug_class": med["drug_class"], "requires_meal": bool(med["requires_meal"]),
                                              "meal_taken": meal, "patient_verified": bool(dev.get("patient_tag")) and not ident,
                                              "readings": {"spo2": r.spo2, "bp_sys": r.bp_sys, "bp_dia": r.bp_dia, "hr": r.hr}}),
                          tighten_steps=steps)
            if g.decision == GateDecision.PROCEED:
                too_soon = self._too_soon(d, med)
                if too_soon:
                    gr = json.loads(common["gate_reasons"])
                    gr["reasons"] = [too_soon] + gr["reasons"]
                    d = self._move(d, S.HELD, "double_dose_guard", too_soon, **{**common, "gate_reasons": dumps(gr)})
                    self._escalate(d, 3, "double-dose protection: " + too_soon)
                    self._prompt(d["device_id"], "TOO_SOON", d["id"])
                    out.append(f"dose {d['id']}: HELD (too soon)")
                    continue
                self.db.x("UPDATE doses SET gate_attempts=?, gate_decision=?, gate_reasons=?, tighten_steps=? WHERE id=?",
                          (*common.values(), d["id"]))
                self._issue_dispense(self.db.one("SELECT * FROM doses WHERE id=?", (d["id"],)), "health_gate", "; ".join(g.reasons))
            elif g.decision == GateDecision.RETAKE:
                self._move(d, S.RETAKE, "health_gate", "; ".join(g.reasons), **common)
                key = ident[0] if ident else ("CONFIRM_MEAL" if any("meal not confirmed" in x for x in g.reasons) else "RETAKE")
                self._prompt(d["device_id"], key, d["id"])
            else:
                physio = bool(g.triggered_rules)
                d = self._move(d, S.HELD, "health_gate", "dispensing suppressed: " + "; ".join(g.reasons),
                               physio_hold=int(physio), **common)
                self._escalate(d, 3, ("health alert to doctor + caretaker: " if physio else "unusable readings: ") + "; ".join(g.reasons))
                self._prompt(d["device_id"], "WRONG_PATIENT" if ident and ident[0] == "WRONG_PATIENT" else "HELD", d["id"])
            out.append(f"dose {d['id']}: {g.decision.value}")
        return "; ".join(out)

    def _too_soon(self, d: dict, med: dict) -> Optional[str]:
        """Double-dose protection: the same medicine is never released twice within its minimum
        interval (e.g. a late dose followed by the next scheduled one). 0 h disables the check."""
        gap_s = med["min_interval_h"] * 60 * self.cfg.escalation.minute_s   # same time scale as escalation
        if gap_s <= 0:
            return None
        last = self.db.one("SELECT MAX(dispensed_at) t FROM doses WHERE medication_id=? AND id<>? AND dispensed_at IS NOT NULL",
                           (med["id"], d["id"]))["t"]
        if last and self.clock() - last < gap_s:
            return (f"{med['name']} was already released {(self.clock() - last) / self.cfg.escalation.minute_s:.0f} min ago; "
                    f"minimum interval is {med['min_interval_h']:g} h (double-dose protection)")
        return None

    # ------------------------------------------------------------ early-warning trends
    def _check_trends(self, device_id: str):
        rows = self.db.q("SELECT spo2, bp_sys FROM (SELECT id, spo2, bp_sys FROM vitals WHERE device_id=? AND status='VALID' "
                         "ORDER BY id DESC LIMIT 10) ORDER BY id", (device_id,))
        found = {t["field"]: t for t in detect_trends({"spo2": [r["spo2"] for r in rows], "bp_sys": [r["bp_sys"] for r in rows]})}
        for field, kind in (("spo2", "TREND_SPO2"), ("bp_sys", "TREND_BP")):
            if field in found:
                self.raise_alert(kind, device_id, None, found[field]["message"], found[field]["newer_mean"])
            else:
                self.resolve_alerts(kind, device_id=device_id, by="auto")
        return list(found.values())

    def _on_arrival(self, d, dev, data):
        med = self.db.one("SELECT * FROM medications WHERE id=?", (d["medication_id"],))
        if S(d["state"]) == S.EJECTED:  # device may skip the 'started' message
            d = self._move(d, S.IN_TRANSIT, dev["id"], "conveyor arrival implies start")
        delta = data.get("tray_delta_g")
        v = verify_delivery(bool(d["ir_beam"]), delta, med["unit_weight_g"], med["weight_tol_g"])
        # How many tablets left the box: needed for stock, whatever the verdict.
        out = {DeliveryVerdict.NOT_DISPENSED: 0,
               DeliveryVerdict.WEIGHT_OUT_OF_RANGE: max(1, round((delta or 0) / med["unit_weight_g"]))}.get(v, 1)
        self._consume_stock(med, out, d["id"])
        if v == DeliveryVerdict.CONFIRMED:
            self._move(d, S.DELIVERED, dev["id"], "IR beam and tray weight agree", tray_delta_g=delta,
                       delivery_verdict=v.value, delivered_at=self.clock())
            self._prompt(d["device_id"], "DELIVERED", d["id"])
        elif v == DeliveryVerdict.NOT_DISPENSED:
            d = self._move(d, S.FAULT, dev["id"], "neither sensor saw a tablet (empty compartment or jam); not retried automatically",
                           tray_delta_g=delta, delivery_verdict=v.value, fault="NOTHING_DISPENSED")
            self._escalate(d, 3, "nothing dispensed")
        else:
            d = self._move(d, S.REVIEW_REQUIRED, dev["id"], f"sensor disagreement ({v.value}); not counted as delivered",
                           tray_delta_g=delta, delivery_verdict=v.value)
            self._escalate(d, 3, f"sensor disagreement: {v.value}")
        return v.value

    def _on_collect(self, d, dev, data):
        v = verify_collection(bool(data.get("weight_removed")), bool(data.get("presence")))
        if v == CollectionVerdict.NOT_COLLECTED:
            self.audit(dev["id"], "COLLECTION_CHECK", d["id"], {"verdict": v.value})
            return v.value
        if v == CollectionVerdict.TRAY_EMPTIED_NO_PRESENCE:
            d = self._move(d, S.REVIEW_REQUIRED, dev["id"],
                           "tray emptied but no hand detected: compliance gate (weight AND proximity) not satisfied",
                           collection_verdict=v.value)
            self._escalate(d, 3, "tablet removed without hand detection")
            return v.value
        d = self._move(d, S.COLLECTED, dev["id"], "weight removed AND hand detected", collection_verdict=v.value,
                       collected_at=self.clock())
        self._tighten(d, -MAX_TIGHTEN_STEPS, "successful pickup resets same-session tightening")
        self._prompt(d["device_id"], "COLLECTED", d["id"])
        return v.value

    # ------------------------------------------------------------ stock + refill
    def _consume_stock(self, med: dict, n: int, dose_id: int):
        if n <= 0:
            return
        new = max(0, med["stock"] - n)
        self.db.x("UPDATE medications SET stock=? WHERE id=?", (new, med["id"]))
        self.audit("stock", "STOCK_USED", dose_id, {"medication_id": med["id"], "used": n, "left": new})
        if new <= med["refill_threshold"]:
            days = self.days_of_supply(med["id"], new)
            self.raise_alert("REFILL_NEEDED", med["device_id"], med["id"],
                             f"Box {med['compartment']} ({med['name']}): {new} tablet(s) left"
                             + (f", about {days} day(s) of supply" if days is not None else ""), new)

    def days_of_supply(self, medication_id: int, stock: int):
        per_day = 0.0
        for s in self.db.q("SELECT days FROM schedules WHERE medication_id=? AND active=1", (medication_id,)):
            per_day += len(s["days"].split(",")) / 7
        return round(stock / per_day, 1) if per_day else None

    def refill(self, medication_id: int, count: int, actor: str = "caregiver", expiry_date: Optional[str] = None):
        med = self.db.one("SELECT * FROM medications WHERE id=?", (medication_id,))
        if not med:
            raise EngineError("unknown medication", 404)
        if count < 0 or count > 500:
            raise EngineError("count must be 0..500")
        expiry_date = self._valid_date(expiry_date)
        self.db.x("UPDATE medications SET stock=?, expiry_date=COALESCE(?, expiry_date) WHERE id=?",
                  (count, expiry_date, medication_id))
        self.audit(actor, "REFILLED", None, {"medication_id": medication_id, "from": med["stock"], "to": count,
                                             "expiry_date": expiry_date})
        if expiry_date:
            self.check_expiry()
        if count > med["refill_threshold"]:
            self.resolve_alerts("REFILL_NEEDED", medication_id=medication_id, by=actor)

    # ------------------------------------------------------------ alerts (not tied to a dose)
    def raise_alert(self, kind, device_id, medication_id, message, value=None):
        with self.db.lock:
            if self.db.one("SELECT 1 FROM alerts WHERE kind=? AND resolved_at IS NULL AND "
                           "COALESCE(device_id,'')=COALESCE(?,'') AND COALESCE(medication_id,-1)=COALESCE(?,-1)",
                           (kind, device_id, medication_id)):
                self.db.x("UPDATE alerts SET message=?, value=? WHERE kind=? AND resolved_at IS NULL AND "
                          "COALESCE(device_id,'')=COALESCE(?,'') AND COALESCE(medication_id,-1)=COALESCE(?,-1)",
                          (message, value, kind, device_id, medication_id))
                return False
            aid = self.db.x("INSERT INTO alerts(kind,device_id,medication_id,message,value,created_at) VALUES(?,?,?,?,?,?)",
                            (kind, device_id, medication_id, message, value, self.clock()))
            self.audit("alerts", "ALERT_RAISED", None, {"id": aid, "kind": kind, "message": message})
            return True

    def resolve_alerts(self, kind=None, device_id=None, medication_id=None, alert_id=None, by="system"):
        conds, args = ["resolved_at IS NULL"], []
        for col, val in (("kind", kind), ("device_id", device_id), ("medication_id", medication_id), ("id", alert_id)):
            if val is not None:
                conds.append(f"{col}=?"); args.append(val)
        rows = self.db.q(f"SELECT id FROM alerts WHERE {' AND '.join(conds)}", args)
        for r in rows:
            self.db.x("UPDATE alerts SET resolved_at=?, resolved_by=? WHERE id=?", (self.clock(), by, r["id"]))
            self.audit(by, "ALERT_RESOLVED", None, {"id": r["id"]})
        return len(rows)

    # ------------------------------------------------------------ storage environment
    def _on_env(self, dev, data):
        t, h = data.get("temp_c"), data.get("humidity")
        valid = isinstance(t, (int, float)) and isinstance(h, (int, float)) and -40 <= t <= 85 and 0 <= h <= 100
        cfg = self.cfg
        status = "INVALID" if not valid else (
            "OUT_OF_RANGE" if t > cfg.storage_max_temp_c or h > cfg.storage_max_humidity else "OK")
        self.db.x("INSERT INTO env_readings(device_id,temp_c,humidity,measured_at,received_at,status) VALUES(?,?,?,?,?,?)",
                  (dev["id"], t, h, data.get("measured_at"), self.clock(), status))
        if not valid:
            self.raise_alert("STORAGE_SENSOR", dev["id"], None, f"Storage sensor reading invalid (temp={t}, humidity={h})")
            return "INVALID"
        self.resolve_alerts("STORAGE_SENSOR", device_id=dev["id"], by="auto")
        if t > cfg.storage_max_temp_c:
            self.raise_alert("STORAGE_TEMP", dev["id"], None,
                             f"Medicine storage at {t:.1f} °C (limit {cfg.storage_max_temp_c:g} °C)", t)
        else:
            self.resolve_alerts("STORAGE_TEMP", device_id=dev["id"], by="auto")
        if h > cfg.storage_max_humidity:
            self.raise_alert("STORAGE_HUMIDITY", dev["id"], None,
                             f"Medicine storage at {h:.0f} % humidity (limit {cfg.storage_max_humidity:g} %)", h)
        else:
            self.resolve_alerts("STORAGE_HUMIDITY", device_id=dev["id"], by="auto")
        return status

    # ------------------------------------------------------------ device commands
    def pending_commands(self, device_id: str) -> list:
        with self.db.lock:
            cmds = self.db.q("SELECT * FROM commands WHERE device_id=? AND status IN ('PENDING','SENT') ORDER BY id", (device_id,))
            now = self.clock()
            for c in cmds:
                if c["status"] == "PENDING":
                    self.db.x("UPDATE commands SET status='SENT', sent_at=? WHERE id=?", (now, c["id"]))
        return [{"command_id": c["id"], "kind": c["kind"], **json.loads(c["payload"])} for c in cmds]

    # ------------------------------------------------------------ human review
    def review(self, dose_id: int, decision: str, reviewer: str, note: str = ""):
        if not reviewer or not reviewer.strip():
            raise EngineError("reviewer name required")
        d = self.db.one("SELECT * FROM doses WHERE id=?", (dose_id,))
        if not d:
            raise EngineError("dose not found", 404)
        if d["state"] != S.HELD.value:
            raise EngineError(f"dose is {d['state']}, only HELD doses can be reviewed", 409)
        self.db.x("UPDATE doses SET reviewer=?, review_note=? WHERE id=?", (reviewer, note, dose_id))
        if decision == "approve":
            return self._issue_dispense(d, f"reviewer:{reviewer}", f"gate overridden by named reviewer: {note}")
        if decision == "withhold":
            return self._move(d, S.WITHHELD, f"reviewer:{reviewer}", f"withheld by reviewer: {note}")
        raise EngineError("decision must be approve or withhold")

    # ------------------------------------------------------------ reads
    def stats(self) -> dict:
        rows = self.db.q("SELECT state, COUNT(*) n FROM doses GROUP BY state")
        by = {r["state"]: r["n"] for r in rows}
        done = by.get("COLLECTED", 0)
        counted = done + by.get("MISSED", 0)
        return {"by_state": by, "total_doses": sum(by.values()),
                "adherence_pct": round(100 * done / counted, 1) if counted else None,
                "adherence_basis": f"{done} collected of {counted} doses with a patient outcome",
                "open_escalations": self.db.one("SELECT COUNT(*) n FROM doses WHERE escalation_level>=3 AND state IN ('HELD','DELIVERED','DUE','RETAKE','REVIEW_REQUIRED','FAULT')")["n"],
                "awaiting_review": by.get("HELD", 0) + by.get("REVIEW_REQUIRED", 0),
                "open_alerts": self.db.one("SELECT COUNT(*) n FROM alerts WHERE resolved_at IS NULL")["n"],
                "open_sos": self.db.one("SELECT COUNT(*) n FROM alerts WHERE kind='SOS' AND resolved_at IS NULL")["n"]}

    def doctor_portal(self, limit: int = 100) -> dict:
        """IDF-B §7.7 Doctor Portal: per-event gate audit + gate-failure vs non-adherence trend."""
        events = self.db.q("SELECT d.id, d.due_at, d.session, d.food, d.state, d.gate_decision, d.gate_reasons, d.tighten_steps, "
                           "d.escalation_level, d.reviewer, m.name medication FROM doses d JOIN medications m ON m.id=d.medication_id "
                           "WHERE d.gate_decision IS NOT NULL ORDER BY d.due_at DESC LIMIT ?", (limit,))
        for e in events:
            e["gate_reasons"] = json.loads(e["gate_reasons"]) if e["gate_reasons"] else None
        trend = self.db.q(
            "SELECT date(due_at,'unixepoch','localtime') day, "
            "SUM(gate_decision='HOLD_FOR_REVIEW') gate_failures, SUM(gate_decision IS NOT NULL) gated, "
            "SUM(state='MISSED') missed, SUM(state IN ('MISSED','COLLECTED')) outcomes "
            "FROM doses GROUP BY day ORDER BY day")
        for t in trend:
            t["gate_failure_rate"] = round(t["gate_failures"] / t["gated"], 2) if t["gated"] else None
            t["non_adherence_rate"] = round(t["missed"] / t["outcomes"], 2) if t["outcomes"] else None
        per_session = self.db.q(
            "SELECT session, SUM(gate_decision='HOLD_FOR_REVIEW') gate_failures, SUM(gate_decision IS NOT NULL) gated, "
            "AVG(CASE WHEN gate_reasons IS NOT NULL THEN json_extract(gate_reasons,'$.readings.spo2') END) avg_spo2, "
            "AVG(CASE WHEN gate_reasons IS NOT NULL THEN json_extract(gate_reasons,'$.readings.bp_sys') END) avg_bp_sys "
            "FROM doses GROUP BY session")
        return {"events": events, "daily_trend": trend, "per_session": per_session}

    def caretaker_portal(self) -> dict:
        """IDF-B §7.7 Caretaker Portal: status, escalation history and urgency score."""
        out = []
        for dev in self.db.q("SELECT id, name, kind, patient_name FROM devices ORDER BY id"):
            recent = self.db.q("SELECT gate_decision FROM doses WHERE device_id=? AND gate_decision IS NOT NULL "
                               "ORDER BY due_at DESC LIMIT ?", (dev["id"], self.cfg.escalation.lookback_sessions))
            rate = sum(r["gate_decision"] == "HOLD_FOR_REVIEW" for r in recent) / len(recent) if recent else 0.0
            missed = self.db.one("SELECT COUNT(*) n FROM doses WHERE device_id=? AND state='MISSED' "
                                 "AND due_at > ?", (dev["id"], self.clock() - 7 * 86400))["n"]
            max_lvl = self.db.one("SELECT COALESCE(MAX(escalation_level),0) n FROM doses WHERE device_id=? "
                                  "AND due_at > ?", (dev["id"], self.clock() - 7 * 86400))["n"]
            idf_score = round(rate * missed * max_lvl, 2)
            score = round((1 + rate) * missed * max_lvl, 2)
            latest = self.db.q("SELECT d.id, d.state, d.due_at, d.escalation_level, m.name medication FROM doses d "
                               "JOIN medications m ON m.id=d.medication_id WHERE d.device_id=? ORDER BY d.due_at DESC LIMIT 10",
                               (dev["id"],))
            out.append({"device": dev, "gate_failure_rate_last5": round(rate, 2), "missed_7d": missed,
                        "max_escalation_level_7d": max_lvl, "urgency_score": score, "urgency_score_idf_formula": idf_score,
                        "urgency_note": "urgency = (1 + gate failure rate) x missed doses (7 d) x highest escalation level. "
                                        "The IDF-B text multiplies by the rate itself, which gives 0 for a patient who misses "
                                        "every dose but has normal vitals; both values are shown.",
                        "latest": latest,
                        "sos_open": bool(self.db.one("SELECT 1 FROM alerts WHERE device_id=? AND kind='SOS' AND resolved_at IS NULL",
                                                     (dev["id"],)))})
        return {"patients": out}

    # ------------------------------------------------------------ doctor report (MedGate addition)
    def doctor_report(self, device_id: str, days: int = 30) -> dict:
        """Everything a doctor needs at a clinic visit, for one dispenser/patient, from stored records."""
        dev = self.db.one("SELECT id, name, kind, patient_name, patient_tag, language FROM devices WHERE id=?", (device_id,))
        if not dev:
            raise EngineError("unknown device", 404)
        since = self.clock() - days * 86400
        by = {r["state"]: r["n"] for r in self.db.q(
            "SELECT state, COUNT(*) n FROM doses WHERE device_id=? AND due_at>=? GROUP BY state", (device_id, since))}
        done, missed = by.get("COLLECTED", 0), by.get("MISSED", 0)
        meds = self.db.q("SELECT id, name, compartment, drug_class, rules_json, requires_meal, min_interval_h, late_window_min, "
                         "stock, refill_threshold FROM medications WHERE device_id=? ORDER BY compartment", (device_id,))
        for m in meds:
            m["rules"] = json.loads(m.pop("rules_json"))
            m["drug_class_label"] = DRUG_CLASSES.get(m["drug_class"], {}).get("label", m["drug_class"])
            m["per_med"] = {r["state"]: r["n"] for r in self.db.q(
                "SELECT state, COUNT(*) n FROM doses WHERE medication_id=? AND due_at>=? GROUP BY state", (m["id"], since))}
        holds = self.db.q("SELECT d.id, d.due_at, d.state, d.gate_reasons, d.reviewer, d.review_note, m.name medication "
                          "FROM doses d JOIN medications m ON m.id=d.medication_id WHERE d.device_id=? AND d.due_at>=? "
                          "AND (d.gate_decision='HOLD_FOR_REVIEW' OR d.state IN ('HELD','WITHHELD')) ORDER BY d.due_at DESC LIMIT 50", (device_id, since))
        for h in holds:
            h["reasons"] = (json.loads(h.pop("gate_reasons")) or {}).get("reasons", []) if h.get("gate_reasons") else []
        vit = self.db.q("SELECT received_at, spo2, bp_sys, bp_dia, hr FROM vitals WHERE device_id=? AND status='VALID' "
                        "AND received_at>=? ORDER BY id", (device_id, since))

        def summary(field):
            xs = [r[field] for r in vit if r[field] is not None]
            return {"n": len(xs), "min": min(xs), "avg": round(sum(xs) / len(xs), 1), "max": max(xs)} if xs else None

        alerts = self.db.q("SELECT kind, message, created_at, resolved_at, resolved_by FROM alerts WHERE device_id=? "
                           "AND created_at>=? ORDER BY id DESC LIMIT 50", (device_id, since))
        overrides = self.db.q("SELECT d.id, d.due_at, d.state, d.reviewer, d.review_note, m.name medication FROM doses d "
                              "JOIN medications m ON m.id=d.medication_id WHERE d.device_id=? AND d.due_at>=? "
                              "AND d.reviewer IS NOT NULL ORDER BY d.due_at DESC", (device_id, since))
        return {"device": dev, "days": days, "generated_at": self.clock(),
                "adherence_pct": round(100 * done / (done + missed), 1) if done + missed else None,
                "by_state": by, "medications": meds, "holds": holds, "overrides": overrides,
                "vitals": {"spo2": summary("spo2"), "bp_sys": summary("bp_sys"), "bp_dia": summary("bp_dia"),
                           "hr": summary("hr"), "series": vit[-120:]},
                "trends": [a for a in alerts if a["kind"].startswith("TREND_")],
                "alerts": alerts, "audit": self.verify_audit()}


    # ------------------------------------------------------------ medicine expiry (MedGate addition)
    @staticmethod
    def _valid_date(d):
        if d in (None, ""):
            return None
        try:
            return date.fromisoformat(str(d)).isoformat()
        except ValueError:
            raise EngineError("expiry date must be YYYY-MM-DD")

    def set_expiry(self, medication_id: int, expiry_date: Optional[str], actor: str = "caregiver"):
        if not self.db.one("SELECT 1 FROM medications WHERE id=?", (medication_id,)):
            raise EngineError("unknown medication", 404)
        d = self._valid_date(expiry_date)
        self.db.x("UPDATE medications SET expiry_date=? WHERE id=?", (d, medication_id))
        self.audit(actor, "EXPIRY_SET", None, {"medication_id": medication_id, "expiry_date": d})
        self.check_expiry()

    EXPIRY_WARN_DAYS = 14

    def check_expiry(self):
        """Expired box: alert (and _issue_dispense refuses it). Expiring within 14 days: early alert."""
        today = date.fromtimestamp(self.clock())
        for m in self.db.q("SELECT id, device_id, name, compartment, expiry_date FROM medications"):
            if not m["expiry_date"]:
                self.resolve_alerts("MEDICINE_EXPIRED", medication_id=m["id"], by="auto")
                self.resolve_alerts("EXPIRY_SOON", medication_id=m["id"], by="auto")
                continue
            left = (date.fromisoformat(m["expiry_date"]) - today).days
            if left < 0:
                self.resolve_alerts("EXPIRY_SOON", medication_id=m["id"], by="auto")
                self.raise_alert("MEDICINE_EXPIRED", m["device_id"], m["id"],
                                 f"Box {m['compartment']} ({m['name']}) expired on {m['expiry_date']}: replace it, it will not be given", left)
            elif left <= self.EXPIRY_WARN_DAYS:
                self.resolve_alerts("MEDICINE_EXPIRED", medication_id=m["id"], by="auto")
                self.raise_alert("EXPIRY_SOON", m["device_id"], m["id"],
                                 f"Box {m['compartment']} ({m['name']}) expires on {m['expiry_date']} ({left} day(s) left): order a fresh strip", left)
            else:
                self.resolve_alerts("MEDICINE_EXPIRED", medication_id=m["id"], by="auto")
                self.resolve_alerts("EXPIRY_SOON", medication_id=m["id"], by="auto")

    # ------------------------------------------------------------ SOS help button (MedGate addition)
    def sos(self, device_id: str, source: str = "dashboard", note: str = ""):
        dev = self.db.one("SELECT * FROM devices WHERE id=?", (device_id,))
        if not dev:
            raise EngineError("unknown device", 404)
        who = dev["patient_name"] or device_id
        when = datetime.fromtimestamp(self.clock()).strftime("%H:%M")
        self.raise_alert("SOS", device_id, None, f"HELP requested by {who} at {when} (from {source})"
                         + (f": {note}" if note else ""))
        self.audit(source, "SOS", None, {"device_id": device_id, "note": note})
        self._prompt(device_id, "SOS_ACK")
        return {"ok": True, "message": f"Help requested for {who}"}

    # ------------------------------------------------------------ patient screen (MedGate addition)
    def _day_bounds(self, ts: float):
        d0 = datetime.fromtimestamp(ts).replace(hour=0, minute=0, second=0, microsecond=0)
        return d0.timestamp(), (d0 + timedelta(days=1)).timestamp()

    def patient_screen(self, device_id: str) -> dict:
        dev = self.db.one("SELECT id, name, kind, patient_name, language FROM devices WHERE id=?", (device_id,))
        if not dev:
            raise EngineError("unknown device", 404)
        now = self.clock()
        a, b = self._day_bounds(now)
        today = self.db.q("SELECT d.id, d.due_at, d.state, m.name medication, m.compartment FROM doses d "
                          "JOIN medications m ON m.id=d.medication_id WHERE d.device_id=? AND d.due_at>=? AND d.due_at<? "
                          "ORDER BY d.due_at", (device_id, a, b))
        active = self.db.one("SELECT d.id, d.due_at, d.state, m.name medication, m.compartment FROM doses d "
                             "JOIN medications m ON m.id=d.medication_id WHERE d.device_id=? AND d.state IN "
                             "('DUE','RETAKE','HELD','DISPENSE_SENT','EJECTED','IN_TRANSIT','DELIVERED') ORDER BY d.due_at DESC LIMIT 1",
                             (device_id,))
        nxt = self.db.one("SELECT d.id, d.due_at, m.name medication, m.compartment FROM doses d JOIN medications m "
                          "ON m.id=d.medication_id WHERE d.device_id=? AND d.state='SCHEDULED' AND d.due_at>=? "
                          "ORDER BY d.due_at LIMIT 1", (device_id, now))
        prompt_row = self.db.one("SELECT id, key, lang, text, fallback, created_at FROM prompts WHERE device_id=? "
                                 "ORDER BY id DESC LIMIT 1", (device_id,))
        sos = self.db.one("SELECT id, message, created_at FROM alerts WHERE device_id=? AND kind='SOS' AND resolved_at IS NULL "
                          "ORDER BY id DESC LIMIT 1", (device_id,))
        return {"device": dev, "now": now, "today": today, "active": active, "next": nxt,
                "prompt": prompt_row, "sos": sos,
                "taken_today": sum(d["state"] == "COLLECTED" for d in today), "total_today": len(today)}

    # ------------------------------------------------------------ adherence calendar (MedGate addition)
    def adherence_calendar(self, device_id: str, days: int = 28) -> dict:
        if not self.db.one("SELECT 1 FROM devices WHERE id=?", (device_id,)):
            raise EngineError("unknown device", 404)
        a, _ = self._day_bounds(self.clock())
        since = a - (days - 1) * 86400
        rows = self.db.q(
            "SELECT date(due_at,'unixepoch','localtime') day, session, "
            "SUM(state='COLLECTED') taken, SUM(state='MISSED') missed, SUM(state IN ('HELD','WITHHELD')) held, "
            "SUM(state IN ('FAULT','REVIEW_REQUIRED')) problem, COUNT(*) total "
            "FROM doses WHERE device_id=? AND due_at>=? GROUP BY day, session ORDER BY day", (device_id, since))
        day_list = [(datetime.fromtimestamp(since) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
        return {"days": day_list, "sessions": ["MORNING", "AFTERNOON", "NIGHT"], "cells": rows}

    # ------------------------------------------------------------ family daily digest (MedGate addition)
    ALERT_LABEL = {"SOS": "HELP", "TREND_BP": "BP rising (early warning)", "TREND_SPO2": "Oxygen falling (early warning)",
                   "PATIENT_MISMATCH": "Wrong card", "REFILL_NEEDED": "Refill", "EXPIRY_SOON": "Expiring soon",
                   "MEDICINE_EXPIRED": "Expired medicine", "STORAGE_TEMP": "Box too hot", "STORAGE_HUMIDITY": "Box too damp",
                   "STORAGE_SENSOR": "Storage sensor"}

    def family_digest(self, device_id: str) -> dict:
        p = self.patient_screen(device_id)
        who = p["device"]["patient_name"] or device_id
        day = datetime.fromtimestamp(p["now"]).strftime("%d %b %Y")
        lines = [f"MedGate update for {who}, {day}"]
        missed = [d for d in p["today"] if d["state"] == "MISSED"]
        held = [d for d in p["today"] if d["state"] in ("HELD", "WITHHELD")]
        lines.append(f"Medicines taken today: {p['taken_today']} of {p['total_today']}")
        for d in missed:
            lines.append(f"- MISSED: {d['medication']} at {datetime.fromtimestamp(d['due_at']).strftime('%H:%M')}")
        for d in held:
            lines.append(f"- HELD for doctor check: {d['medication']} at {datetime.fromtimestamp(d['due_at']).strftime('%H:%M')}")
        alerts = self.db.q("SELECT kind, message FROM alerts WHERE device_id=? AND resolved_at IS NULL ORDER BY id DESC",
                           (device_id,))
        alerts += self.db.q("SELECT a.kind, a.message FROM alerts a JOIN medications m ON m.id=a.medication_id "
                            "WHERE m.device_id=? AND a.device_id IS NULL AND a.resolved_at IS NULL", (device_id,))
        for a in alerts:
            lines.append(f"- {self.ALERT_LABEL.get(a['kind'], a['kind'].replace('_', ' ').capitalize())}: {a['message']}")
        if p["next"]:
            lines.append(f"Next dose: {p['next']['medication']} at {datetime.fromtimestamp(p['next']['due_at']).strftime('%H:%M')}")
        if not missed and not held and not alerts:
            lines.append("All good. No missed doses and no alerts.")
        lines.append("(Prototype. Pickup means the tray was emptied, not that the tablet was swallowed.)")
        return {"text": "\n".join(lines), "lines": lines}
