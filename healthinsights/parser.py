"""Streaming parser for Apple Health's ``export.xml``.

The whole design of this module comes from one fact: these files are enormous.
A few years of Apple Watch wear produces an ``export.xml`` in the hundreds of
megabytes to low gigabytes, containing millions of ``<Record>`` elements —
because a watch samples heart rate every few seconds, all day, forever.

``ElementTree.parse()`` builds the entire document in memory before you can
touch it, which on a 1.5 GB export means several gigabytes of RAM and, on most
laptops, a dead process. So this module never holds the document. It uses
``iterparse`` to receive elements as they finish, converts each one to a small
frozen dataclass, and then *deletes the element and its now-useless previous
siblings* so the tree behind it never accumulates.

The result is a parser whose memory use is flat regardless of file size — it
depends on what you keep, not on how big the input is. Feeding it a 2 GB export
costs no more memory than a 2 MB one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Iterator, Optional, Set
from xml.etree import ElementTree

from healthinsights.records import HealthRecord, RecordType

# Apple writes timestamps as "2026-01-15 09:30:00 -0500" — a space before the
# UTC offset, which is not ISO 8601, so datetime.fromisoformat can't read it
# directly on older Pythons.
APPLE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S %z"


@dataclass
class ParseStats:
    """A tally of what the parser saw.

    Every element is accounted for: kept, skipped as an unmodelled type, or
    rejected as malformed. Silence about skipped data is how you end up
    confidently reporting statistics over a third of a file.
    """

    total_elements: int = 0
    records_kept: int = 0
    skipped_unknown_type: int = 0
    skipped_malformed: int = 0
    unknown_types_seen: Set[str] = field(default_factory=set)
    file_bytes: int = 0

    @property
    def accounted_for(self) -> int:
        return self.records_kept + self.skipped_unknown_type + self.skipped_malformed

    @property
    def is_balanced(self) -> bool:
        """Every element seen ended up in exactly one bucket."""
        return self.accounted_for == self.total_elements

    def summary(self) -> str:
        lines = [
            f"Elements seen:      {self.total_elements:,}",
            f"Records kept:       {self.records_kept:,}",
            f"Skipped (untracked): {self.skipped_unknown_type:,}",
            f"Skipped (malformed): {self.skipped_malformed:,}",
        ]
        if self.file_bytes:
            lines.insert(0, f"File size:          {self.file_bytes / 1_048_576:.1f} MB")
        if self.unknown_types_seen:
            shown = sorted(self.unknown_types_seen)[:5]
            more = len(self.unknown_types_seen) - len(shown)
            suffix = f" (+{more} more)" if more > 0 else ""
            lines.append("Untracked types:    " + ", ".join(shown) + suffix)
        return "\n".join(lines)


def parse_apple_date(raw: str) -> Optional[datetime]:
    """Parse Apple's timestamp format, returning ``None`` if it isn't one.

    Falls back to ISO parsing, since exports produced by third-party apps
    writing into HealthKit are not always consistent about the format.
    """
    if not raw:
        return None
    try:
        return datetime.strptime(raw, APPLE_DATE_FORMAT)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _record_from_element(element, stats: ParseStats) -> Optional[HealthRecord]:
    """Convert one ``<Record>`` element, or return ``None`` and tally why not."""
    identifier = element.get("type", "")
    record_type = RecordType.from_identifier(identifier)
    if record_type is None:
        stats.skipped_unknown_type += 1
        # Bounded: there are ~150 possible identifiers, so this set cannot grow
        # without limit even on a huge file.
        stats.unknown_types_seen.add(identifier or "<missing>")
        return None

    start = parse_apple_date(element.get("startDate", ""))
    end = parse_apple_date(element.get("endDate", "")) or start
    if start is None:
        stats.skipped_malformed += 1
        return None

    raw_value = element.get("value")
    if record_type is RecordType.SLEEP:
        # Sleep is a category, not a quantity: the "value" is a state name like
        # HKCategoryValueSleepAnalysisAsleepDeep. The useful number is how long
        # the interval lasted, so encode duration as the value and keep only
        # intervals that represent actual sleep rather than time in bed.
        if raw_value and "InBed" in raw_value:
            stats.skipped_unknown_type += 1
            stats.unknown_types_seen.add("SleepAnalysis/InBed")
            return None
        value = max(0.0, (end - start).total_seconds() / 3600.0)
        unit = "hr"
    else:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            stats.skipped_malformed += 1
            return None
        unit = element.get("unit", "")

    stats.records_kept += 1
    return HealthRecord(
        type=record_type,
        value=value,
        unit=unit,
        start=start,
        end=end,
        source=element.get("sourceName", ""),
    )


def parse_export(
    path: str,
    types: Optional[Iterable[RecordType]] = None,
    progress: Optional[Callable[[int], None]] = None,
    progress_every: int = 250_000,
) -> tuple[list[HealthRecord], ParseStats]:
    """Read an export file into records.

    Args:
        path: Path to ``export.xml``.
        types: Restrict to these record types. Filtering here rather than after
            the fact is the difference between holding 8 million records and
            80,000 — on a large export it is the whole ballgame.
        progress: Called periodically with the number of elements seen so far,
            so a CLI can show that a five-minute parse is still alive.
        progress_every: How many elements between progress callbacks.

    Returns:
        The records kept, and a :class:`ParseStats` accounting for everything.
    """
    wanted = set(types) if types else None
    stats = ParseStats()
    try:
        stats.file_bytes = os.path.getsize(path)
    except OSError:
        stats.file_bytes = 0

    records: list[HealthRecord] = []

    # iterparse yields elements as their closing tag is reached. Holding the
    # root lets us delete finished children, which is what keeps memory flat.
    context = ElementTree.iterparse(path, events=("start", "end"))
    _, root = next(context)

    for event, element in context:
        if event != "end" or element.tag != "Record":
            continue

        stats.total_elements += 1
        record = _record_from_element(element, stats)
        if record is not None and (wanted is None or record.type in wanted):
            records.append(record)
        elif record is not None:
            # Parsed fine, just not a type this run asked for. It was already
            # counted as kept, so move it to the skipped tally to keep the
            # accounting balanced.
            stats.records_kept -= 1
            stats.skipped_unknown_type += 1

        # Free the element and everything before it. Without this the tree
        # grows to the size of the file and the flat-memory property is lost.
        element.clear()
        while root and root[0] is not element:
            del root[0]
        if len(root):
            del root[0]

        if progress and stats.total_elements % progress_every == 0:
            progress(stats.total_elements)

    return records, stats


def iter_export(
    path: str,
    types: Optional[Iterable[RecordType]] = None,
) -> Iterator[HealthRecord]:
    """Yield records one at a time without ever building a list.

    Use this when a caller can process records in a single pass — it makes the
    whole pipeline constant-memory rather than just the parse.
    """
    wanted = set(types) if types else None
    stats = ParseStats()

    context = ElementTree.iterparse(path, events=("start", "end"))
    _, root = next(context)

    for event, element in context:
        if event != "end" or element.tag != "Record":
            continue
        stats.total_elements += 1
        record = _record_from_element(element, stats)
        if record is not None and (wanted is None or record.type in wanted):
            yield record
        element.clear()
        while root and root[0] is not element:
            del root[0]
        if len(root):
            del root[0]
