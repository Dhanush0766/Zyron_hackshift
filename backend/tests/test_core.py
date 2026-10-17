import csv
from pathlib import Path

import pytest

from medgate.core.compliance import CAP_MIN, features, lstm_predict, plan
from medgate.core.escalation import EscalationPolicy, level_for
from medgate.core.fsm import DoseState as S, IllegalTransition, check_transition
from medgate.core.gate import GateDecision, health_gate
from medgate.core.verification import (CollectionVerdict, DeliveryVerdict, verify_collection,
                                       verify_delivery)
from medgate.core.vitals import ReadingStatus, VitalsReading, validate_reading

NOW = 1_800_000_000.0
VECTORS = Path(__file__).resolve().parents[2] / "shared" / "gate_vectors.csv"


def _f(x):
    return None if x == "" else float(x)


def load_vectors():
    with open(VECTORS) as fh:
        return [row for row in csv.DictReader(fh) if not row["name"].startswith("#")]


@pytest.mark.parametrize("row", load_vectors(), ids=lambda r: r["name"])
def test_gate_vectors_shared_with_firmware(row):
    """Same CSV is executed by firmware/test/native: Python and C++ must agree."""
    age = _f(row["age_s"])
    r = None if row["hr"] == "NONE" else VitalsReading(
        hr=_f(row["hr"]), spo2=_f(row["spo2"]), bp_sys=_f(row["bp_sys"]), bp_dia=_f(row["bp_dia"]),
        quality=_f(row["quality"]), measured_at=None if age is None else NOW - age)
    g = health_gate(r, NOW, attempts_so_far=int(row["attempts"]), tighten_steps=int(row["tighten"]))
    assert g.decision.value == row["expected_decision"], g.reasons
    assert g.reading_status.value == row["expected_status"], g.reasons


def test_missing_is_not_normal():
    assert validate_reading(None, NOW).status == ReadingStatus.MISSING
    assert validate_reading(VitalsReading(hr=70, quality=1, measured_at=NOW), NOW).status == ReadingStatus.MISSING
    # IDF-B AND gate: SpO2 alone is not enough
    assert validate_reading(VitalsReading(spo2=98, quality=1, measured_at=NOW), NOW).status == ReadingStatus.MISSING


def test_bp_dia_above_sys_is_invalid():
    r = VitalsReading(hr=70, spo2=98, bp_sys=80, bp_dia=90, quality=0.9, measured_at=NOW)
    assert validate_reading(r, NOW).status == ReadingStatus.INVALID


def test_future_timestamp_invalid():
    r = VitalsReading(hr=70, spo2=98, bp_sys=120, bp_dia=80, quality=0.9, measured_at=NOW + 600)
    assert validate_reading(r, NOW).status == ReadingStatus.INVALID


def test_gate_custom_rules_override_idf_rules():
    r = VitalsReading(hr=70, spo2=95, bp_sys=120, bp_dia=80, quality=0.9, measured_at=NOW)
    assert health_gate(r, NOW).decision == GateDecision.PROCEED
    rules = [{"field": "spo2", "op": "<", "value": 96, "label": "custom"}]
    assert health_gate(r, NOW, rules=rules).decision == GateDecision.HOLD_FOR_REVIEW
    assert health_gate(r, NOW, rules=[]).decision == GateDecision.PROCEED


def test_tightening_only_ever_stricter():
    from medgate.core.gate import IDF_RULES, tightened
    for k in range(0, 6):
        t = tightened(IDF_RULES, k)
        assert t[0]["value"] >= 94 and t[1]["value"] <= 160
    assert tightened(IDF_RULES, -5)[0]["value"] == 94


@pytest.mark.parametrize("ir,delta,expected", [
    (True, 0.5, DeliveryVerdict.CONFIRMED),
    (False, 0.0, DeliveryVerdict.NOT_DISPENSED),
    (True, 0.0, DeliveryVerdict.DISAGREEMENT_IR_ONLY),
    (False, 0.52, DeliveryVerdict.DISAGREEMENT_WEIGHT_ONLY),
    (True, 1.0, DeliveryVerdict.WEIGHT_OUT_OF_RANGE),   # two tablets
    (True, None, DeliveryVerdict.DISAGREEMENT_IR_ONLY),  # load cell gave nothing
])
def test_dual_sensor_matrix(ir, delta, expected):
    assert verify_delivery(ir, delta, expected_g=0.5, tol_g=0.15) == expected


def test_collection_never_claims_ingestion():
    assert verify_collection(True, True) == CollectionVerdict.COLLECTED
    assert verify_collection(True, False) == CollectionVerdict.TRAY_EMPTIED_NO_PRESENCE
    assert verify_collection(False, True) == CollectionVerdict.NOT_COLLECTED
    assert not any("INGEST" in v.value or "SWALLOW" in v.value for v in CollectionVerdict)


def test_fsm_blocks_duplicate_dispense():
    check_transition(S.DUE, S.DISPENSE_SENT)
    for st in (S.DISPENSE_SENT, S.EJECTED, S.IN_TRANSIT, S.DELIVERED, S.COLLECTED, S.FAULT):
        with pytest.raises(IllegalTransition):
            check_transition(st, S.DISPENSE_SENT)


def test_fsm_terminal_states_are_final():
    for st in (S.COLLECTED, S.MISSED, S.WITHHELD, S.REVIEW_REQUIRED, S.FAULT):
        for tgt in S:
            with pytest.raises(IllegalTransition):
                check_transition(st, tgt)


def test_escalation_levels_window_and_contraction():
    p = EscalationPolicy(minute_s=60)          # real time: +5/+10/+15 min after the window
    w = 600                                    # 10-minute adaptive window
    at = lambda m, rate=0.0: level_for(m * 60, w, p, gate_fail_rate=rate)
    assert [at(m) for m in (0, 9, 15, 20, 25)] == [1, 1, 2, 3, 4]
    # IDF-B example: 2 of last 5 sessions failed the gate -> L3/L4 windows halved
    assert [at(m, 0.4) for m in (14, 15, 17.5, 25)] == [1, 3, 4, 4]
    assert at(15, 0.2) == 2                    # 1 of 5 failures: no contraction
    assert level_for(0, w, p, immediate_level=3) == 3





def _sess(lat, gate=0, missed=0, hour=8):
    return {"latency_min": lat, "missed": missed, "gate_fail": gate, "hour": hour, "weekend": False}


def test_lstm_matches_trainer_vectors():
    """Same CSV is checked by the C++ firmware LSTM: three implementations agree."""
    import csv
    from itertools import groupby
    rows = list(csv.DictReader(open(VECTORS.with_name("lstm_vectors.csv"))))
    feats = [k for k in rows[0] if k not in ("seq", "step", "expected_minutes")]
    for _, g in groupby(rows, key=lambda r: r["seq"]):
        g = list(g)
        assert abs(lstm_predict([[float(r[k]) for k in feats] for r in g]) - float(g[-1]["expected_minutes"])) < 1e-3


def test_compliance_plan_defaults_then_lstm():
    p = plan([], 8, False)
    assert p.model == "DEFAULT" and p.reminder_lead_min == 0
    fast = plan([_sess(2) for _ in range(10)], 8, False)
    slow = plan([_sess(22) for _ in range(10)], 8, False)
    assert fast.model == slow.model == "LSTM"
    assert slow.predicted_latency_min > fast.predicted_latency_min
    assert slow.reminder_lead_min >= fast.reminder_lead_min and slow.alert_volume_pct >= fast.alert_volume_pct
    assert 0 <= fast.predicted_latency_min <= CAP_MIN


def test_compliance_gate_failure_rate_reported():
    hist = [_sess(5, gate=1), _sess(5, gate=1)] + [_sess(5) for _ in range(3)]
    assert plan(hist, 8, False).gate_fail_rate == 0.4
    seq = features(hist, 8, False)
    assert len(seq) == 5 and seq[1][3] == 1.0   # rolling gate-failure rate feature


def test_gate_suppressed_session_carries_latency_forward():
    seq = features([_sess(12), _sess(None, gate=1)], 8, False)
    assert seq[1][0] == seq[0][0]
