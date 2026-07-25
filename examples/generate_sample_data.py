#!/usr/bin/env python3
"""Generate a synthetic ``export.xml`` so the toolkit can be run immediately.

Real Apple Health exports contain a person's medical history, so this repo does
not ship one. This script fabricates a statistically plausible substitute:
circadian heart-rate variation, weekday/weekend step differences, a slow upward
drift in resting heart rate, occasional tachycardia episodes, and a stretch of
missing days where the watch was "off".

It also deliberately injects the kinds of defects real exports contain —
malformed values, unknown record types, out-of-order rows — so the parser's
error handling is exercised by the example data rather than only by tests.

Usage:
    python examples/generate_sample_data.py --days 120 --out sample_export.xml
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import datetime, timedelta
from xml.sax.saxutils import quoteattr

TZ = "-0500"


def apple_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S ") + TZ


def record(rtype: str, value, unit: str, start: datetime, end: datetime, source: str) -> str:
    return (
        f'  <Record type={quoteattr(rtype)} sourceName={quoteattr(source)} '
        f'unit={quoteattr(unit)} value={quoteattr(str(value))} '
        f'startDate={quoteattr(apple_time(start))} endDate={quoteattr(apple_time(end))}/>'
    )


def generate(days: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    rows: list[str] = []
    start_day = datetime(2026, 1, 1, 0, 0, 0)

    # A stretch where the watch went unworn, so coverage-gap detection has
    # something real to find.
    gap_start = days // 3
    gap_days = set(range(gap_start, gap_start + 6))

    for day_index in range(days):
        if day_index in gap_days:
            continue

        day = start_day + timedelta(days=day_index)
        is_weekend = day.weekday() >= 5

        # Resting heart rate: a slow upward drift plus noise, so the trend
        # calculation has a real signal to recover.
        resting = 58 + day_index * 0.02 + rng.gauss(0, 1.6)
        rows.append(
            record(
                "HKQuantityTypeIdentifierRestingHeartRate",
                f"{resting:.0f}", "count/min",
                day.replace(hour=6), day.replace(hour=6), "Apple Watch",
            )
        )

        # Sleep: shorter on weeknights, and correlated with the next morning's
        # resting rate so the lagged-association feature has something to find.
        sleep_hours = (7.9 if is_weekend else 6.6) + rng.gauss(0, 0.8)
        sleep_hours = max(3.5, min(10.5, sleep_hours))
        sleep_start = day.replace(hour=23) - timedelta(days=1)
        rows.append(
            record(
                "HKCategoryTypeIdentifierSleepAnalysis",
                "HKCategoryValueSleepAnalysisAsleepCore", "",
                sleep_start, sleep_start + timedelta(hours=sleep_hours), "Apple Watch",
            )
        )
        # Time in bed, which the parser should skip rather than double-count.
        rows.append(
            record(
                "HKCategoryTypeIdentifierSleepAnalysis",
                "HKCategoryValueSleepAnalysisInBed", "",
                sleep_start - timedelta(minutes=20),
                sleep_start + timedelta(hours=sleep_hours + 0.3), "iPhone",
            )
        )

        # Steps, in hourly buckets, so daily totals must be summed not averaged.
        daily_steps = rng.gauss(9200 if not is_weekend else 6100, 2200)
        for hour in range(7, 22):
            share = max(0.0, rng.gauss(daily_steps / 15, daily_steps / 30))
            if share < 1:
                continue
            rows.append(
                record(
                    "HKQuantityTypeIdentifierStepCount",
                    f"{share:.0f}", "count",
                    day.replace(hour=hour), day.replace(hour=hour, minute=59), "iPhone",
                )
            )

        # Heart rate every 10 minutes, following a circadian curve.
        for minute_of_day in range(0, 24 * 60, 10):
            hour = minute_of_day / 60.0
            circadian = 12 * math.sin((hour - 9) / 24 * 2 * math.pi)
            bpm = resting + 14 + circadian + rng.gauss(0, 5)
            if 7 <= hour <= 21 and rng.random() < 0.05:
                bpm += rng.uniform(25, 55)  # activity burst
            moment = day + timedelta(minutes=minute_of_day)
            rows.append(
                record(
                    "HKQuantityTypeIdentifierHeartRate",
                    f"{max(42, bpm):.0f}", "count/min", moment, moment, "Apple Watch",
                )
            )

        # A sustained tachycardia episode every couple of weeks, so episode
        # detection has genuine runs to find rather than isolated spikes.
        if rng.random() < 0.07:
            onset = day.replace(hour=rng.randint(9, 20), minute=rng.choice([0, 20, 40]))
            for step in range(rng.randint(4, 9)):
                moment = onset + timedelta(minutes=step)
                rows.append(
                    record(
                        "HKQuantityTypeIdentifierHeartRate",
                        f"{rng.uniform(158, 195):.0f}", "count/min",
                        moment, moment, "Apple Watch",
                    )
                )

        # Defects real exports contain.
        if day_index % 37 == 0:
            rows.append(
                record(
                    "HKQuantityTypeIdentifierHeartRate",
                    "", "count/min", day.replace(hour=12), day.replace(hour=12), "Apple Watch",
                )
            )
        if day_index % 41 == 0:
            rows.append(
                record(
                    "HKQuantityTypeIdentifierDietaryCaffeine",
                    "95", "mg", day.replace(hour=8), day.replace(hour=8), "MyFitnessPal",
                )
            )

    # Shuffle mildly so the file is not perfectly chronological.
    rng.shuffle(rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default="sample_export.xml")
    args = parser.parse_args()

    rows = generate(args.days, args.seed)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write('<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_CA">\n')
        handle.write("\n".join(rows))
        handle.write("\n</HealthData>\n")

    print(f"Wrote {args.out}: {len(rows):,} records across {args.days} days")


if __name__ == "__main__":
    main()
