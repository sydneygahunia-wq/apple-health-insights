"""Apple Health export analysis toolkit.

Turns the ``export.xml`` file Apple Health produces into readable statistics,
charts and a shareable report.

The package is deliberately split so that parsing (I/O bound, streaming),
analysis (pure functions over records) and rendering (matplotlib) never touch
each other. Only :mod:`healthinsights.parser` knows what XML looks like, and
only :mod:`healthinsights.charts` knows what a plot looks like — which is what
makes the analysis layer testable without either.
"""

__version__ = "1.0.0"

from healthinsights.records import HealthRecord, RecordType
from healthinsights.parser import parse_export, ParseStats

__all__ = ["HealthRecord", "RecordType", "parse_export", "ParseStats", "__version__"]
