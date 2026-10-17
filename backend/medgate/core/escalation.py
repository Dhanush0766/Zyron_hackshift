"""Physiologically-conditioned 4-level escalation (IDF-B §6 Stage 5, §7.6, Claim 5).

  L1  0 .. window              audio prompt + OLED          (device)
  L2  window + l2_offset       repeated buzzer              (device)
  L3  window + l3_offset       SMS to caretaker/family/doctor (SIM800L)
  L4  window + l4_offset       automated voice call         (SIM800L)

`window` is the adaptive reminder window predicted by the compliance model.
Contraction: when the patient's health-gate failure rate over the last N
sessions is >= `contraction_threshold` (IDF-B example: 2 of the last 5 = 40 %),
the L3 and L4 offsets are multiplied by `contraction_factor` (0.5 = halved).

IDF-B offsets are +5 / +10 / +15 minutes. Demo scale uses 1 IDF minute = 6 s
(+30 / +60 / +90 s); set `minute_s=60` for real time.

The channel is what the system REQUESTS. SMS/voice delivery depends on the
SIM800L, a 2G network and a funded SIM.
"""
from __future__ import annotations

from dataclasses import dataclass

CHANNEL = {1: "AUDIO_PROMPT+OLED", 2: "BUZZER_REPEAT", 3: "CAREGIVER_SMS", 4: "CAREGIVER_VOICE_CALL"}


@dataclass
class EscalationPolicy:
    minute_s: float = 6.0               # demo: 1 IDF minute = 6 s
    l2_minutes: float = 5
    l3_minutes: float = 10
    l4_minutes: float = 15
    contraction_threshold: float = 0.4  # gate failure rate at/above which L3/L4 contract
    contraction_factor: float = 0.5
    lookback_sessions: int = 5

    def offsets(self, gate_fail_rate: float) -> tuple:
        f = self.contraction_factor if gate_fail_rate >= self.contraction_threshold else 1.0
        m = self.minute_s
        return (self.l2_minutes * m, self.l3_minutes * m * f, self.l4_minutes * m * f)


def level_for(elapsed_s: float, window_s: float, policy: EscalationPolicy, *,
              gate_fail_rate: float = 0.0, immediate_level: int = 0) -> int:
    """Level that should be active `elapsed_s` after the trigger.

    immediate_level: start at least at this level (e.g. 3 when the health gate
    suppressed a dose: a reminder to the patient cannot resolve that)."""
    l2, l3, l4 = policy.offsets(gate_fail_rate)
    t = elapsed_s - window_s
    lvl = 1
    if t >= l2:
        lvl = 2
    if t >= l3:
        lvl = 3
    if t >= l4:
        lvl = 4
    return max(lvl, immediate_level)


def contracted(gate_fail_rate: float, policy: EscalationPolicy) -> bool:
    return gate_fail_rate >= policy.contraction_threshold
