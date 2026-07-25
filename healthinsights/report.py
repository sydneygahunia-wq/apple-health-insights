"""Report assembly.

Ties parsing, analysis and charts together into one Markdown document. This is
the only module that knows about the *narrative* — which findings are worth
stating, in what order, and with what caveats.
"""

from __future__ import annotations

import os
import statistics
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from healthinsights import analysis, charts
from healthinsights.parser import ParseStats
from healthinsights.records import HealthRecord, RecordType


def hourly_profile(records: Sequence[HealthRecord]) -> List[float]:
    """Mean value for each hour of the day, 0–23.

    Hours with no readings inherit the overall mean rather than zero, so a
    missing hour flattens the chart instead of punching a hole in it.
    """
    buckets: Dict[int, List[float]] = {}
    for record in records:
        buckets.setdefault(record.start.hour, []).append(record.value)
    if not buckets:
        return []
    overall = statistics.fmean(r.value for r in records)
    return [
        statistics.fmean(buckets[hour]) if hour in buckets else overall
        for hour in range(24)
    ]


def build_report(
    records: Sequence[HealthRecord],
    stats: ParseStats,
    out_dir: str,
    hr_threshold: float = 150.0,
    chart_prefix: str = "charts",
) -> str:
    """Write charts and a Markdown report into ``out_dir``; return the .md path."""
    chart_dir = os.path.join(out_dir, chart_prefix)
    os.makedirs(chart_dir, exist_ok=True)

    grouped = analysis.group_by_type(records)
    lines: List[str] = []
    figures: List[str] = []

    lines.append("# Apple Health report")
    lines.append("")
    lines.append(f"Generated {datetime.now():%Y-%m-%d %H:%M} by "
                 "[apple-health-insights](https://github.com/sydneygahunia-wq/apple-health-insights).")
    lines.append("")
    lines.append("> This is a descriptive summary of exported sensor data. "
                 "It is not a medical assessment, and nothing here diagnoses anything. "
                 "Associations between metrics are just that — associations.")
    lines.append("")

    # --- What was read -----------------------------------------------------
    lines.append("## What was read")
    lines.append("")
    lines.append("```")
    lines.append(stats.summary())
    lines.append("```")
    lines.append("")

    # --- Per-metric summary table -----------------------------------------
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Readings | Mean | Median | 5th–95th | Days |")
    lines.append("| --- | ---: | ---: | ---: | :---: | ---: |")
    for record_type in sorted(grouped, key=lambda t: t.label):
        summary = analysis.summarize(grouped[record_type])
        if summary is None:
            continue
        lines.append(
            f"| {summary.type.label} | {summary.count:,} | {summary.mean:.1f} | "
            f"{summary.median:.1f} | {summary.p05:.1f}–{summary.p95:.1f} | "
            f"{summary.days_covered} |"
        )
    lines.append("")

    # --- Resting heart rate trend -----------------------------------------
    resting = grouped.get(RecordType.RESTING_HEART_RATE)
    if resting:
        points = analysis.daily_series(resting)
        slope = analysis.trend_slope(points)
        path = charts.daily_trend_chart(
            points, RecordType.RESTING_HEART_RATE,
            os.path.join(chart_dir, "resting_heart_rate.png"),
        )
        lines.append("## Resting heart rate")
        lines.append("")
        if slope is not None:
            monthly = slope * 30
            direction = "rising" if monthly > 0 else "falling"
            lines.append(
                f"Trend: **{direction} {abs(monthly):.1f} BPM per month** "
                f"({slope:+.3f} BPM/day, least-squares fit over {len(points)} days)."
            )
            if abs(monthly) < 0.5:
                lines.append("")
                lines.append("That is small enough to be indistinguishable from noise.")
        gaps = analysis.coverage_gaps(points)
        if gaps:
            lines.append("")
            total_missing = sum(g[2] for g in gaps)
            lines.append(
                f"Coverage: {len(gaps)} gap(s) totalling {total_missing} days with no "
                "readings — averages above exclude those days rather than treating them as zero."
            )
        if path:
            figures.append(path)
            lines.append("")
            lines.append(f"![Resting heart rate]({chart_prefix}/resting_heart_rate.png)")
        lines.append("")

    # --- Heart rate: distribution, circadian profile, episodes -------------
    heart_rate = grouped.get(RecordType.HEART_RATE)
    if heart_rate:
        lines.append("## Heart rate")
        lines.append("")

        dist = charts.distribution_chart(
            [r.value for r in heart_rate], RecordType.HEART_RATE,
            os.path.join(chart_dir, "heart_rate_distribution.png"),
            highlight=hr_threshold,
        )
        if dist:
            figures.append(dist)
            lines.append(f"![Heart rate distribution]({chart_prefix}/heart_rate_distribution.png)")
            lines.append("")

        profile = hourly_profile(heart_rate)
        prof_path = charts.hourly_profile_chart(
            profile, os.path.join(chart_dir, "hourly_profile.png")
        )
        if prof_path:
            figures.append(prof_path)
            quietest = min(range(24), key=lambda h: profile[h])
            busiest = max(range(24), key=lambda h: profile[h])
            lines.append(
                f"Quietest hour is **{quietest:02d}:00** ({profile[quietest]:.0f} BPM); "
                f"busiest is **{busiest:02d}:00** ({profile[busiest]:.0f} BPM)."
            )
            lines.append("")
            lines.append(f"![Hourly profile]({chart_prefix}/hourly_profile.png)")
            lines.append("")

        episodes = analysis.find_episodes(heart_rate, threshold=hr_threshold, min_samples=3)
        lines.append(f"### Sustained readings at or above {hr_threshold:g} BPM")
        lines.append("")
        if not episodes:
            lines.append("None found.")
        else:
            lines.append(
                f"Found **{len(episodes)}** run(s) of at least 3 consecutive readings. "
                "Isolated spikes are excluded, since a single high reading from a loose "
                "strap is far more common than a real one."
            )
            lines.append("")
            lines.append("| Start | Duration | Peak | Mean | Readings |")
            lines.append("| --- | ---: | ---: | ---: | ---: |")
            for episode in episodes[:15]:
                lines.append(
                    f"| {episode.start:%Y-%m-%d %H:%M} | {episode.duration_minutes:.0f} min | "
                    f"{episode.peak:.0f} | {episode.mean:.0f} | {episode.sample_count} |"
                )
            if len(episodes) > 15:
                lines.append("")
                lines.append(f"_({len(episodes) - 15} more not shown.)_")
        lines.append("")

    # --- Sleep vs next-day resting heart rate ------------------------------
    sleep = grouped.get(RecordType.SLEEP)
    if sleep and resting:
        sleep_daily = analysis.daily_series(sleep)
        resting_daily = analysis.daily_series(resting)
        pairs = analysis.align_by_day(sleep_daily, resting_daily, lag_days=1)
        r = analysis.correlation(pairs)

        lines.append("## Sleep and the following morning")
        lines.append("")
        lines.append(
            "Each night's sleep duration paired with the **next** morning's resting "
            "heart rate: " + analysis.describe_correlation(r, len(pairs)) + "."
        )
        lines.append("")
        lines.append(
            "_Association only. Plenty of things move both numbers at once — illness, "
            "alcohol, stress, a hard workout the day before._"
        )
        scatter = charts.scatter_chart(
            pairs, "Sleep (hours)", "Next-day resting HR (BPM)",
            os.path.join(chart_dir, "sleep_vs_resting.png"),
            title="Sleep vs next-day resting heart rate",
        )
        if scatter:
            figures.append(scatter)
            lines.append("")
            lines.append(f"![Sleep vs resting heart rate]({chart_prefix}/sleep_vs_resting.png)")
        lines.append("")

    # --- Steps -------------------------------------------------------------
    steps = grouped.get(RecordType.STEPS)
    if steps:
        points = analysis.daily_series(steps)
        path = charts.daily_trend_chart(
            points, RecordType.STEPS, os.path.join(chart_dir, "steps.png")
        )
        weekday = [p.value for p in points if p.day.weekday() < 5]
        weekend = [p.value for p in points if p.day.weekday() >= 5]
        lines.append("## Steps")
        lines.append("")
        if weekday and weekend:
            lines.append(
                f"Weekdays average **{statistics.fmean(weekday):,.0f}** steps; "
                f"weekends **{statistics.fmean(weekend):,.0f}**."
            )
        if path:
            figures.append(path)
            lines.append("")
            lines.append(f"![Steps]({chart_prefix}/steps.png)")
        lines.append("")

    report_path = os.path.join(out_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return report_path
