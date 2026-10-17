"""Dose lifecycle state machine.

Every dose moves only along the transitions listed here. Any other move is
rejected, which is what prevents duplicate dispense commands: once a dose
has left an "awaiting dispense" state, DISPENSE_SENT can never be entered
again for it.
"""
from __future__ import annotations

from enum import Enum


class DoseState(str, Enum):
    SCHEDULED = "SCHEDULED"
    DUE = "DUE"                      # reminder active, awaiting vitals
    RETAKE = "RETAKE"                # last reading unusable, awaiting another
    HELD = "HELD"                    # awaiting human review
    DISPENSE_SENT = "DISPENSE_SENT"  # command issued to device (exactly once)
    EJECTED = "EJECTED"              # device reports servo cycle + IR result
    IN_TRANSIT = "IN_TRANSIT"        # conveyor moving
    DELIVERED = "DELIVERED"          # dual-sensor confirmed, awaiting pickup
    # terminal
    COLLECTED = "COLLECTED"
    MISSED = "MISSED"
    WITHHELD = "WITHHELD"            # human reviewer decided not to dispense
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # sensor disagreement; never auto-resolved
    FAULT = "FAULT"


TERMINAL = {DoseState.COLLECTED, DoseState.MISSED, DoseState.WITHHELD,
            DoseState.REVIEW_REQUIRED, DoseState.FAULT}

AWAITING_VITALS = {DoseState.DUE, DoseState.RETAKE}

S = DoseState
TRANSITIONS = {
    S.SCHEDULED: {S.DUE},
    S.DUE: {S.RETAKE, S.HELD, S.DISPENSE_SENT, S.MISSED, S.FAULT},      # FAULT: empty compartment
    S.RETAKE: {S.RETAKE, S.HELD, S.DISPENSE_SENT, S.MISSED, S.FAULT},
    S.HELD: {S.DISPENSE_SENT, S.WITHHELD, S.MISSED, S.FAULT},
    S.DISPENSE_SENT: {S.EJECTED, S.FAULT},
    S.EJECTED: {S.IN_TRANSIT, S.FAULT},
    S.IN_TRANSIT: {S.DELIVERED, S.REVIEW_REQUIRED, S.FAULT},
    S.DELIVERED: {S.COLLECTED, S.MISSED, S.REVIEW_REQUIRED},  # REVIEW: tray emptied without presence
}


class IllegalTransition(Exception):
    pass


def check_transition(current: DoseState, target: DoseState) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise IllegalTransition(f"{current.value} -> {target.value} not allowed")
