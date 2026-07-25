"""Parser tests.

The interesting cases here are all about *bad* input, because real exports are
full of it: values that aren't numbers, timestamps in the wrong format, record
types Apple added after this code was written, and rows in no particular order.
A parser that only works on clean data is not a parser, it's a demo.
"""

import os
import tempfile
import unittest
from datetime import datetime

from healthinsights.parser import iter_export, parse_apple_date, parse_export
from healthinsights.records import RecordType

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_CA">\n'
FOOTER = "\n</HealthData>\n"


def make_export(rows):
    handle = tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8")
    handle.write(HEADER + "\n".join(rows) + FOOTER)
    handle.close()
    return handle.name


def rec(rtype="HKQuantityTypeIdentifierHeartRate", value="72",
        start="2026-01-01 09:00:00 -0500", end=None, unit="count/min", source="Apple Watch"):
    end = end or start
    return (f'<Record type="{rtype}" sourceName="{source}" unit="{unit}" '
            f'value="{value}" startDate="{start}" endDate="{end}"/>')


class DateParsingTests(unittest.TestCase):
    def test_parses_apple_format_with_offset(self):
        parsed = parse_apple_date("2026-01-15 09:30:00 -0500")
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 1, 15))
        self.assertEqual(parsed.hour, 9)

    def test_falls_back_to_iso(self):
        self.assertEqual(
            parse_apple_date("2026-01-15T09:30:00"), datetime(2026, 1, 15, 9, 30)
        )

    def test_returns_none_for_garbage_rather_than_raising(self):
        for bad in ["", "not a date", "2026-13-45 99:99:99 -0500", "1736938200"]:
            self.assertIsNone(parse_apple_date(bad), f"should reject {bad!r}")


class ParseExportTests(unittest.TestCase):
    def setUp(self):
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            os.unlink(path)

    def build(self, rows):
        path = make_export(rows)
        self.paths.append(path)
        return path

    def test_reads_a_simple_record(self):
        records, stats = parse_export(self.build([rec()]))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].type, RecordType.HEART_RATE)
        self.assertEqual(records[0].value, 72.0)
        self.assertEqual(records[0].source, "Apple Watch")
        self.assertEqual(stats.records_kept, 1)

    def test_every_element_is_accounted_for(self):
        rows = [
            rec(),
            rec(value="not-a-number"),
            rec(rtype="HKQuantityTypeIdentifierMadeUpMetric"),
            rec(start="nonsense"),
        ]
        _, stats = parse_export(self.build(rows))
        self.assertEqual(stats.total_elements, 4)
        self.assertTrue(stats.is_balanced,
                        "kept + skipped must equal total, or the summary lies")

    def test_malformed_value_is_skipped_not_fatal(self):
        records, stats = parse_export(self.build([rec(value="")]))
        self.assertEqual(records, [])
        self.assertEqual(stats.skipped_malformed, 1)

    def test_unknown_type_is_recorded_by_name(self):
        _, stats = parse_export(self.build([rec(rtype="HKQuantityTypeIdentifierFuture")]))
        self.assertEqual(stats.skipped_unknown_type, 1)
        self.assertIn("HKQuantityTypeIdentifierFuture", stats.unknown_types_seen)

    def test_type_filter_excludes_other_types(self):
        rows = [rec(), rec(rtype="HKQuantityTypeIdentifierStepCount", value="500", unit="count")]
        records, stats = parse_export(self.build(rows), types=[RecordType.STEPS])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].type, RecordType.STEPS)
        self.assertTrue(stats.is_balanced, "filtering must not break the accounting")

    def test_sleep_value_becomes_duration_in_hours(self):
        row = rec(
            rtype="HKCategoryTypeIdentifierSleepAnalysis",
            value="HKCategoryValueSleepAnalysisAsleepCore", unit="",
            start="2026-01-01 23:00:00 -0500", end="2026-01-02 06:30:00 -0500",
        )
        records, _ = parse_export(self.build([row]))
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0].value, 7.5, places=3)
        self.assertEqual(records[0].unit, "hr")

    def test_in_bed_sleep_is_skipped_to_avoid_double_counting(self):
        row = rec(
            rtype="HKCategoryTypeIdentifierSleepAnalysis",
            value="HKCategoryValueSleepAnalysisInBed", unit="",
            start="2026-01-01 22:30:00 -0500", end="2026-01-02 07:00:00 -0500",
        )
        records, stats = parse_export(self.build([row]))
        self.assertEqual(records, [])
        self.assertEqual(stats.skipped_unknown_type, 1)

    def test_missing_end_date_falls_back_to_start(self):
        row = ('<Record type="HKQuantityTypeIdentifierHeartRate" value="80" '
               'unit="count/min" startDate="2026-01-01 09:00:00 -0500"/>')
        records, _ = parse_export(self.build([row]))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].start, records[0].end)

    def test_non_record_elements_are_ignored(self):
        rows = ['<ExportDate value="2026-01-01 09:00:00 -0500"/>',
                '<Me HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexFemale"/>',
                rec()]
        records, stats = parse_export(self.build(rows))
        self.assertEqual(len(records), 1)
        self.assertEqual(stats.total_elements, 1, "only <Record> elements are counted")

    def test_empty_file_yields_nothing_without_error(self):
        records, stats = parse_export(self.build([]))
        self.assertEqual(records, [])
        self.assertEqual(stats.total_elements, 0)

    def test_iter_export_matches_parse_export(self):
        rows = [rec(value=str(60 + i), start=f"2026-01-01 09:{i:02d}:00 -0500")
                for i in range(25)]
        path = self.build(rows)
        eager, _ = parse_export(path)
        streamed = list(iter_export(path))
        self.assertEqual([r.value for r in eager], [r.value for r in streamed])

    def test_iter_export_is_lazy(self):
        # Pulling a single item must not require reading the whole file.
        rows = [rec(value=str(60 + i), start=f"2026-01-01 09:{i:02d}:00 -0500")
                for i in range(50)]
        stream = iter_export(self.build(rows))
        first = next(stream)
        self.assertEqual(first.value, 60.0)
        stream.close()


if __name__ == "__main__":
    unittest.main()
