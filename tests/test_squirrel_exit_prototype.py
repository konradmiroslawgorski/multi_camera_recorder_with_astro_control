import os
import unittest
from datetime import date, datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from mcrwac.analysis.squirrel_exit_prototype import (
    ActivityRegion,
    Appearance,
    AppearanceDetector,
    OpeningCandidate,
    REFERENCE_DIR,
    TrackResult,
    _activity_mask,
    _find_opening,
    _format_elapsed,
    _load_analyzed,
    _load_anchor_templates,
    _mark_analyzed,
    _recording_start_time,
    _reference_window,
    _reserve_output_path,
    _save_appearance_clips,
    _timestamped_output_path,
    _update_background,
    activity_regions,
    locate_opening_by_triangle,
    next_pending_recording,
    select_recordings,
    track_opening,
    watch_recordings,
)


def _scene() -> np.ndarray:
    frame = np.full((300, 300, 3), 180, dtype=np.uint8)
    cv2.rectangle(frame, (105, 35), (155, 265), (120, 120, 120), -1)
    cv2.line(frame, (30, 145), (250, 145), (230, 230, 230), 3)
    cv2.ellipse(frame, (132, 150), (2, 4), 0, 0, 360, (5, 5, 5), -1)
    return frame


class SquirrelExitPrototypeTests(unittest.TestCase):
    def test_tracks_opening_after_small_camera_translation(self) -> None:
        reference = _scene()
        reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
        transform = np.float32([[1, 0, 3], [0, 1, 2]])
        shifted = cv2.warpAffine(reference, transform, (300, 300))
        opening = OpeningCandidate(132, 150, 1.0)

        tracked = track_opening(shifted, reference_gray, opening)
        mask, bounds, change_ratio = _activity_mask(
            shifted, reference_gray, opening, tracked
        )

        self.assertAlmostEqual(tracked.x, 135, delta=1)
        self.assertAlmostEqual(tracked.y, 152, delta=1)
        self.assertGreaterEqual(tracked.confidence, 0.60)
        self.assertIsNotNone(mask)
        self.assertIsNotNone(bounds)
        self.assertLess(change_ratio, 0.02)

    def test_background_absorbs_slow_light_change_but_not_sudden_object(self) -> None:
        frame = _scene()
        reference_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        opening = OpeningCandidate(132, 150, 1.0)
        tracked = TrackResult(132, 150, 1.0)

        # Lokalne przyciemnienie jasnej ściany (cień) nie jest wiewiórką.
        shadow = frame.copy()
        shadow[60:110, 140:160] = (shadow[60:110, 140:160] * 0.8).astype(np.uint8)
        mask, _, _ = _activity_mask(shadow, reference_gray, opening, tracked)
        self.assertEqual(cv2.countNonZero(mask), 0)

        # Ciemny obiekt tej samej wielkości musi być widoczny.
        dark = frame.copy()
        dark[60:110, 140:160] = (20, 20, 20)
        mask, _, _ = _activity_mask(dark, reference_gray, opening, tracked)
        self.assertGreater(cv2.countNonZero(mask), 300)

    def _roof_frame(self, shift=(0, 0)) -> np.ndarray:
        """Klatka z szumem, na której wklejono komin i szczyt dachu ze wzorców."""
        rng = np.random.default_rng(1)
        gray = rng.integers(90, 130, (1296, 2304), dtype=np.uint8)
        for name, (x, y) in (
            ("poe_brama_chimney.png", (150, 485)),
            ("poe_brama_ridge.png", (610, 515)),
        ):
            patch = cv2.imread(str(REFERENCE_DIR / name), cv2.IMREAD_GRAYSCALE)
            h, w = patch.shape
            gray[y + shift[1]:y + shift[1] + h, x + shift[0]:x + shift[0] + w] = patch
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    def test_loads_both_anchor_templates(self) -> None:
        templates = _load_anchor_templates()

        self.assertIsNotNone(templates)
        for template in templates:
            self.assertGreater(len(template.scaled), 5)

    def test_triangle_gives_reference_opening_and_follows_camera_shift(self) -> None:
        opening = locate_opening_by_triangle([self._roof_frame()], min_estimates=1)
        shifted = locate_opening_by_triangle(
            [self._roof_frame((120, -40))], min_estimates=1
        )

        self.assertAlmostEqual(opening.x, 347, delta=2)
        self.assertAlmostEqual(opening.y, 744, delta=2)
        self.assertAlmostEqual(shifted.x, 467, delta=2)
        self.assertAlmostEqual(shifted.y, 704, delta=2)

    def test_triangle_rejects_frame_without_chimney_and_ridge(self) -> None:
        rng = np.random.default_rng(2)
        gray = rng.integers(90, 130, (1296, 2304), dtype=np.uint8)
        frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        self.assertIsNone(locate_opening_by_triangle([frame]))

    def test_background_slow_change_scenario(self) -> None:
        reference = _scene()
        reference_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
        opening = OpeningCandidate(132, 150, 1.0)
        background = _reference_window(reference_gray, opening)
        tracked = TrackResult(132, 150, 1.0)
        self.assertIsNotNone(background)

        # Powolne rozjaśnianie nie może wywołać alarmu, mimo +40 względem wzorca.
        for step in range(1, 401):
            frame = cv2.convertScaleAbs(reference, alpha=1.0, beta=step * 0.1)
            mask, bounds, _ = _activity_mask(
                frame, reference_gray, opening, tracked, background
            )
            if step > 100:
                self.assertEqual(cv2.countNonZero(mask), 0, f"krok {step}")
            _update_background(background, frame, bounds, mask, 12.0)

        # Nagły ciemny obiekt musi nadal być widoczny.
        frame = cv2.convertScaleAbs(reference, alpha=1.0, beta=40)
        cv2.rectangle(frame, (125, 120), (142, 152), (20, 20, 20), -1)
        mask, _, _ = _activity_mask(frame, reference_gray, opening, tracked, background)
        regions, _ = activity_regions(mask, 30.0)
        self.assertEqual(len(regions), 1)

    def test_activity_regions_filter_by_bounding_box_diagonal(self) -> None:
        mask = np.zeros((100, 100), dtype=np.uint8)
        cv2.rectangle(mask, (5, 5), (15, 15), 255, -1)
        cv2.rectangle(mask, (40, 20), (75, 40), 255, -1)

        regions, largest_diagonal = activity_regions(mask, min_diagonal_px=25)

        self.assertEqual(len(regions), 1)
        self.assertGreater(regions[0].diagonal, 25)
        self.assertAlmostEqual(largest_diagonal, regions[0].diagonal)
        self.assertEqual(regions[0], ActivityRegion(40, 20, 36, 21, regions[0].diagonal))

    def test_activity_regions_reject_thin_vibration_with_large_diagonal(self) -> None:
        mask = np.zeros((100, 100), dtype=np.uint8)
        cv2.rectangle(mask, (10, 10), (12, 50), 255, -1)
        cv2.rectangle(mask, (20, 10), (28, 38), 255, -1)
        cv2.rectangle(mask, (40, 20), (55, 50), 255, -1)

        regions, largest_diagonal = activity_regions(mask, min_diagonal_px=30)

        self.assertEqual(len(regions), 1)
        self.assertEqual((regions[0].width, regions[0].height), (16, 31))
        self.assertGreater(largest_diagonal, regions[0].diagonal)

    def _run_find_opening(self, visible_from, frame_count=1000, visible_until=None):
        """Otwór widoczny w klatkach [visible_from, visible_until)."""
        frame = _scene()
        opening = OpeningCandidate(132, 150, 13)
        probes: list[int] = []

        def probe(capture, index):
            probes.append(index)
            return visible_from <= index < (visible_until or frame_count + 1)

        with patch("mcrwac.analysis.squirrel_exit_prototype._probe_reference", side_effect=probe), patch(
            "mcrwac.analysis.squirrel_exit_prototype._sample_calibration_frames",
            side_effect=lambda capture, fps, start: [frame],
        ), patch(
            "mcrwac.analysis.squirrel_exit_prototype.locate_opening_by_triangle",
            return_value=opening,
        ):
            found = _find_opening(
                capture=None, fps=10, frame_count=frame_count, start_frame=0, retry_frames=150
            )
        return found, probes

    def test_find_opening_uses_grid_then_bisection_to_find_first_visible_moment(self) -> None:
        found, probes = self._run_find_opening(visible_from=1700, frame_count=3000)

        self.assertIsNotNone(found)
        self.assertGreaterEqual(found[2], 1700)
        self.assertLess(found[2], 1700 + 150)
        # siatka co 600 klatek (60 s) i kilka kroków bisekcji, nie próba co 150
        self.assertLess(len(probes), 12)

    def test_find_opening_visible_at_start_needs_one_probe(self) -> None:
        found, probes = self._run_find_opening(visible_from=0)

        self.assertEqual(found[2], 0)
        self.assertEqual(probes, [0])

    def test_find_opening_returns_none_when_it_never_becomes_visible(self) -> None:
        found, probes = self._run_find_opening(visible_from=10**9)

        self.assertIsNone(found)
        self.assertEqual(probes, [0, 600])

    def test_activity_regions_reject_long_edge_like_regions(self) -> None:
        mask = np.zeros((200, 100), dtype=np.uint8)
        cv2.rectangle(mask, (10, 10), (22, 141), 255, -1)
        cv2.rectangle(mask, (40, 20), (55, 50), 255, -1)

        regions, _ = activity_regions(mask, min_diagonal_px=30)

        self.assertEqual(len(regions), 1)
        self.assertEqual((regions[0].width, regions[0].height), (16, 31))

    def test_appearance_detector_confirms_a_persistent_large_region_once(self) -> None:
        detector = AppearanceDetector(fps=10)
        results = [
            detector.update(index, detected)
            for index, detected in enumerate(
                [False, True, True, True, True, True, False, False]
            )
        ]

        appearances = [result for result in results if result is not None]

        self.assertEqual(appearances, [Appearance(start_frame=1)])

    def test_sparse_sampling_confirms_with_two_of_three_samples(self) -> None:
        detector = AppearanceDetector(fps=12, frame_step=4)
        results = [
            detector.update(index * 4, detected)
            for index, detected in enumerate([False, True, True, False])
        ]

        self.assertEqual(
            [r for r in results if r is not None], [Appearance(start_frame=4)]
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

    def test_default_output_path_is_analysis_directory(self) -> None:
        video_path = Path("/opt/cameras/recordings/CAM/2026-10-25/05-49-02.mkv")

        output_path = _timestamped_output_path(
            video_path, None, datetime(2026, 10, 25, 5, 49, 2)
        )

        self.assertEqual(
            output_path,
            Path("/opt/cameras/recordings/analysis/2026-10-25/05-49-02_analysis_20261025054902.mp4"),
        )

    def _make_day(self, root: Path, names_ages: dict[str, float], now: float) -> Path:
        day = root / "cam" / "2026-10-27"
        day.mkdir(parents=True)
        for name, age in names_ages.items():
            path = day / name
            path.touch()
            os.utime(path, (now - age, now - age))
        return root / "cam"

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
            self.assertEqual(_load_analyzed(analysis / "2026-10-27" / "analyzed.txt"),
                             {"13-00-00.mkv"})
            self.assertIsNone(
                next_pending_recording(camera_dir, day, day, now, analysis_dir=analysis)
            )
            # po zatrzymaniu nagrywania (plik nie rośnie od 2 min) ostatni też wchodzi
            last = next_pending_recording(
                camera_dir, day, day, now + 200, analysis_dir=analysis
            )
            self.assertEqual(last.name, "13-30-00.mkv")

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

            lines = (analysis / "2026-10-27" / "analyzed.txt").read_text().splitlines()

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

    def test_elapsed_time_filename_is_zero_padded(self) -> None:
        self.assertEqual(_format_elapsed(3723.125), "01-02-03-125")


if __name__ == "__main__":
    unittest.main()
