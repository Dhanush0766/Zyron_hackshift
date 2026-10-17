"""Drug-specific health gate presets (MedGate addition beyond the IDF-B).

The IDF-B gate uses one rule set for every medicine (SpO2 >= 94 AND systolic <= 160).
But whether a medicine is safe to take *right now* depends on what it does:

  * a BP-lowering tablet is risky when BP is already LOW,
  * a beta-blocker slows the heart, so it is risky when the pulse is already slow,
  * a sulfonylurea lowers blood sugar, so it is risky if the patient has not eaten.

Each medication therefore gets its own rule set, selected by drug class and
editable per medication. The values below mirror common nursing "hold
parameters" taught for these classes. They are EXAMPLE values for a
demonstration with dummy tablets, NOT clinical guidance: in real use the
prescriber sets them per patient.

A rule is the FAILING condition (same format as gate.IDF_RULES). Any field a
rule refers to becomes REQUIRED: a missing value can never pass a rule.
"""
from __future__ import annotations

from .gate import IDF_RULES

_LOW_BP = {"field": "bp_sys", "op": "<", "value": 100, "label": "Systolic too low for a BP-lowering drug (hypotension risk)"}
_LOW_HR = {"field": "hr", "op": "<", "value": 60, "label": "Pulse too slow for a heart-rate-lowering drug (bradycardia risk)"}

DRUG_CLASSES = {
    "GENERAL": {
        "label": "General (IDF-B default gate)",
        "rules": IDF_RULES,
        "requires_meal": False,
        "min_interval_h": 0,          # 0 = no double-dose guard unless the prescriber sets one
    },
    "ANTIHYPERTENSIVE": {
        "label": "BP-lowering (e.g. amlodipine-type)",
        "rules": IDF_RULES + [_LOW_BP],
        "requires_meal": False,
        "min_interval_h": 20,
    },
    "BETA_BLOCKER": {
        "label": "Beta-blocker (e.g. metoprolol-type)",
        "rules": IDF_RULES + [_LOW_HR, _LOW_BP],
        "requires_meal": False,
        "min_interval_h": 10,
    },
    "SULFONYLUREA": {
        "label": "Blood-sugar-lowering, take with food (e.g. glimepiride-type)",
        "rules": IDF_RULES,
        "requires_meal": True,
        "min_interval_h": 20,
    },
}


def preset(drug_class: str) -> dict:
    if drug_class not in DRUG_CLASSES:
        raise ValueError(f"drug_class must be one of {sorted(DRUG_CLASSES)}")
    p = DRUG_CLASSES[drug_class]
    return {"rules": [dict(r) for r in p["rules"]], "requires_meal": p["requires_meal"],
            "min_interval_h": p["min_interval_h"]}
