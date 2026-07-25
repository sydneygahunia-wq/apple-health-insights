"""Core value types.

Apple writes health data as XML elements with an opaque ``type`` attribute such
as ``HKQuantityTypeIdentifierHeartRate``. Those strings are long, easy to
mistype, and appear in dozens of places, so they are wrapped in an enum once
here rather than being passed around as raw text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class RecordType(str, Enum):
    """The record types this toolkit understands.

    Apple emits well over a hundred identifiers. Only the ones with a sensible
    analysis story are modelled; everything else is counted and skipped rather
    than silently dropped, so the parse summary always accounts for 100% of the
    file.
    """

    HEART_RATE = "HKQuantityTypeIdentifierHeartRate"
    RESTING_HEART_RATE = "HKQuantityTypeIdentifierRestingHeartRate"
    WALKING_HEART_RATE = "HKQuantityTypeIdentifierWalkingHeartRateAverage"
    HRV = "HKQuantityTypeIdentifierHeartRateVariabilitySDNN"
    STEPS = "HKQuantityTypeIdentifierStepCount"
    ACTIVE_ENERGY = "HKQuantityTypeIdentifierActiveEnergyBurned"
    EXERCISE_TIME = "HKQuantityTypeIdentifierAppleExerciseTime"
    RESPIRATORY_RATE = "HKQuantityTypeIdentifierRespiratoryRate"
    OXYGEN_SATURATION = "HKQuantityTypeIdentifierOxygenSaturation"
    SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"

    @property
    def label(self) -> str:
        """Human-readable name, used in reports and chart titles."""
        return {
            RecordType.HEART_RATE: "Heart rate",
            RecordType.RESTING_HEART_RATE: "Resting heart rate",
            RecordType.WALKING_HEART_RATE: "Walking heart rate",
            RecordType.HRV: "Heart rate variability",
            RecordType.STEPS: "Steps",
            RecordType.ACTIVE_ENERGY: "Active energy",
            RecordType.EXERCISE_TIME: "Exercise time",
            RecordType.RESPIRATORY_RATE: "Respiratory rate",
            RecordType.OXYGEN_SATURATION: "Blood oxygen",
            RecordType.SLEEP: "Sleep",
        }[self]

    @property
    def is_cumulative(self) -> bool:
        """Whether daily values should be summed rather than averaged.

        Steps and energy accumulate across a day; heart rate does not. Getting
        this backwards produces a "resting heart rate of 40,000", which is the
        kind of bug that is obvious in a chart and invisible in a unit test that
        only checks the code runs.
        """
        return self in {
            RecordType.STEPS,
            RecordType.ACTIVE_ENERGY,
            RecordType.EXERCISE_TIME,
        }

    @classmethod
    def from_identifier(cls, identifier: str) -> Optional["RecordType"]:
        """Look up a type, returning ``None`` for identifiers we don't model."""
        try:
            return cls(identifier)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class HealthRecord:
    """A single measurement.

    ``slots=True`` matters more than it looks: a multi-year export can contain
    several million heart-rate samples, and slotted dataclasses drop the
    per-instance ``__dict__``, cutting memory roughly in half.
    """

    type: RecordType
    value: float
    unit: str
    start: datetime
    end: datetime
    source: str = ""

    @property
    def duration_seconds(self) -> float:
        """How long the measurement covers. Zero for instantaneous readings."""
        return max(0.0, (self.end - self.start).total_seconds())

    @property
    def day(self):
        """The calendar date this record is attributed to."""
        return self.start.date()
