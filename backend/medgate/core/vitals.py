"""Vital-sign reading validation.

A reading is classified BEFORE any gate decision. Missing, implausible,
stale or low-quality readings are never treated as normal.

Plausibility limits below are sensor sanity limits (is this a physically
possible human reading from this sensor?), NOT clinical thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ReadingStatus(str, Enum):
    VALID = "VALID"
    MISSING = "MISSING"
    INVALID = "INVALID"        # physically implausible / sensor error value
    STALE = "STALE"            # older than max age
    LOW_QUALITY = "LOW_QUALITY"  # sensor reports poor contact / signal


# Sensor sanity ranges (inclusive). Outside => INVALID, not "abnormal".
PLAUSIBLE = {
    "hr": (25, 250),
    "spo2": (50, 100),
    "bp_sys": (60, 260),
    "bp_dia": (30, 160),
}

# IDF-B §4.3/§7.2: the gate is a Boolean AND of SpO2 (MAX30102) and blood pressure
# (UART BP module). Both sources are required; HR is recorded but optional.
REQUIRED_FIELDS = ("spo2", "bp_sys", "bp_dia")


@dataclass
class VitalsReading:
    hr: Optional[float] = None
    spo2: Optional[float] = None
    bp_sys: Optional[float] = None
    bp_dia: Optional[float] = None
    quality: Optional[float] = None   # 0..1 signal quality reported by device
    measured_at: Optional[float] = None  # epoch seconds (device RTC)


@dataclass
class ValidationResult:
    status: ReadingStatus
    reasons: list = field(default_factory=list)


def validate_reading(r: Optional[VitalsReading], now: float, *,
                     max_age_s: float = 120.0,
                     min_quality: float = 0.6) -> ValidationResult:
    if r is None:
        return ValidationResult(ReadingStatus.MISSING, ["no reading received"])

    missing = [f for f in REQUIRED_FIELDS if getattr(r, f) is None]
    if missing:
        return ValidationResult(ReadingStatus.MISSING,
                                [f"missing field: {f}" for f in missing])
    if r.measured_at is None:
        return ValidationResult(ReadingStatus.MISSING, ["missing timestamp"])

    invalid = []
    for f, (lo, hi) in PLAUSIBLE.items():
        v = getattr(r, f)
        if v is None:
            continue
        if not (lo <= v <= hi):
            invalid.append(f"{f}={v} outside sensor range [{lo},{hi}]")
    if r.bp_sys is not None and r.bp_dia is not None and r.bp_dia >= r.bp_sys:
        invalid.append("bp_dia >= bp_sys")
    if invalid:
        return ValidationResult(ReadingStatus.INVALID, invalid)

    age = now - r.measured_at
    if age < -30:
        return ValidationResult(ReadingStatus.INVALID,
                                [f"timestamp {-age:.0f}s in the future (clock fault)"])
    if age > max_age_s:
        return ValidationResult(ReadingStatus.STALE,
                                [f"reading is {age:.0f}s old (max {max_age_s:.0f}s)"])

    if r.quality is None or r.quality < min_quality:
        return ValidationResult(ReadingStatus.LOW_QUALITY,
                                [f"signal quality {r.quality} < {min_quality}"])

    return ValidationResult(ReadingStatus.VALID, [])


def session_of(hour: int) -> str:
    """IDF-B §7.7 sessions: morning / afternoon / night."""
    if 5 <= hour < 12:
        return "MORNING"
    if 12 <= hour < 17:
        return "AFTERNOON"
    return "NIGHT"
