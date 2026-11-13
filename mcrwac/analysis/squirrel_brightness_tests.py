import argparse
import json
import math
import signal
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Optional, Sequence, Union

import cv2
from openpyxl import Workbook, load_workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Font

from mcrwac.analysis.squirrel_exit_prototype import (
    ANALYZED_LIST_NAME,
    ANALYSIS_SUBDIR,
    Appearance,
    AppearanceDetector,
    BrightnessMeasurement,
    CLIP_PREROLL_SECONDS,
    DEFAULT_ANALYSIS_OUTPUT_DIR,
    DEFAULT_CAMERA,
    DEFAULT_RECORDINGS_ROOT,
    OpeningGeometry,
    WATCH_POLL_SECONDS,
    _log,
    _detect_opening_geometry,
    _mark_analyzed,
    _measure_opening_brightness,
    _measure_opening_brightness_with_fallback,
    _opening_geometry_from_measurement,
    _recording_start_time,
    _reserve_output_path,
    _save_appearance_clips,
    build_day_result,
    watch_recordings,
    RECORDING_NAME_PATTERN,
)

BRIGHTNESS_CHANGE_THRESHOLD = 0.030
BRIGHTNESS_SAMPLE_SECONDS = 1.0
DEFAULT_CHANGE_OFFSETS = (3,)
DAY_GEOMETRY_FILENAME = "opening_geometry.json"
DEFAULT_CLIP_DURATION_SECONDS = 3.0
CLIP_TIMING_BY_N = {
    1: (2.0, 5.0),
    4: (6.0, 7.0),
}
ThresholdValues = Union[float, Sequence[float]]


class _NoOpeningGeometryError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrightnessSample:
    frame_index: int
    seconds: float
    timestamp: datetime
    measurement: Optional[BrightnessMeasurement]
    status: str


def _day_geometry_path(output_dir: Path) -> Path:
    return output_dir / DAY_GEOMETRY_FILENAME


def _load_day_geometry(output_dir: Path) -> Optional[OpeningGeometry]:
    path = _day_geometry_path(output_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("plik geometrii nie zawiera obiektu JSON")
        geometry = OpeningGeometry(**payload)
        coordinates = (
            geometry.cross_x,
            geometry.cross_y,
            geometry.test_x,
            geometry.test_y,
            geometry.orange_reference_x,
            geometry.orange_reference_y,
            geometry.upper_reference_x,
            geometry.upper_reference_y,
        )
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("geometria zawiera niefinitywne współrzędne")
        return geometry
    except FileNotFoundError:
        return None
    except (OSError, TypeError, ValueError) as error:
        _log(f"Nie można użyć zapisanej geometrii {path}: {error}; wyznaczę ją ponownie.")
        return None


def _save_day_geometry(output_dir: Path, geometry: OpeningGeometry) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _day_geometry_path(output_dir)
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(asdict(geometry), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def clean_recording_outputs(video_path: Path, output_dir: Path) -> list[Path]:
    if not RECORDING_NAME_PATTERN.fullmatch(video_path.name):
        raise ValueError(
            "Nazwa nagrania musi mieć format GG-MM-SS.mkv, aby bezpiecznie "
            "usunąć jego poprzednie wyniki."
        )

    removed: list[Path] = []
    for path in output_dir.glob(f"{video_path.stem}_*"):
        if path.is_file() and path.suffix.lower() in {".mkv", ".mp4", ".xlsx"}:
            path.unlink()
            removed.append(path)
    return removed


def _find_first_opening_geometry(video_path: Path) -> Optional[OpeningGeometry]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"Nie można otworzyć pliku do wyznaczenia linii: {video_path}")

    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
            raise RuntimeError("Nie udało się odczytać parametrów nagrania.")
        sample_step_frames = max(1, int(round(fps * BRIGHTNESS_SAMPLE_SECONDS)))
        frame_index = 0
        while frame_index < frame_count:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(
                    f"Nie można odczytać klatki {frame_index} z {video_path}"
                )
            try:
                geometry = _detect_opening_geometry(frame)
            except ValueError:
                geometry = None
            if geometry is not None:
                _log(
                    f"Pierwsza poprawna geometria w {video_path.name}, "
                    f"około {frame_index / fps:.0f} s."
                )
                return geometry

            frame_index += 1
            frames_to_skip = min(
                sample_step_frames - 1,
                frame_count - frame_index,
            )
            for _ in range(frames_to_skip):
                if not capture.grab():
                    if frame_index >= frame_count - sample_step_frames:
                        frame_index = frame_count
                        break
                    raise RuntimeError(
                        f"Nie można odczytać klatek przed indeksem {frame_index} "
                        f"z {video_path}"
                    )
                frame_index += 1
        return None
    finally:
        capture.release()


def _excel_brightness_values(
    samples: Sequence[BrightnessSample],
) -> list[Optional[float]]:
    return [
        round(sample.measurement.test_mean, 4)
        if sample.measurement is not None
        else None
        for sample in samples
    ]


def _representative_geometry(
    samples: Sequence[BrightnessSample],
) -> Optional[OpeningGeometry]:
    measurements = [
        sample.measurement
        for sample in samples
        if sample.status == "ok" and sample.measurement is not None
    ]
    if not measurements:
        return None

    median_x = median(measurement.cross_x for measurement in measurements)
    median_y = median(measurement.cross_y for measurement in measurements)
    representative = min(
        measurements,
        key=lambda measurement: (measurement.cross_x - median_x) ** 2
        + (measurement.cross_y - median_y) ** 2,
    )
    return _opening_geometry_from_measurement(representative)


def _read_brightness_samples(
    video_path: Path,
    initial_geometry: Optional[OpeningGeometry] = None,
) -> tuple[list[BrightnessSample], float, int, datetime]:
    fixed_geometry = initial_geometry
    if initial_geometry is None:
        initial_geometry = _find_first_opening_geometry(video_path)
    if initial_geometry is None:
        raise _NoOpeningGeometryError(
            f"Nie udało się wyznaczyć geometrii otworu w żadnej klatce: {video_path}"
        )

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Nie można otworzyć pliku: {video_path}")

    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
            raise RuntimeError("Nie udało się odczytać parametrów nagrania.")
        recording_start = _recording_start_time(video_path, frame_count / fps)
        sample_step_frames = max(1, int(round(fps * BRIGHTNESS_SAMPLE_SECONDS)))
        total_samples = (frame_count + sample_step_frames - 1) // sample_step_frames
        samples: list[BrightnessSample] = []
        last_valid_geometry: Optional[OpeningGeometry] = initial_geometry
        frame_index = 0
        reported_progress = 0

        while frame_index < frame_count:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(
                    f"Nie można odczytać klatki {frame_index} z {video_path}"
                )

            seconds = frame_index / fps
            if fixed_geometry is not None:
                try:
                    measurement = _measure_opening_brightness(
                        frame, geometry=fixed_geometry
                    )
                except ValueError as error:
                    measurement = None
                    status = f"blad_geometrii_dziennej: {error}"
                else:
                    status = "geometria_dzienna"
            else:
                measurement, detected_geometry, status = (
                    _measure_opening_brightness_with_fallback(
                        frame, last_valid_geometry
                    )
                )
                if detected_geometry is not None:
                    last_valid_geometry = detected_geometry
            samples.append(
                BrightnessSample(
                    frame_index=frame_index,
                    seconds=seconds,
                    timestamp=recording_start + timedelta(seconds=seconds),
                    measurement=measurement,
                    status=status,
                )
            )

            frame_index += 1
            frames_to_skip = min(
                sample_step_frames - 1,
                frame_count - frame_index,
            )
            for _ in range(frames_to_skip):
                if not capture.grab():
                    if frame_index >= frame_count - sample_step_frames:
                        frame_index = frame_count
                        break
                    raise RuntimeError(
                        f"Nie można odczytać klatek przed indeksem {frame_index} "
                        f"z {video_path}"
                    )
                frame_index += 1
            progress = min(100, int(frame_index * 100 / frame_count))
            if progress >= reported_progress + 10 or frame_index >= frame_count:
                _log(
                    f"TestZmianyJasnosci: próbkowanie {progress}% "
                    f"({len(samples)}/{total_samples} próbek)"
                )
                reported_progress = progress

        if not any(sample.measurement is not None for sample in samples):
            raise RuntimeError(
                f"Nie udało się zmierzyć pola jasności w żadnej klatce: {video_path}"
            )
        return samples, fps, frame_count, recording_start
    finally:
        capture.release()


def _calculate_brightness_changes(
    brightness_values: Sequence[Optional[float]], n: int
) -> list[Optional[float]]:
    if n < 1:
        raise ValueError("N musi być co najmniej 1.")

    changes: list[Optional[float]] = [None] * len(brightness_values)
    for current_index in range(n, len(brightness_values)):
        base = brightness_values[current_index - n]
        current = brightness_values[current_index]
        if base is None or current is None or base == 0:
            continue
        relative_change = (current - base) / base
        changes[current_index] = relative_change * relative_change
    return changes


def _find_change_appearances(
    samples: Sequence[BrightnessSample],
    changes: Sequence[Optional[float]],
    fps: float,
    threshold: float = BRIGHTNESS_CHANGE_THRESHOLD,
    *,
    pre_detection_seconds: float = CLIP_PREROLL_SECONDS,
    clip_duration_seconds: float = DEFAULT_CLIP_DURATION_SECONDS,
) -> tuple[list[Appearance], set[int], set[int]]:
    detector = AppearanceDetector(fps)
    appearances: list[Appearance] = []
    confirmed_frames: set[int] = set()
    clip_start_frames: set[int] = set()

    for sample, change in zip(samples, changes):
        signal = change is not None and change >= threshold
        detected_appearance = detector.update(
            sample.frame_index,
            signal,
            change * 100.0 if change is not None else 0.0,
        )
        if detected_appearance is None:
            continue
        appearance = Appearance(
            start_frame=detected_appearance.start_frame,
            detection_percent=detected_appearance.detection_percent,
            pre_detection_seconds=pre_detection_seconds,
            clip_duration_seconds=clip_duration_seconds,
        )

        confirmed_frames.add(sample.frame_index)
        if not appearances or (
            appearance.start_frame - appearances[-1].start_frame
            >= int(round(fps * appearances[-1].post_detection_seconds))
        ):
            appearances.append(appearance)
            clip_start_frames.add(sample.frame_index)

    return appearances, confirmed_frames, clip_start_frames


def _merge_overlapping_appearances(
    appearances: Sequence[Appearance], fps: float
) -> tuple[list[Appearance], dict[int, float], dict[int, float]]:
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Liczba klatek na sekundę musi być dodatnia.")

    ordered = sorted(
        appearances,
        key=lambda appearance: (
            appearance.start_frame,
            -appearance.detection_percent,
        ),
    )
    merged: list[Appearance] = []
    clip_start_seconds: dict[int, float] = {}
    clip_end_seconds: dict[int, float] = {}

    for appearance in ordered:
        detection_seconds = appearance.start_frame / fps
        current_start = max(
            0.0, detection_seconds - appearance.pre_detection_seconds
        )
        clip_end = detection_seconds + appearance.post_detection_seconds
        if merged:
            previous = merged[-1]
            previous_end = clip_end_seconds[previous.start_frame]
            if current_start <= previous_end:
                clip_start_seconds[previous.start_frame] = min(
                    clip_start_seconds[previous.start_frame], current_start
                )
                clip_end_seconds[previous.start_frame] = max(previous_end, clip_end)
                continue

        merged.append(appearance)
        clip_start_seconds[appearance.start_frame] = current_start
        clip_end_seconds[appearance.start_frame] = clip_end

    return merged, clip_start_seconds, clip_end_seconds


def _thresholds_by_n(
    n_values: Sequence[int], threshold: ThresholdValues
) -> dict[int, float]:
    if isinstance(threshold, (int, float)):
        values = [float(threshold)] * len(n_values)
    else:
        values = list(threshold)
        if len(values) == 1 and len(n_values) > 1:
            values *= len(n_values)
        elif len(values) != len(n_values):
            raise ValueError(
                "Podaj jeden próg wspólny dla wszystkich N albo po jednym "
                "progu w kolejności odpowiadającej wartościom N."
            )
        try:
            values = [float(value) for value in values]
        except (TypeError, ValueError) as error:
            raise ValueError("Każdy próg P musi być liczbą.") from error
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("Każdy próg P musi być skończony i nieujemny.")
    return dict(zip(n_values, values))


def _write_change_report(
    video_path: Path,
    output_dir: Path,
    n: int,
    samples: Sequence[BrightnessSample],
    changes: Sequence[Optional[float]],
    confirmed_frames: set[int],
    clip_start_frames: set[int],
    recording_start: datetime,
    threshold: float = BRIGHTNESS_CHANGE_THRESHOLD,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = _reserve_output_path(
        output_dir
        / (
            f"{video_path.stem}_brightness_TestZmianyJasnosci_N{n}_"
            f"{recording_start:%Y%m%d%H%M%S}.xlsx"
        )
    )
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Pomiary"
    worksheet.freeze_panes = "A2"
    headers = [
        "czas_nagrania",
        "czas_s",
        "klatka_video",
        "krzyzyk_x_px",
        "krzyzyk_y_px",
        "test_x_lewy_px",
        "test_y_gorny_px",
        "jasnosc_testowa_srednia_0_255",
        "jasnosc_testowa_srednia_0_255_i",
        "N_probki_1s",
        "ZmianaJasnosci",
        f"sygnal_progu_{threshold:.3f}",
        "zdarzenie_potwierdzone",
        "poczatek_klipu",
        "status_pomiaru",
        "status_porownania",
    ]
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    brightness_values = _excel_brightness_values(samples)
    for index, (sample, change) in enumerate(zip(samples, changes)):
        measurement = sample.measurement
        base_index = index - n
        base_value = brightness_values[base_index] if base_index >= 0 else None
        if base_index < 0:
            comparison_status = "brak_próbki_bazowej"
        elif brightness_values[index] is None or base_value is None:
            comparison_status = "brak_jasności"
        elif base_value == 0:
            comparison_status = "jasność_bazowa_zero"
        else:
            comparison_status = "ok"
        signal = change is not None and change >= threshold
        worksheet.append(
            [
                sample.timestamp,
                sample.seconds,
                sample.frame_index,
                round(measurement.cross_x, 3) if measurement else None,
                round(measurement.cross_y, 3) if measurement else None,
                measurement.test_x if measurement else None,
                measurement.test_y if measurement else None,
                round(measurement.test_mean, 4) if measurement else None,
                round(base_value, 4) if base_value is not None else None,
                n,
                round(change, 8) if change is not None else None,
                int(signal),
                int(sample.frame_index in confirmed_frames),
                int(sample.frame_index in clip_start_frames),
                sample.status,
                comparison_status,
            ]
        )

    for row in worksheet.iter_rows(min_row=2):
        row[0].number_format = "yyyy-mm-dd hh:mm:ss"
        row[1].number_format = "0.000"
        row[7].number_format = "0.0000"
        row[8].number_format = "0.0000"
        row[10].number_format = "0.00000000"
    widths = {
        "A": 22,
        "B": 12,
        "D": 16,
        "E": 16,
        "H": 36,
        "I": 39,
        "J": 14,
        "K": 20,
        "O": 42,
        "P": 28,
    }
    for column, width in widths.items():
        worksheet.column_dimensions[column].width = width
    worksheet.auto_filter.ref = worksheet.dimensions

    chart = LineChart()
    chart_threshold = f"{threshold:.3f}".replace(".", ",")
    chart.title = f"ZmianaJasnosci w czasie (N={n}, próg {chart_threshold})"
    chart.x_axis.title = "Czas od początku nagrania [s]"
    chart.y_axis.title = "ZmianaJasnosci"
    chart.height = 8
    chart.width = 22
    chart.add_data(
        Reference(
            worksheet,
            min_col=11,
            min_row=1,
            max_row=worksheet.max_row,
        ),
        titles_from_data=True,
    )
    chart.set_categories(
        Reference(
            worksheet,
            min_col=2,
            min_row=2,
            max_row=worksheet.max_row,
        )
    )
    chart_sheet = workbook.create_sheet("Wykres", 0)
    chart_sheet.sheet_view.showGridLines = False
    chart_sheet.add_chart(chart, "A1")
    workbook.active = workbook.index(chart_sheet)

    try:
        workbook.save(xlsx_path)
    except Exception:
        xlsx_path.unlink(missing_ok=True)
        raise
    return xlsx_path


def run_brightness_tests(
    video_path: Path,
    n_values: Sequence[int] = DEFAULT_CHANGE_OFFSETS,
    output_root: Optional[Path] = None,
    *,
    per_recording_directory: bool = True,
    unique_run_directory: bool = True,
    output_in_root: bool = False,
    threshold: ThresholdValues = BRIGHTNESS_CHANGE_THRESHOLD,
) -> dict[str, Optional[Path]]:
    if not n_values or any(n < 1 for n in n_values):
        raise ValueError("Każde N musi być dodatnią liczbą całkowitą.")
    if len(set(n_values)) != len(n_values):
        raise ValueError("Wartości N nie mogą się powtarzać.")
    thresholds = _thresholds_by_n(n_values, threshold)

    geometry_output_dir = (
        output_root
        if output_root is not None and output_root.name == ANALYSIS_SUBDIR
        else None
    )
    initial_geometry = (
        _load_day_geometry(geometry_output_dir)
        if geometry_output_dir is not None
        else None
    )
    if initial_geometry is not None:
        _log(
            f"Używam stałej geometrii dnia: krzyżyk="
            f"({initial_geometry.cross_x:.3f}, {initial_geometry.cross_y:.3f})."
        )
    samples, fps, frame_count, recording_start = _read_brightness_samples(
        video_path, initial_geometry
    )
    clip_geometry = initial_geometry or _representative_geometry(samples)
    if geometry_output_dir is not None and initial_geometry is None:
        if clip_geometry is not None:
            _save_day_geometry(geometry_output_dir, clip_geometry)
            valid_measurements = sum(
                sample.status == "ok" and sample.measurement is not None
                for sample in samples
            )
            _log(
                f"Geometria dnia ustalona z {valid_measurements} pomiarów "
                f"poprawnych: krzyżyk=({clip_geometry.cross_x:.3f}, "
                f"{clip_geometry.cross_y:.3f})."
            )
    day_output_dir = output_root or (
        DEFAULT_ANALYSIS_OUTPUT_DIR / recording_start.strftime("%Y-%m-%d")
    )

    results: dict[str, Optional[Path]] = {}
    test_directories: dict[str, Path] = {}
    detected_appearances: list[Appearance] = []
    run_id = (
        datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        if unique_run_directory
        else ""
    )
    brightness_values = _excel_brightness_values(samples)
    for n in n_values:
        threshold_for_n = thresholds[n]
        pre_detection_seconds, clip_duration_seconds = CLIP_TIMING_BY_N.get(
            n, (CLIP_PREROLL_SECONDS, DEFAULT_CLIP_DURATION_SECONDS)
        )
        changes = _calculate_brightness_changes(brightness_values, n)
        appearances, confirmed_frames, clip_start_frames = _find_change_appearances(
            samples,
            changes,
            fps,
            threshold_for_n,
            pre_detection_seconds=pre_detection_seconds,
            clip_duration_seconds=clip_duration_seconds,
        )
        detected_appearances.extend(appearances)
        if output_in_root:
            test_name = f"{day_output_dir.name}_N{n}"
            test_dir = day_output_dir
        else:
            name_parts = [f"TestZmianyJasnosci_N{n}"]
            if per_recording_directory:
                name_parts.append(video_path.stem)
            if unique_run_directory:
                name_parts.append(run_id)
            test_name = "_".join(name_parts)
            test_dir = day_output_dir / test_name
        xlsx_path = _write_change_report(
            video_path,
            test_dir,
            n,
            samples,
            changes,
            confirmed_frames,
            clip_start_frames,
            recording_start,
            threshold_for_n,
        )
        _log(
            f"{test_name}: próbki {len(samples)}, "
            f"sygnały >= {threshold_for_n:.3f}: "
            f"{sum(change is not None and change >= threshold_for_n for change in changes)}, "
            f"wykryte zdarzenia: {len(appearances)}; raport {xlsx_path}"
        )
        test_directories[test_name] = test_dir

    merged_appearances, clip_start_seconds, clip_end_seconds = (
        _merge_overlapping_appearances(detected_appearances, fps)
    )
    _log(
        f"Po połączeniu N={','.join(map(str, n_values))}: "
        f"{len(detected_appearances)} wykryć, "
        f"{len(merged_appearances)} klipów po usunięciu nakładających się."
    )
    if merged_appearances:
        if clip_geometry is None:
            raise RuntimeError("Brak geometrii do oznaczenia wykrytych klipów.")
        clip_output_dir = next(iter(test_directories.values()))
        _save_appearance_clips(
            video_path,
            clip_output_dir / f"{video_path.stem}.mp4",
            merged_appearances,
            fps,
            frame_count,
            recording_start,
            overlay_geometries={
                appearance.start_frame: clip_geometry
                for appearance in merged_appearances
            },
            clip_start_seconds=clip_start_seconds,
            clip_end_seconds=clip_end_seconds,
        )

    directory_results = {
        test_dir: build_day_result(test_dir)
        for test_dir in dict.fromkeys(test_directories.values())
    }
    results = {
        test_name: directory_results[test_dir]
        for test_name, test_dir in test_directories.items()
    }
    return results


def _analyze_watched_recording(
    video_path: Path,
    n_values: Sequence[int],
    analysis_root: Path,
    threshold: ThresholdValues = BRIGHTNESS_CHANGE_THRESHOLD,
) -> dict[str, Optional[Path]]:
    return run_brightness_tests(
        video_path,
        n_values,
        analysis_root / video_path.parent.name / ANALYSIS_SUBDIR,
        per_recording_directory=False,
        unique_run_directory=False,
        output_in_root=True,
        threshold=threshold,
    )


def _retry_failed_recordings_before(
    video_path: Path,
    n_values: Sequence[int],
    analysis_root: Path,
    threshold: ThresholdValues = BRIGHTNESS_CHANGE_THRESHOLD,
) -> None:
    output_dir = analysis_root / video_path.parent.name / ANALYSIS_SUBDIR
    if _load_day_geometry(output_dir) is None:
        return

    analyzed_path = output_dir / ANALYZED_LIST_NAME
    try:
        lines = analyzed_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    statuses: dict[str, str] = {}
    for line in lines:
        name, separator, status = line.partition("\t")
        if separator:
            statuses[name] = status

    def report_has_geometry_errors(video_name: str) -> bool:
        reports = list(
            output_dir.glob(
                f"{Path(video_name).stem}_brightness_TestZmianyJasnosci_"
                f"N{n_values[0]}_*.xlsx"
            )
        )
        if not reports:
            return False
        report_path = max(reports, key=lambda path: path.stat().st_mtime)
        try:
            workbook = load_workbook(report_path, read_only=True, data_only=True)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            _log(f"Nie można odczytać raportu {report_path}: {error}; ponowię analizę.")
            return True
        try:
            worksheet = workbook["Pomiary"]
            headers = next(
                worksheet.iter_rows(min_row=1, max_row=1, values_only=True)
            )
            status_column = headers.index("status_pomiaru")
            return any(
                isinstance(row[status_column], str)
                and row[status_column].startswith("blad_geometrii")
                for row in worksheet.iter_rows(min_row=2, values_only=True)
            )
        except (KeyError, StopIteration, ValueError) as error:
            _log(f"Nie można sprawdzić geometrii w raporcie {report_path}: {error}")
            return False
        finally:
            workbook.close()

    failed_recordings = [
        video_path.parent / name
        for name, status in statuses.items()
        if name < video_path.name
        and RECORDING_NAME_PATTERN.fullmatch(name)
        and (video_path.parent / name).is_file()
        and (status == "blad" or report_has_geometry_errors(name))
    ]
    for failed_video in sorted(failed_recordings):
        _log(
            f"Ponawiam {failed_video.name} z geometrią wyznaczoną później tego dnia."
        )
        try:
            _analyze_watched_recording(
                failed_video, n_values, analysis_root, threshold
            )
        except (OSError, RuntimeError, ValueError) as error:
            _log(f"Ponowna analiza {failed_video.name} nie powiodła się: {error}")
            _mark_analyzed(failed_video, "blad", analysis_root)
        else:
            _mark_analyzed(failed_video, "ok", analysis_root)


def _terminate(_signum: int, _frame: object) -> None:
    raise SystemExit(0)


def run_brightness_watcher(
    n_values: Sequence[int] = DEFAULT_CHANGE_OFFSETS,
    analysis_root: Path = DEFAULT_ANALYSIS_OUTPUT_DIR,
    camera: str = DEFAULT_CAMERA,
    recordings_root: Path = DEFAULT_RECORDINGS_ROOT,
    first_date: Optional[date] = None,
    poll_seconds: float = WATCH_POLL_SECONDS,
    threshold: ThresholdValues = BRIGHTNESS_CHANGE_THRESHOLD,
) -> None:
    if not n_values or any(n < 1 for n in n_values):
        raise ValueError("Watcher wymaga co najmniej jednego dodatniego wariantu N.")
    if len(set(n_values)) != len(n_values):
        raise ValueError("Wartości N nie mogą się powtarzać.")
    if not math.isfinite(poll_seconds) or poll_seconds <= 0:
        raise ValueError("Częstotliwość sprawdzania kolejki musi być dodatnia.")
    thresholds = _thresholds_by_n(n_values, threshold)

    camera_dir = recordings_root / camera
    signal.signal(signal.SIGTERM, _terminate)
    _log(
        f"Obserwuję {camera_dir}; TestZmianyJasnosci N={','.join(map(str, n_values))}, "
        "progi "
        + ", ".join(
            f"N={n}: {value:.3f}" for n, value in thresholds.items()
        )
        + "; "
        f"od {first_date.isoformat() if first_date else 'dzisiaj'}."
    )

    from mcrwac.notify import build_service_notifier

    notifier = build_service_notifier(f"{camera} wiewiorki")
    notifier.event("start analizy", title="Start", tags="red_circle")
    stop_reason = "zatrzymanie usługi"
    geometry_backfill_checked: set[str] = set()

    def analyze_watched(video: Path) -> None:
        _analyze_watched_recording(video, n_values, analysis_root, threshold)
        day = video.parent.name
        if day in geometry_backfill_checked:
            return
        output_dir = analysis_root / day / ANALYSIS_SUBDIR
        if _load_day_geometry(output_dir) is not None:
            geometry_backfill_checked.add(day)
            _retry_failed_recordings_before(
                video, n_values, analysis_root, threshold
            )

    try:
        watch_recordings(
            camera_dir,
            analyze_watched,
            poll_seconds=poll_seconds,
            analysis_dir=analysis_root,
            subdir=ANALYSIS_SUBDIR,
            first_date=first_date,
        )
    except KeyboardInterrupt:
        _log("Przerwano.")
    except SystemExit:
        raise
    except BaseException as error:
        stop_reason = f"błąd: {type(error).__name__}: {error}"
        raise
    finally:
        notifier.event(
            f"stop analizy ({stop_reason})",
            title="Stop",
            tags="black_square_for_stop",
        )
        notifier.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Testy zmiany jasności i watcher analizy wiewiórek."
    )
    parser.add_argument(
        "video",
        type=Path,
        nargs="?",
        help="Ścieżka do pojedynczego nagrania (zamiast --watch).",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Uruchamia ciągły watcher zakończonych nagrań.",
    )
    parser.add_argument(
        "--from-date",
        help="Z --watch: nadrabiaj nagrania od tej daty (RRRR-MM-DD; domyślnie dziś).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=WATCH_POLL_SECONDS,
        help=f"Częstotliwość sprawdzania kolejki (domyślnie {WATCH_POLL_SECONDS:g} s).",
    )
    parser.add_argument(
        "--camera",
        default=DEFAULT_CAMERA,
        help=f"Nazwa kamery (domyślnie {DEFAULT_CAMERA}).",
    )
    parser.add_argument(
        "--recordings-root",
        type=Path,
        default=DEFAULT_RECORDINGS_ROOT,
        help=f"Katalog nagrań (domyślnie {DEFAULT_RECORDINGS_ROOT}).",
    )
    parser.add_argument(
        "--analysis-root",
        type=Path,
        default=DEFAULT_ANALYSIS_OUTPUT_DIR,
        help=f"Katalog wyników usługi (domyślnie {DEFAULT_ANALYSIS_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--n",
        type=int,
        nargs="+",
        default=list(DEFAULT_CHANGE_OFFSETS),
        help="Odstęp w sekundowych próbkach dla TestZmianyJasnosci (domyślnie 3).",
    )
    parser.add_argument(
        "--p",
        type=float,
        nargs="+",
        default=[BRIGHTNESS_CHANGE_THRESHOLD],
        help=(
            "Jeden próg wspólny dla wszystkich N albo po jednym progu "
            f"w kolejności N (domyślnie {BRIGHTNESS_CHANGE_THRESHOLD:.3f})."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Katalog bazowy wyników; domyślnie analysis/<data nagrania>/.",
    )
    args = parser.parse_args()
    if not args.n or any(n < 1 for n in args.n):
        parser.error("Każde N musi być dodatnią liczbą całkowitą.")
    threshold: ThresholdValues = args.p[0] if len(args.p) == 1 else args.p
    try:
        _thresholds_by_n(args.n, threshold)
    except ValueError as error:
        parser.error(str(error))
    if args.watch:
        if args.video is not None or args.output_root is not None:
            parser.error("--watch nie łączy się z plikiem ani z --output-root.")
        if args.poll_seconds <= 0 or not math.isfinite(args.poll_seconds):
            parser.error("--poll-seconds musi być dodatnie.")
        try:
            first_date = (
                date.fromisoformat(args.from_date)
                if args.from_date is not None
                else None
            )
        except ValueError as error:
            parser.error(str(error))
        run_brightness_watcher(
            args.n,
            args.analysis_root,
            args.camera,
            args.recordings_root,
            first_date,
            args.poll_seconds,
            threshold,
        )
        return
    if args.from_date is not None:
        parser.error("--from-date wymaga --watch.")
    if args.video is None:
        parser.error("Podaj plik wideo albo użyj --watch.")
    run_brightness_tests(
        args.video, args.n, args.output_root, threshold=threshold
    )


if __name__ == "__main__":
    main()
