import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from mcrwac.analysis.poe1_bird_motion import (
    MotionMetrics,
    DEFAULT_CLIP_AFTER_SECONDS,
    DEFAULT_CLIP_BEFORE_SECONDS,
    DEFAULT_SKIP_AFTER_SECONDS,
    analyze_video,
    default_clips_dir,
    elapsed_time_label,
    find_clean_ranges,
    overlaps_twilight_or_night,
    recording_start,
    group_motion_candidates,
    merge_candidate_intervals,
    measure_difference,
)


class Poe1BirdMotionTests(unittest.TestCase):
    def test_identical_frames_have_no_change(self) -> None:
        frame = np.full((40, 50), 100, dtype=np.uint8)

        metrics = measure_difference(frame, frame)

        self.assertEqual(metrics.mean_abs_difference, 0)
        self.assertEqual(metrics.changed_pixels, (0, 0, 0))
        self.assertEqual(metrics.largest_component, 0)

    def test_local_object_is_measured_by_area_and_component(self) -> None:
        previous = np.full((40, 50), 100, dtype=np.uint8)
        current = previous.copy()
        current[10:20, 12:24] = 180

        metrics = measure_difference(previous, current)

        self.assertGreater(metrics.mean_abs_difference, 0)
        self.assertGreater(metrics.changed_pixels[0], 0)
        self.assertGreaterEqual(
            metrics.changed_pixels[0], metrics.changed_pixels[1]
        )
        self.assertGreaterEqual(
            metrics.changed_pixels[1], metrics.changed_pixels[2]
        )
        self.assertGreater(metrics.largest_component, 50)

    def test_mask_excludes_motion_outside_polygon(self) -> None:
        previous = np.full((40, 50), 100, dtype=np.uint8)
        current = previous.copy()
        current[10:20, 35:45] = 220
        mask = np.zeros_like(previous)
        mask[5:30, 5:30] = 255

        metrics = measure_difference(previous, current, mask=mask)

        self.assertEqual(metrics.mean_abs_difference, 0)
        self.assertEqual(metrics.changed_pixels, (0, 0, 0))
        self.assertEqual(metrics.largest_component, 0)

    def test_rejects_mismatched_frames(self) -> None:
        with self.assertRaises(ValueError):
            measure_difference(
                np.zeros((10, 10), dtype=np.uint8),
                np.zeros((11, 10), dtype=np.uint8),
            )

    def test_groups_nearby_candidate_frames_and_ignores_small_changes(self) -> None:
        small = MotionMetrics(1.0, (20, 10, 2), 50)
        candidate = MotionMetrics(2.0, (300, 240, 150), 220)
        intervals = group_motion_candidates(
            [(10, small), (20, candidate), (25, candidate), (40, candidate)],
            fps=25,
            lag=3,
            min_component_px=100,
            merge_gap_seconds=0.25,
        )

        self.assertEqual(len(intervals), 2)
        self.assertEqual(intervals[0]["start_seconds"], 0.8)
        self.assertEqual(intervals[0]["end_seconds"], 1.0)
        self.assertEqual(intervals[0]["sample_count"], 2)
        self.assertEqual(intervals[1]["peak_component_px"], 220)

    def test_candidate_grouping_rejects_invalid_parameters(self) -> None:
        with self.assertRaises(ValueError):
            group_motion_candidates(
                [],
                fps=0,
                lag=1,
                min_component_px=1,
                merge_gap_seconds=0.25,
            )

    def test_merges_overlapping_lag_results_but_keeps_separate_events(self) -> None:
        intervals = [
            {
                "lag_frames": 1,
                "start_seconds": 2.0,
                "end_seconds": 2.5,
                "sample_count": 4,
                "peak_seconds": 2.2,
                "peak_component_px": 120,
            },
            {
                "lag_frames": 3,
                "start_seconds": 2.1,
                "end_seconds": 2.7,
                "sample_count": 5,
                "peak_seconds": 2.4,
                "peak_component_px": 180,
            },
            {
                "lag_frames": 1,
                "start_seconds": 5.0,
                "end_seconds": 5.2,
                "sample_count": 2,
                "peak_seconds": 5.1,
                "peak_component_px": 200,
            },
        ]

        merged = merge_candidate_intervals(intervals, merge_gap_seconds=0.25)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["start_seconds"], 2.0)
        self.assertEqual(merged[0]["end_seconds"], 2.7)
        self.assertEqual(merged[0]["peak_seconds"], 2.4)
        self.assertEqual(merged[0]["sample_count"], 9)

    def test_elapsed_time_label_is_relative_mm_ss(self) -> None:
        self.assertEqual(elapsed_time_label(424.0), "07_04")
        self.assertEqual(elapsed_time_label(3599.9), "59_59")
        self.assertEqual(elapsed_time_label(3600.0), "60_00")

    def test_find_clean_ranges_dawn_transition(self) -> None:
        ranges = find_clean_ranges(lambda t: t < 637.0, 1800.0)

        self.assertEqual(len(ranges), 1)
        self.assertAlmostEqual(ranges[0][0], 637.0, delta=2.0)
        self.assertEqual(ranges[0][1], 1800.0)

    def test_find_clean_ranges_dusk_and_all_noisy(self) -> None:
        dusk = find_clean_ranges(lambda t: t > 905.0, 1800.0)
        self.assertEqual(len(dusk), 1)
        self.assertEqual(dusk[0][0], 0.0)
        self.assertAlmostEqual(dusk[0][1], 905.0, delta=2.0)
        self.assertEqual(find_clean_ranges(lambda t: True, 1800.0), [])
        self.assertEqual(
            find_clean_ranges(lambda t: False, 1800.0), [(0.0, 1800.0)]
        )

    def test_recording_start_parses_name_and_offset(self) -> None:
        self.assertEqual(
            recording_start(Path("/x/2026-10-27/05-22-27.mkv")),
            datetime(2026, 10, 27, 5, 22, 27),
        )
        self.assertEqual(
            recording_start(Path("/x/2026-10-27/13-22-28_25_25.mkv")),
            datetime(2026, 10, 27, 13, 47, 53),
        )
        self.assertIsNone(recording_start(Path("/x/other/05-22-27.mkv")))

    def test_daylight_only_recording_skips_noise_scan(self) -> None:
        sunrise = datetime(2026, 10, 27, 6, 25)
        sunset = datetime(2026, 10, 27, 16, 40)

        self.assertTrue(
            overlaps_twilight_or_night(
                datetime(2026, 10, 27, 5, 22), 1800, sunrise, sunset
            )
        )
        self.assertFalse(
            overlaps_twilight_or_night(
                datetime(2026, 10, 27, 11, 22), 1800, sunrise, sunset
            )
        )
        self.assertTrue(
            overlaps_twilight_or_night(
                datetime(2026, 10, 27, 15, 52), 1800, sunrise, sunset
            )
        )

    def test_default_clips_dir_is_separate_sikorki_folder(self) -> None:
        self.assertEqual(
            default_clips_dir(
                Path("/rec/POE1/2026-10-28/05-24-15.mkv"), Path("/tmp/x.csv")
            ),
            Path("/opt/cameras/recordings/analysis/2026-10-28/sikorki"),
        )

    def test_events_inside_skip_window_make_one_clip(self) -> None:
        fps, seconds = 10, 60
        polygon = [(20, 20), (100, 20), (100, 80), (20, 80)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "2026-10-28" / "05-00-00.mkv"
            video.parent.mkdir()
            avi = root / "src.avi"
            writer = cv2.VideoWriter(
                str(avi), cv2.VideoWriter_fourcc(*"MJPG"), fps, (160, 100)
            )
            for index in range(fps * seconds):
                frame = np.full((100, 160, 3), 60, dtype=np.uint8)
                if index // fps in (10, 12, 40):
                    frame[30:70, 30:90] = 220
                writer.write(frame)
            writer.release()
            video.write_bytes(avi.read_bytes())
            clips = root / "clips"

            analyze_video(
                video,
                clips / "motion.csv",
                polygon,
                min_component_px=150,
                clips_dir=clips,
                noise_filter=False,
                skip_after_seconds=3.0,
            )

            names = sorted(path.name for path in clips.glob("*_candidate_*"))
            self.assertEqual(len(names), 2, names)

    def test_default_clip_is_three_seconds_with_3s_skip(self) -> None:
        self.assertEqual(DEFAULT_SKIP_AFTER_SECONDS, 3.0)
        self.assertEqual(
            DEFAULT_CLIP_BEFORE_SECONDS + DEFAULT_CLIP_AFTER_SECONDS, 3.0
        )


if __name__ == "__main__":
    unittest.main()
