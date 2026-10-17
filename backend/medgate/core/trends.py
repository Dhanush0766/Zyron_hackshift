"""Early-warning trend detection on gate readings (MedGate addition, remote monitoring).

The health gate looks at ONE reading. A patient can drift towards trouble while
every single reading still passes (e.g. SpO2 98 -> 97 -> 96 -> 95 -> 94.5). This
module looks at the last N VALID readings and flags a sustained drift so the
doctor hears about it BEFORE a dose is blocked.

A trend is flagged only when ALL of these hold (to avoid alarms on one spike):
  1. at least MIN_READINGS valid readings,
  2. mean of the newer half differs from the older half by >= the limit,
  3. every one of the last 3 readings is beyond the older-half mean in the same
     direction (a single outlier cannot trigger it).

Limits are demonstration values, not clinical guidance.
"""
from __future__ import annotations

from statistics import mean

WINDOW = 10
MIN_READINGS = 6
LIMITS = {
    "spo2": {"direction": -1, "delta": 2.0, "label": "SpO2 trending down", "unit": "%"},
    "bp_sys": {"direction": +1, "delta": 15.0, "label": "Systolic BP trending up", "unit": "mmHg"},
}


def detect(series: dict) -> list:
    """series: {"spo2": [oldest..newest], "bp_sys": [...]} of VALID readings.
    Returns a list of {field, label, older_mean, newer_mean, change, n, message}."""
    out = []
    for f, lim in LIMITS.items():
        xs = [x for x in series.get(f, []) if x is not None][-WINDOW:]
        if len(xs) < MIN_READINGS:
            continue
        half = len(xs) // 2
        old, new = mean(xs[:half]), mean(xs[half:])
        change = new - old
        sustained = all((x - old) * lim["direction"] > 0 for x in xs[-3:])
        if change * lim["direction"] >= lim["delta"] and sustained:
            out.append({"field": f, "label": lim["label"], "older_mean": round(old, 1), "newer_mean": round(new, 1),
                        "change": round(change, 1), "n": len(xs),
                        "message": f'{lim["label"]}: average {old:.1f} -> {new:.1f} {lim["unit"]} over the last '
                                   f'{len(xs)} valid readings. Doses may still pass the gate; please review.'})
    return out
