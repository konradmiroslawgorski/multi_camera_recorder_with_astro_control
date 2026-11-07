import argparse
import math
import os
import re
import shutil
import signal
import subprocess
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Optional, Sequence

import cv2
import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Font


@dataclass(frozen=True)
class Appearance:
    start_frame: int
    detection_percent: float

    @property
    def clip_seconds(self) -> float:
        return _clip_duration_for_detection(self.detection_percent)

    @property
    def post_detection_seconds(self) -> float:
        return self.clip_seconds - CLIP_PREROLL_SECONDS


@dataclass(frozen=True)
class BrightnessMeasurement:
    cross_x: float
    cross_y: float
    test_x: int
    test_y: int
    reference_x: int
    reference_y: int
    orange_reference_x: int
    orange_reference_y: int
    upper_reference_x: int
    upper_reference_y: int
    test_mean: float
    reference_mean: float
    orange_reference_mean: float
    upper_reference_mean: float
    reference_source: str
    signed_difference: float
    absolute_difference: float
    difference_percent: float


DEFAULT_ANALYSIS_OUTPUT_DIR = Path("/opt/cameras/recordings/analysis")
ANALYSIS_SUBDIR = "POE_BRAMA_wiewiorki"
DEFAULT_BRIGHTNESS_THRESHOLD_PERCENT = 22.0
BRIGHTNESS_SAMPLE_SECONDS = 1.0
CLIP_PREROLL_SECONDS = 2.0
BRIGHTNESS_MIN_EDGE_GRADIENT = 8.0
BRIGHTNESS_MAX_LINE_RESIDUAL_PX = 3.0
BRIGHTNESS_MIN_LINE_POINTS = 5
RELEASE_GAP_SECONDS = 1.5
WATCH_POLL_SECONDS = 30.0
WATCH_SETTLE_SECONDS = 120.0
ANALYZED_LIST_NAME = "analyzed.txt"
DEFAULT_RECORDINGS_ROOT = Path("/opt/cameras/recordings")
DEFAULT_CAMERA = "POE_BRAMA"
RECORDING_NAME_PATTERN = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\.mkv$")


def _clip_duration_for_detection(detection_percent: float) -> float:
    for level, duration in (
        (30.0, 12.0),
        (25.0, 9.0),
        (22.0, 7.0),
    ):
        if detection_percent >= level:
            return duration
    raise ValueError("Poziom wykrycia musi wynosić co najmniej 22%.")


def _log(message: str, flush: bool = True) -> None:
    """Komunikat na ekran z czasem zegarowym [GG:MM:SS] (nie z nagrania)."""
    print(f"{datetime.now():[%H:%M:%S]} {message}", flush=flush)


class AppearanceDetector:
    def __init__(self, fps: float):
        self._release_gap_frames = max(1, int(round(fps * RELEASE_GAP_SECONDS)))
        self._active = False
        self._last_seen_frame: Optional[int] = None

    def update(
        self, frame_index: int, signal: bool, detection_percent: float
    ) -> Optional[Appearance]:
        if self._active:
            if (
                self._last_seen_frame is not None
                and frame_index - self._last_seen_frame < self._release_gap_frames
            ):
                if signal:
                    self._last_seen_frame = frame_index
                return None

            self._active = False
            self._last_seen_frame = None

        if not signal:
            return None

        self._active = True
        self._last_seen_frame = frame_index
        return Appearance(
            start_frame=frame_index,
            detection_percent=detection_percent,
        )


def _recording_start_time(video_path: Path, video_duration: float) -> datetime:
    try:
        recording_date = datetime.strptime(video_path.parent.name, "%Y-%m-%d").date()
        recording_time = datetime.strptime(video_path.stem[:8], "%H-%M-%S").time()
    except ValueError:
        modified_at = datetime.fromtimestamp(video_path.stat().st_mtime)
        return modified_at - timedelta(seconds=video_duration)
    return datetime.combine(recording_date, recording_time)


def _timestamped_output_path(
    video_path: Path,
    output_path: Optional[Path],
    recording_start: datetime,
) -> Path:
    timestamp = recording_start.strftime("%Y%m%d%H%M%S")
    if output_path is None:
        return (
            DEFAULT_ANALYSIS_OUTPUT_DIR
            / recording_start.strftime("%Y-%m-%d")
            / ANALYSIS_SUBDIR
            / f"{video_path.stem}_analysis_{timestamp}.mp4"
        )
    return output_path.with_name(
        f"{output_path.stem}_{timestamp}{output_path.suffix or '.mp4'}"
    )


def _reserve_output_path(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        suffix = "" if index == 1 else f"_{index:02d}"
        candidate = output_path.with_name(
            f"{output_path.stem}{suffix}{output_path.suffix}"
        )
        if candidate.exists():
            index += 1
            continue
        try:
            with candidate.open("x"):
                pass
        except FileExistsError:
            index += 1
            continue
        return candidate


def _save_appearance_clips(
    video_path: Path,
    output_path: Path,
    appearances: list[Appearance],
    fps: float,
    frame_count: int,
    recording_start: datetime,
) -> list[Path]:
    if not appearances:
        return []
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("Nie znaleziono ffmpeg wymaganego do zapisu klipów.")

    clips_dir = output_path.parent
    video_duration = frame_count / fps
    clip_paths: list[Path] = []

    for clip_index, appearance in enumerate(appearances, start=1):
        detection_seconds = appearance.start_frame / fps
        start_seconds = max(0.0, detection_seconds - CLIP_PREROLL_SECONDS)
        end_seconds = min(
            detection_seconds + appearance.post_detection_seconds,
            video_duration,
        )
        duration = end_seconds - start_seconds
        if duration <= 0:
            continue

        detection_time = recording_start + timedelta(seconds=detection_seconds)
        time_label = detection_time.strftime("%H_%M_%S")
        detection_label = f"{appearance.detection_percent:.1f}".replace(".", "_")
        clip_path = clips_dir / (
            f"{video_path.stem}_{time_label}_{detection_label}pct.mkv"
        )
        suffix = 1
        while True:
            try:
                with clip_path.open("x"):
                    pass
                break
            except FileExistsError:
                suffix += 1
                clip_path = clips_dir / (
                    f"{video_path.stem}_{time_label}_{detection_label}pct"
                    f"_{suffix:02d}.mkv"
                )
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_seconds:.6f}",
            "-i",
            str(video_path),
            "-t",
            f"{duration:.6f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-y",
            str(clip_path),
        ]
        _log(f"Rozpoczynam zapis klipu {clip_index}/{len(appearances)}: {clip_path.name}")
        clip_started = _time.perf_counter()
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
        finally:
            elapsed = _time.perf_counter() - clip_started
            _log(
                f"Czas zapisu klipu {clip_index}/{len(appearances)}: "
                f"{elapsed:.2f} s ({clip_path.name})"
            )
        if result.returncode != 0:
            clip_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg nie zapisał klipu {clip_path}: {result.stderr.strip()}"
            )
        clip_paths.append(clip_path)

    return clip_paths


def _fit_wall_line(
    points: Sequence[tuple[int, int]], vertical: bool = False
) -> tuple[float, float, float]:
    samples = np.asarray(points, dtype=np.float64)
    if samples.ndim != 2 or samples.shape[0] < BRIGHTNESS_MIN_LINE_POINTS:
        raise ValueError("za mało punktów krawędzi do dopasowania prostej")

    x_values, y_values = samples.T
    independent = y_values if vertical else x_values
    dependent = x_values if vertical else y_values
    if np.ptp(independent) < 1:
        raise ValueError("punkty krawędzi nie rozciągają się wzdłuż prostej")

    inlier_limit = max(2.0, BRIGHTNESS_MAX_LINE_RESIDUAL_PX)
    best_inliers: Optional[np.ndarray] = None
    best_key = (-1, float("inf"))
    for first in range(len(independent)):
        for second in range(first + 1, len(independent)):
            delta = independent[second] - independent[first]
            if delta == 0:
                continue
            candidate_slope = (
                dependent[second] - dependent[first]
            ) / delta
            candidate_intercept = (
                dependent[first] - candidate_slope * independent[first]
            )
            residuals = np.abs(
                dependent - (candidate_slope * independent + candidate_intercept)
            ) / math.sqrt(1.0 + candidate_slope * candidate_slope)
            inliers = residuals <= inlier_limit
            inlier_count = int(inliers.sum())
            inlier_error = float(np.mean(np.square(residuals[inliers])))
            key = (inlier_count, -inlier_error)
            if key > best_key:
                best_key = key
                best_inliers = inliers

    if best_inliers is None:
        raise ValueError("punkty krawędzi nie rozciągają się wzdłuż prostej")
    inliers = best_inliers
    if int(inliers.sum()) < BRIGHTNESS_MIN_LINE_POINTS:
        raise ValueError("za mało zgodnych punktów krawędzi")

    independent = independent[inliers]
    dependent = dependent[inliers]
    slope, intercept = np.polyfit(independent, dependent, 1)
    residuals = dependent - (slope * independent + intercept)
    sigma = float(np.sqrt(np.mean(np.square(residuals)) / (1.0 + slope * slope)))
    if sigma > BRIGHTNESS_MAX_LINE_RESIDUAL_PX:
        raise ValueError("zbyt duży rozrzut punktów dopasowanej krawędzi")
    return float(slope), float(intercept), sigma


def _wall_intersection(
    vertical: tuple[float, float, float],
    horizontal: tuple[float, float, float],
) -> tuple[float, float]:
    vertical_slope, vertical_intercept, _ = vertical
    horizontal_slope, horizontal_intercept, _ = horizontal
    denominator = 1.0 - horizontal_slope * vertical_slope
    if abs(denominator) < 1e-8:
        raise ValueError("dopasowane linie są równoległe")
    y = (
        horizontal_slope * vertical_intercept + horizontal_intercept
    ) / denominator
    return vertical_slope * y + vertical_intercept, y


def _measure_opening_brightness(
    frame: np.ndarray, offset_x: int = 0, offset_y: int = 0
) -> BrightnessMeasurement:
    """Measure grayscale in the test box and use the brighter of two references."""
    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("pusta klatka")
    if (
        344 + offset_x < 1
        or 460 + offset_x >= width - 1
        or 678 + offset_y < 1
        or 825 + offset_y >= height - 1
        or 825 + offset_y + 13 >= height - 1
    ):
        raise ValueError("pas wyszukiwania linii poza klatką")

    red_points: list[tuple[int, int]] = []
    red_strengths: list[float] = []
    for y in np.linspace(680 + offset_y, 825 + offset_y, 10).round().astype(int):
        x_candidates = range(346 + offset_x, 369 + offset_x)
        scores = [
            float(
                np.linalg.norm(
                    frame[y, x + 1].astype(np.float32)
                    - frame[y, x - 1].astype(np.float32)
                )
            )
            for x in x_candidates
        ]
        best_x = 346 + offset_x + int(np.argmax(scores))
        red_points.append((best_x, int(y)))
        red_strengths.append(max(scores))

    blue_points: list[tuple[int, int]] = []
    blue_strengths: list[float] = []
    for x in np.linspace(278 + offset_x, 340 + offset_x, 10).round().astype(int):
        y_candidates = range(738 + offset_y, 776 + offset_y)
        scores = [
            float(
                np.linalg.norm(
                    frame[y + 1, x].astype(np.float32)
                    - frame[y - 1, x].astype(np.float32)
                )
            )
            for y in y_candidates
        ]
        best_y = 738 + offset_y + int(np.argmax(scores))
        blue_points.append((int(x), best_y))
        blue_strengths.append(max(scores))

    yellow_points: list[tuple[int, int]] = []
    yellow_strengths: list[float] = []
    yellow_x_values = np.r_[
        np.linspace(362 + offset_x, 386 + offset_x, 5),
        np.linspace(442 + offset_x, 458 + offset_x, 5),
    ].round().astype(int)
    for x in yellow_x_values:
        center_y = 752 + offset_y + 0.55 * (x - (355 + offset_x))
        y_candidates = np.arange(round(center_y - 13), round(center_y + 14))
        scores = [
            float(
                np.linalg.norm(
                    frame[y + 1, x].astype(np.float32)
                    - frame[y - 1, x].astype(np.float32)
                )
            )
            for y in y_candidates
        ]
        best_y = int(y_candidates[int(np.argmax(scores))])
        yellow_points.append((int(x), best_y))
        yellow_strengths.append(max(scores))

    for points in (red_points, blue_points, yellow_points):
        if any(x < 1 or x >= width - 1 or y < 1 or y >= height - 1 for x, y in points):
            raise ValueError("pas wyszukiwania linii poza klatką")
    if any(
        sum(strength >= BRIGHTNESS_MIN_EDGE_GRADIENT for strength in strengths)
        < BRIGHTNESS_MIN_LINE_POINTS
        for strengths in (red_strengths, blue_strengths, yellow_strengths)
    ):
        raise ValueError("zbyt słaby kontrast krawędzi ściany")

    red = _fit_wall_line(red_points, vertical=True)
    blue = _fit_wall_line(blue_points)
    yellow = _fit_wall_line(yellow_points)
    intersections = (
        _wall_intersection(red, blue),
        _wall_intersection(red, yellow),
    )
    cross_x = float(np.mean([point[0] for point in intersections]))
    cross_y = float(np.mean([point[1] for point in intersections]))
    if math.dist(*intersections) > 20.0:
        raise ValueError("przecięcia linii niebieskiej i żółtej są zbyt odległe")
    if not (320 + offset_x <= cross_x <= 400 + offset_x):
        raise ValueError("przecięcie poza zakresem poziomym otworu")
    if not (700 + offset_y <= cross_y <= 850 + offset_y):
        raise ValueError("przecięcie poza zakresem pionowym otworu")

    test_x = round(cross_x - 12)
    test_y = round(cross_y - 19)
    test_x_end = round(cross_x - 2)
    test_y_end = round(cross_y - 2)
    if test_x_end - test_x != 10 or test_y_end - test_y != 17:
        raise ValueError("nieprawidłowy rozmiar pola testowego")
    orange_reference_x = test_x - 2
    reference_y_end = test_y - 5
    orange_reference_y = reference_y_end - 17
    upper_reference_x = orange_reference_x
    upper_reference_y = orange_reference_y - 17
    if (
        orange_reference_x < 0
        or orange_reference_y < 0
        or upper_reference_y < 0
        or test_x < 0
        or test_y < 0
        or test_x_end > width
        or test_y_end > height
        or orange_reference_x + 10 > width
        or reference_y_end > height
        or upper_reference_y + 17 > height
    ):
        raise ValueError("pole jasności wychodzi poza klatkę")

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    test_mean = float(gray[test_y:test_y_end, test_x:test_x_end].mean())
    orange_reference_mean = float(
        gray[
            orange_reference_y:reference_y_end,
            orange_reference_x : orange_reference_x + 10,
        ].mean()
    )
    upper_reference_mean = float(
        gray[
            upper_reference_y : upper_reference_y + 17,
            upper_reference_x : upper_reference_x + 10,
        ].mean()
    )
    if orange_reference_mean >= upper_reference_mean:
        reference_x = orange_reference_x
        reference_y = orange_reference_y
        reference_mean = orange_reference_mean
        reference_source = "lower"
    else:
        reference_x = upper_reference_x
        reference_y = upper_reference_y
        reference_mean = upper_reference_mean
        reference_source = "upper"
    if reference_mean <= 0:
        raise ValueError("jasność pola odniesienia wynosi zero")
    signed_difference = test_mean - reference_mean
    absolute_difference = abs(signed_difference)
    return BrightnessMeasurement(
        cross_x=cross_x,
        cross_y=cross_y,
        test_x=test_x,
        test_y=test_y,
        reference_x=reference_x,
        reference_y=reference_y,
        orange_reference_x=orange_reference_x,
        orange_reference_y=orange_reference_y,
        upper_reference_x=upper_reference_x,
        upper_reference_y=upper_reference_y,
        test_mean=test_mean,
        reference_mean=reference_mean,
        orange_reference_mean=orange_reference_mean,
        upper_reference_mean=upper_reference_mean,
        reference_source=reference_source,
        signed_difference=signed_difference,
        absolute_difference=absolute_difference,
        difference_percent=absolute_difference / reference_mean * 100.0,
    )


def _analyze_brightness_video(
    video_path: Path,
    xlsx_path: Path,
    fps: float,
    frame_count: int,
    recording_start: datetime,
    threshold_percent: float,
    offset_x: int = 0,
    offset_y: int = 0,
    mode: str = "all",
) -> tuple[list[Appearance], int, int]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        xlsx_path.unlink(missing_ok=True)
        raise RuntimeError(f"Nie można otworzyć pliku do pomiaru jasności: {video_path}")

    sample_step_frames = max(1, int(round(fps * BRIGHTNESS_SAMPLE_SECONDS)))
    detector = AppearanceDetector(fps)
    appearances: list[Appearance] = []
    valid_rows = 0
    invalid_rows = 0
    threshold_hits = 0
    headers = [
        "czas_nagrania",
        "czas_s",
        "klatka_video",
        "krzyzyk_x_px",
        "krzyzyk_y_px",
        "test_x_lewy_px",
        "test_y_gorny_px",
        "odniesienie_x_lewy_px",
        "odniesienie_y_gorny_px",
        "jasnosc_testowa_srednia_0_255",
        "jasnosc_odniesienia_srednia_0_255",
        "roznica_test_minus_odniesienie",
        "roznica_bezwzgledna",
        "roznica_procent_wzgledem_odniesienia",
        "sygnal_progu",
        "zdarzenie_potwierdzone",
        "poczatek_klipu",
        "status",
        "odniesienie_pomaranczowe_x_lewy_px",
        "odniesienie_pomaranczowe_y_gorny_px",
        "jasnosc_odniesienia_pomaranczowego_srednia_0_255",
        "odniesienie_gorne_x_lewy_px",
        "odniesienie_gorne_y_gorny_px",
        "jasnosc_odniesienia_gornego_srednia_0_255",
        "odniesienie_uzyte",
    ]
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Pomiary"
    worksheet.freeze_panes = "A2"
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    worksheet.column_dimensions["A"].width = 22
    worksheet.column_dimensions["B"].width = 12
    worksheet.column_dimensions["R"].width = 32
    worksheet.column_dimensions["S"].width = 27
    worksheet.column_dimensions["T"].width = 29
    worksheet.column_dimensions["U"].width = 44
    worksheet.column_dimensions["V"].width = 22
    worksheet.column_dimensions["W"].width = 24
    worksheet.column_dimensions["X"].width = 39
    worksheet.column_dimensions["Y"].width = 18

    try:
        frame_index = 0
        while frame_index < frame_count:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(
                    f"Nie można odczytać klatki {frame_index} z {video_path}"
                )

            timestamp = recording_start + timedelta(seconds=frame_index / fps)
            row: list[object] = [
                timestamp,
                frame_index / fps,
                frame_index,
            ]
            threshold_hit = False
            try:
                measurement = _measure_opening_brightness(
                    frame, offset_x, offset_y
                )
            except ValueError as error:
                invalid_rows += 1
                appearance = detector.update(frame_index, False, 0.0)
                accepted_event = False
                if appearance is not None and (
                    (mode == "all" or not appearances)
                    and (
                        not appearances
                        or appearance.start_frame - appearances[-1].start_frame
                        >= int(round(fps * appearances[-1].post_detection_seconds))
                    )
                ):
                    appearances.append(appearance)
                    accepted_event = True
                row.extend([None] * 11)
                row.extend(
                    [
                        0,
                        int(appearance is not None),
                        int(accepted_event),
                        f"blad_geometrii: {error}",
                    ]
                )
                row.extend([None] * 7)
            else:
                valid_rows += 1
                threshold_hit = measurement.difference_percent >= threshold_percent
                threshold_hits += int(threshold_hit)
                appearance = detector.update(
                    frame_index,
                    threshold_hit,
                    measurement.difference_percent,
                )
                accepted_event = False
                if appearance is not None and (
                    (mode == "all" or not appearances)
                    and (
                        not appearances
                        or appearance.start_frame - appearances[-1].start_frame
                        >= int(round(fps * appearances[-1].post_detection_seconds))
                    )
                ):
                    appearances.append(appearance)
                    accepted_event = True
                row.extend(
                    [
                        round(measurement.cross_x, 3),
                        round(measurement.cross_y, 3),
                        measurement.test_x,
                        measurement.test_y,
                        measurement.reference_x,
                        measurement.reference_y,
                        round(measurement.test_mean, 4),
                        round(measurement.reference_mean, 4),
                        round(measurement.signed_difference, 4),
                        round(measurement.absolute_difference, 4),
                        round(measurement.difference_percent, 4),
                        int(threshold_hit),
                        int(appearance is not None),
                        int(accepted_event),
                        "ok",
                    ]
                )
                row.extend(
                    [
                        measurement.orange_reference_x,
                        measurement.orange_reference_y,
                        round(measurement.orange_reference_mean, 4),
                        measurement.upper_reference_x,
                        measurement.upper_reference_y,
                        round(measurement.upper_reference_mean, 4),
                        (
                            "pomaranczowe"
                            if measurement.reference_source == "lower"
                            else "gorne"
                        ),
                    ]
                )
            worksheet.append(row)

            sample_advance_frames = sample_step_frames
            if appearance is not None:
                sample_advance_frames = max(
                    sample_step_frames,
                    int(round(fps * appearance.post_detection_seconds)),
                )
            frame_index += 1
            frames_to_skip = min(
                sample_advance_frames - 1,
                frame_count - frame_index,
            )
            for _ in range(frames_to_skip):
                if frame_index >= frame_count:
                    break
                if not capture.grab():
                    if frame_index >= frame_count - sample_step_frames:
                        frame_index = frame_count
                        break
                    raise RuntimeError(
                        f"Nie można odczytać klatek przed indeksem {frame_index} "
                        f"z {video_path}"
                    )
                frame_index += 1
        if valid_rows == 0:
            raise RuntimeError(
                f"Nie udało się zmierzyć pola jasności w żadnej klatce: {video_path}"
            )
        for row in worksheet.iter_rows(min_row=2):
            row[1].number_format = "0.000"
            for cell in row[3:5]:
                cell.number_format = "0.000"
            for cell in row[9:14]:
                cell.number_format = "0.0000"
            for cell in (row[20], row[23]):
                cell.number_format = "0.0000"
        worksheet.auto_filter.ref = worksheet.dimensions
        workbook.save(xlsx_path)
    except (KeyboardInterrupt, SystemExit):
        xlsx_path.unlink(missing_ok=True)
        raise
    except Exception:
        xlsx_path.unlink(missing_ok=True)
        raise
    finally:
        capture.release()

    _log(
        f"Jasność test/odniesienie: {xlsx_path}; próbki {valid_rows}, "
        f"błędy geometrii {invalid_rows}, sygnały >= {threshold_percent:.1f}%: "
        f"{threshold_hits}, potwierdzone klipy: {len(appearances)}."
    )
    return appearances, valid_rows, invalid_rows


def _analyze_brightness_only(
    video_path: Path,
    output_path: Optional[Path],
    mode: str,
    threshold_percent: float,
) -> Path:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Nie można otworzyć pliku: {video_path}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        capture.release()
    if fps <= 0 or frame_count <= 0:
        raise RuntimeError("Nie udało się odczytać parametrów nagrania.")

    recording_start = _recording_start_time(video_path, frame_count / fps)
    output_path = _timestamped_output_path(video_path, output_path, recording_start)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    brightness_xlsx_path = _reserve_output_path(
        output_path.with_name(
            f"{video_path.stem}_brightness_{recording_start:%Y%m%d%H%M%S}.xlsx"
        )
    )
    appearances, _, _ = _analyze_brightness_video(
        video_path,
        brightness_xlsx_path,
        fps,
        frame_count,
        recording_start,
        threshold_percent,
        mode=mode,
    )
    clip_paths = _save_appearance_clips(
        video_path,
        output_path,
        appearances,
        fps,
        frame_count,
        recording_start,
    )
    _log(f"Wyniki jasności: {brightness_xlsx_path}")
    if clip_paths:
        _log("Zapisane klipy z progu jasności:")
        for clip_path in clip_paths:
            _log(f"  {clip_path}")
    else:
        _log("Nie wykryto sygnału jasności >= zadanego progu.")
    return output_path


def analyze_video(
    video_path: Path,
    output_path: Optional[Path] = None,
    mode: str = "all",
    brightness_threshold_percent: float = DEFAULT_BRIGHTNESS_THRESHOLD_PERCENT,
) -> Path:
    if mode not in {"first", "all"}:
        raise ValueError("Tryb musi mieć wartość 'first' albo 'all'.")
    if (
        not math.isfinite(brightness_threshold_percent)
        or brightness_threshold_percent < DEFAULT_BRIGHTNESS_THRESHOLD_PERCENT
    ):
        raise ValueError(
            "Próg jasności musi być skończoną wartością co najmniej 22%."
        )
    return _analyze_brightness_only(
        video_path,
        output_path,
        mode,
        brightness_threshold_percent,
    )


def _parse_clock(value: str) -> time:
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, pattern).time()
        except ValueError:
            continue
    raise ValueError(f"Nieprawidłowa godzina '{value}'; użyj formatu GG:MM.")


def select_recordings(
    directory: Path,
    start: Optional[time] = None,
    end: Optional[time] = None,
) -> list[Path]:
    """Wybiera nagrania z katalogu dnia według godziny startu z nazwy pliku.

    Zakres to [start, end): plik zaczynający się o `end` już się nie łapie.
    Brak `start` oznacza początek dnia, brak `end` — koniec dnia.
    """
    selected: list[tuple[time, Path]] = []
    for path in directory.iterdir():
        match = RECORDING_NAME_PATTERN.match(path.name)
        if not match or not path.is_file():
            continue
        try:
            started = time(*(int(part) for part in match.groups()))
        except ValueError:
            continue
        if start is not None and started < start:
            continue
        if end is not None and started >= end:
            continue
        selected.append((started, path))
    return [path for _, path in sorted(selected)]


def _analyzed_list_path(
    video_path: Path, analysis_dir: Path, subdir: str = ANALYSIS_SUBDIR
) -> Path:
    return analysis_dir / video_path.parent.name / subdir / ANALYZED_LIST_NAME


def _load_analyzed(list_path: Path) -> set[str]:
    try:
        lines = list_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    return {line.split("\t")[0] for line in lines if line}


def _mark_analyzed(
    video_path: Path,
    status: str,
    analysis_dir: Path = DEFAULT_ANALYSIS_OUTPUT_DIR,
    subdir: str = ANALYSIS_SUBDIR,
) -> None:
    list_path = _analyzed_list_path(video_path, analysis_dir, subdir)
    list_path.parent.mkdir(parents=True, exist_ok=True)
    with list_path.open("a", encoding="utf-8") as list_file:
        list_file.write(f"{video_path.name}\t{status}\n")


def day_result_path(day_directory: Path) -> Path:
    return day_directory / f"result_{day_directory.parent.name.replace('-', '_')}.mkv"


def _day_clips(day_directory: Path) -> list[Path]:
    """Klipy dnia w kolejności czasu (nazwy zaczynają się od HH-MM-SS)."""
    result = day_result_path(day_directory)
    return sorted(
        path
        for path in day_directory.glob("*.mkv")
        if path != result and not path.name.startswith(".")
    )


def build_day_result(day_directory: Path) -> Optional[Path]:
    """Skleja wszystkie klipy dnia w jeden plik result_RRRR_MM_DD.mkv.

    `day_directory` to katalog typu analysis/RRRR-MM-DD/<subdir>. Zwraca None,
    gdy nie ma klipów. Najpierw kopiuje strumienie, a przy błędzie koduje ponownie.
    """
    clips = _day_clips(day_directory)
    if not clips:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("Nie znaleziono ffmpeg wymaganego do sklejenia klipów.")
    result = day_result_path(day_directory)
    list_path = day_directory / ".result-list.txt"
    temporary = day_directory / ".result-tmp.mkv"
    list_path.write_text(
        "".join(
            "file '" + str(clip.resolve()).replace("'", "'\\''") + "'\n"
            for clip in clips
        ),
        encoding="utf-8",
    )
    try:
        last_error = ""
        for codec_args in (
            ["-c", "copy"],
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac"],
        ):
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-map", "0:v:0", "-map", "0:a?", *codec_args,
                "-y", str(temporary),
            ]
            outcome = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            if outcome.returncode == 0:
                os.replace(temporary, result)
                return result
            last_error = outcome.stderr.strip()
        raise RuntimeError(f"ffmpeg nie skleił klipów do {result}: {last_error}")
    finally:
        temporary.unlink(missing_ok=True)
        list_path.unlink(missing_ok=True)


def update_day_results(
    camera_dir: Path,
    first_date: date,
    last_date: date,
    analysis_dir: Path,
    subdir: str,
    failed: set[tuple[str, float]],
) -> None:
    """Tworzy/odświeża result_* dla każdego dnia, który ma nowsze klipy."""
    for offset in range((last_date - first_date).days + 1):
        current = first_date + timedelta(days=offset)
        day_directory = analysis_dir / current.isoformat() / subdir
        if not day_directory.is_dir():
            continue
        clips = _day_clips(day_directory)
        if not clips:
            continue
        newest = max(clip.stat().st_mtime for clip in clips)
        result = day_result_path(day_directory)
        if result.exists() and result.stat().st_mtime >= newest:
            continue
        key = (str(day_directory), newest)
        if key in failed:
            continue
        try:
            build_day_result(day_directory)
        except (RuntimeError, OSError) as error:
            failed.add(key)
            _log(f"Błąd składania {result.name}: {error}")
        else:
            _log(f"Zapisano {result} ({len(clips)} klipów)")


def next_pending_recording(
    camera_dir: Path,
    first_date: date,
    last_date: date,
    now_timestamp: float,
    start: Optional[time] = None,
    end: Optional[time] = None,
    settle_seconds: float = WATCH_SETTLE_SECONDS,
    analysis_dir: Path = DEFAULT_ANALYSIS_OUTPUT_DIR,
    subdir: str = ANALYSIS_SUBDIR,
    newest_day_first: bool = False,
) -> Optional[Path]:
    """Zwraca najstarsze zakończone, jeszcze nieanalizowane nagranie.

    Plik jest zakończony, gdy istnieje nowszy plik z tego dnia (rejestrator
    zamknął poprzedni segment) albo gdy nie był zapisywany od `settle_seconds`
    (nagrywanie zatrzymane, np. wieczorem przez astro).
    """
    day_count = (last_date - first_date).days + 1
    days = [first_date + timedelta(days=offset) for offset in range(day_count)]
    if newest_day_first:
        days.reverse()
    for current in days:
        directory = camera_dir / current.isoformat()
        if not directory.is_dir():
            continue
        everything = select_recordings(directory)
        if not everything:
            continue
        analyzed = _load_analyzed(
            _analyzed_list_path(everything[0], analysis_dir, subdir)
        )
        wanted = set(select_recordings(directory, start, end))
        for path in everything:
            if path not in wanted or path.name in analyzed:
                continue
            try:
                idle = now_timestamp - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if path != everything[-1] or idle >= settle_seconds:
                return path
    return None


def watch_recordings(
    camera_dir: Path,
    analyze: Callable[[Path], None],
    start: Optional[time] = None,
    end: Optional[time] = None,
    poll_seconds: float = WATCH_POLL_SECONDS,
    settle_seconds: float = WATCH_SETTLE_SECONDS,
    analysis_dir: Path = DEFAULT_ANALYSIS_OUTPUT_DIR,
    clock: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = _time.sleep,
    subdir: str = ANALYSIS_SUBDIR,
    first_date: Optional[date] = None,
    newest_day_first: bool = False,
) -> None:
    """Analizuje kolejno każde nowe nagranie zaraz po jego zakończeniu.

    Śledzi dni od uruchomienia do bieżącego, więc zaległości po północy też
    zostaną nadrobione. Błąd jednego pliku nie zatrzymuje kolejki.
    """
    first_date = first_date or clock().date()
    failed_results: set[tuple[str, float]] = set()
    while True:
        now = clock()
        video = next_pending_recording(
            camera_dir,
            first_date,
            now.date(),
            now.timestamp(),
            start,
            end,
            settle_seconds,
            analysis_dir,
            subdir,
            newest_day_first,
        )
        update_day_results(
            camera_dir, first_date, now.date(), analysis_dir, subdir, failed_results
        )
        if video is None:
            sleep(poll_seconds)
            continue
        _log(f"=== {video.parent.name}/{video.name} ===")
        try:
            analyze(video)
        except (RuntimeError, OSError, ValueError) as error:
            _log(f"Błąd analizy {video.name}: {error}")
            _mark_analyzed(video, "blad", analysis_dir, subdir)
        else:
            _mark_analyzed(video, "ok", analysis_dir, subdir)
        update_day_results(
            camera_dir, first_date, clock().date(), analysis_dir, subdir, failed_results
        )


def _terminate(_signum: int, _frame: object) -> None:
    raise SystemExit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pomiar jasności przy otworze i zapis klipów."
    )
    parser.add_argument(
        "video",
        type=Path,
        nargs="?",
        help="Ścieżka do pojedynczego pliku wideo (zamiast --date)",
    )
    parser.add_argument(
        "--date",
        help="Data RRRR-MM-DD: analizuj nagrania z katalogu tego dnia",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Tryb ciągły: analizuj każde nagranie zaraz po jego zakończeniu "
        "(od --from-date lub dziś), także zaległe; kończy się sygnałem "
        "SIGTERM/Ctrl+C",
    )
    parser.add_argument(
        "--from-date",
        help="Z --watch: data RRRR-MM-DD, od której nadrabiać zaległe nagrania "
        "(domyślnie dziś)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=WATCH_POLL_SECONDS,
        help=f"Z --watch: co ile sekund szukać nowych plików (domyślnie "
        f"{WATCH_POLL_SECONDS:g})",
    )
    parser.add_argument(
        "--nice",
        type=int,
        default=10,
        help="Z --watch: obniżenie priorytetu procesu, by nie dławić rejestratora "
        "(domyślnie 10)",
    )
    parser.add_argument(
        "--hours",
        nargs="+",
        metavar="GG:MM",
        help="Z --date lub --watch: jedna godzina = od niej do końca dnia, dwie = od-do "
        "(według godziny startu z nazwy pliku; koniec wyłączony)",
    )
    parser.add_argument(
        "--camera",
        default=DEFAULT_CAMERA,
        help=f"Z --date: nazwa kamery (domyślnie {DEFAULT_CAMERA})",
    )
    parser.add_argument(
        "--recordings-root",
        type=Path,
        default=DEFAULT_RECORDINGS_ROOT,
        help=f"Z --date: katalog nagrań (domyślnie {DEFAULT_RECORDINGS_ROOT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Opcjonalna ścieżka bazowa wyników; domyślnie katalog analizy",
    )
    parser.add_argument(
        "--mode",
        choices=("first", "all"),
        default="all",
        help="all: zapisz wszystkie wykryte zdarzenia (domyślnie); "
        "first: zapisz najwyżej pierwszy klip; oba tryby analizują cały film",
    )
    parser.add_argument(
        "--brightness-threshold-percent",
        type=float,
        default=DEFAULT_BRIGHTNESS_THRESHOLD_PERCENT,
        help="Próg bezwzględnej różnicy jasności test–odniesienie w procentach "
        f"(co najmniej {DEFAULT_BRIGHTNESS_THRESHOLD_PERCENT:g}; domyślnie "
        f"{DEFAULT_BRIGHTNESS_THRESHOLD_PERCENT:g})",
    )
    args = parser.parse_args()

    if args.watch:
        if args.video is not None or args.date is not None:
            parser.error("--watch nie łączy się z plikiem ani z --date.")
    elif (args.video is None) == (args.date is None):
        parser.error("Podaj plik wideo, --date albo --watch.")
    if args.from_date is not None and not args.watch:
        parser.error("--from-date wymaga --watch.")
    if args.hours and args.date is None and not args.watch:
        parser.error("--hours wymaga --date lub --watch.")
    if args.hours and len(args.hours) > 2:
        parser.error("--hours przyjmuje najwyżej dwie godziny (od i do).")

    try:
        if args.date is not None:
            datetime.strptime(args.date, "%Y-%m-%d")
        first_date = (
            datetime.strptime(args.from_date, "%Y-%m-%d").date()
            if args.from_date is not None
            else None
        )
        hours = [_parse_clock(value) for value in args.hours or []]
    except ValueError as error:
        parser.error(str(error))
    start = hours[0] if hours else None
    end = hours[1] if len(hours) > 1 else None
    if start and end and end <= start:
        parser.error("Druga godzina musi być późniejsza niż pierwsza.")

    if args.watch:
        camera_dir = args.recordings_root / args.camera
        if args.nice:
            os.nice(args.nice)
        signal.signal(signal.SIGTERM, _terminate)
        _log(
            f"Obserwuję {camera_dir}; analiza każdego zakończonego nagrania.",
            flush=True,
        )

        def analyze_watched(video: Path) -> None:
            analyze_video(
                video,
                args.output,
                mode=args.mode,
                brightness_threshold_percent=args.brightness_threshold_percent,
            )

        from mcrwac.notify import build_service_notifier

        notifier = build_service_notifier(f"{args.camera} wiewiorki")
        notifier.event("start analizy", title="Start", tags="red_circle")
        stop_reason = "zatrzymanie uslugi"
        try:
            watch_recordings(
                camera_dir,
                analyze_watched,
                start,
                end,
                poll_seconds=args.poll_seconds,
                first_date=first_date,
            )
        except KeyboardInterrupt:
            _log("Przerwano.")
        except SystemExit:
            raise
        except BaseException as error:
            stop_reason = f"blad: {type(error).__name__}: {error}"
            raise
        finally:
            notifier.event(
                f"stop analizy ({stop_reason})",
                title="Stop",
                tags="black_square_for_stop",
            )
            notifier.close()
        return

    if args.video is not None:
        videos = [args.video]
    else:
        directory = args.recordings_root / args.camera / args.date
        if not directory.is_dir():
            parser.error(f"Brak katalogu nagrań: {directory}")
        videos = select_recordings(directory, start, end)
        if not videos:
            _log("Brak nagrań w podanym zakresie.")
            return
        _log(f"Do analizy: {len(videos)} plików.")

    failures = 0
    for index, video in enumerate(videos, start=1):
        if len(videos) > 1:
            _log(f"=== [{index}/{len(videos)}] {video.name} ===")
        try:
            analyze_video(
                video,
                args.output,
                mode=args.mode,
                brightness_threshold_percent=args.brightness_threshold_percent,
            )
            if args.output is None and RECORDING_NAME_PATTERN.match(video.name):
                _mark_analyzed(video, "ok")
        except (RuntimeError, OSError, ValueError) as error:
            if len(videos) == 1:
                raise
            failures += 1
            _log(f"Błąd analizy {video.name}: {error}")
    if failures:
        _log(f"Pliki z błędem: {failures}/{len(videos)}.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
