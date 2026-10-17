"""SIMULATED dispenser (no hardware).

Device id SIM-001 is registered with kind=SIMULATOR; every event it produces
is stored with that label and the dashboard shows a SIMULATOR badge. It
speaks the same event protocol as the ESP32 firmware, through either an
in-process transport (dashboard buttons) or HTTP (`python -m medgate.simulator`).
"""
from __future__ import annotations

import argparse
import threading
import time

SIM_ID = "SIM-001"
SIM_TAG = "MG-CARD-0001"   # simulated patient card (RFID UID)


class InProcess:
    def __init__(self, engine):
        self.e = engine

    def send(self, device_id, events):
        return self.e.ingest(device_id, events, kind_expected="SIMULATOR")

    def commands(self, device_id):
        return self.e.pending_commands(device_id)


class Http:
    def __init__(self, base, key):
        import httpx
        self.c = httpx.Client(base_url=base, headers={"X-Device-Key": key}, timeout=10)

    def send(self, device_id, events):
        r = self.c.post(f"/api/device/{device_id}/events", json={"events": events})
        r.raise_for_status()
        return r.json()["results"]

    def commands(self, device_id):
        r = self.c.get(f"/api/device/{device_id}/commands")
        r.raise_for_status()
        return r.json()["commands"]


class VirtualDevice:
    def __init__(self, transport, clock=time.time, device_id=SIM_ID, step_s=1.5):
        self.t, self.clock, self.id, self.step_s = transport, clock, device_id, step_s
        self.seq = int(clock() * 1000)  # monotonic across restarts
        self.offline = False
        self.buffer = []   # offline queue, flushed on reconnect
        self.log = []
        self.focus_dose = None   # the scenario's own dose: handle its command first

    def _ev(self, etype, **data):
        self.seq += 1
        return {"seq": self.seq, "type": etype, "ts": self.clock(), "data": data}

    def emit(self, etype, **data):
        ev = self._ev(etype, **data)
        self.buffer.append(ev)
        if self.offline:
            self.log.append(f"OFFLINE: buffered {etype} (seq {ev['seq']})")
            return None
        return self.flush()

    def flush(self):
        if not self.buffer:
            return []
        res = self.t.send(self.id, self.buffer)
        self.log.append(f"sent {len(self.buffer)} event(s): " + ", ".join(f"{r['seq']}={r['result']}" for r in res))
        self.buffer = []
        return res

    def wait(self, k=1.0):
        time.sleep(self.step_s * k)

    def vitals(self, hr=74, spo2=97, bp_sys=122, bp_dia=78, quality=0.92, age=3, patient_tag=SIM_TAG, **kw):
        data = dict(hr=hr, spo2=spo2, bp_sys=bp_sys, bp_dia=bp_dia, quality=quality, measured_at=self.clock() - age,
                    patient_tag=patient_tag, **kw)
        return self.emit("VITALS", **{k: v for k, v in data.items() if v is not None})

    def run_dispense_cycle(self, ir=True, tray_delta_g=None, conveyor="ok", collect=True, presence=True):
        cmds = []
        for _ in range(10):
            cmds = self.t.commands(self.id)
            if cmds:
                break
            self.wait(0.3)
        if not cmds:
            self.log.append("no dispense command received (gate did not proceed)")
            return None
        c = next((x for x in cmds if x["dose_id"] == self.focus_dose), cmds[0])
        others = [x for x in cmds if x is not c]
        did = self._cycle(c, ir, tray_delta_g, conveyor, collect, presence)
        for o in others:   # one reading can release several waiting doses: a real device runs them all
            self.log.append(f"also pending: dose {o['dose_id']}")
            self._cycle(o, True, None, "ok", True, True)
        return did

    def _cycle(self, c, ir, tray_delta_g, conveyor, collect, presence):
        did = c["dose_id"]
        if tray_delta_g is None:                      # one tablet of THIS medicine lands on the tray
            tray_delta_g = c.get("unit_weight_g", 0.5)
        self.emit("CMD_ACK", command_id=c["command_id"])
        self.log.append(f"servo: compartment {c['compartment']} for dose {did}")
        self.wait()
        self.emit("EJECT", dose_id=did, ir_beam=ir)
        self.wait()
        self.emit("CONVEYOR", dose_id=did, status="started")
        self.wait()
        if conveyor == "jam":
            self.emit("CONVEYOR", dose_id=did, status="jam")
            return did
        self.emit("CONVEYOR", dose_id=did, status="arrived", tray_delta_g=tray_delta_g)
        if collect:
            self.wait(2)
            self.emit("COLLECT", dose_id=did, weight_removed=True, presence=presence)
        return did


SCENARIOS = {
    "normal": "SpO2 97 AND BP 122/78 pass → dispense → IR + tray weight agree → weight removed AND hand detected → COLLECTED",
    "abnormal_vitals": "Systolic 172 > 160 → dispensing suppressed (HELD), health alert L3; a named reviewer may approve or withhold",
    "low_spo2": "SpO2 90 < 94 → dispensing suppressed (HELD)",
    "missing_bp": "BP module returns nothing → AND gate cannot pass → retake",
    "bad_readings": "Three unusable readings (poor contact, sensor error, stale) → HELD for review",
    "borderline": "SpO2 94.5, BP 158/90: passes normally, but is HELD if a previous same-session pickup was missed (threshold tightening)",
    "low_stock": "Box 1 refilled with only 1 tablet → dose dispensed → refill alert; next dose → EMPTY_COMPARTMENT fault, nothing dispensed",
    "hot_storage": "AHT20 reports 34.5 °C / 78 % → storage temperature and humidity alerts (resolve them in Alerts, or run storage_ok)",
    "storage_ok": "AHT20 reports 25 °C / 50 % → storage alerts clear automatically",
    "removed_no_hand": "Tray weight drops but no hand detected → compliance AND gate fails → REVIEW_REQUIRED",
    "sensor_disagreement": "IR beam broke but no weight reached the tray → REVIEW_REQUIRED, no auto re-dispense",
    "two_tablets": "Tray weight equals two tablets → REVIEW_REQUIRED",
    "conveyor_jam": "Conveyor reports jam → FAULT + escalation",
    "uncollected": "Delivered but never picked up → L1..L4 (L3/L4 sooner after recent gate failures) → MISSED after ~2.5 min → next same-session gate tightened",
    "offline_replay": "Wi-Fi drops mid-cycle; events buffered, replayed twice; duplicates ignored",
    # --- MedGate additions: drug-specific gate, patient check, double-dose guard, early warning
    "bp_drug_low_bp": "Box 3 BP-lowering drug, BP 95/62: passes the general gate, but HELD by this drug's own rule (systolic < 100: hypotension risk)",
    "beta_blocker_slow_pulse": "Box 4 beta-blocker, pulse 52: HELD by this drug's own rule (HR < 60: bradycardia risk)",
    "beta_blocker_no_hr": "Box 4 beta-blocker, pulse not measured: HR is required for this drug → RETAKE (a missing value never passes)",
    "diabetes_no_meal": "Box 5 diabetes tablet, patient says 'not eaten': HELD (hypoglycaemia risk)",
    "diabetes_meal_ok": "Box 5 diabetes tablet: device asks to confirm the meal → RETAKE; patient confirms → dispensed → COLLECTED",
    "double_dose": "Box 3: one dose given and taken, then a second dose comes due right away → HELD by double-dose protection",
    "no_card": "Patient forgets to tap the card → RETAKE with voice prompt; taps it → dose proceeds normally",
    "wrong_patient": "Someone else's card is tapped → reading not used, PATIENT_MISMATCH alert, dose not released",
    "sos": "Patient holds a hand over the tray for 5 s (or presses HELP): SOS alert to family and doctor, calming voice prompt",
    "expired_medicine": "Box 2's strip is past its expiry date: the dose is refused (FAULT), alert to replace it. Fix it on Medicines → expiry",
    "full_demo": "One click runs the whole story in order: normal, high BP held, BP drug held at LOW BP, meal check, wrong card, double dose, slow drift warning, SOS",
    "trend_decline": "10 readings over time: every one passes the gate, but SpO2 drifts 98 → 94.5 and systolic rises 118 → 146 → early-warning alerts to the doctor",
}

# Which compartment/medication each scenario uses (1 = general demo tablet).
SCENARIO_MED = {"bp_drug_low_bp": 3, "double_dose": 3, "beta_blocker_slow_pulse": 4, "beta_blocker_no_hr": 4,
                "diabetes_no_meal": 5, "diabetes_meal_ok": 5}


def run_scenario(engine, name: str, medication_id: int = None, step_s=1.5, status: dict = None):
    status = status if status is not None else {}
    medication_id = medication_id or SCENARIO_MED.get(name, 1)
    dev = VirtualDevice(InProcess(engine), engine.clock, step_s=step_s)
    status.update(name=name, running=True, log=dev.log, dose_id=None)
    try:
        if name == "full_demo":
            story = [("normal", "Everything fine: checked, given, picked up"),
                     ("abnormal_vitals", "BP 172 is too high: box stays shut, doctor must decide (see Review)"),
                     ("bp_drug_low_bp", "BP 95 passes the general rule but a BP tablet is held at LOW BP"),
                     ("diabetes_meal_ok", "Diabetes tablet: device asks 'have you eaten?', then gives it"),
                     ("wrong_patient", "Someone else's card: nothing released, alert raised"),
                     ("double_dose", "Same BP tablet due again too soon: held, no double dose"),
                     ("trend_decline", "Every reading passes, but oxygen drifts down: early warning to doctor"),
                     ("sos", "Patient asks for help: SOS to family and doctor")]
            for i, (sub, say) in enumerate(story, 1):
                dev.log.append(f"===== STEP {i}/{len(story)}: {sub} =====")
                dev.log.append(say)
                sub_status = run_scenario(engine, sub, step_s=step_s)
                dev.log.extend("   " + line for line in sub_status.get("log", []))
                status["dose_id"] = sub_status.get("dose_id") or status.get("dose_id")
            dev.log.append("===== DEMO FINISHED: open Review, Alerts, Doctor → report, and Patient screen =====")
            return status
        if name == "sos":
            dev.log.append("patient holds a hand over the tray for 5 s while nothing is due")
            dev.emit("SOS", note="hand held over tray 5 s")
            return status
        if name == "expired_medicine":
            from datetime import date, timedelta
            engine.set_expiry(2, (date.fromtimestamp(engine.clock()) - timedelta(days=1)).isoformat(), actor="simulator")
            dev.log.append("box 2 strip set to expired yesterday")
            did = engine.create_dose_now(2, actor="simulator")
            status["dose_id"] = dev.focus_dose = did
            engine.tick()
            dev.vitals()
            return status
        if name in ("hot_storage", "storage_ok"):     # storage checks need no dose
            dev.emit("ENV", temp_c=34.5 if name == "hot_storage" else 25.0,
                     humidity=78 if name == "hot_storage" else 50, measured_at=dev.clock())
            return status
        if name == "trend_decline":                    # readings first, then a dose that still passes
            series = [(98, 118), (98, 120), (98, 122), (97.5, 124), (97, 127), (96, 131), (95.5, 135), (95, 139), (94.8, 143), (94.5, 146)]
            for spo2, sys_ in series[:-1]:
                dev.vitals(spo2=spo2, bp_sys=sys_, bp_dia=80)
                dev.wait(0.3)
            dev.log.append("dose due: last reading still passes the gate")
            did = engine.create_dose_now(medication_id, actor="simulator")
            status["dose_id"] = dev.focus_dose = did
            engine.tick()
            dev.vitals(spo2=series[-1][0], bp_sys=series[-1][1], bp_dia=80)
            dev.run_dispense_cycle()
            return status
        did = engine.create_dose_now(medication_id, actor="simulator")
        status["dose_id"] = dev.focus_dose = did
        engine.tick()
        dev.log.append(f"dose {did} created and due")
        dev.wait()
        if name == "normal":
            dev.vitals()
            dev.run_dispense_cycle()
        elif name == "abnormal_vitals":
            dev.vitals(bp_sys=172, bp_dia=96)
        elif name == "low_spo2":
            dev.vitals(spo2=90)
        elif name == "missing_bp":
            dev.vitals(bp_sys=None, bp_dia=None)
        elif name == "borderline":
            dev.vitals(spo2=94.5, bp_sys=158, bp_dia=90)
            dev.run_dispense_cycle()
        elif name == "low_stock":
            engine.refill(medication_id, 1, actor="simulator")
            dev.log.append("box 1 set to 1 tablet")
            dev.vitals()
            dev.run_dispense_cycle()
            dev.log.append("second dose with the box now empty")
            did2 = engine.create_dose_now(medication_id, actor="simulator")
            engine.tick()
            dev.vitals()
            status["dose_id"] = did2
        elif name == "removed_no_hand":
            dev.vitals()
            dev.run_dispense_cycle(presence=False)
        elif name == "bad_readings":
            dev.vitals(quality=0.25)
            dev.wait()
            dev.vitals(spo2=0)
            dev.wait()
            dev.vitals(age=900)
        elif name == "sensor_disagreement":
            dev.vitals()
            dev.run_dispense_cycle(ir=True, tray_delta_g=0.0, collect=False)
        elif name == "two_tablets":
            dev.vitals()
            dev.run_dispense_cycle(tray_delta_g=1.0, collect=False)
        elif name == "conveyor_jam":
            dev.vitals()
            dev.run_dispense_cycle(conveyor="jam")
        elif name == "uncollected":
            dev.vitals()
            dev.run_dispense_cycle(collect=False)
        elif name == "offline_replay":
            dev.vitals()
            dev.wait()
            cmds = dev.t.commands(dev.id)
            c = cmds[0]
            dev.emit("CMD_ACK", command_id=c["command_id"])
            dev.offline = True
            dev.log.append("Wi-Fi lost: device keeps running and buffers events locally")
            dev.emit("EJECT", dose_id=c["dose_id"], ir_beam=True)
            dev.emit("CONVEYOR", dose_id=c["dose_id"], status="started")
            dev.emit("CONVEYOR", dose_id=c["dose_id"], status="arrived", tray_delta_g=0.5)
            replay = list(dev.buffer)
            dev.wait(2)
            dev.offline = False
            dev.log.append("Wi-Fi restored: flushing buffer")
            dev.flush()
            dev.log.append("network glitch: same batch delivered again")
            res = dev.t.send(dev.id, replay)
            dev.log.append("second delivery: " + ", ".join(r["result"] for r in res))
            dev.wait(2)
            dev.emit("COLLECT", dose_id=c["dose_id"], weight_removed=True, presence=True)
        elif name == "bp_drug_low_bp":
            dev.vitals(bp_sys=95, bp_dia=62)
        elif name == "beta_blocker_slow_pulse":
            dev.vitals(hr=52)
        elif name == "beta_blocker_no_hr":
            dev.vitals(hr=None)
        elif name == "diabetes_no_meal":
            dev.vitals(meal_taken=False)
        elif name == "diabetes_meal_ok":
            dev.vitals()
            dev.log.append("device asks: 'have you eaten?' → patient holds hand over tray (meal confirmed)")
            dev.wait()
            dev.vitals(meal_taken=True)
            dev.run_dispense_cycle()
        elif name == "double_dose":
            dev.vitals()
            dev.run_dispense_cycle()
            dev.log.append("a second dose of the same medicine comes due straight away")
            did2 = engine.create_dose_now(medication_id, actor="simulator")
            status["dose_id"] = did2
            engine.tick()
            dev.vitals()
        elif name == "no_card":
            dev.vitals(patient_tag=None)
            dev.log.append("voice prompt: 'please tap your patient card' → patient taps it")
            dev.wait()
            dev.vitals()
            dev.run_dispense_cycle()
        elif name == "wrong_patient":
            dev.vitals(patient_tag="MG-CARD-9999")
        else:
            raise ValueError(f"unknown scenario {name}")
    except Exception as e:
        dev.log.append(f"ERROR: {e}")
    finally:
        status["running"] = False
    return status


def execute_pending(engine, status: dict, step_s=1.5):
    """Simulated device picks up a command it did not start itself (e.g. a dose a
    human reviewer just approved) and runs the dispense cycle, like the firmware's poll."""
    dev = VirtualDevice(InProcess(engine), engine.clock, step_s=step_s)
    status.update(name="execute_approved_command", running=True, log=dev.log, dose_id=None)
    try:
        dev.log.append("polled server: pending command found")
        status["dose_id"] = dev.run_dispense_cycle()
    except Exception as e:
        dev.log.append(f"ERROR: {e}")
    finally:
        status["running"] = False


def start_scenario_thread(engine, name, status):
    th = threading.Thread(target=run_scenario, args=(engine, name), kwargs={"status": status}, daemon=True)
    th.start()
    return th


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SIMULATED dispenser over HTTP (same protocol as firmware)")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--key", default="dev-key-change-me")
    ap.add_argument("--spo2", type=float, default=97)
    a = ap.parse_args()
    d = VirtualDevice(Http(a.base, a.key), step_s=1.0)
    print("SIMULATOR: sending vitals, then executing any dispense command")
    d.emit("HEARTBEAT", fw="simulator", rssi=-50)
    d.vitals(spo2=a.spo2)
    d.run_dispense_cycle()
    print("\n".join(d.log))
