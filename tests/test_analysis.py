"""Analysis tests.

These matter more than the parser tests. A parser bug usually announces itself
with a crash; a statistics bug produces a plausible-looking wrong number that
nobody questions. So the assertions here are against values worked out by hand,
not against whatever the code happened to return the first time.
"""

import unittest
from datetime import date, datetime, timedelta

from healthinsights import analysis
from healthinsights.records import HealthRecord, RecordType


def hr(value, day=1, hour=9, minute=0, rtype=RecordType.HEART_RATE):
    moment = datetime(2026, 1, day, hour, minute)
    return HealthRecord(rtype, float(value), "count/min", moment, moment)


class PercentileTests(unittest.TestCase):
    def test_median_of_even_length(self):
        self.assertAlmostEqual(analysis.percentile([1, 2, 3, 4], 0.5), 2.5)

    def test_median_of_odd_length(self):
        self.assertAlmostEqual(analysis.percentile([1, 2, 3], 0.5), 2.0)

    def test_extremes(self):
        values = [10, 20, 30, 40]
        self.assertAlmostEqual(analysis.percentile(values, 0.0), 10)
        self.assertAlmostEqual(analysis.percentile(values, 1.0), 40)

    def test_single_value(self):
        self.assertAlmostEqual(analysis.percentile([42], 0.9), 42)

    def test_out_of_range_is_clamped(self):
        self.assertAlmostEqual(analysis.percentile([1, 2, 3], 5.0), 3)
        self.assertAlmostEqual(analysis.percentile([1, 2, 3], -5.0), 1)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            analysis.percentile([], 0.5)

    def test_order_does_not_matter(self):
        self.assertAlmostEqual(
            analysis.percentile([9, 1, 5, 3], 0.5),
            analysis.percentile([1, 3, 5, 9], 0.5),
        )


class DailySeriesTests(unittest.TestCase):
    def test_non_cumulative_metrics_are_averaged(self):
        points = analysis.daily_series([hr(60), hr(80), hr(70)])
        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0].value, 70.0)
        self.assertEqual(points[0].sample_count, 3)

    def test_cumulative_metrics_are_summed(self):
        steps = [
            HealthRecord(RecordType.STEPS, 1000, "count",
                         datetime(2026, 1, 1, h), datetime(2026, 1, 1, h))
            for h in (9, 12, 18)
        ]
        points = analysis.daily_series(steps)
        self.assertAlmostEqual(points[0].value, 3000.0,
                               msg="steps must sum; averaging understates the day")

    def test_days_come_back_in_order(self):
        points = analysis.daily_series([hr(70, day=3), hr(60, day=1), hr(65, day=2)])
        self.assertEqual([p.day for p in points],
                         [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)])

    def test_empty_input(self):
        self.assertEqual(analysis.daily_series([]), [])


class RollingAverageTests(unittest.TestCase):
    def test_trailing_window(self):
        points = analysis.daily_series([hr(10, day=1), hr(20, day=2), hr(30, day=3)])
        smoothed = analysis.rolling_average(points, window=2)
        self.assertAlmostEqual(smoothed[0].value, 10.0)   # only itself available
        self.assertAlmostEqual(smoothed[1].value, 15.0)   # (10+20)/2
        self.assertAlmostEqual(smoothed[2].value, 25.0)   # (20+30)/2

    def test_window_of_one_is_identity(self):
        points = analysis.daily_series([hr(10, day=1), hr(20, day=2)])
        smoothed = analysis.rolling_average(points, window=1)
        self.assertEqual([p.value for p in smoothed], [10.0, 20.0])

    def test_invalid_window_raises(self):
        with self.assertRaises(ValueError):
            analysis.rolling_average([], window=0)


class TrendTests(unittest.TestCase):
    def test_recovers_a_known_slope(self):
        # Exactly +2 per day, so the fit must return exactly 2.
        points = analysis.daily_series([hr(60 + 2 * d, day=d + 1) for d in range(10)])
        self.assertAlmostEqual(analysis.trend_slope(points), 2.0, places=9)

    def test_flat_series_has_zero_slope(self):
        points = analysis.daily_series([hr(60, day=d + 1) for d in range(5)])
        self.assertAlmostEqual(analysis.trend_slope(points), 0.0, places=9)

    def test_negative_slope(self):
        points = analysis.daily_series([hr(80 - d, day=d + 1) for d in range(6)])
        self.assertLess(analysis.trend_slope(points), 0)

    def test_too_few_points_returns_none(self):
        self.assertIsNone(analysis.trend_slope([]))
        self.assertIsNone(analysis.trend_slope(analysis.daily_series([hr(60)])))


class EpisodeTests(unittest.TestCase):
    def test_finds_a_sustained_run(self):
        records = [hr(160, minute=m) for m in range(4)]
        episodes = analysis.find_episodes(records, threshold=150, min_samples=3)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].sample_count, 4)
        self.assertAlmostEqual(episodes[0].peak, 160)

    def test_ignores_isolated_spikes(self):
        # One high reading surrounded by normal ones: the classic loose-strap
        # artefact, and exactly what min_samples exists to reject.
        records = [hr(70, minute=0), hr(190, minute=1), hr(72, minute=2)]
        self.assertEqual(analysis.find_episodes(records, 150, min_samples=3), [])

    def test_dropping_below_threshold_ends_the_run(self):
        records = [hr(160, minute=0), hr(160, minute=1), hr(160, minute=2),
                   hr(80, minute=3),
                   hr(155, minute=4), hr(155, minute=5), hr(155, minute=6)]
        episodes = analysis.find_episodes(records, 150, min_samples=3)
        self.assertEqual(len(episodes), 2)

    def test_a_long_gap_splits_the_run(self):
        # Same-day high readings hours apart are not one episode.
        records = [hr(160, hour=9, minute=m) for m in range(3)]
        records += [hr(160, hour=17, minute=m) for m in range(3)]
        episodes = analysis.find_episodes(records, 150, min_samples=3, max_gap_seconds=300)
        self.assertEqual(len(episodes), 2, "an 8-hour gap must not merge into one episode")

    def test_out_of_order_input_is_sorted_first(self):
        ordered = [hr(160, minute=m) for m in range(4)]
        shuffled = [ordered[2], ordered[0], ordered[3], ordered[1]]
        self.assertEqual(
            len(analysis.find_episodes(shuffled, 150, min_samples=3)),
            len(analysis.find_episodes(ordered, 150, min_samples=3)),
        )

    def test_peak_and_mean_are_correct(self):
        records = [hr(160, minute=0), hr(180, minute=1), hr(170, minute=2)]
        episode = analysis.find_episodes(records, 150, min_samples=3)[0]
        self.assertAlmostEqual(episode.peak, 180)
        self.assertAlmostEqual(episode.mean, 170)

    def test_threshold_is_inclusive(self):
        records = [hr(150, minute=m) for m in range(3)]
        self.assertEqual(len(analysis.find_episodes(records, 150, min_samples=3)), 1)

    def test_invalid_min_samples_raises(self):
        with self.assertRaises(ValueError):
            analysis.find_episodes([], 150, min_samples=0)


class CorrelationTests(unittest.TestCase):
    def _pairs(self, xs, ys):
        return [(date(2026, 1, i + 1), x, y) for i, (x, y) in enumerate(zip(xs, ys))]

    def test_perfect_positive(self):
        r = analysis.correlation(self._pairs([1, 2, 3, 4], [2, 4, 6, 8]))
        self.assertAlmostEqual(r, 1.0, places=9)

    def test_perfect_negative(self):
        r = analysis.correlation(self._pairs([1, 2, 3, 4], [8, 6, 4, 2]))
        self.assertAlmostEqual(r, -1.0, places=9)

    def test_constant_series_is_undefined_not_zero(self):
        self.assertIsNone(analysis.correlation(self._pairs([1, 1, 1], [1, 2, 3])))

    def test_too_few_pairs_returns_none(self):
        # Two points always correlate perfectly, which means nothing.
        self.assertIsNone(analysis.correlation(self._pairs([1, 2], [3, 4])))

    def test_description_flags_small_samples(self):
        self.assertIn("hint", analysis.describe_correlation(0.8, 9))
        self.assertNotIn("hint", analysis.describe_correlation(0.8, 60))

    def test_description_handles_none(self):
        self.assertIn("not enough", analysis.describe_correlation(None, 0))

    def test_weak_correlation_is_called_no_meaningful(self):
        self.assertIn("no meaningful", analysis.describe_correlation(0.05, 100))


class AlignmentTests(unittest.TestCase):
    def test_lag_pairs_each_day_with_the_next(self):
        left = analysis.daily_series([hr(1, day=1), hr(2, day=2)])
        right = analysis.daily_series([hr(10, day=2), hr(20, day=3)])
        paired = analysis.align_by_day(left, right, lag_days=1)
        self.assertEqual(len(paired), 2)
        self.assertAlmostEqual(paired[0][1], 1.0)
        self.assertAlmostEqual(paired[0][2], 10.0)

    def test_unmatched_days_are_dropped(self):
        left = analysis.daily_series([hr(1, day=1), hr(2, day=5)])
        right = analysis.daily_series([hr(10, day=1)])
        self.assertEqual(len(analysis.align_by_day(left, right)), 1)


class CoverageTests(unittest.TestCase):
    def test_finds_a_gap(self):
        points = analysis.daily_series([hr(60, day=1), hr(61, day=10)])
        gaps = analysis.coverage_gaps(points)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0][2], 8)

    def test_consecutive_days_have_no_gap(self):
        points = analysis.daily_series([hr(60, day=d) for d in range(1, 6)])
        self.assertEqual(analysis.coverage_gaps(points), [])


class SummaryTests(unittest.TestCase):
    def test_summary_values(self):
        summary = analysis.summarize([hr(60), hr(70), hr(80)])
        self.assertEqual(summary.count, 3)
        self.assertAlmostEqual(summary.mean, 70.0)
        self.assertAlmostEqual(summary.median, 70.0)
        self.assertAlmostEqual(summary.minimum, 60.0)
        self.assertAlmostEqual(summary.maximum, 80.0)

    def test_days_covered_counts_inclusively(self):
        summary = analysis.summarize([hr(60, day=1), hr(60, day=3)])
        self.assertEqual(summary.days_covered, 3)

    def test_empty_returns_none(self):
        self.assertIsNone(analysis.summarize([]))


if __name__ == "__main__":
    unittest.main()
