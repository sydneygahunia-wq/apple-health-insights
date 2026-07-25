"""Chart rendering.

Isolated from analysis on purpose: this is the only module that imports
matplotlib, so everything else stays testable in a headless environment without
pulling in a plotting stack. The Agg backend is selected explicitly because
these charts are written to files, never shown in a window.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")  # must precede pyplot import; no display in CI or a server

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from healthinsights.analysis import DailyPoint, rolling_average
from healthinsights.records import RecordType

# A calm palette, borrowed from my SVT Monitor app so the two projects look
# like they were made by the same person.
INK = "#2b3240"
INK_SOFT = "#5a6375"
BLUE = "#4e86be"
BLUE_LIGHT = "#79aede"
PEACH = "#c9775a"
GRID = "#d9dfe8"


def _style(ax, title: str, ylabel: str) -> None:
    ax.set_title(title, fontsize=13, fontweight="bold", color=INK, pad=12)
    ax.set_ylabel(ylabel, fontsize=10, color=INK_SOFT)
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_SOFT, labelsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))


def daily_trend_chart(
    points: Sequence[DailyPoint],
    record_type: RecordType,
    out_path: str,
    window: int = 7,
) -> Optional[str]:
    """Daily values with a rolling average over the top.

    The raw series is drawn faint and the smoothed line solid, because on
    day-to-day wrist data the noise is genuinely larger than the trend and
    plotting only the raw values hides the thing you care about.
    """
    if not points:
        return None

    days = [p.day for p in points]
    values = [p.value for p in points]

    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=140)
    ax.plot(days, values, color=BLUE_LIGHT, linewidth=1, alpha=0.55, label="Daily")

    if len(points) >= window:
        smoothed = rolling_average(points, window)
        ax.plot(
            [p.day for p in smoothed],
            [p.value for p in smoothed],
            color=BLUE, linewidth=2.4, label=f"{window}-day average",
        )

    _style(ax, f"{record_type.label} over time", record_type.label)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK_SOFT)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)
    return out_path


def distribution_chart(
    values: Sequence[float],
    record_type: RecordType,
    out_path: str,
    highlight: Optional[float] = None,
) -> Optional[str]:
    """Histogram of every reading, with an optional threshold marker."""
    if not values:
        return None

    fig, ax = plt.subplots(figsize=(10, 3.6), dpi=140)
    ax.hist(values, bins=48, color=BLUE_LIGHT, edgecolor="white", linewidth=0.6)
    if highlight is not None:
        ax.axvline(highlight, color=PEACH, linestyle="--", linewidth=1.6)
        ax.text(
            highlight, ax.get_ylim()[1] * 0.92, f"  {highlight:g}",
            color=PEACH, fontsize=9, fontweight="bold",
        )

    _style(ax, f"Distribution of {record_type.label.lower()} readings", "Readings")
    ax.set_xlabel(record_type.label, fontsize=10, color=INK_SOFT)
    ax.xaxis.set_major_formatter(plt.ScalarFormatter())
    fig.tight_layout()
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)
    return out_path


def scatter_chart(
    pairs: Sequence[Tuple[object, float, float]],
    x_label: str,
    y_label: str,
    out_path: str,
    title: str = "",
) -> Optional[str]:
    """Scatter of two aligned daily series, with a fitted line.

    The fit line is drawn only when there are enough points for it to mean
    anything; below that it is visual noise that implies confidence the data
    does not support.
    """
    if len(pairs) < 3:
        return None

    xs = [p[1] for p in pairs]
    ys = [p[2] for p in pairs]

    fig, ax = plt.subplots(figsize=(6.4, 4.6), dpi=140)
    ax.scatter(xs, ys, color=BLUE, alpha=0.65, s=34, edgecolor="white", linewidth=0.6)

    if len(pairs) >= 10:
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator:
            slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
            intercept = mean_y - slope * mean_x
            line_x = [min(xs), max(xs)]
            ax.plot(line_x, [slope * x + intercept for x in line_x],
                    color=PEACH, linewidth=2, alpha=0.9)

    ax.set_title(title or f"{x_label} vs {y_label}", fontsize=13,
                 fontweight="bold", color=INK, pad=12)
    ax.set_xlabel(x_label, fontsize=10, color=INK_SOFT)
    ax.set_ylabel(y_label, fontsize=10, color=INK_SOFT)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=INK_SOFT, labelsize=9)
    fig.tight_layout()
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)
    return out_path


def hourly_profile_chart(
    hourly_means: List[float],
    out_path: str,
    title: str = "Average heart rate by hour of day",
) -> Optional[str]:
    """Bar chart of a 24-slot circadian profile."""
    if not hourly_means or len(hourly_means) != 24:
        return None

    fig, ax = plt.subplots(figsize=(10, 3.6), dpi=140)
    colors = [BLUE if 7 <= h <= 22 else BLUE_LIGHT for h in range(24)]
    ax.bar(range(24), hourly_means, color=colors, width=0.78)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels([f"{h:02d}" for h in range(0, 24, 2)])
    ax.set_title(title, fontsize=13, fontweight="bold", color=INK, pad=12)
    ax.set_xlabel("Hour", fontsize=10, color=INK_SOFT)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=INK_SOFT, labelsize=9)

    lo = min(hourly_means)
    hi = max(hourly_means)
    ax.set_ylim(max(0, lo - (hi - lo) * 0.4), hi + (hi - lo) * 0.15)
    fig.tight_layout()
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)
    return out_path
