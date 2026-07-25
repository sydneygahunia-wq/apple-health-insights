"""Command-line interface.

Three subcommands, deliberately narrow:

    summary   what is in the file, and the headline numbers
    episodes  sustained runs above a heart-rate threshold
    report    charts and a full Markdown write-up

Everything the CLI prints comes from :mod:`healthinsights.analysis`; this module
only handles arguments and formatting, which keeps the interesting logic in the
place that has tests.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List, Optional, Sequence

from healthinsights import __version__, analysis
from healthinsights.parser import parse_export
from healthinsights.records import RecordType

TYPE_CHOICES = {
    "heart-rate": RecordType.HEART_RATE,
    "resting-heart-rate": RecordType.RESTING_HEART_RATE,
    "hrv": RecordType.HRV,
    "steps": RecordType.STEPS,
    "sleep": RecordType.SLEEP,
    "energy": RecordType.ACTIVE_ENERGY,
    "respiratory-rate": RecordType.RESPIRATORY_RATE,
    "oxygen": RecordType.OXYGEN_SATURATION,
}


def _progress(count: int) -> None:
    print(f"  ...{count:,} elements", file=sys.stderr)


def _load(path: str, type_names: Optional[Sequence[str]], quiet: bool):
    if not os.path.exists(path):
        print(f"error: no such file: {path}", file=sys.stderr)
        raise SystemExit(2)

    types = None
    if type_names:
        try:
            types = [TYPE_CHOICES[name] for name in type_names]
        except KeyError as exc:
            print(f"error: unknown type {exc}. Choose from: "
                  f"{', '.join(sorted(TYPE_CHOICES))}", file=sys.stderr)
            raise SystemExit(2)

    started = time.time()
    records, stats = parse_export(
        path, types=types, progress=None if quiet else _progress
    )
    if not quiet:
        print(f"Parsed in {time.time() - started:.1f}s\n", file=sys.stderr)
    return records, stats


def cmd_summary(args: argparse.Namespace) -> int:
    records, stats = _load(args.export, args.type, args.quiet)
    print(stats.summary())
    print()

    if not records:
        print("No records of the requested type(s) were found.")
        return 1

    grouped = analysis.group_by_type(records)
    header = f"{'Metric':<24}{'n':>9}{'mean':>10}{'median':>10}{'p95':>10}{'days':>7}"
    print(header)
    print("-" * len(header))
    for record_type in sorted(grouped, key=lambda t: t.label):
        summary = analysis.summarize(grouped[record_type])
        if summary is None:
            continue
        print(
            f"{summary.type.label:<24}{summary.count:>9,}{summary.mean:>10.1f}"
            f"{summary.median:>10.1f}{summary.p95:>10.1f}{summary.days_covered:>7}"
        )

    resting = grouped.get(RecordType.RESTING_HEART_RATE)
    if resting:
        points = analysis.daily_series(resting)
        slope = analysis.trend_slope(points)
        if slope is not None:
            print(f"\nResting heart rate trend: {slope * 30:+.1f} BPM per month")
        gaps = analysis.coverage_gaps(points)
        if gaps:
            missing = sum(gap[2] for gap in gaps)
            print(f"Coverage gaps: {len(gaps)} ({missing} days with no readings)")
    return 0


def cmd_episodes(args: argparse.Namespace) -> int:
    records, _ = _load(args.export, ["heart-rate"], args.quiet)
    if not records:
        print("No heart-rate records found.")
        return 1

    episodes = analysis.find_episodes(
        records, threshold=args.threshold, min_samples=args.min_samples
    )
    print(f"Sustained runs at or above {args.threshold:g} BPM "
          f"({args.min_samples}+ consecutive readings): {len(episodes)}\n")
    if not episodes:
        return 0

    print(f"{'Start':<20}{'Duration':>10}{'Peak':>7}{'Mean':>7}{'n':>6}")
    print("-" * 50)
    for episode in episodes:
        print(
            f"{episode.start:%Y-%m-%d %H:%M}{'':<4}"
            f"{episode.duration_minutes:>9.0f}m{episode.peak:>7.0f}"
            f"{episode.mean:>7.0f}{episode.sample_count:>6}"
        )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from healthinsights.report import build_report  # imported late: pulls matplotlib

    records, stats = _load(args.export, args.type, args.quiet)
    if not records:
        print("No records found; nothing to report.")
        return 1

    os.makedirs(args.out, exist_ok=True)
    path = build_report(records, stats, args.out, hr_threshold=args.threshold)
    print(f"Report written to {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="health-insights",
        description="Analyse an Apple Health export.xml file.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument("export", help="Path to export.xml")
        sub.add_argument("-q", "--quiet", action="store_true",
                         help="Suppress parse progress output")

    summary = subparsers.add_parser("summary", help="Headline statistics per metric")
    add_common(summary)
    summary.add_argument("-t", "--type", action="append", choices=sorted(TYPE_CHOICES),
                         help="Limit to a metric (repeatable). Filtering during the "
                              "parse keeps memory down on large exports.")
    summary.set_defaults(func=cmd_summary)

    episodes = subparsers.add_parser("episodes", help="Sustained high heart-rate runs")
    add_common(episodes)
    episodes.add_argument("--threshold", type=float, default=150.0,
                          help="BPM threshold (default: 150)")
    episodes.add_argument("--min-samples", type=int, default=3,
                          help="Consecutive readings required (default: 3)")
    episodes.set_defaults(func=cmd_episodes)

    report = subparsers.add_parser("report", help="Charts and a Markdown report")
    add_common(report)
    report.add_argument("-o", "--out", default="health-report",
                        help="Output directory (default: health-report)")
    report.add_argument("-t", "--type", action="append", choices=sorted(TYPE_CHOICES))
    report.add_argument("--threshold", type=float, default=150.0)
    report.set_defaults(func=cmd_report)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
