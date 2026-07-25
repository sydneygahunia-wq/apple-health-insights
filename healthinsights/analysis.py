"""Analysis over parsed health records.

Everything here is a pure function: records in, numbers out. No file reading, no
plotting, no printing. That is what makes this module the one with real test
coverage — you can hand it a hand-built list of records and assert on the exact
answer, which you cannot do with anything that touches matplotlib.

A note on what this module deliberately does *not* do: it never claims a cause.
It will tell you that short-sleep nights are followed by higher resting heart
rates, and it will tell you how strong that association is and how many nights
it is based on. It will not tell you that poor sleep raises your heart rate.
With observational data from a wrist sensor, association is the honest ceiling.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from healthinsights.records import HealthRecord, RecordType


@dataclass(frozen=True)
class Summary:
    """Descriptive statistics for one record type."""

    type: RecordType
    count: int
    mean: float
    median: float
    minimum: float
    maximum: float
    p05: float
    p95: float
    first_day: Optional[date]
    last_day: Optional[date]
    unit: str = ""

    @property
    def days_covered(self) -> int:
        if self.first_day is None or self.last_day is None:
            return 0
        return (self.last_day - self.first_day).days + 1


@dataclass(frozen=True)
class DailyPoint:
    """One day's value for one metric."""

    day: date
    value: float
    sample_count: int


@dataclass(frozen=True)
class Episode:
    """A stretch of consecutive readings above a threshold.

    Included because a raw maximum is nearly useless on wrist data — a single
    spurious 180 BPM reading from a loose watch strap will dominate it. A run of
    sustained high readings is a far better signal, which is the same reasoning
    behind the detector in my SVT Monitor app.
    """

    start: "object"
    end: "object"
    peak: float
    mean: float
    sample_count: int

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile.

    Written out rather than pulled from numpy so the analysis layer has no
    third-party dependency and the arithmetic is inspectable.
    """
    if not values:
        raise ValueError("percentile() requires at least one value")
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    rank = (len(ordered) - 1) * max(0.0, min(1.0, pct))
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def summarize(records: Sequence[HealthRecord]) -> Optional[Summary]:
    """Descriptive statistics for a single-type collection of records."""
    if not records:
        return None
    values = [r.value for r in records]
    days = [r.day for r in records]
    return Summary(
        type=records[0].type,
        count=len(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
        minimum=min(values),
        maximum=max(values),
        p05=percentile(values, 0.05),
        p95=percentile(values, 0.95),
        first_day=min(days),
        last_day=max(days),
        unit=records[0].unit,
    )


def group_by_type(
    records: Iterable[HealthRecord],
) -> Dict[RecordType, List[HealthRecord]]:
    """Bucket a mixed stream of records by their type."""
    grouped: Dict[RecordType, List[HealthRecord]] = {}
    for record in records:
        grouped.setdefault(record.type, []).append(record)
    return grouped


def daily_series(records: Sequence[HealthRecord]) -> List[DailyPoint]:
    """Collapse records into one value per day, oldest first.

    Cumulative metrics (steps, energy) are summed; everything else is averaged.
    Getting that distinction wrong is the single easiest way to produce a chart
    that is confidently, spectacularly wrong.
    """
    if not records:
        return []

    cumulative = records[0].type.is_cumulative
    buckets: Dict[date, List[float]] = {}
    for record in records:
        buckets.setdefault(record.day, []).append(record.value)

    points = []
    for day in sorted(buckets):
        values = buckets[day]
        value = sum(values) if cumulative else statistics.fmean(values)
        points.append(DailyPoint(day=day, value=value, sample_count=len(values)))
    return points


def rolling_average(points: Sequence[DailyPoint], window: int = 7) -> List[DailyPoint]:
    """Trailing rolling mean, used to make noisy daily series legible.

    Days with no data are simply absent rather than zero-filled — averaging in a
    zero for a day the watch was on the charger would invent a dip that never
    happened.
    """
    if window < 1:
        raise ValueError("window must be at least 1")
    out = []
    for i, point in enumerate(points):
        window_slice = points[max(0, i - window + 1) : i + 1]
        out.append(
            DailyPoint(
                day=point.day,
                value=statistics.fmean(p.value for p in window_slice),
                sample_count=sum(p.sample_count for p in window_slice),
            )
        )
    return out


def trend_slope(points: Sequence[DailyPoint]) -> Optional[float]:
    """Least-squares slope in units per day, or ``None`` if under two points.

    Ordinary linear regression, computed directly. A positive slope on resting
    heart rate over months is one of the few wrist-data signals worth actually
    paying attention to.
    """
    if len(points) < 2:
        return None
    xs = [(p.day - points[0].day).days for p in points]
    ys = [p.value for p in points]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return numerator / denominator


def find_episodes(
    records: Sequence[HealthRecord],
    threshold: float,
    min_samples: int = 3,
    max_gap_seconds: float = 300,
) -> List[Episode]:
    """Find sustained runs of readings at or above ``threshold``.

    A run ends when a reading falls below the threshold *or* when more than
    ``max_gap_seconds`` passes between readings — otherwise a high reading on
    Monday and another on Thursday would merge into one three-day "episode".

    Records are sorted first, because export order is not guaranteed when
    several devices have written to HealthKit.
    """
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1")

    ordered = sorted(records, key=lambda r: r.start)
    episodes: List[Episode] = []
    current: List[HealthRecord] = []

    def flush() -> None:
        if len(current) >= min_samples:
            values = [r.value for r in current]
            episodes.append(
                Episode(
                    start=current[0].start,
                    end=current[-1].end,
                    peak=max(values),
                    mean=statistics.fmean(values),
                    sample_count=len(values),
                )
            )
        current.clear()

    for record in ordered:
        if record.value < threshold:
            flush()
            continue
        if current:
            gap = (record.start - current[-1].end).total_seconds()
            if gap > max_gap_seconds:
                flush()
        current.append(record)
    flush()
    return episodes


def align_by_day(
    left: Sequence[DailyPoint],
    right: Sequence[DailyPoint],
    lag_days: int = 0,
) -> List[Tuple[date, float, float]]:
    """Pair up two daily series on shared dates, optionally lagging the second.

    ``lag_days=1`` pairs each day on the left with the *following* day on the
    right — the shape you need to ask "does last night's sleep line up with
    today's resting heart rate?"
    """
    right_by_day = {p.day: p.value for p in right}
    paired = []
    for point in left:
        target = point.day + timedelta(days=lag_days)
        if target in right_by_day:
            paired.append((point.day, point.value, right_by_day[target]))
    return paired


def correlation(pairs: Sequence[Tuple[date, float, float]]) -> Optional[float]:
    """Pearson correlation for aligned pairs, or ``None`` if undefined.

    Requires at least three pairs. Two points always correlate perfectly, which
    is meaningless, so returning a confident 1.0 there would be misleading.
    """
    if len(pairs) < 3:
        return None
    xs = [p[1] for p in pairs]
    ys = [p[2] for p in pairs]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = (
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    ) ** 0.5
    if denominator == 0:
        return None
    return numerator / denominator


def describe_correlation(r: Optional[float], n: int) -> str:
    """Put a correlation into words, with the caveats attached.

    The sample size is always stated, and causal language is never used. An
    r of 0.6 over eight days is noise wearing a suit.
    """
    if r is None:
        return "not enough overlapping days to compare"
    strength = (
        "strong" if abs(r) >= 0.7
        else "moderate" if abs(r) >= 0.4
        else "weak" if abs(r) >= 0.2
        else "no meaningful"
    )
    direction = "positive" if r > 0 else "negative"
    confidence = "" if n >= 30 else f" — only {n} days, so treat this as a hint, not a finding"
    if strength == "no meaningful":
        return f"no meaningful association (r = {r:+.2f}, n = {n})"
    return f"{strength} {direction} association (r = {r:+.2f}, n = {n}){confidence}"


def coverage_gaps(points: Sequence[DailyPoint], min_gap_days: int = 2) -> List[Tuple[date, date, int]]:
    """Find stretches with no data at all.

    Reporting a "90-day average" that is really 31 days of wear and 59 days of
    the watch in a drawer is misleading, so gaps get surfaced explicitly.
    """
    gaps = []
    for previous, current in zip(points, points[1:]):
        missing = (current.day - previous.day).days - 1
        if missing >= min_gap_days:
            gaps.append((previous.day, current.day, missing))
    return gaps
