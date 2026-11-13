import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np
from openpyxl import Workbook, load_workbook

from mcrwac.analysis.squirrel_brightness_tests import (
    BRIGHTNESS_CHANGE_THRESHOLD,
    DEFAULT_CHANGE_OFFSETS,
    BrightnessSample,
    _calculate_brightness_changes,
    _excel_brightness_values,
    _find_change_appearances,
    _merge_overlapping_appearances,
    _read_brightness_samples,
    clean_recording_outputs,
    _load_day_geometry,
    _representative_geometry,
    _retry_failed_recordings_before,
    _save_day_geometry,
    _analyze_watched_recording,
    run_brightness_tests,
    _write_change_report,
    main,
)
from mcrwac.analysis.squirrel_exit_prototype import (
    Appearance,
    BrightnessMeasurement,
    OpeningGeometry,
)


def _measurement(
    test_mean: float,
    *,
    cross_x: float = 12.0,
    cross_y: float = 78.0,
    test_x: int = 10,
    test_y: int = 20,
) -> BrightnessMeasurement:
    return BrightnessMeasurement(
        cross_x=cross_x,
        cross_y=cross_y,
        test_x=test_x,
        test_y=test_y,
        reference_x=8,
        reference_y=2,
        orange_reference_x=8,
        orange_reference_y=2,
        upper_reference_x=8,
        upper_reference_y=-15,
        test_mean=test_mean,
        reference_mean=100.0,
        orange_reference_mean=100.0,
        upper_reference_mean=90.0,
        reference_source="lower",
        signed_difference=test_mean - 100.0,
        absolute_difference=abs(test_mean - 100.0),
        difference_percent=abs(test_mean - 100.0),
    )


class SquirrelBrightnessTests(unittest.TestCase):
    def test_representative_geometry_uses_ok_measurement_nearest_to_median(self) -> None:
        start = datetime(2026, 11, 11)
        samples = [
            BrightnessSample(
                frame_index=index,
                seconds=float(index),
                timestamp=start + timedelta(seconds=index),
                measurement=measurement,
                status=status,
            )
            for index, (measurement, status) in enumerate(
                (
                    (
                        _measurement(
                            100.0,
                            cross_x=349.0,
                            cross_y=755.0,
                            test_x=337,
                            test_y=736,
                        ),
                        "ok",
                    ),
                    (
                        _measurement(
                            100.0,
                            cross_x=355.002,
                            cross_y=756.773,
                            test_x=343,
                            test_y=738,
                        ),
                        "ok",
                    ),
                    (
                        _measurement(
                            100.0,
                            cross_x=355.01,
                            cross_y=756.78,
                            test_x=343,
                            test_y=738,
                        ),
                        "ok",
                    ),
                    (
                        _measurement(
                            100.0,
                            cross_x=900.0,
                            cross_y=900.0,
                            test_x=888,
                            test_y=881,
                        ),
                        "poprzednia_geometria: słaby kontrast",
                    ),
                )
            )
        ]

        geometry = _representative_geometry(samples)

        self.assertIsNotNone(geometry)
        assert geometry is not None
        self.assertEqual((geometry.cross_x, geometry.cross_y), (355.002, 756.773))
        self.assertEqual((geometry.test_x, geometry.test_y), (343, 738))

    def test_representative_geometry_requires_fresh_measurements(self) -> None:
        sample = BrightnessSample(
            frame_index=0,
            seconds=0.0,
            timestamp=datetime(2026, 11, 11),
            measurement=_measurement(100.0),
            status="geometria_dzienna",
        )

        self.assertIsNone(_representative_geometry([sample]))

    def test_runner_persists_robust_geometry_only_when_day_is_uninitialized(self) -> None:
        start = datetime(2026, 11, 11)
        samples = [
            BrightnessSample(
                frame_index=index,
                seconds=float(index),
                timestamp=start + timedelta(seconds=index),
                measurement=measurement,
                status=status,
            )
            for index, (measurement, status) in enumerate(
                (
                    (
                        _measurement(
                            100.0,
                            cross_x=349.0,
                            cross_y=755.0,
                            test_x=337,
                            test_y=736,
                        ),
                        "ok",
                    ),
                    (
                        _measurement(
                            100.0,
                            cross_x=355.002,
                            cross_y=756.773,
                            test_x=343,
                            test_y=738,
                        ),
                        "ok",
                    ),
                    (
                        _measurement(
                            100.0,
                            cross_x=355.01,
                            cross_y=756.78,
                            test_x=343,
                            test_y=738,
                        ),
                        "ok",
                    ),
                )
            )
        ]
        with TemporaryDirectory() as directory:
            output_root = Path(directory) / "POE_BRAMA_wiewiorki"
            common_patches = (
                patch(
                    "mcrwac.analysis.squirrel_brightness_tests._read_brightness_samples",
                    return_value=(samples, 1.0, len(samples), start),
                ),
                patch(
                    "mcrwac.analysis.squirrel_brightness_tests._write_change_report",
                    side_effect=lambda _, folder, *_args: folder / "report.xlsx",
                ),
                patch(
                    "mcrwac.analysis.squirrel_brightness_tests._find_change_appearances",
                    return_value=([], set(), set()),
                ),
                patch(
                    "mcrwac.analysis.squirrel_brightness_tests._save_appearance_clips",
                    return_value=[],
                ),
                patch(
                    "mcrwac.analysis.squirrel_brightness_tests.build_day_result",
                    return_value=None,
                ),
            )
            with common_patches[0], common_patches[1], common_patches[2], common_patches[3], common_patches[4]:
                run_brightness_tests(
                    Path("recording.mkv"),
                    n_values=(1,),
                    output_root=output_root,
                    output_in_root=True,
                )

            stored = _load_day_geometry(output_root)
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual((stored.cross_x, stored.cross_y), (355.002, 756.773))
            self.assertEqual((stored.test_x, stored.test_y), (343, 738))

            with patch(
                "mcrwac.analysis.squirrel_brightness_tests._read_brightness_samples",
                return_value=(samples, 1.0, len(samples), start),
            ), patch(
                "mcrwac.analysis.squirrel_brightness_tests._write_change_report",
                side_effect=lambda _, folder, *_args: folder / "report.xlsx",
            ), patch(
                "mcrwac.analysis.squirrel_brightness_tests._find_change_appearances",
                return_value=([], set(), set()),
            ), patch(
                "mcrwac.analysis.squirrel_brightness_tests._save_appearance_clips",
                return_value=[],
            ), patch(
                "mcrwac.analysis.squirrel_brightness_tests.build_day_result",
                return_value=None,
            ), patch(
                "mcrwac.analysis.squirrel_brightness_tests._save_day_geometry"
            ) as save_geometry:
                run_brightness_tests(
                    Path("another-recording.mkv"),
                    n_values=(1,),
                    output_root=output_root,
                    output_in_root=True,
                )

            save_geometry.assert_not_called()
            self.assertEqual(_load_day_geometry(output_root), stored)

    def test_change_is_squared_relative_delta_for_n_samples(self) -> None:
        values = [100.0, 110.0, 120.0, 90.0, 80.0, None, 20.0]

        changes_n3 = _calculate_brightness_changes(values, 3)
        changes_n5 = _calculate_brightness_changes(values, 5)

        self.assertIsNone(changes_n3[2])
        self.assertAlmostEqual(changes_n3[3], 0.01)
        self.assertAlmostEqual(changes_n3[4], (80.0 / 110.0 - 1) ** 2)
        self.assertIsNone(changes_n3[5])
        self.assertIsNone(changes_n5[5])
        self.assertAlmostEqual(changes_n5[6], (20.0 / 110.0 - 1) ** 2)
        self.assertIsNone(_calculate_brightness_changes([0.0, 1.0], 1)[1])

    def test_change_uses_exact_rounded_values_written_to_excel(self) -> None:
        samples = [
            BrightnessSample(
                frame_index=index,
                seconds=float(index),
                timestamp=datetime(2026, 11, 11) + timedelta(seconds=index),
                measurement=_measurement(value),
                status="ok",
            )
            for index, value in enumerate((100.12345, 98.76544))
        ]

        values = _excel_brightness_values(samples)
        changes = _calculate_brightness_changes(values, 1)

        self.assertEqual(values, [100.1235, 98.7654])
        self.assertEqual(changes[1], ((98.7654 - 100.1235) / 100.1235) ** 2)

    def test_change_threshold_is_inclusive(self) -> None:
        self.assertEqual(BRIGHTNESS_CHANGE_THRESHOLD, 0.03)
        samples = [
            BrightnessSample(
                frame_index=index * 12,
                seconds=float(index),
                timestamp=datetime(2026, 11, 11) + timedelta(seconds=index),
                measurement=_measurement(100.0),
                status="ok",
            )
            for index in range(4)
        ]

        appearances, confirmed, clip_starts = _find_change_appearances(
            samples,
            [None, None, None, BRIGHTNESS_CHANGE_THRESHOLD],
            fps=12.0,
        )

        self.assertEqual(len(appearances), 1)
        self.assertEqual(appearances[0].start_frame, 36)
        self.assertEqual(confirmed, {36})
        self.assertEqual(clip_starts, {36})

    def test_change_detector_uses_custom_p_threshold(self) -> None:
        sample = BrightnessSample(
            frame_index=0,
            seconds=0.0,
            timestamp=datetime(2026, 11, 11),
            measurement=_measurement(100.0),
            status="ok",
        )

        appearances, confirmed, clip_starts = _find_change_appearances(
            [sample], [0.1], fps=1.0, threshold=0.1
        )

        self.assertEqual(len(appearances), 1)
        self.assertEqual(confirmed, {0})
        self.assertEqual(clip_starts, {0})

    def test_change_detector_applies_configured_clip_timing(self) -> None:
        sample = BrightnessSample(
            frame_index=10,
            seconds=10.0,
            timestamp=datetime(2026, 11, 11, 0, 0, 10),
            measurement=_measurement(100.0),
            status="ok",
        )

        appearances, _, _ = _find_change_appearances(
            [sample],
            [0.1],
            fps=1.0,
            threshold=0.1,
            pre_detection_seconds=6.0,
            clip_duration_seconds=7.0,
        )

        self.assertEqual(
            appearances,
            [Appearance(10, 10.0, pre_detection_seconds=6.0, clip_duration_seconds=7.0)],
        )
        self.assertEqual(appearances[0].post_detection_seconds, 1.0)

    def test_overlapping_n1_and_n4_clips_use_the_union_of_their_time_ranges(self) -> None:
        appearances, starts, ends = _merge_overlapping_appearances(
            [
                Appearance(10, 12.0, pre_detection_seconds=2.0, clip_duration_seconds=5.0),
                Appearance(14, 14.0, pre_detection_seconds=6.0, clip_duration_seconds=7.0),
            ],
            fps=1.0,
        )

        self.assertEqual(len(appearances), 1)
        self.assertEqual(appearances[0].start_frame, 10)
        self.assertEqual(starts, {10: 8.0})
        self.assertEqual(ends, {10: 15.0})

    def test_samples_continue_once_per_second_across_entire_video(self) -> None:
        capture = unittest.mock.MagicMock()
        capture.isOpened.return_value = True
        capture.get.side_effect = lambda prop: {
            cv2.CAP_PROP_FPS: 2.0,
            cv2.CAP_PROP_FRAME_COUNT: 5.0,
        }[prop]
        capture.read.side_effect = [
            (True, np.zeros((2, 2, 3), dtype=np.uint8)) for _ in range(3)
        ]
        capture.grab.return_value = True
        start = datetime(2026, 11, 11, 9, 49, 49)
        geometry = OpeningGeometry(12, 78, 10, 20, 8, 2, 8, 0)

        with patch(
            "mcrwac.analysis.squirrel_brightness_tests.cv2.VideoCapture",
            return_value=capture,
        ), patch(
            "mcrwac.analysis.squirrel_brightness_tests._recording_start_time",
            return_value=start,
        ), patch(
            "mcrwac.analysis.squirrel_brightness_tests._measure_opening_brightness",
            return_value=_measurement(100.0),
        ) as measure, patch(
            "mcrwac.analysis.squirrel_brightness_tests."
            "_measure_opening_brightness_with_fallback",
        ) as measure_fallback:
            samples, fps, frame_count, recording_start = _read_brightness_samples(
                Path("recording.mkv"), geometry
            )

        self.assertEqual([sample.frame_index for sample in samples], [0, 2, 4])
        self.assertEqual([sample.seconds for sample in samples], [0.0, 1.0, 2.0])
        self.assertEqual(fps, 2.0)
        self.assertEqual(frame_count, 5)
        self.assertEqual(recording_start, start)
        self.assertEqual(capture.grab.call_count, 2)
        capture.release.assert_called_once()
        self.assertEqual(measure.call_count, 3)
        self.assertTrue(
            all(call.kwargs["geometry"] == geometry for call in measure.call_args_list)
        )
        self.assertTrue(
            all(sample.status == "geometria_dzienna" for sample in samples)
        )
        measure_fallback.assert_not_called()

    def test_first_good_line_is_applied_to_samples_before_it_was_found(self) -> None:
        scout = unittest.mock.MagicMock()
        scout.isOpened.return_value = True
        scout.get.side_effect = lambda prop: {
            cv2.CAP_PROP_FPS: 2.0,
            cv2.CAP_PROP_FRAME_COUNT: 5.0,
        }[prop]
        scout.read.side_effect = [
            (True, np.zeros((2, 2, 3), dtype=np.uint8)),
            (True, np.zeros((2, 2, 3), dtype=np.uint8)),
        ]
        scout.grab.return_value = True

        capture = unittest.mock.MagicMock()
        capture.isOpened.return_value = True
        capture.get.side_effect = lambda prop: {
            cv2.CAP_PROP_FPS: 2.0,
            cv2.CAP_PROP_FRAME_COUNT: 5.0,
        }[prop]
        capture.read.side_effect = [
            (True, np.zeros((2, 2, 3), dtype=np.uint8)) for _ in range(3)
        ]
        capture.grab.return_value = True
        geometry = OpeningGeometry(12, 78, 10, 20, 8, 2, 8, 0)
        start = datetime(2026, 11, 11, 6, 19, 47)

        with patch(
            "mcrwac.analysis.squirrel_brightness_tests.cv2.VideoCapture",
            side_effect=[scout, capture],
        ), patch(
            "mcrwac.analysis.squirrel_brightness_tests._recording_start_time",
            return_value=start,
        ), patch(
            "mcrwac.analysis.squirrel_brightness_tests._detect_opening_geometry",
            side_effect=[ValueError("słaby kontrast"), geometry],
        ), patch(
            "mcrwac.analysis.squirrel_brightness_tests."
            "_measure_opening_brightness_with_fallback",
            return_value=(
                _measurement(100.0),
                None,
                "poprzednia_geometria: słaby kontrast",
            ),
        ) as measure:
            samples, _, _, _ = _read_brightness_samples(Path("recording.mkv"))

        self.assertEqual(len(samples), 3)
        self.assertTrue(
            all(call.args[1] == geometry for call in measure.call_args_list)
        )
        scout.release.assert_called_once()
        capture.release.assert_called_once()

    def test_daily_geometry_is_persisted_and_used_to_retry_earlier_failures(self) -> None:
        geometry = OpeningGeometry(12, 78, 10, 20, 8, 2, 8, 0)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            analysis_root = root / "analysis"
            video_day = root / "recordings" / "2026-11-11"
            video_day.mkdir(parents=True)
            earlier = video_day / "05-49-40.mkv"
            partial = video_day / "06-00-00.mkv"
            current = video_day / "06-19-47.mkv"
            later = video_day / "06-49-57.mkv"
            earlier.touch()
            partial.touch()
            current.touch()
            later.touch()
            output_dir = analysis_root / "2026-11-11" / "POE_BRAMA_wiewiorki"
            _save_day_geometry(output_dir, geometry)
            (output_dir / "analyzed.txt").write_text(
                "05-49-40.mkv\tblad\n"
                "06-00-00.mkv\tok\n"
                "06-19-47.mkv\tok\n"
                "06-49-57.mkv\tblad\n",
                encoding="utf-8",
            )
            report = output_dir / (
                "06-00-00_brightness_TestZmianyJasnosci_N3_20261111060000.xlsx"
            )
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Pomiary"
            worksheet.append(["status_pomiaru"])
            worksheet.append(["blad_geometrii: zbyt słaby kontrast"])
            workbook.save(report)
            workbook.close()

            self.assertEqual(_load_day_geometry(output_dir), geometry)
            with patch(
                "mcrwac.analysis.squirrel_brightness_tests."
                "_analyze_watched_recording"
            ) as analyze:
                _retry_failed_recordings_before(
                    current, (3,), analysis_root, threshold=0.1
                )

            self.assertEqual(
                [call.args[0] for call in analyze.call_args_list],
                [earlier, partial],
            )
            self.assertTrue(
                all(
                    call.args[1:] == ((3,), analysis_root, 0.1)
                    for call in analyze.call_args_list
                )
            )
            lines = (output_dir / "analyzed.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(
                lines,
                [
                    "05-49-40.mkv\tok",
                    "06-00-00.mkv\tok",
                    "06-19-47.mkv\tok",
                    "06-49-57.mkv\tblad",
                ],
            )

    def test_clean_recording_outputs_removes_only_matching_video_artifacts(self) -> None:
        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            target_files = (
                "06-19-47_brightness_TestZmianyJasnosci_N3.xlsx",
                "06-19-47_06_23_35_39_9pct.mkv",
                "06-19-47_preview.mp4",
            )
            preserved_files = (
                "06-19-47.mkv",
                "06-49-57_brightness_TestZmianyJasnosci_N3.xlsx",
                "opening_geometry.json",
                "analyzed.txt",
                "result_2026_11_11.mkv",
            )
            for name in target_files + preserved_files:
                (output_dir / name).touch()

            removed = clean_recording_outputs(
                Path("/recordings/06-19-47.mkv"), output_dir
            )

            self.assertEqual({path.name for path in removed}, set(target_files))
            self.assertTrue(
                all((output_dir / name).exists() for name in preserved_files)
            )
            self.assertTrue(
                all(not (output_dir / name).exists() for name in target_files)
            )

        with self.assertRaisesRegex(ValueError, "format GG-MM-SS.mkv"):
            clean_recording_outputs(Path("recording.mkv"), Path(directory))

    def test_report_contains_all_samples_and_time_series_chart(self) -> None:
        start = datetime(2026, 11, 11, 9, 49, 49)
        samples = [
            BrightnessSample(
                frame_index=index * 12,
                seconds=float(index),
                timestamp=start + timedelta(seconds=index),
                measurement=_measurement(100.0 - index),
                status="ok",
            )
            for index in range(3)
        ]

        with TemporaryDirectory() as directory:
            report_path = _write_change_report(
                Path("09-49-49.mkv"),
                Path(directory),
                2,
                samples,
                [None, None, 0.1],
                set(),
                {24},
                start,
                threshold=0.1,
            )
            workbook = load_workbook(report_path, data_only=True)
            worksheet = workbook["Pomiary"]
            chart_sheet = workbook["Wykres"]

            self.assertEqual(worksheet.max_row, len(samples) + 1)
            self.assertEqual(
                worksheet["H1"].value,
                "jasnosc_testowa_srednia_0_255",
            )
            self.assertEqual(worksheet["K1"].value, "ZmianaJasnosci")
            self.assertEqual(worksheet["L1"].value, "sygnal_progu_0.100")
            self.assertEqual(worksheet["K4"].value, 0.1)
            self.assertEqual(worksheet["L4"].value, 1)
            self.assertEqual(workbook.active.title, "Wykres")
            self.assertEqual(len(chart_sheet._charts), 1)
            self.assertEqual(chart_sheet._charts[0].anchor._from.col, 0)
            self.assertEqual(chart_sheet._charts[0].anchor._from.row, 0)
            self.assertEqual(len(worksheet._charts), 0)
            workbook.close()

    def test_runner_only_generates_change_tests_in_fresh_test_dirs(self) -> None:
        start = datetime(2026, 11, 11, 9, 49, 49)
        samples = [
            BrightnessSample(
                frame_index=index,
                seconds=float(index),
                timestamp=start + timedelta(seconds=index),
                measurement=_measurement(100.0 - index),
                status="ok",
            )
            for index in range(6)
        ]
        with TemporaryDirectory() as directory:
            output_root = Path(directory)
            with patch(
                "mcrwac.analysis.squirrel_brightness_tests._read_brightness_samples",
                return_value=(samples, 1.0, 6, start),
            ), patch(
                "mcrwac.analysis.squirrel_brightness_tests._write_change_report",
                side_effect=lambda _, folder, n, *_args: folder
                / f"report_N{n}.xlsx",
            ) as write_report, patch(
                "mcrwac.analysis.squirrel_brightness_tests._find_change_appearances",
                side_effect=[
                    (
                        [Appearance(2, 20.0, 2.0, 5.0)],
                        {2},
                        {2},
                    ),
                    (
                        [Appearance(6, 30.0, 6.0, 7.0)],
                        {6},
                        {6},
                    ),
                ],
            ) as find_appearances, patch(
                "mcrwac.analysis.squirrel_brightness_tests._save_appearance_clips",
                return_value=[],
            ) as save_clips, patch(
                "mcrwac.analysis.squirrel_brightness_tests.build_day_result",
                return_value=None,
            ) as build_result:
                results = run_brightness_tests(
                    Path("recording.mkv"),
                    n_values=(1, 4),
                    output_root=output_root,
                    threshold=(0.05, 0.1),
                )

        self.assertEqual(len(results), 2)
        self.assertEqual(
            {name.split("_")[1] for name in results},
            {"N1", "N4"},
        )
        test_dirs = [call.args[0] for call in build_result.call_args_list]
        self.assertTrue(all(path.parent == output_root for path in test_dirs))
        self.assertEqual(len(set(test_dirs)), 2)
        self.assertEqual(
            [call.args[3] for call in find_appearances.call_args_list],
            [0.05, 0.1],
        )
        self.assertEqual(
            [
                (
                    call.kwargs["pre_detection_seconds"],
                    call.kwargs["clip_duration_seconds"],
                )
                for call in find_appearances.call_args_list
            ],
            [(2.0, 5.0), (6.0, 7.0)],
        )
        self.assertEqual(
            [call.args[8] for call in write_report.call_args_list],
            [0.05, 0.1],
        )
        save_clips.assert_called_once()
        self.assertEqual(
            [appearance.start_frame for appearance in save_clips.call_args.args[2]],
            [2],
        )
        self.assertEqual(
            save_clips.call_args.kwargs["clip_start_seconds"],
            {2: 0.0},
        )
        self.assertEqual(
            save_clips.call_args.kwargs["clip_end_seconds"],
            {2: 7.0},
        )
        self.assertTrue(
            all(
                call.kwargs["overlay_geometries"] is not None
                for call in save_clips.call_args_list
            )
        )

    def test_watcher_writes_primary_output_to_standard_directory(self) -> None:
        video_path = Path("/recordings/2026-11-11/09-49-49.mkv")
        analysis_root = Path("/analysis")
        self.assertEqual(DEFAULT_CHANGE_OFFSETS, (3,))

        with patch(
            "mcrwac.analysis.squirrel_brightness_tests.run_brightness_tests"
        ) as run_tests:
            _analyze_watched_recording(video_path, (3, 7), analysis_root)

        run_tests.assert_called_once_with(
            video_path,
            (3, 7),
            analysis_root / "2026-11-11" / "POE_BRAMA_wiewiorki",
            per_recording_directory=False,
            unique_run_directory=False,
            output_in_root=True,
            threshold=BRIGHTNESS_CHANGE_THRESHOLD,
        )

    def test_watcher_cli_accepts_n_and_p(self) -> None:
        with patch(
            "mcrwac.analysis.squirrel_brightness_tests.run_brightness_watcher"
        ) as run_watcher, patch(
            "sys.argv",
            [
                "squirrel_brightness_tests",
                "--watch",
                "--from-date",
                "2026-11-11",
                "--n",
                "7",
                "--p",
                "0.1",
            ],
        ):
            main()

        self.assertEqual(run_watcher.call_args.args[0], [7])
        self.assertEqual(run_watcher.call_args.args[4], datetime(2026, 11, 11).date())
        self.assertEqual(run_watcher.call_args.args[-1], 0.1)

    def test_watcher_cli_accepts_a_threshold_for_each_n(self) -> None:
        with patch(
            "mcrwac.analysis.squirrel_brightness_tests.run_brightness_watcher"
        ) as run_watcher, patch(
            "sys.argv",
            [
                "squirrel_brightness_tests",
                "--watch",
                "--n",
                "1",
                "4",
                "--p",
                "0.05",
                "0.1",
            ],
        ):
            main()

        self.assertEqual(run_watcher.call_args.args[0], [1, 4])
        self.assertEqual(run_watcher.call_args.args[-1], [0.05, 0.1])


if __name__ == "__main__":
    unittest.main()
