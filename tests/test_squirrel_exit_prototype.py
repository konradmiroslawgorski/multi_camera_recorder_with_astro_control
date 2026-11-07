import os
import shutil
import subprocess
import unittest
from datetime import date, datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from mcrwac.analysis.squirrel_exit_prototype import (
    Appearance,
    AppearanceDetector,
    _fit_wall_line,
    _analyzed_list_path,
    _load_analyzed,
    _mark_analyzed,
    _measure_opening_brightness,
    _recording_start_time,
    _reserve_output_path,
    _save_appearance_clips,
    _timestamped_output_path,
    analyze_video,
    build_day_result,
    update_day_results,
    next_pending_recording,
    select_recordings,
    watch_recordings,
)


class SquirrelExitPrototypeTests(unittest.TestCase):
    def test_analysis_uses_only_brightness_measurements(self) -> None:
        video_path = Path("recording.mkv")
        expected_output = Path("analysis.mp4")

        with patch(
            "mcrwac.analysis.squirrel_exit_prototype._analyze_brightness_only",
            return_value=expected_output,
        ) as analyze_brightness_only:
            result = analyze_video(video_path)

        self.assertEqual(result, expected_output)
        analyze_brightness_only.assert_called_once()
        self.assertEqual(analyze_brightness_only.call_args.args[0], video_path)
        self.assertEqual(analyze_brightness_only.call_args.args[2], "all")
        self.assertEqual(analyze_brightness_only.call_args.args[3], 60.0)
        self.assertEqual(analyze_brightness_only.call_args.args[4], 20.0)

    def test_wall_line_brightness_uses_test_and_reference_rectangles(self) -> None:
        frame = np.full((1296, 2304, 3), 100, dtype=np.uint8)
        cv2.line(frame, (355, 680), (346, 825), (220, 220, 220), 2)
        cv2.line(frame, (278, 752), (340, 752), (20, 20, 20), 2)
        cv2.line(frame, (362, 756), (458, 809), (20, 20, 20), 2)

        baseline = _measure_opening_brightness(frame)
        self.assertAlmostEqual(baseline.test_mean, 100.0)
        self.assertAlmostEqual(baseline.reference_mean, 100.0)
        self.assertAlmostEqual(baseline.difference_percent, 0.0)

        dark_test = frame.copy()
        dark_test[
            baseline.test_y : baseline.test_y + 17,
            baseline.test_x : baseline.test_x + 10,
        ] = 50
        measurement = _measure_opening_brightness(dark_test)
        self.assertAlmostEqual(measurement.test_mean, 50.0)
        self.assertAlmostEqual(measurement.reference_mean, 100.0)
        self.assertAlmostEqual(measurement.difference_percent, 50.0)

        transform = np.float32([[1, 0, 20], [0, 1, 15]])
        shifted = cv2.warpAffine(frame, transform, (2304, 1296))
        translated = _measure_opening_brightness(shifted, offset_x=20, offset_y=15)
        self.assertAlmostEqual(translated.cross_x, baseline.cross_x + 20, delta=0.1)
        self.assertAlmostEqual(translated.cross_y, baseline.cross_y + 15, delta=0.1)

    def test_wall_line_fit_ignores_two_bad_candidates(self) -> None:
        points = [
            (278, 755),
            (285, 755),
            (292, 755),
            (299, 755),
            (306, 755),
            (312, 755),
            (319, 755),
            (326, 745),
            (333, 739),
            (340, 754),
        ]

        slope, intercept, residual = _fit_wall_line(points)

        self.assertAlmostEqual(slope * 309 + intercept, 755.0, delta=1.0)
        self.assertLess(residual, 1.0)

    def test_wall_line_brightness_rejects_missing_edges(self) -> None:
        frame = np.full((1296, 2304, 3), 100, dtype=np.uint8)

        with self.assertRaisesRegex(ValueError, "słaby kontrast"):
            _measure_opening_brightness(frame)

    def test_appearance_detector_confirms_two_of_four_samples_once(self) -> None:
        detector = AppearanceDetector(fps=10)
        results = [
            detector.update(index, detected)
            for index, detected in enumerate(
                [False, True, True, True, True, True, False, False]
            )
        ]

        appearances = [result for result in results if result is not None]

        self.assertEqual(appearances, [Appearance(start_frame=1)])

    def test_sparse_sampling_confirms_with_two_of_four_samples(self) -> None:
        detector = AppearanceDetector(fps=12)
        results = [
            detector.update(index * 4, detected)
            for index, detected in enumerate([False, True, True, False])
        ]

        self.assertEqual(
            [r for r in results if r is not None], [Appearance(start_frame=4)]
        )

    def test_sparse_sampling_counts_hits_across_four_samples(self) -> None:
        detector = AppearanceDetector(fps=12)
        results = [
            detector.update(index * 12, detected)
            for index, detected in enumerate([True, False, False, True])
        ]

        self.assertEqual(
            [r for r in results if r is not None], [Appearance(start_frame=0)]
        )

    def test_appearance_detector_rearms_after_activity_disappears(self) -> None:
        detector = AppearanceDetector(fps=2)
        detected_frames = {1, 2, 3, 20, 21, 22}
        appearances = []
        for frame_index in range(30):
            result = detector.update(frame_index, frame_index in detected_frames)
            if result is not None:
                appearances.append(result)

        self.assertEqual(
            appearances,
            [Appearance(start_frame=1), Appearance(start_frame=20)],
        )

    def test_clip_name_uses_recording_date_and_clip_start_time(self) -> None:
        with TemporaryDirectory() as temp_dir:
            recording_dir = Path(temp_dir) / "2026-10-25"
            recording_dir.mkdir()
            output_path = recording_dir / "preview.mp4"
            video_path = recording_dir / "05-49-02-5-25.mkv"
            recording_start = datetime(2026, 10, 25, 5, 49, 2)
            with patch(
                "mcrwac.analysis.squirrel_exit_prototype.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ), patch(
                "mcrwac.analysis.squirrel_exit_prototype.subprocess.run",
                return_value=type("Result", (), {"returncode": 0, "stderr": ""})(),
            ) as run:
                clips = _save_appearance_clips(
                    video_path,
                    output_path,
                    [Appearance(start_frame=3)],
                    fps=2,
                    frame_count=20,
                    clip_seconds=60,
                    recording_start=recording_start,
                )

        self.assertEqual(clips[0].name, "05-49-02-5-25_20261025054903.mkv")
        self.assertEqual(clips[0].parent, recording_dir)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-ss") + 1], "1.500000")
        self.assertEqual(command[command.index("-t") + 1], "8.500000")
        self.assertGreater(command.index("-ss"), command.index("-i"))
        self.assertEqual(command[command.index("-c:a") + 1], "aac")
        self.assertEqual(command[-2], "-y")

    def test_output_paths_include_recording_timestamp_and_avoid_collisions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            recording_dir = Path(temp_dir) / "2026-10-25"
            recording_dir.mkdir()
            video_path = recording_dir / "05-49-02-5-25.mkv"
            recording_start = _recording_start_time(video_path, 1200)
            requested_path = Path(temp_dir) / "analysis.mp4"
            timestamped_path = _timestamped_output_path(
                video_path, requested_path, recording_start
            )

            self.assertEqual(
                timestamped_path.name, "analysis_20261025054902.mp4"
            )
            first_path = _reserve_output_path(timestamped_path)
            second_path = _reserve_output_path(timestamped_path)

        self.assertEqual(first_path.name, "analysis_20261025054902.mp4")
        self.assertEqual(second_path.name, "analysis_20261025054902_02.mp4")

    def test_analyzed_list_subdir_is_configurable(self) -> None:
        video = Path("/rec/POE1/2026-10-28/05-24-15.mkv")

        self.assertEqual(
            _analyzed_list_path(video, Path("/a"), "POE1_sikorki"),
            Path("/a/2026-10-28/POE1_sikorki/analyzed.txt"),
        )
        self.assertEqual(
            _analyzed_list_path(video, Path("/a")),
            Path("/a/2026-10-28/POE_BRAMA_wiewiorki/analyzed.txt"),
        )

    def test_default_output_path_is_analysis_directory(self) -> None:
        video_path = Path("/opt/cameras/recordings/CAM/2026-10-25/05-49-02.mkv")

        output_path = _timestamped_output_path(
            video_path, None, datetime(2026, 10, 25, 5, 49, 2)
        )

        self.assertEqual(
            output_path,
            Path("/opt/cameras/recordings/analysis/2026-10-25/POE_BRAMA_wiewiorki/05-49-02_analysis_20261025054902.mp4"),
        )

    def _make_day(self, root: Path, names_ages: dict[str, float], now: float) -> Path:
        day = root / "cam" / "2026-10-27"
        day.mkdir(parents=True)
        for name, age in names_ages.items():
            path = day / name
            path.touch()
            os.utime(path, (now - age, now - age))
        return root / "cam"

    def test_next_pending_can_prefer_newest_day(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            now = 2_000_000_000.0
            for day_name in ("2026-10-28", "2026-10-29"):
                day = root / "cam" / day_name
                day.mkdir(parents=True)
                for name in ("05-00-00.mkv", "05-30-00.mkv"):
                    path = day / name
                    path.touch()
                    os.utime(path, (now - 9999, now - 9999))
            args = (root / "cam", date(2026, 10, 28), date(2026, 10, 29), now)
            analysis = root / "analysis"

            oldest = next_pending_recording(*args, analysis_dir=analysis)
            newest = next_pending_recording(
                *args, analysis_dir=analysis, newest_day_first=True
            )

            self.assertEqual(oldest.parent.name, "2026-10-28")
            self.assertEqual(newest, root / "cam" / "2026-10-29" / "05-00-00.mkv")

    def test_next_pending_skips_file_still_being_recorded(self) -> None:
        now = 1_800_000_000.0
        with TemporaryDirectory() as directory:
            root = Path(directory)
            camera_dir = self._make_day(
                root, {"13-00-00.mkv": 1800, "13-30-00.mkv": 5}, now
            )
            analysis = root / "analysis"
            day = date(2026, 10, 27)

            first = next_pending_recording(
                camera_dir, day, day, now, analysis_dir=analysis
            )
            self.assertEqual(first.name, "13-00-00.mkv")

            _mark_analyzed(first, "ok", analysis)
            self.assertEqual(_load_analyzed(analysis / "2026-10-27" / "POE_BRAMA_wiewiorki" / "analyzed.txt"),
                             {"13-00-00.mkv"})
            self.assertIsNone(
                next_pending_recording(camera_dir, day, day, now, analysis_dir=analysis)
            )
            # po zatrzymaniu nagrywania (plik nie rośnie od 2 min) ostatni też wchodzi
            last = next_pending_recording(
                camera_dir, day, day, now + 200, analysis_dir=analysis
            )
            self.assertEqual(last.name, "13-30-00.mkv")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
    def test_day_result_is_built_as_soon_as_clips_exist(self) -> None:
        now = 1_800_000_000.0
        with TemporaryDirectory() as directory:
            root = Path(directory)
            camera_dir = self._make_day(
                root, {"13-00-00.mkv": 1800, "13-30-00.mkv": 1800}, now
            )
            analysis = root / "analysis"
            day_dir = analysis / "2026-10-27" / "POE_BRAMA_wiewiorki"
            day_dir.mkdir(parents=True)
            for name in ("13-00-00_a.mkv", "13-30-00_b.mkv"):
                subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                     "testsrc=size=64x48:rate=10:duration=1", "-c:v", "libx264",
                     str(day_dir / name)],
                    check=True,
                )
            day = date(2026, 10, 27)
            failed: set = set()
            update_day_results(camera_dir, day, day, analysis, "POE_BRAMA_wiewiorki", failed)
            result = day_dir / "result_2026_10_27.mkv"
            self.assertTrue(result.exists())
            duration = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(result)],
                capture_output=True, text=True, check=True,
            ).stdout)
            self.assertGreater(duration, 1.8)

    def test_next_pending_respects_hours_range(self) -> None:
        now = 1_800_000_000.0
        with TemporaryDirectory() as directory:
            root = Path(directory)
            camera_dir = self._make_day(
                root,
                {"09-00-00.mkv": 9000, "14-00-00.mkv": 5000, "15-00-00.mkv": 3000},
                now,
            )
            day = date(2026, 10, 27)

            found = next_pending_recording(
                camera_dir, day, day, now, start=time(13, 0), end=time(15, 0),
                analysis_dir=root / "analysis",
            )

        self.assertEqual(found.name, "14-00-00.mkv")

    def test_watch_analyzes_in_order_continues_after_error_and_never_repeats(self) -> None:
        now = datetime(2026, 10, 27, 20, 0, 0)
        stamp = now.timestamp()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            camera_dir = self._make_day(
                root, {"10-00-00.mkv": 9000, "10-30-00.mkv": 7000, "11-00-00.mkv": 5000},
                stamp,
            )
            analysis = root / "analysis"
            analyzed: list[str] = []

            def analyze(video: Path) -> None:
                analyzed.append(video.name)
                if video.name == "10-30-00.mkv":
                    raise RuntimeError("zepsuty plik")

            def stop(_seconds: float) -> None:
                raise KeyboardInterrupt

            with self.assertRaises(KeyboardInterrupt):
                watch_recordings(
                    camera_dir, analyze, analysis_dir=analysis,
                    clock=lambda: now, sleep=stop,
                )

            lines = (analysis / "2026-10-27" / "POE_BRAMA_wiewiorki" / "analyzed.txt").read_text().splitlines()

        self.assertEqual(analyzed, ["10-00-00.mkv", "10-30-00.mkv", "11-00-00.mkv"])
        self.assertEqual(
            lines,
            ["10-00-00.mkv\tok", "10-30-00.mkv\tblad", "11-00-00.mkv\tok"],
        )

    def test_selects_recordings_by_start_time_in_file_name(self) -> None:
        names = ["12-59-59.mkv", "13-00-08.mkv", "16-30-00.mkv", "17-00-00.mkv",
                 "17-00-05.mkv", "13-00-08-5-25.mkv", "notes.txt"]
        with TemporaryDirectory() as directory:
            for name in names:
                (Path(directory) / name).touch()
            root = Path(directory)

            everything = select_recordings(root)
            from_13 = select_recordings(root, time(13, 0))
            between = select_recordings(root, time(13, 0), time(17, 0))

        self.assertEqual(len(everything), 5)
        self.assertEqual(
            [path.name for path in from_13],
            ["13-00-08.mkv", "16-30-00.mkv", "17-00-00.mkv", "17-00-05.mkv"],
        )
        self.assertEqual(
            [path.name for path in between], ["13-00-08.mkv", "16-30-00.mkv"]
        )



if __name__ == "__main__":
    unittest.main()
