"""Experimental grayscale motion measurements for the POE1 birdhouse ROI."""

import argparse
import csv
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


DEFAULT_POLYGON = ((830, 481), (1005, 435), (1108, 865), (947, 909))
DEFAULT_LAGS = (1, 3, 5)
DEFAULT_THRESHOLDS = (12, 20, 32)
DEFAULT_CLIP_BEFORE_SECONDS = 1.0
DEFAULT_CLIP_AFTER_SECONDS = 5.0
DEFAULT_SKIP_AFTER_SECONDS = 3.0
DEFAULT_ANALYSIS_ROOT = Path("/opt/cameras/recordings/analysis")
ANALYSIS_SUBDIR = "sikorki"
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


def group_motion_candidates(
    candidates: Sequence[tuple[int, MotionMetrics]],
    fps: float,
    lag: int,
    min_component_px: int,
    merge_gap_seconds: float,
) -> list[dict[str, int | float]]:
    """Group large per-frame changes into reviewable candidate intervals."""
    if fps <= 0 or lag < 1 or min_component_px < 1 or merge_gap_seconds < 0:
        raise ValueError("Invalid candidate grouping parameters")

    qualifying = [
        (frame_index, metrics)
        for frame_index, metrics in candidates
        if metrics.largest_component >= min_component_px
    ]
    max_gap_frames = int(round(merge_gap_seconds * fps))
    groups: list[list[tuple[int, MotionMetrics]]] = []
    for candidate in qualifying:
        if (
            not groups
            or candidate[0] - groups[-1][-1][0] > max_gap_frames + 1
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
                "start_seconds": group[0][0] / fps,
                "end_seconds": group[-1][0] / fps,
                "sample_count": len(group),
                "peak_seconds": peak_frame / fps,
                "peak_component_px": peak.largest_component,
                "peak_changed_gt12_px": peak.changed_pixels[0],
                "peak_changed_gt20_px": peak.changed_pixels[1],
                "peak_changed_gt32_px": peak.changed_pixels[2],
                "peak_mean_abs": peak.mean_abs_difference,
            }
        )
    return intervals


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
    if before_seconds < 0 or after_seconds <= 0:
        raise ValueError("Clip timing must be non-negative with positive duration")
    if not intervals:
        return []
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required to save candidate clips")

    output_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
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
        saved_paths.append(clip_path)
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
) -> MotionMetrics:
    """Measure pixel-wise grayscale change and the largest connected change."""
    if previous_gray.shape != current_gray.shape:
        raise ValueError("Frames must have identical dimensions")
    if previous_gray.ndim != 2:
        raise ValueError("Frames must be grayscale")
    if mask is not None and mask.shape != previous_gray.shape:
        raise ValueError("Mask must have the same dimensions as frames")

    previous = cv2.GaussianBlur(previous_gray, (3, 3), 0)
    current = cv2.GaussianBlur(current_gray, (3, 3), 0)
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

    fps = capture.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        capture.release()
        raise RuntimeError(f"Invalid frame rate in video: {video_path}")

    if len(polygon) < 3:
        capture.release()
        raise ValueError("Polygon must have at least three vertices")
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    video_duration = total_frames / fps if total_frames > 0 else 0.0
    if video_duration <= 0:
        capture.release()
        raise RuntimeError(f"Invalid frame count in video: {video_path}")

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

    def to_roi_gray(frame: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(
            frame[y : y + height, x : x + width], cv2.COLOR_BGR2GRAY
        )

    def is_noisy(seconds: float) -> bool:
        # Median over several adjacent-frame pairs so one bird cannot
        # make a clean image look like snow.
        fractions = []
        for pair in range(5):
            capture.set(
                cv2.CAP_PROP_POS_FRAMES, int((seconds + pair * 0.4) * fps)
            )
            ok_a, frame_a = capture.read()
            ok_b, frame_b = capture.read()
            if ok_a and ok_b:
                fractions.append(
                    changed_fraction(
                        to_roi_gray(frame_a), to_roi_gray(frame_b), polygon_mask
                    )
                )
        return bool(fractions) and float(np.median(fractions)) >= (
            NOISE_CHANGED_FRACTION
        )

    analysis_ranges = [(0.0, video_duration)]
    if noise_filter:
        start_time = recording_start(video_path)
        sun = sun_times(start_time.date()) if start_time else None
        if sun is None or overlaps_twilight_or_night(
            start_time, video_duration, *sun
        ):
            analysis_ranges = find_clean_ranges(is_noisy, video_duration)
            clean_seconds = sum(end - begin for begin, end in analysis_ranges)
            print(
                f"Noise scan {video_path.name}: "
                f"{clean_seconds:.0f}/{video_duration:.0f} s clean "
                + ", ".join(f"{b:.0f}-{e:.0f}s" for b, e in analysis_ranges),
                flush=True,
            )
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)

    history: deque[np.ndarray] = deque(maxlen=max(lags) + 1)
    candidates_by_lag: dict[int, list[tuple[int, MotionMetrics]]] = {
        lag: [] for lag in lags
    }
    active_candidate: dict[str, int | float] | None = None
    saved_clip_count = 0
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
        nonlocal active_candidate, saved_clip_count
        if active_candidate is None:
            return
        clips = save_candidate_clips(
            video_path,
            clips_dir or default_clips_dir(video_path, output_path),
            [active_candidate],
            video_duration,
            clip_before_seconds,
            clip_after_seconds,
        )
        saved_clip_count += len(clips)
        active_candidate = None

    frame_index = 0
    range_index = -1
    range_end_frame = 0
    try:
        with output_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(headers)
            while True:
                if frame_index >= range_end_frame:
                    flush_active_candidate()
                    range_index += 1
                    if range_index >= len(analysis_ranges):
                        break
                    range_start, range_end = analysis_ranges[range_index]
                    frame_index = int(range_start * fps)
                    range_end_frame = int(range_end * fps)
                    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                    history.clear()
                    if frame_index >= range_end_frame:
                        continue
                ok, frame = capture.read()
                if not ok:
                    flush_active_candidate()
                    range_end_frame = frame_index
                    continue
                gray = to_roi_gray(frame)
                row: list[float | int] = [frame_index, frame_index / fps]
                frame_candidates: list[tuple[int, MotionMetrics]] = []
                for lag in lags:
                    if len(history) >= lag:
                        metrics = measure_difference(
                            history[-lag], gray, mask=polygon_mask
                        )
                        if metrics.largest_component >= min_component_px:
                            candidates_by_lag[lag].append(
                                (frame_index, metrics)
                            )
                            frame_candidates.append((lag, metrics))
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
                frame_index += 1

                if frame_candidates:
                    lag, peak = max(
                        frame_candidates,
                        key=lambda candidate: candidate[1].largest_component,
                    )
                    candidate_time = (frame_index - 1) / fps
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
                            "last_candidate_frame": frame_index - 1,
                        }
                    else:
                        active_candidate["end_seconds"] = candidate_time
                        active_candidate["last_candidate_frame"] = (
                            frame_index - 1
                        )
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
                    quiet_frames = (
                        frame_index - 1 - int(active_candidate["last_candidate_frame"])
                    )
                    if quiet_frames / fps > merge_gap_seconds:
                        skip_to_frame = int(
                            active_candidate["last_candidate_frame"]
                            + skip_after_seconds * fps
                        )
                        flush_active_candidate()
                        # The clip already covers the following seconds, so
                        # resume analysis only after the skip window.
                        if skip_to_frame > frame_index:
                            frame_index = min(skip_to_frame, range_end_frame)
                            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                            history.clear()

                if frame_index % int(fps * 60) == 0:
                    print(f"Analyzed {frame_index / fps:.0f} seconds")
    finally:
        capture.release()

    flush_active_candidate()

    events_path = output_path.with_name(
        f"{output_path.stem}_events{output_path.suffix}"
    )
    event_headers = [
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
    ]
    with events_path.open("w", newline="", encoding="utf-8") as events_file:
        writer = csv.DictWriter(events_file, fieldnames=event_headers)
        writer.writeheader()
        all_intervals: list[dict[str, int | float]] = []
        for lag in lags:
            intervals = group_motion_candidates(
                candidates_by_lag[lag],
                fps,
                lag,
                min_component_px,
                merge_gap_seconds,
            )
            for interval in intervals:
                all_intervals.append(interval)
                writer.writerow(interval)
                print(
                    f"Candidate lag={lag} frames "
                    f"{interval['start_seconds']:.2f}-"
                    f"{interval['end_seconds']:.2f}s; peak "
                    f"{interval['peak_component_px']} px component at "
                    f"{interval['peak_seconds']:.2f}s"
                )

    print(f"Candidate intervals: {events_path}")
    clips_dir = clips_dir or default_clips_dir(video_path, output_path)
    print(f"Saved {saved_clip_count} candidate clips to {clips_dir}")
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
    """Analyze each finished recording once, tracked in sikorki/analyzed.txt."""
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
