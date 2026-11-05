"""Experimental grayscale motion measurements for the POE1 birdhouse ROI."""

import argparse
import csv
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence
from zoneinfo import ZoneInfo

import cv2
import numpy as np


# Obszar badania na ciemnym podbiciu dachu; współrzędne dla ROOF_CORNER_REFERENCE.
DEFAULT_POLYGON = ((1088, 605), (1318, 564), (1462, 1133), (1258, 1187))
# Prawy dolny róg dachu na tle nieba; wzorzec to wycinek 160x160 px
# (od rogu: 110 px w lewo i w górę, 50 px w prawo i w dół).
ROOF_CORNER_REFERENCE = (1473, 1148)
ROOF_TEMPLATE_FILE = Path(__file__).parent / "reference" / "poe1_roof_corner.png"
ROOF_TEMPLATE_BEFORE = 110
ROOF_TEMPLATE_SIZE = 160
ROOF_MIN_SCORE = 0.35
ROOF_LOCAL_RADIUS_PX = 8
ROOF_WIDE_RADIUS_PX = 60
ROOF_WIDE_SEARCH_SECONDS = 5.0
ROOF_MOVE_TOLERANCE_PX = 2
DEFAULT_LAGS = (1, 3)
DEFAULT_THRESHOLDS = (12, 20, 32)
DEFAULT_CLIP_BEFORE_SECONDS = 1.0
DEFAULT_CLIP_AFTER_SECONDS = 2.0
DEFAULT_SKIP_AFTER_SECONDS = 3.0
DEFAULT_ANALYSIS_ROOT = Path("/opt/cameras/recordings/analysis")
ANALYSIS_SUBDIR = "POE1_sikorki"
MAX_CHANGED_PIXELS = 5000
MIN_CHANGED_PIXELS_FOR_COHERENCE = 500
MIN_COHERENT_FRACTION = 0.35
MAX_COHERENT_COMPONENT_PIXELS = 2000
DEFAULT_LOCATION_CONFIG = (
    Path(__file__).resolve().parents[2] / "config" / "recording_config.yaml"
)
# Share of ROI pixels changed by more than 12 levels between adjacent frames
# above which the image is treated as sensor "snow" (clean daylight: <0.005).
NOISE_CHANGED_FRACTION = 0.015
NOISE_COARSE_STEP_SECONDS = 60.0
NOISE_REFINE_TOLERANCE_SECONDS = 2.0
TWILIGHT_MARGIN = timedelta(minutes=90)


def sun_times(
    day: date, config_path: Path = DEFAULT_LOCATION_CONFIG
) -> tuple[datetime, datetime] | None:
    """Return naive local (sunrise, sunset) for a day, or None if unavailable."""
    try:
        import yaml
        from astral import LocationInfo
        from astral.sun import sun

        with config_path.open(encoding="utf-8") as config_file:
            location = yaml.safe_load(config_file)["location"]
        zone = ZoneInfo(location["timezone"])
        info = LocationInfo(
            "local",
            "local",
            location["timezone"],
            location["latitude"],
            location["longitude"],
        )
        times = sun(info.observer, date=day, tzinfo=zone)
        return (
            times["sunrise"].replace(tzinfo=None),
            times["sunset"].replace(tzinfo=None),
        )
    except Exception:
        return None


def recording_start(video_path: Path) -> datetime | None:
    """Wall-clock start of a recording from `<date dir>/HH-MM-SS[_MM_SS].mkv`."""
    match = re.fullmatch(
        r"(\d{2})-(\d{2})-(\d{2})(?:_(\d+)_(\d+))?", video_path.stem
    )
    try:
        day = datetime.strptime(video_path.parent.name, "%Y-%m-%d")
    except ValueError:
        return None
    if match is None:
        return None
    hours, minutes, seconds = (int(match.group(i)) for i in (1, 2, 3))
    offset = timedelta(
        minutes=int(match.group(4) or 0), seconds=int(match.group(5) or 0)
    )
    return day + timedelta(hours=hours, minutes=minutes, seconds=seconds) + offset


def overlaps_twilight_or_night(
    start: datetime,
    duration_seconds: float,
    sunrise: datetime,
    sunset: datetime,
    margin: timedelta = TWILIGHT_MARGIN,
) -> bool:
    """True unless the whole recording lies in solid daylight."""
    end = start + timedelta(seconds=duration_seconds)
    return start < sunrise + margin or end > sunset - margin


def find_clean_ranges(
    is_noisy: Callable[[float], bool],
    duration_seconds: float,
    coarse_step: float = NOISE_COARSE_STEP_SECONDS,
    tolerance: float = NOISE_REFINE_TOLERANCE_SECONDS,
) -> list[tuple[float, float]]:
    """Probe coarsely, then binary-search each noisy/clean boundary."""
    times = [0.0]
    while times[-1] + coarse_step < duration_seconds:
        times.append(times[-1] + coarse_step)
    times.append(duration_seconds)
    states = [is_noisy(min(t, max(duration_seconds - 1.0, 0.0))) for t in times]

    boundaries: list[float] = []
    for (low, high), (low_state, high_state) in zip(
        zip(times, times[1:]), zip(states, states[1:])
    ):
        if low_state == high_state:
            continue
        while high - low > tolerance:
            middle = (low + high) / 2
            if is_noisy(middle) == low_state:
                low = middle
            else:
                high = middle
        boundaries.append((low + high) / 2)

    edges = [0.0, *boundaries, duration_seconds]
    clean_ranges: list[tuple[float, float]] = []
    state = states[0]
    for start, end in zip(edges, edges[1:]):
        if not state:
            clean_ranges.append((start, end))
        state = not state
    return clean_ranges


def video_duration_seconds(video_path: Path) -> float:
    """Read container duration; OpenCV's reported frame count may be inaccurate."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe is required to read recording duration")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {video_path}: {result.stderr.strip()}"
        )
    try:
        duration = float(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError(
            f"ffprobe returned an invalid duration for {video_path}: "
            f"{result.stdout.strip()!r}"
        ) from error
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(
            f"ffprobe returned a non-positive duration for {video_path}: "
            f"{duration}"
        )
    return duration


def read_timed_frame(
    capture: cv2.VideoCapture, video_path: Path
) -> tuple[float, np.ndarray] | None:
    """Read one frame and return its presentation timestamp in seconds."""
    ok, frame = capture.read()
    if not ok:
        return None
    timestamp_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
    if not math.isfinite(timestamp_ms) or timestamp_ms < 0:
        raise RuntimeError(
            f"Invalid frame timestamp in {video_path}: {timestamp_ms}"
        )
    return timestamp_ms / 1000.0, frame


def sample_noise_fractions(
    video_path: Path,
    duration_seconds: float,
    polygon_mask: np.ndarray,
    to_roi_gray: Callable[[np.ndarray], np.ndarray],
) -> list[float]:
    """Sample adjacent-frame change every 0.4 s using sequential PTS reads."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video for noise scan: {video_path}")

    fractions: list[float] = []
    current = read_timed_frame(capture, video_path)
    sample_time = 0.0
    last_timestamp = -1.0
    try:
        while current is not None and sample_time < duration_seconds:
            while current is not None and current[0] < sample_time:
                if current[0] < last_timestamp:
                    raise RuntimeError(
                        f"Non-monotonic frame timestamps in {video_path}"
                    )
                last_timestamp = current[0]
                current = read_timed_frame(capture, video_path)
            if current is None:
                break

            first_timestamp, first_frame = current
            if first_timestamp < last_timestamp:
                raise RuntimeError(
                    f"Non-monotonic frame timestamps in {video_path}"
                )
            following = read_timed_frame(capture, video_path)
            if following is None:
                break
            second_timestamp, second_frame = following
            if second_timestamp < first_timestamp:
                raise RuntimeError(
                    f"Non-monotonic frame timestamps in {video_path}"
                )
            fractions.append(
                changed_fraction(
                    to_roi_gray(first_frame),
                    to_roi_gray(second_frame),
                    polygon_mask,
                )
            )
            last_timestamp = second_timestamp
            current = following
            sample_time += 0.4
    finally:
        capture.release()
    return fractions


def noise_at_seconds(
    samples: Sequence[float], seconds: float
) -> bool:
    """Apply the existing five-pair median noise test to sequential samples."""
    first_index = max(0, math.ceil(seconds / 0.4 - 1e-9))
    window = samples[first_index : first_index + 5]
    return bool(window) and float(np.median(window)) >= NOISE_CHANGED_FRACTION


def changed_fraction(
    previous_gray: np.ndarray, current_gray: np.ndarray, mask: np.ndarray
) -> float:
    metrics = measure_difference(previous_gray, current_gray, mask=mask)
    return metrics.changed_pixels[0] / max(cv2.countNonZero(mask), 1)


@dataclass(frozen=True)
class MotionMetrics:
    mean_abs_difference: float
    changed_pixels: tuple[int, ...]
    largest_component: int


def _edge_map(gray: np.ndarray) -> np.ndarray:
    """Mapa krawędzi niezależna od jasności (świt, słońce, pochmurno)."""
    gray = cv2.GaussianBlur(cv2.createCLAHE(3.0, (8, 8)).apply(gray), (0, 0), 1.2)
    return cv2.magnitude(
        cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    )


class RoofCornerTracker:
    """Śledzi róg dachu, żeby obszar badania podążał za przesuwającą się kamerą.

    Co klatkę szuka wzorca w małym oknie wokół ostatniej pozycji; co
    `ROOF_WIDE_SEARCH_SECONDS` (i gdy rogu nie widać) w szerokim oknie. Gdy
    dopasowanie jest za słabe (noc, mgła), zostaje ostatnia pozycja.
    """

    def __init__(self, fps: float) -> None:
        image = cv2.imread(str(ROOF_TEMPLATE_FILE), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise RuntimeError(f"Missing roof corner template: {ROOF_TEMPLATE_FILE}")
        self._template = _edge_map(image)
        self._wide_every = max(1, int(round(fps * ROOF_WIDE_SEARCH_SECONDS)))
        self.corner = ROOF_CORNER_REFERENCE
        self.score = 0.0
        self._frames_since_wide = 0
        self._lost = True

    @property
    def shift(self) -> tuple[int, int]:
        return (
            self.corner[0] - ROOF_CORNER_REFERENCE[0],
            self.corner[1] - ROOF_CORNER_REFERENCE[1],
        )

    def _search(
        self, gray: np.ndarray, radius: int
    ) -> tuple[float, tuple[int, int]] | None:
        x1 = self.corner[0] - ROOF_TEMPLATE_BEFORE - radius
        y1 = self.corner[1] - ROOF_TEMPLATE_BEFORE - radius
        size = ROOF_TEMPLATE_SIZE + 2 * radius
        if x1 < 0 or y1 < 0 or x1 + size > gray.shape[1] or y1 + size > gray.shape[0]:
            return None
        match = cv2.matchTemplate(
            _edge_map(gray[y1 : y1 + size, x1 : x1 + size]),
            self._template,
            cv2.TM_CCOEFF_NORMED,
        )
        _, score, _, location = cv2.minMaxLoc(match)
        return float(score), (
            x1 + location[0] + ROOF_TEMPLATE_BEFORE,
            y1 + location[1] + ROOF_TEMPLATE_BEFORE,
        )

    def update(self, frame: np.ndarray) -> bool:
        """Aktualizuje pozycję rogu; True, gdy obszar badania trzeba przesunąć."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self._frames_since_wide += 1
        wide = self._lost or self._frames_since_wide >= self._wide_every
        result = self._search(
            gray, ROOF_WIDE_RADIUS_PX if wide else ROOF_LOCAL_RADIUS_PX
        )
        if wide:
            self._frames_since_wide = 0
        if result is None or result[0] < ROOF_MIN_SCORE:
            self._lost = True
            return False
        self._lost = False
        self.score, position = result
        if (
            abs(position[0] - self.corner[0]) >= ROOF_MOVE_TOLERANCE_PX
            or abs(position[1] - self.corner[1]) >= ROOF_MOVE_TOLERANCE_PX
        ):
            self.corner = position
            return True
        return False


def shift_polygon(
    polygon: Sequence[tuple[int, int]], shift: tuple[int, int]
) -> list[tuple[int, int]]:
    return [(x + shift[0], y + shift[1]) for x, y in polygon]


@dataclass
class DetectionSnapshot:
    last_candidate_seconds: float
    peak_seconds: float
    frame: np.ndarray
    previous_gray: np.ndarray
    current_gray: np.ndarray
    metrics: MotionMetrics
    shift: tuple[int, int] = (0, 0)


def save_roi_visualization(
    frame: np.ndarray,
    polygon: Sequence[tuple[int, int]],
    roi_mask: np.ndarray,
    output_path: Path,
) -> None:
    """Save an overview marking the exact polygon included in motion analysis."""
    polygon_array = np.asarray(polygon, dtype=np.int32)
    full_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    cv2.fillPoly(full_mask, [polygon_array], 255)
    overlay = frame.copy()
    overlay[full_mask > 0] = (255, 120, 0)
    image = cv2.addWeighted(frame, 0.68, overlay, 0.32, 0)
    cv2.polylines(image, [polygon_array], True, (0, 255, 255), 3, cv2.LINE_AA)
    roi_pixels = cv2.countNonZero(roi_mask)
    text = f"Measured ROI: {roi_pixels:,} pixels"
    cv2.putText(
        image, text, (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
        (0, 0, 0), 5, cv2.LINE_AA
    )
    cv2.putText(
        image, text, (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
        (0, 255, 255), 2, cv2.LINE_AA
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), image, [cv2.IMWRITE_JPEG_QUALITY, 88]):
        raise RuntimeError(f"Could not save ROI visualization: {output_path}")


def save_detection_visualization(
    frame: np.ndarray,
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    polygon: Sequence[tuple[int, int]],
    roi_mask: np.ndarray,
    lag: int,
    seconds: float,
    metrics: MotionMetrics,
    output_path: Path,
) -> None:
    """Save a frame with changed pixels and the largest component highlighted."""
    polygon_array = np.asarray(polygon, dtype=np.int32)
    x, y, width, height = cv2.boundingRect(polygon_array)
    difference = cv2.absdiff(previous_gray, current_gray)
    difference = cv2.bitwise_and(difference, difference, mask=roi_mask)
    changed_mask = cv2.compare(difference, 20, cv2.CMP_GT)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        changed_mask, connectivity=8
    )
    largest_index = max(
        range(1, count),
        key=lambda index: int(stats[index, cv2.CC_STAT_AREA]),
        default=None,
    )

    image = frame.copy()
    full_changed_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    full_changed_mask[y : y + height, x : x + width] = changed_mask
    image[full_changed_mask > 0] = (0, 0, 255)

    if largest_index is not None:
        largest_mask = np.zeros_like(changed_mask)
        largest_mask[labels == largest_index] = 255
        image[y : y + height, x : x + width][largest_mask > 0] = (
            0, 255, 0
        )
        component_x = int(stats[largest_index, cv2.CC_STAT_LEFT]) + x
        component_y = int(stats[largest_index, cv2.CC_STAT_TOP]) + y
        component_width = int(stats[largest_index, cv2.CC_STAT_WIDTH])
        component_height = int(stats[largest_index, cv2.CC_STAT_HEIGHT])
        cv2.rectangle(
            image,
            (component_x, component_y),
            (
                component_x + component_width - 1,
                component_y + component_height - 1,
            ),
            (0, 255, 0),
            3,
        )

    cv2.polylines(image, [polygon_array], True, (0, 255, 255), 3, cv2.LINE_AA)
    text = (
        f"lag {lag} | {seconds:.3f}s | "
        f"{metrics.changed_pixels[1]} changed px (>20) | "
        f"largest connected area {metrics.largest_component} px"
    )
    cv2.putText(
        image, text, (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
        (0, 0, 0), 5, cv2.LINE_AA
    )
    cv2.putText(
        image, text, (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
        (255, 255, 255), 2, cv2.LINE_AA
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), image, [cv2.IMWRITE_JPEG_QUALITY, 88]):
        raise RuntimeError(
            f"Could not save detection visualization: {output_path}"
        )


def group_motion_candidates(
    candidates: Sequence[tuple[float, MotionMetrics]],
    lag: int,
    min_component_px: int,
    merge_gap_seconds: float,
) -> list[dict[str, int | float]]:
    """Group large per-frame changes into reviewable candidate intervals."""
    if lag < 1 or min_component_px < 1 or merge_gap_seconds < 0:
        raise ValueError("Invalid candidate grouping parameters")

    qualifying = [
        (frame_index, metrics)
        for frame_index, metrics in candidates
        if metrics.largest_component >= min_component_px
    ]
    groups: list[list[tuple[float, MotionMetrics]]] = []
    for candidate in qualifying:
        if (
            not groups
            or candidate[0] - groups[-1][-1][0] > merge_gap_seconds
        ):
            groups.append([])
        groups[-1].append(candidate)

    intervals: list[dict[str, int | float]] = []
    for group in groups:
        peak_frame, peak = max(
            group, key=lambda item: item[1].largest_component
        )
        intervals.append(
            {
                "lag_frames": lag,
                "start_seconds": group[0][0],
                "end_seconds": group[-1][0],
                "sample_count": len(group),
                "peak_seconds": peak_frame,
                "peak_component_px": peak.largest_component,
                "peak_changed_gt12_px": peak.changed_pixels[0],
                "peak_changed_gt20_px": peak.changed_pixels[1],
                "peak_changed_gt32_px": peak.changed_pixels[2],
                "peak_mean_abs": peak.mean_abs_difference,
            }
        )
    return intervals


def classify_candidate(
    interval: dict[str, int | float],
) -> tuple[str, str, float]:
    """Classify an event by changed area and the share in its largest component."""
    changed_pixels = int(interval["peak_changed_gt20_px"])
    largest_component = int(interval["peak_component_px"])
    coherent_fraction = largest_component / max(changed_pixels, 1)

    if changed_pixels > MAX_CHANGED_PIXELS:
        return "rejected", "changed pixels > 5000", coherent_fraction
    if (
        MIN_CHANGED_PIXELS_FOR_COHERENCE
        <= changed_pixels
        <= MAX_CHANGED_PIXELS
        and coherent_fraction < MIN_COHERENT_FRACTION
    ):
        return (
            "rejected",
            "changed pixels 500-5000 and coherent area < 35%",
            coherent_fraction,
        )
    if largest_component > MAX_COHERENT_COMPONENT_PIXELS:
        return "rejected", "largest coherent area > 2000 px", coherent_fraction
    return "interesting", "within acceptance thresholds", coherent_fraction


def merge_candidate_intervals(
    intervals: Sequence[dict[str, int | float]],
    merge_gap_seconds: float,
) -> list[dict[str, int | float]]:
    """Merge overlapping lag results into one clip-worthy candidate per event."""
    if merge_gap_seconds < 0:
        raise ValueError("Merge gap must not be negative")

    ordered = sorted(intervals, key=lambda event: float(event["start_seconds"]))
    merged: list[dict[str, int | float]] = []
    for event in ordered:
        if (
            not merged
            or float(event["start_seconds"])
            > float(merged[-1]["end_seconds"]) + merge_gap_seconds
        ):
            merged.append(dict(event))
            continue

        current = merged[-1]
        current["end_seconds"] = max(
            float(current["end_seconds"]), float(event["end_seconds"])
        )
        current["start_seconds"] = min(
            float(current["start_seconds"]), float(event["start_seconds"])
        )
        current["sample_count"] = int(current["sample_count"]) + int(
            event["sample_count"]
        )
        if int(event["peak_component_px"]) > int(current["peak_component_px"]):
            for key, value in event.items():
                if key.startswith("peak_") or key == "lag_frames":
                    current[key] = value
    return merged


def elapsed_time_label(event_seconds: float) -> str:
    """Format elapsed video time as MM_SS, with minutes beyond 59 allowed."""
    total_seconds = max(0, int(event_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}_{seconds:02d}"


def save_candidate_clips(
    video_path: Path,
    output_dir: Path,
    intervals: Sequence[dict[str, int | float]],
    video_duration: float,
    before_seconds: float = 1.0,
    after_seconds: float = 5.0,
) -> list[Path]:
    """Save one source-video clip around each merged candidate peak."""
    return [
        path
        for _, path in save_candidate_clips_for_events(
            video_path,
            output_dir,
            intervals,
            video_duration,
            before_seconds,
            after_seconds,
        )
    ]


def save_candidate_clips_for_events(
    video_path: Path,
    output_dir: Path,
    intervals: Sequence[dict[str, int | float]],
    video_duration: float,
    before_seconds: float = 1.0,
    after_seconds: float = 5.0,
) -> list[tuple[dict[str, int | float], Path]]:
    """Save one clip per merged event and return (merged event, clip path)."""
    if before_seconds < 0 or after_seconds <= 0:
        raise ValueError("Clip timing must be non-negative with positive duration")
    if not intervals:
        return []
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to save candidate clips")

    output_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[tuple[dict[str, int | float], Path]] = []
    merged = merge_candidate_intervals(intervals, merge_gap_seconds=0.25)
    for index, event in enumerate(merged, start=1):
        peak_seconds = float(event["peak_seconds"])
        start_seconds = max(0.0, peak_seconds - before_seconds)
        end_seconds = min(video_duration, peak_seconds + after_seconds)
        duration = end_seconds - start_seconds
        if duration <= 0:
            continue

        event_label = elapsed_time_label(peak_seconds)
        clip_path = output_dir / (
            f"{video_path.stem}_candidate_{index:03d}_{event_label}.mkv"
        )
        if clip_path.exists():
            print(f"Candidate clip already exists; keeping {clip_path}")
            saved_paths.append((event, clip_path))
            continue
        suffix = 1
        while True:
            try:
                with clip_path.open("x"):
                    pass
                break
            except FileExistsError:
                suffix += 1
                clip_path = output_dir / (
                    f"{video_path.stem}_candidate_{index:03d}_"
                    f"{event_label}_{suffix:02d}.mkv"
                )

        with tempfile.NamedTemporaryFile(
            prefix=".poe1-clip-",
            suffix=".mkv",
            dir=output_dir,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
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
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-y",
            str(temporary_path),
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            temporary_path.unlink(missing_ok=True)
            clip_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg failed to save clip {clip_path}: {result.stderr.strip()}"
            )
        os.replace(temporary_path, clip_path)
        saved_paths.append((event, clip_path))
        print(
            f"Saved candidate clip {clip_path} "
            f"({start_seconds:.2f}-{end_seconds:.2f}s)"
        )
    return saved_paths


def default_clips_dir(video_path: Path, output_path: Path) -> Path:
    """Place clips under the analysis date matching the recording directory."""
    date_name = video_path.parent.name
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_name):
        return DEFAULT_ANALYSIS_ROOT / date_name / ANALYSIS_SUBDIR
    return output_path.parent


def measure_difference(
    previous_gray: np.ndarray,
    current_gray: np.ndarray,
    thresholds: Sequence[int] = DEFAULT_THRESHOLDS,
    component_threshold: int = 20,
    mask: np.ndarray | None = None,
    min_component_px: int = 0,
) -> MotionMetrics:
    """Measure pixel-wise grayscale change and the largest connected change."""
    if previous_gray.shape != current_gray.shape:
        raise ValueError("Frames must have identical dimensions")
    if previous_gray.ndim != 2:
        raise ValueError("Frames must be grayscale")
    if mask is not None and mask.shape != previous_gray.shape:
        raise ValueError("Mask must have the same dimensions as frames")

    return measure_blurred_difference(
        blur_gray(previous_gray),
        blur_gray(current_gray),
        thresholds,
        component_threshold,
        mask,
        min_component_px,
    )


def blur_gray(gray: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(gray, (3, 3), 0)


def measure_blurred_difference(
    previous: np.ndarray,
    current: np.ndarray,
    thresholds: Sequence[int] = DEFAULT_THRESHOLDS,
    component_threshold: int = 20,
    mask: np.ndarray | None = None,
    min_component_px: int = 0,
) -> MotionMetrics:
    """Like measure_difference for frames that are already blurred.

    The connected-component pass is skipped (largest component reported as 0)
    when fewer than `min_component_px` pixels exceed the component threshold,
    since no component can then reach that size.
    """
    difference = cv2.absdiff(previous, current)
    if mask is not None:
        difference = cv2.bitwise_and(difference, difference, mask=mask)
    changed_pixels = tuple(
        int(cv2.countNonZero(cv2.compare(difference, threshold, cv2.CMP_GT)))
        for threshold in thresholds
    )
    component_mask = cv2.compare(
        difference, component_threshold, cv2.CMP_GT
    )
    largest_component = 0
    if min_component_px <= 0 or cv2.countNonZero(component_mask) >= min_component_px:
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            component_mask, connectivity=8
        )
        largest_component = max(
            (int(stats[index, cv2.CC_STAT_AREA]) for index in range(1, count)),
            default=0,
        )
    return MotionMetrics(
        mean_abs_difference=float(
            np.mean(difference if mask is None else difference[mask > 0])
        ),
        changed_pixels=changed_pixels,
        largest_component=largest_component,
    )


def analyze_video(
    video_path: Path,
    output_path: Path,
    polygon: Sequence[tuple[int, int]] = DEFAULT_POLYGON,
    lags: Sequence[int] = DEFAULT_LAGS,
    min_component_px: int = 150,
    merge_gap_seconds: float = 0.25,
    clips_dir: Path | None = None,
    clip_before_seconds: float = DEFAULT_CLIP_BEFORE_SECONDS,
    clip_after_seconds: float = DEFAULT_CLIP_AFTER_SECONDS,
    noise_filter: bool = True,
    skip_after_seconds: float = DEFAULT_SKIP_AFTER_SECONDS,
) -> int:
    if not lags or any(lag < 1 for lag in lags):
        raise ValueError("Lags must be positive integers")
    if min_component_px < 1 or merge_gap_seconds < 0:
        raise ValueError("Invalid candidate grouping parameters")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    if len(polygon) < 3:
        capture.release()
        raise ValueError("Polygon must have at least three vertices")
    video_duration = video_duration_seconds(video_path)
    if video_duration <= 0:
        capture.release()
        raise RuntimeError(f"Invalid video duration: {video_path}")

    frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if any(
        x < 0 or y < 0 or x >= frame_width or y >= frame_height
        for x, y in polygon
    ):
        capture.release()
        raise ValueError(
            f"Polygon {polygon} falls outside frame "
            f"{frame_width}x{frame_height}"
        )
    polygon_array = np.asarray(polygon, dtype=np.int32)
    x, y, width, height = cv2.boundingRect(polygon_array)
    polygon_mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(polygon_mask, [polygon_array - np.array([x, y])], 255)

    tracker = RoofCornerTracker(capture.get(cv2.CAP_PROP_FPS) or 25.0)
    roi_origin = [x, y]
    current_shift = (0, 0)

    def to_roi_gray(frame: np.ndarray) -> np.ndarray:
        left, top = roi_origin
        return cv2.cvtColor(
            frame[top : top + height, left : left + width], cv2.COLOR_BGR2GRAY
        )

    def apply_shift() -> bool:
        """Przesuwa ROI za rogiem dachu; False, gdy wyszłoby poza kadr."""
        nonlocal current_shift
        shift = tracker.shift
        left, top = x + shift[0], y + shift[1]
        if left < 0 or top < 0 or left + width > frame_width or top + height > frame_height:
            return False
        roi_origin[:] = [left, top]
        current_shift = shift
        return True

    analysis_ranges = [(0.0, video_duration)]
    if noise_filter:
        start_time = recording_start(video_path)
        sun = sun_times(start_time.date()) if start_time else None
        if sun is None or overlaps_twilight_or_night(
            start_time, video_duration, *sun
        ):
            noise_samples = sample_noise_fractions(
                video_path, video_duration, polygon_mask, to_roi_gray
            )
            analysis_ranges = find_clean_ranges(
                lambda seconds: noise_at_seconds(noise_samples, seconds),
                video_duration,
            )
            clean_seconds = sum(end - begin for begin, end in analysis_ranges)
            print(
                f"Noise scan {video_path.name}: "
                f"{clean_seconds:.0f}/{video_duration:.0f} s clean "
                + ", ".join(f"{b:.0f}-{e:.0f}s" for b, e in analysis_ranges),
                flush=True,
            )
    history: deque[np.ndarray] = deque(maxlen=max(lags) + 1)
    candidates_by_lag: dict[int, list[tuple[float, MotionMetrics]]] = {
        lag: [] for lag in lags
    }
    visualization_dir = output_path.parent / "visualizations"
    visual_snapshots: dict[int, DetectionSnapshot] = {}
    visual_counts = {lag: 0 for lag in lags}
    visual_names: dict[int, list[str]] = {lag: [] for lag in lags}
    active_candidate: dict[str, int | float] | None = None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["frame", "time_seconds"]
    for lag in lags:
        headers.extend(
            [
                f"lag{lag}_mean_abs",
                f"lag{lag}_pixels_gt12",
                f"lag{lag}_pixels_gt20",
                f"lag{lag}_pixels_gt32",
                f"lag{lag}_largest_component_gt20",
            ]
        )

    def flush_active_candidate() -> None:
        nonlocal active_candidate
        active_candidate = None

    def flush_visual_snapshot(lag: int) -> None:
        snapshot = visual_snapshots.pop(lag, None)
        if snapshot is None:
            return
        visual_counts[lag] += 1
        image_path = visualization_dir / (
            f"{video_path.stem}_lag{lag}_candidate_{visual_counts[lag]:03d}_"
            f"{elapsed_time_label(snapshot.peak_seconds)}.jpg"
        )
        visual_names[lag].append(image_path.name)
        save_detection_visualization(
            snapshot.frame,
            snapshot.previous_gray,
            snapshot.current_gray,
            shift_polygon(polygon, snapshot.shift),
            polygon_mask,
            lag,
            snapshot.peak_seconds,
            snapshot.metrics,
            image_path,
        )

    frame_index = 0
    range_index = 0
    range_active = False
    skip_until_seconds = 0.0
    last_progress_minute = -1
    last_timestamp = -1.0
    try:
        with output_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(headers)
            while True:
                timed_frame = read_timed_frame(capture, video_path)
                if timed_frame is None:
                    flush_active_candidate()
                    break
                frame_seconds, frame = timed_frame
                if frame_seconds < last_timestamp:
                    raise RuntimeError(
                        f"Non-monotonic frame timestamps in {video_path}: "
                        f"{frame_seconds:.3f} after {last_timestamp:.3f}"
                    )
                last_timestamp = frame_seconds
                current_frame_index = frame_index
                frame_index += 1
                if current_frame_index == 0:
                    tracker.update(frame)
                    apply_shift()
                    print(
                        f"Roof corner {tracker.corner} "
                        f"(shift {current_shift}, score {tracker.score:.2f})",
                        flush=True,
                    )
                    save_roi_visualization(
                        frame,
                        shift_polygon(polygon, current_shift),
                        polygon_mask,
                        visualization_dir / f"{video_path.stem}_roi.jpg",
                    )

                while (
                    range_index < len(analysis_ranges)
                    and frame_seconds >= analysis_ranges[range_index][1]
                ):
                    if range_active:
                        flush_active_candidate()
                    range_active = False
                    history.clear()
                    range_index += 1
                if range_index >= len(analysis_ranges):
                    break

                range_start, range_end = analysis_ranges[range_index]
                if frame_seconds < range_start or frame_seconds >= range_end:
                    history.clear()
                    continue
                if not range_active:
                    range_active = True
                    history.clear()
                if current_frame_index > 0 and tracker.update(frame):
                    if apply_shift():
                        print(
                            f"[{frame_seconds:.1f}s] Roof corner moved to "
                            f"{tracker.corner}; ROI shift {current_shift}",
                            flush=True,
                        )
                        history.clear()
                    else:
                        tracker.corner = (
                            ROOF_CORNER_REFERENCE[0] + current_shift[0],
                            ROOF_CORNER_REFERENCE[1] + current_shift[1],
                        )
                if frame_seconds < skip_until_seconds:
                    history.clear()
                    continue

                gray = blur_gray(to_roi_gray(frame))
                row: list[float | int] = [
                    current_frame_index,
                    round(frame_seconds, 3),
                ]
                frame_candidates: list[tuple[int, MotionMetrics]] = []
                for lag in lags:
                    if len(history) >= lag:
                        metrics = measure_blurred_difference(
                            history[-lag],
                            gray,
                            mask=polygon_mask,
                            min_component_px=min_component_px,
                        )
                        if metrics.largest_component >= min_component_px:
                            candidates_by_lag[lag].append(
                                (frame_seconds, metrics)
                            )
                            frame_candidates.append((lag, metrics))
                            snapshot = visual_snapshots.get(lag)
                            if (
                                snapshot is not None
                                and frame_seconds - snapshot.last_candidate_seconds
                                > merge_gap_seconds
                            ):
                                flush_visual_snapshot(lag)
                                snapshot = None
                            if snapshot is None:
                                visual_snapshots[lag] = DetectionSnapshot(
                                    last_candidate_seconds=frame_seconds,
                                    peak_seconds=frame_seconds,
                                    frame=frame.copy(),
                                    previous_gray=history[-lag].copy(),
                                    current_gray=gray.copy(),
                                    metrics=metrics,
                                    shift=current_shift,
                                )
                            else:
                                snapshot.last_candidate_seconds = frame_seconds
                                if (
                                    metrics.largest_component
                                    > snapshot.metrics.largest_component
                                ):
                                    snapshot.peak_seconds = frame_seconds
                                    snapshot.frame = frame.copy()
                                    snapshot.previous_gray = history[-lag].copy()
                                    snapshot.current_gray = gray.copy()
                                    snapshot.metrics = metrics
                                    snapshot.shift = current_shift
                        row.extend(
                            [
                                round(metrics.mean_abs_difference, 3),
                                *metrics.changed_pixels,
                                metrics.largest_component,
                            ]
                        )
                    else:
                        row.extend(["", "", "", "", ""])
                writer.writerow(row)
                history.append(gray)

                if frame_candidates:
                    lag, peak = max(
                        frame_candidates,
                        key=lambda candidate: candidate[1].largest_component,
                    )
                    candidate_time = frame_seconds
                    if active_candidate is None:
                        active_candidate = {
                            "lag_frames": lag,
                            "start_seconds": candidate_time,
                            "end_seconds": candidate_time,
                            "sample_count": 1,
                            "peak_seconds": candidate_time,
                            "peak_component_px": peak.largest_component,
                            "peak_changed_gt12_px": peak.changed_pixels[0],
                            "peak_changed_gt20_px": peak.changed_pixels[1],
                            "peak_changed_gt32_px": peak.changed_pixels[2],
                            "peak_mean_abs": peak.mean_abs_difference,
                            "last_candidate_seconds": candidate_time,
                        }
                    else:
                        active_candidate["end_seconds"] = candidate_time
                        active_candidate["last_candidate_seconds"] = candidate_time
                        active_candidate["sample_count"] = (
                            int(active_candidate["sample_count"]) + 1
                        )
                        if peak.largest_component > int(
                            active_candidate["peak_component_px"]
                        ):
                            active_candidate.update(
                                {
                                    "lag_frames": lag,
                                    "peak_seconds": candidate_time,
                                    "peak_component_px": peak.largest_component,
                                    "peak_changed_gt12_px": peak.changed_pixels[0],
                                    "peak_changed_gt20_px": peak.changed_pixels[1],
                                    "peak_changed_gt32_px": peak.changed_pixels[2],
                                    "peak_mean_abs": peak.mean_abs_difference,
                                }
                            )
                elif active_candidate is not None:
                    quiet_seconds = (
                        frame_seconds
                        - float(active_candidate["last_candidate_seconds"])
                    )
                    if quiet_seconds > merge_gap_seconds:
                        skip_until_seconds = (
                            float(active_candidate["last_candidate_seconds"])
                            + skip_after_seconds
                        )
                        flush_active_candidate()
                        history.clear()

                progress_minute = int(frame_seconds // 60)
                if progress_minute > last_progress_minute:
                    print(f"Analyzed {frame_seconds:.0f} seconds", flush=True)
                    last_progress_minute = progress_minute
    finally:
        capture.release()

    flush_active_candidate()
    for lag in lags:
        flush_visual_snapshot(lag)

    events_path = output_path.with_name(
        f"{output_path.stem}_events{output_path.suffix}"
    )
    event_headers = [
        "recording",
        "clip_file",
        "visualization_file",
        "lag_frames",
        "start_seconds",
        "end_seconds",
        "sample_count",
        "peak_seconds",
        "peak_component_px",
        "peak_changed_gt12_px",
        "peak_changed_gt20_px",
        "peak_changed_gt32_px",
        "peak_mean_abs",
        "coherent_area_percent",
        "decision",
        "decision_reason",
    ]
    decided_events: list[
        tuple[dict[str, int | float], str, str, float, str]
    ] = []
    accepted_intervals: list[dict[str, int | float]] = []
    for lag in lags:
        intervals = group_motion_candidates(
            candidates_by_lag[lag],
            lag,
            min_component_px,
            merge_gap_seconds,
        )
        if len(intervals) != len(visual_names[lag]):
            raise RuntimeError(
                f"Lag {lag}: {len(intervals)} events but "
                f"{len(visual_names[lag])} detection images"
            )
        for interval, image_name in zip(intervals, visual_names[lag]):
            decision, reason, coherent_fraction = classify_candidate(interval)
            decided_events.append(
                (interval, decision, reason, coherent_fraction, image_name)
            )
            if decision == "interesting":
                accepted_intervals.append(interval)

    decided_events.sort(
        key=lambda item: (float(item[0]["peak_seconds"]), int(item[0]["lag_frames"]))
    )
    clips_output_dir = clips_dir or default_clips_dir(video_path, output_path)
    saved_clips = save_candidate_clips_for_events(
        video_path,
        clips_output_dir,
        accepted_intervals,
        video_duration,
        clip_before_seconds,
        clip_after_seconds,
    )

    def clip_name_for(interval: dict[str, int | float]) -> str:
        for merged_event, clip_path in saved_clips:
            if float(merged_event["start_seconds"]) <= float(
                interval["start_seconds"]
            ) and float(interval["end_seconds"]) <= float(
                merged_event["end_seconds"]
            ):
                return clip_path.name
        return ""

    with events_path.open("w", newline="", encoding="utf-8") as events_file:
        writer = csv.DictWriter(events_file, fieldnames=event_headers)
        writer.writeheader()
        for interval, decision, reason, coherent_fraction, image_name in (
            decided_events
        ):
            clip_name = (
                clip_name_for(interval) if decision == "interesting" else ""
            )
            writer.writerow(
                {
                    "recording": video_path.name,
                    "clip_file": clip_name,
                    "visualization_file": image_name,
                    **interval,
                    "coherent_area_percent": round(coherent_fraction * 100, 2),
                    "decision": decision,
                    "decision_reason": reason,
                }
            )
            print(
                f"Candidate lag={interval['lag_frames']} "
                f"{interval['start_seconds']:.2f}-"
                f"{interval['end_seconds']:.2f}s; peak "
                f"{interval['peak_changed_gt20_px']} changed pixels, "
                f"{interval['peak_component_px']} px component "
                f"({coherent_fraction:.1%}); {decision}: {reason}"
                + (f" -> {clip_name}" if clip_name else "")
            )

    saved_clip_count = len(saved_clips)
    print(f"Candidate intervals: {events_path}")
    print(f"Saved {saved_clip_count} candidate clips to {clips_output_dir}")
    return frame_index


def run_watch(
    camera_dir: Path,
    polygon: Sequence[tuple[int, int]],
    lags: Sequence[int],
    min_component_px: int,
    merge_gap_seconds: float,
    clip_before: float,
    clip_after: float,
    noise_filter: bool,
    poll_seconds: float,
    skip_after: float,
    days_back: int = 1,
) -> None:
    """Analyze each finished recording once, tracked in POE1_sikorki/analyzed.txt."""
    from mcrwac.analysis import squirrel_exit_prototype as watcher

    def analyze(video: Path) -> None:
        clips_dir = default_clips_dir(video, video)
        analyze_video(
            video,
            clips_dir / f"{video.stem}_motion.csv",
            polygon,
            lags,
            min_component_px,
            merge_gap_seconds,
            clips_dir,
            clip_before,
            clip_after,
            noise_filter,
            skip_after,
        )

    def terminate(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    print(f"Watching {camera_dir}", flush=True)
    from mcrwac.notify import build_service_notifier

    notifier = build_service_notifier("POE1 sikorki")
    notifier.event("start analizy", title="Start", tags="red_circle")
    stop_reason = "zatrzymanie uslugi"
    try:
        watcher.watch_recordings(
            camera_dir,
            analyze,
            poll_seconds=poll_seconds,
            subdir=ANALYSIS_SUBDIR,
            first_date=date.today() - timedelta(days=days_back),
            newest_day_first=True,
        )
    except KeyboardInterrupt:
        print("Interrupted", flush=True)
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "video",
        type=Path,
        nargs="?",
        help="recording file or directory (omit with --watch)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="CSV output path for one file; directory inputs use per-recording CSVs under clips-dir",
    )
    parser.add_argument(
        "--clips-dir",
        type=Path,
        help="directory for candidate clips (default: analysis/<recording-date>)",
    )
    parser.add_argument(
        "--clip-before", type=float, default=DEFAULT_CLIP_BEFORE_SECONDS
    )
    parser.add_argument(
        "--clip-after", type=float, default=DEFAULT_CLIP_AFTER_SECONDS
    )
    parser.add_argument(
        "--skip-after",
        type=float,
        default=DEFAULT_SKIP_AFTER_SECONDS,
        help="seconds of analysis skipped after a detected event",
    )
    parser.add_argument(
        "--polygon",
        nargs="+",
        type=int,
        metavar="COORD",
        default=[coordinate for point in DEFAULT_POLYGON for coordinate in point],
        help="polygon vertices as x1 y1 x2 y2 ... in full-frame pixels",
    )
    parser.add_argument("--lags", nargs="+", type=int, default=DEFAULT_LAGS)
    parser.add_argument(
        "--min-component-px",
        type=int,
        default=150,
        help="minimum connected change area to log as a candidate",
    )
    parser.add_argument(
        "--merge-gap-seconds",
        type=float,
        default=0.25,
        help="merge candidate frames separated by at most this gap",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="analyze each finished recording of --camera-dir as it appears",
    )
    parser.add_argument(
        "--camera-dir",
        type=Path,
        default=Path("/opt/cameras/recordings/POE1"),
        help="camera recordings root used by --watch",
    )
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument(
        "--days-back",
        type=int,
        default=1,
        help="--watch also catches up unanalyzed recordings from N earlier "
        "days; the newest day is always processed first",
    )
    parser.add_argument(
        "--no-noise-filter",
        action="store_true",
        help="analyze whole files even when the image is full of sensor snow",
    )
    args = parser.parse_args()
    if len(args.polygon) < 6 or len(args.polygon) % 2:
        parser.error("--polygon requires at least three x/y coordinate pairs")
    polygon = list(zip(args.polygon[::2], args.polygon[1::2]))

    if args.watch:
        if args.video is not None or args.output is not None:
            parser.error("--watch takes neither a recording nor --output")
        run_watch(
            args.camera_dir,
            polygon,
            tuple(args.lags),
            args.min_component_px,
            args.merge_gap_seconds,
            args.clip_before,
            args.clip_after,
            not args.no_noise_filter,
            args.poll_seconds,
            args.skip_after,
            args.days_back,
        )
        return
    if args.video is None:
        parser.error("recording file or directory is required without --watch")

    if args.video.is_dir():
        if args.output is not None:
            parser.error("--output is only valid when processing a single file")
        recordings = sorted(
            path
            for path in args.video.iterdir()
            if path.is_file() and path.suffix.lower() == ".mkv"
        )
        if not recordings:
            parser.error(f"No MKV recordings found in {args.video}")
        clips_dir = args.clips_dir or default_clips_dir(
            recordings[0], args.video
        )
        failures: list[tuple[Path, str]] = []
        for index, recording in enumerate(recordings, start=1):
            output_path = clips_dir / f"{recording.stem}_motion.csv"
            print(
                f"[{index}/{len(recordings)}] Processing {recording}",
                flush=True,
            )
            try:
                frame_count = analyze_video(
                    recording,
                    output_path,
                    polygon,
                    tuple(args.lags),
                    args.min_component_px,
                    args.merge_gap_seconds,
                    clips_dir,
                    args.clip_before,
                    args.clip_after,
                    not args.no_noise_filter,
                    args.skip_after,
                )
                print(
                    f"Completed {recording.name}: {frame_count} frames",
                    flush=True,
                )
            except Exception as error:
                failures.append((recording, str(error)))
                print(f"ERROR {recording.name}: {error}", flush=True)

        print(
            f"Batch complete: {len(recordings) - len(failures)} succeeded, "
            f"{len(failures)} failed; clips and CSVs in {clips_dir}",
            flush=True,
        )
        if failures:
            raise SystemExit(1)
        return

    if args.output is None:
        parser.error("--output is required when processing a single file")
    frame_count = analyze_video(
        args.video,
        args.output,
        polygon,
        tuple(args.lags),
        args.min_component_px,
        args.merge_gap_seconds,
        args.clips_dir,
        args.clip_before,
        args.clip_after,
        not args.no_noise_filter,
        args.skip_after,
    )
    print(f"Analyzed {frame_count} frames; measurements: {args.output}")


if __name__ == "__main__":
    main()
