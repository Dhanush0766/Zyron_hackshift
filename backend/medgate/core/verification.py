"""Closed-loop dual-sensor verification.

Ejection is checked by two independent sensors:
  * IR break-beam at the compartment chute  (did something fall?)
  * Load-cell weight increase on the delivery tray after the conveyor
    arrives (did something arrive?)
Agreement is required before a dose is counted as delivered. Disagreement
is never resolved automatically and never triggers an automatic re-dispense
(a re-dispense could double-dose if the first tablet is actually present).

Collection is inferred from tray weight returning toward baseline plus a
proximity detection. This shows the tray was emptied. It does NOT prove the
patient swallowed the tablet.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class DeliveryVerdict(str, Enum):
    CONFIRMED = "CONFIRMED"            # both sensors agree: dispensed and arrived
    NOT_DISPENSED = "NOT_DISPENSED"    # both sensors agree: nothing came out
    DISAGREEMENT_IR_ONLY = "DISAGREEMENT_IR_ONLY"          # beam broke, no weight: jam/lost tablet?
    DISAGREEMENT_WEIGHT_ONLY = "DISAGREEMENT_WEIGHT_ONLY"  # weight, no beam: IR fault?
    WEIGHT_OUT_OF_RANGE = "WEIGHT_OUT_OF_RANGE"            # e.g. two tablets


class CollectionVerdict(str, Enum):
    COLLECTED = "COLLECTED"            # tray emptied + presence detected
    TRAY_EMPTIED_NO_PRESENCE = "TRAY_EMPTIED_NO_PRESENCE"
    NOT_COLLECTED = "NOT_COLLECTED"


def weight_matches(delta_g: Optional[float], expected_g: float, tol_g: float) -> Optional[bool]:
    """True = one unit arrived, False = nothing arrived, None = out of range."""
    if delta_g is None:
        return False
    if abs(delta_g) <= tol_g:
        return False
    if abs(delta_g - expected_g) <= tol_g:
        return True
    return None


def verify_delivery(ir_beam_broken: Optional[bool], tray_delta_g: Optional[float],
                    expected_g: float, tol_g: float) -> DeliveryVerdict:
    ir = bool(ir_beam_broken)
    w = weight_matches(tray_delta_g, expected_g, tol_g)
    if w is None:
        return DeliveryVerdict.WEIGHT_OUT_OF_RANGE
    if ir and w:
        return DeliveryVerdict.CONFIRMED
    if not ir and not w:
        return DeliveryVerdict.NOT_DISPENSED
    if ir and not w:
        return DeliveryVerdict.DISAGREEMENT_IR_ONLY
    return DeliveryVerdict.DISAGREEMENT_WEIGHT_ONLY


def verify_collection(weight_removed: bool, presence: bool) -> CollectionVerdict:
    """IDF-B §7.4 Boolean AND: only weight reduction AND hand proximity together
    count as COLLECTED. Weight-only is reported separately and is NOT compliance."""
    if weight_removed and presence:
        return CollectionVerdict.COLLECTED
    if weight_removed:
        return CollectionVerdict.TRAY_EMPTIED_NO_PRESENCE
    return CollectionVerdict.NOT_COLLECTED
