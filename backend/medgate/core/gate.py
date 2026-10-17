"""Composite health gate (IDF-B §4.3, §6 Stage 1, §7.2, Claim 1).

Boolean AND of two sources: SpO2 (MAX30102) AND blood pressure (UART module).
Dispensing is authorised only if BOTH readings are present, valid and inside the
configured thresholds. Any missing, invalid, stale or out-of-range value
suppresses the servo command for this event.

Outputs:
  PROCEED          - both sources valid and within thresholds
  RETAKE           - a reading is unusable; ask again (up to max_attempts)
  HOLD_FOR_REVIEW  - dispensing suppressed (threshold breached, or unusable
                     readings exhausted). Logged; caregiver/doctor notified.
                     Prototype addition: a NAMED reviewer may override.

Threshold tightening (IDF-B §7.4, Claim 6): when a previous same-session dose
was not collected, `tighten_steps` makes every rule stricter by a fixed step
per field. Tightening can only make the gate stricter, never looser.

Threshold values are configuration. The defaults are the IDF-B's example values
(SpO2 >= 94 %, systolic <= 160 mmHg), used here for demonstration only; they are
not clinical guidance. AI predictions are never an input to this function.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .vitals import ReadingStatus, ValidationResult, VitalsReading, validate_reading

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}

# A rule describes the FAILING condition. IDF-B §7.2 example thresholds.
IDF_RULES = [
    {"field": "spo2", "op": "<", "value": 94, "label": "SpO2 below threshold"},
    {"field": "bp_sys", "op": ">", "value": 160, "label": "Systolic BP above threshold"},
]
DEMO_RULES = IDF_RULES  # backwards-compatible name

# How much stricter each field's threshold gets per tightening step.
TIGHTEN_STEP = {"spo2": 1.0, "hr": 5.0, "bp_sys": 5.0, "bp_dia": 5.0}
MAX_TIGHTEN_STEPS = 3


class GateDecision(str, Enum):
    PROCEED = "PROCEED"
    RETAKE = "RETAKE"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"


@dataclass
class GateResult:
    decision: GateDecision
    reading_status: ReadingStatus
    reasons: list = field(default_factory=list)
    triggered_rules: list = field(default_factory=list)
    effective_rules: list = field(default_factory=list)


def tightened(rules: list, steps: int) -> list:
    """Return rules made stricter by `steps` (0..MAX_TIGHTEN_STEPS)."""
    k = max(0, min(int(steps), MAX_TIGHTEN_STEPS))
    out = []
    for r in rules:
        d = TIGHTEN_STEP.get(r["field"], 0.0) * k
        v = r["value"] + d if r["op"] in ("<", "<=") else r["value"] - d
        out.append({**r, "value": v, "tightened_by": d} if k else dict(r))
    return out


def evaluate_rules(r: VitalsReading, rules: list) -> list:
    hits = []
    for rule in rules:
        v = getattr(r, rule["field"], None)
        if v is None:
            continue  # required fields are guaranteed by validation; optional ones cannot fire
        if _OPS[rule["op"]](v, rule["value"]):
            hits.append(f'{rule["label"]} ({rule["field"]}={v:g} {rule["op"]} {rule["value"]:g})')
    return hits


def health_gate(r: Optional[VitalsReading], now: float, *, rules: list = None,
                attempts_so_far: int = 0, max_attempts: int = 3, tighten_steps: int = 0,
                max_age_s: float = 120.0, min_quality: float = 0.6,
                precheck_failures: Optional[list] = None,
                requires_meal: bool = False, meal_taken: Optional[bool] = None) -> GateResult:
    """`precheck_failures`: reasons the reading cannot be used for THIS patient (e.g. patient card
    missing or wrong). Treated like an unusable reading: retake, then hold after max_attempts.
    `requires_meal`: drug-specific condition. meal_taken True passes, False holds, None asks again."""
    rules = tightened(IDF_RULES if rules is None else rules, tighten_steps)
    v: ValidationResult = validate_reading(r, now, max_age_s=max_age_s, min_quality=min_quality)
    unusable = list(v.reasons) if v.status != ReadingStatus.VALID else []
    status = v.status
    if not unusable:
        # A drug-specific rule makes its field required (e.g. HR for a beta-blocker).
        unusable = [f"missing field: {f} (required by this medicine's gate)"
                    for f in sorted({x["field"] for x in rules}) if getattr(r, f, None) is None]
        if unusable:
            status = ReadingStatus.MISSING
    unusable += list(precheck_failures or [])
    if requires_meal and meal_taken is None and not unusable:
        unusable = ["meal not confirmed (this medicine must be taken with food)"]
    if unusable:
        if attempts_so_far + 1 >= max_attempts:
            return GateResult(GateDecision.HOLD_FOR_REVIEW, status,
                              unusable + [f"{max_attempts} unusable attempts: dispensing suppressed"],
                              effective_rules=rules)
        return GateResult(GateDecision.RETAKE, status, unusable, effective_rules=rules)

    hits = evaluate_rules(r, rules)
    if requires_meal and meal_taken is False:
        hits.append("Patient reports no meal: blood-sugar-lowering drug held (hypoglycaemia risk)")
    note = [f"thresholds tightened {tighten_steps} step(s) after a missed same-session pickup"] if tighten_steps else []
    if hits:
        return GateResult(GateDecision.HOLD_FOR_REVIEW, v.status, hits + note, hits, rules)
    ok = "all of this medicine's gate conditions passed (SpO2 AND BP" + (" AND meal" if requires_meal else "") + ")"
    return GateResult(GateDecision.PROCEED, v.status, [ok] + note, [], rules)
