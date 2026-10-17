"""Compliance prediction (IDF-B §7.5, Claim 4): pure-Python LSTM inference.

Input: this patient's past sessions (oldest -> newest). Each session gives
pickup latency, missed flag, gate-failure flag; plus the next session's hour
and weekend flag. Output: predicted pickup latency for the next session, and
the reminder plan derived from it (IDF-B outputs: reminder lead time, alert
volume, escalation window).

Weights come from tools/lstm_train.py, trained on SYNTHETIC data only. The
prediction changes reminder timing and loudness; it never affects the health
gate, dispensing, or whether a dose is counted.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

_W = json.loads((Path(__file__).with_name("lstm_weights.json")).read_text())
CAP_MIN = _W["cap_minutes"]
MIN_HISTORY = 3


def _sig(x):
    return 1 / (1 + math.exp(-max(-60.0, min(60.0, x))))


def lstm_predict(seq: list) -> float:
    """seq: list of feature vectors (FEATURES order). Returns predicted latency in minutes."""
    H, W, b, Wy, by = _W["hidden"], _W["W"], _W["b"], _W["Wy"], _W["by"]
    h, c = [0.0] * H, [0.0] * H
    y = by
    for x in seq:
        z = list(x) + h
        a = [sum(wij * zj for wij, zj in zip(W[k], z)) + b[k] for k in range(4 * H)]
        i = [_sig(v) for v in a[:H]]
        f = [_sig(v) for v in a[H:2 * H]]
        o = [_sig(v) for v in a[2 * H:3 * H]]
        g = [math.tanh(v) for v in a[3 * H:]]
        c = [f[k] * c[k] + i[k] * g[k] for k in range(H)]
        h = [o[k] * math.tanh(c[k]) for k in range(H)]
        y = sum(Wy[k] * h[k] for k in range(H)) + by
    return min(1.0, max(0.0, y)) * CAP_MIN


def features(history: list, next_hour: int, next_weekend: bool) -> list:
    """history: dicts {latency_min|None, missed, gate_fail, hour, weekend} oldest -> newest."""
    seq = []
    prev_lat = 5.0
    for t, s in enumerate(history):
        nh, nw = (history[t + 1]["hour"], history[t + 1]["weekend"]) if t + 1 < len(history) else (next_hour, next_weekend)
        if s["missed"]:
            lat = CAP_MIN
        elif s.get("latency_min") is None:      # gate suppressed the dose: no pickup to time, carry forward
            lat = prev_lat
        else:
            lat = min(CAP_MIN, s["latency_min"])
        prev_lat = lat
        window = history[max(0, t - 4): t + 1]
        rate = sum(x["gate_fail"] for x in window) / len(window)
        seq.append([lat / CAP_MIN, float(s["missed"]), float(s["gate_fail"]), rate,
                    math.sin(2 * math.pi * nh / 24), math.cos(2 * math.pi * nh / 24), float(nw)])
    return seq


@dataclass
class CompliancePlan:
    model: str                       # LSTM or DEFAULT
    predicted_latency_min: float
    reminder_lead_min: int           # start reminding this many minutes early
    alert_volume_pct: int            # IDF-B "alert volume gradient"
    escalation_window_min: float     # L1 window before L2..L4 offsets start
    gate_fail_rate: float            # last 5 sessions, feeds escalation contraction
    explanation: list = field(default_factory=list)


def plan(history: list, next_hour: int, next_weekend: bool) -> CompliancePlan:
    recent = history[-5:]
    rate = round(sum(s["gate_fail"] for s in recent) / len(recent), 2) if recent else 0.0
    if len(history) < MIN_HISTORY:
        return CompliancePlan("DEFAULT", 5.0, 0, 60, 5.0, rate,
                              [f"only {len(history)} past session(s); using defaults until {MIN_HISTORY}"])
    pred = lstm_predict(features(history[-20:], next_hour, next_weekend))
    lead = int(min(15, round(pred * 0.5)))
    vol = int(min(100, max(40, 40 + 3 * pred)))
    window = round(min(30.0, max(5.0, pred + 5)), 1)
    return CompliancePlan("LSTM", round(pred, 1), lead, vol, window, rate, [
        f"LSTM predicts pickup about {pred:.1f} min after dispense (model trained on synthetic data)",
        f"remind {lead} min early, volume {vol}%, L1 window {window} min",
        f"gate failures in last {len(recent)} sessions: {rate:.0%}"])
