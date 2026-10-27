import argparse
import csv
import math
import os
import re
import shutil
import signal
import subprocess
import time as _time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import cv2
import numpy as np


@dataclass(frozen=True)
class OpeningCandidate:
    x: float
    y: float
    score: float


@dataclass
class _CandidateCluster:
    x: float
    y: float
    scores: list[float] = field(default_factory=list)
    samples: set[int] = field(default_factory=set)

    def add(self, candidate: OpeningCandidate, sample_index: int) -> None:
        self.x = (self.x * len(self.scores) + candidate.x) / (len(self.scores) + 1)
        self.y = (self.y * len(self.scores) + candidate.y) / (len(self.scores) + 1)
        self.scores.append(candidate.score)
        self.samples.add(sample_index)


@dataclass(frozen=True)
class TrackResult:
    x: int
    y: int
    confidence: float


@dataclass(frozen=True)
class ActivityRegion:
    x: int
    y: int
    width: int
    height: int
    diagonal: float


@dataclass(frozen=True)
class Appearance:
    start_frame: int


@dataclass(frozen=True)
class ReferenceSpec:
    file_name: str
    crop_origin: tuple[int, int]
    opening: tuple[float, float]


REFERENCE_DIR = Path(__file__).parent / "reference"
# Wzorce z różnym oświetleniem: pochmurny poranek, pełne słońce, błękitne niebo.
REFERENCES = (
    ReferenceSpec("poe_brama_roof.jpg", (600, 400), (998.9, 792.8)),
    ReferenceSpec("poe_brama_roof_sun.jpg", (0, 450), (348.0, 744.0)),
    ReferenceSpec("poe_brama_roof_blue_sky.jpg", (0, 450), (347.0, 744.0)),
)
REFERENCE_FEATURES = 4000
REFERENCE_MIN_INLIERS = 12
REFERENCE_MAX_SPREAD_PX = 12.0
SAMPLE_SECONDS = 3.0
SAMPLE_INTERVAL_SECONDS = 0.25
MIN_CANDIDATE_SCORE = 110.0
MIN_SCORE_MARGIN = 18.0
MIN_TRACK_CONFIDENCE = 0.60
TEMPLATE_RADIUS = 48
SEARCH_RADIUS = 32
BACKGROUND_ALPHA = 0.01
MIN_DARKENING_RATIO = 0.65
BACKGROUND_FREEZE_SECONDS = 30.0
ROI_HALF_WIDTH = 45
ROI_TOP = 60
ROI_BOTTOM = 80
DEFAULT_MIN_DIAGONAL_PX = 30.0
MIN_REGION_SIDE_PX = 12
MAX_REGION_ASPECT = 6.0
DEFAULT_ANALYSIS_OUTPUT_DIR = Path("/opt/cameras/recordings/analysis")
DEFAULT_CLIP_SECONDS = 180.0
DEFAULT_FRAME_STEP = 4
WATCH_POLL_SECONDS = 30.0
WATCH_SETTLE_SECONDS = 120.0
ANALYZED_LIST_NAME = "analyzed.txt"


def _log(message: str, flush: bool = True) -> None:
    """Komunikat na ekran z czasem zegarowym [GG:MM:SS] (nie z nagrania)."""
    print(f"{datetime.now():[%H:%M:%S]} {message}", flush=flush)
DEFAULT_RECORDINGS_ROOT = Path("/opt/cameras/recordings")
DEFAULT_CAMERA = "POE_BRAMA"
RECORDING_NAME_PATTERN = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\.mkv$")
CONFIRM_WINDOW_FRAMES = 5
CONFIRM_HITS = 3
RELEASE_GAP_SECONDS = 1.5
SEARCH_RETRY_SECONDS = 15.0
SEARCH_GRID_SECONDS = 60.0
SEARCH_FALLBACK_SECONDS = 300.0
LOST_TRACKING_SECONDS = 15.0
NEAR_SEARCH_RADIUS_PX = 100.0
MIN_STABILITY = 0.6
NEAR_MIN_STABILITY = 0.4


def _rect_sum(integral: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> int:
    return int(
        integral[y2, x2]
        - integral[y1, x2]
        - integral[y2, x1]
        + integral[y1, x1]
    )


def find_opening_candidates(frame: np.ndarray) -> list[OpeningCandidate]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, dark_mask = cv2.threshold(gray, 45, 255, cv2.THRESH_BINARY_INV)
    count, _, stats, centers = cv2.connectedComponentsWithStats(dark_mask)
    edges = cv2.Canny(gray, 35, 100)
    edge_integral = cv2.integral((edges > 0).astype(np.uint8))
    candidates: list[OpeningCandidate] = []

    for index in range(1, count):
        x, y, width, height, area = stats[index]
        if not (
            15 <= area <= 100
            and 3 <= width <= 12
            and 5 <= height <= 18
            and 1.2 <= height / width <= 4
        ):
            continue

        center_x, center_y = centers[index]
        cx, cy = int(round(center_x)), int(round(center_y))
        y1, y2 = max(0, cy - 40), min(gray.shape[0], cy + 41)
        left_support = max(
            (
                _rect_sum(
                    edge_integral,
                    max(0, cx + offset),
                    y1,
                    max(0, cx + offset) + 1,
                    y2,
                )
                for offset in range(-35, -5)
                if 0 <= cx + offset < gray.shape[1]
            ),
            default=0,
        )
        right_support = max(
            (
                _rect_sum(
                    edge_integral,
                    cx + offset,
                    y1,
                    cx + offset + 1,
                    y2,
                )
                for offset in range(6, 36)
                if 0 <= cx + offset < gray.shape[1]
            ),
            default=0,
        )
        x1, x2 = max(0, cx - 35), min(gray.shape[1], cx + 36)
        horizontal_support = max(
            (
                _rect_sum(edge_integral, x1, row, x2, row + 1)
                for row in range(max(0, cy - 15), min(gray.shape[0], cy + 16))
            ),
            default=0,
        )
        darkness = float(gray[y:y + height, x:x + width].mean())
        score = left_support + right_support + horizontal_support
        score += max(0.0, 80.0 - darkness) * 0.5

        if score >= MIN_CANDIDATE_SCORE:
            candidates.append(OpeningCandidate(float(center_x), float(center_y), score))

    return candidates


def locate_opening(
    frames: Iterable[np.ndarray],
    near: Optional[OpeningCandidate] = None,
    near_radius_px: float = NEAR_SEARCH_RADIUS_PX,
) -> OpeningCandidate:
    clusters: list[_CandidateCluster] = []
    sample_count = 0

    for sample_index, frame in enumerate(frames):
        sample_count = sample_index + 1
        candidates = find_opening_candidates(frame)
        if near is not None:
            candidates = [
                candidate
                for candidate in candidates
                if np.hypot(candidate.x - near.x, candidate.y - near.y) <= near_radius_px
            ]
        candidates = sorted(
            candidates, key=lambda candidate: candidate.score, reverse=True
        )[:100]
        used: set[int] = set()
        for candidate in candidates:
            nearest_index: Optional[int] = None
            nearest_distance = 8.0
            for cluster_index, cluster in enumerate(clusters):
                if sample_index in cluster.samples:
                    continue
                distance = float(np.hypot(candidate.x - cluster.x, candidate.y - cluster.y))
                if distance < nearest_distance:
                    nearest_index = cluster_index
                    nearest_distance = distance
            if nearest_index is None:
                clusters.append(
                    _CandidateCluster(candidate.x, candidate.y, [candidate.score], {sample_index})
                )
                used.add(len(clusters) - 1)
            elif nearest_index in used:
                continue
            else:
                clusters[nearest_index].add(candidate, sample_index)
                used.add(nearest_index)

    ranked = sorted(
        clusters,
        key=lambda cluster: (
            float(np.mean(cluster.scores)),
            len(cluster.samples),
        ),
        reverse=True,
    )
    if not ranked:
        raise RuntimeError("Nie znaleziono wiarygodnego kandydata na otwór.")

    stability = NEAR_MIN_STABILITY if near is not None else MIN_STABILITY
    required_samples = max(3, int(np.ceil(sample_count * stability)))
    stable_candidates = [
        cluster for cluster in ranked if len(cluster.samples) >= required_samples
    ]
    if not stable_candidates:
        best = ranked[0]
        raise RuntimeError(
            "Nie znaleziono stabilnego otworu: najlepszy kandydat "
            f"({best.x:.1f}, {best.y:.1f}) pasował tylko w "
            f"{len(best.samples)}/{sample_count} klatkach kalibracyjnych."
        )

    if near is not None:
        # Kamera tylko minimalnie się porusza, więc liczy się bliskość poprzedniej pozycji.
        closest = min(
            stable_candidates,
            key=lambda cluster: float(np.hypot(cluster.x - near.x, cluster.y - near.y)),
        )
        return OpeningCandidate(closest.x, closest.y, float(np.mean(closest.scores)))

    best, *competitors = stable_candidates
    best_mean = float(np.mean(best.scores))
    second_mean = float(np.mean(competitors[0].scores)) if competitors else 0.0
    score_margin = best_mean - second_mean
    if competitors and score_margin < MIN_SCORE_MARGIN:
        second = competitors[0]
        raise RuntimeError(
            "Lokalizacja otworu jest niejednoznaczna; analiza tej próbki "
            f"została pominięta. Kandydat ({best.x:.1f}, {best.y:.1f}) "
            f"uzyskał {best_mean:.1f} w {len(best.samples)}/{sample_count} "
            f"klatkach, a konkurencyjny ({second.x:.1f}, {second.y:.1f}) "
            f"{second_mean:.1f} w {len(second.samples)}/{sample_count}; "
            f"różnica {score_margin:.1f} jest mniejsza niż "
            f"{MIN_SCORE_MARGIN:.1f}."
        )

    return OpeningCandidate(best.x, best.y, best_mean)


def _sample_calibration_frames(
    capture: cv2.VideoCapture, fps: float, start_frame: int = 0
) -> list[np.ndarray]:
    interval = max(1, int(round(fps * SAMPLE_INTERVAL_SECONDS)))
    sample_count = int(round(SAMPLE_SECONDS * fps / interval)) + 1
    frames: list[np.ndarray] = []

    for sample_index in range(sample_count):
        capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame + sample_index * interval)
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)

    return frames


def _prepare_for_matching(image: np.ndarray) -> np.ndarray:
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.createCLAHE(3.0, (8, 8)).apply(gray)


@dataclass(frozen=True)
class _ReferenceView:
    points: np.ndarray
    descriptors: np.ndarray
    opening: tuple[float, float]


@lru_cache(maxsize=1)
def _load_references() -> list[_ReferenceView]:
    sift = cv2.SIFT_create(REFERENCE_FEATURES)
    views: list[_ReferenceView] = []
    for spec in REFERENCES:
        image = cv2.imread(str(REFERENCE_DIR / spec.file_name), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        keypoints, descriptors = sift.detectAndCompute(_prepare_for_matching(image), None)
        if descriptors is None:
            continue
        points = np.float32([keypoint.pt for keypoint in keypoints]) + np.float32(
            spec.crop_origin
        )
        views.append(_ReferenceView(points, descriptors, spec.opening))
    return views


def _estimate_opening_from_view(
    view: _ReferenceView,
    keypoints: Sequence[cv2.KeyPoint],
    descriptors: np.ndarray,
    frame_shape: tuple[int, ...],
    matcher: cv2.BFMatcher,
) -> Optional[tuple[float, float]]:
    pairs = matcher.knnMatch(view.descriptors, descriptors, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.8 * p[1].distance]
    if len(good) < REFERENCE_MIN_INLIERS:
        return None
    source = np.float32([view.points[m.queryIdx] for m in good])
    target = np.float32([keypoints[m.trainIdx].pt for m in good])
    matrix, inliers = cv2.estimateAffinePartial2D(source, target, ransacReprojThreshold=4.0)
    if matrix is None or int(inliers.sum()) < REFERENCE_MIN_INLIERS:
        return None
    scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
    angle = abs(math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])))
    if not 0.85 <= scale <= 1.18 or angle > 15.0:
        return None
    x, y = matrix @ np.array([view.opening[0], view.opening[1], 1.0])
    height, width = frame_shape[:2]
    if 0 <= x < width and 0 <= y < height:
        return float(x), float(y)
    return None


def _probe_reference(capture: cv2.VideoCapture, frame_index: int) -> bool:
    """Szybki test: czy jedna klatka daje dopasowanie do wzorca (otwór widoczny)."""
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ok, frame = capture.read()
    if not ok:
        return False
    return locate_opening_by_reference([frame], min_estimates=1) is not None


def locate_opening_by_reference(
    frames: Iterable[np.ndarray],
    min_estimates: int = 2,
) -> Optional[OpeningCandidate]:
    """Wylicza położenie otworu z dopasowania dachu i ścian do wzorców.

    Wzorców jest kilka (różne oświetlenie), a w każdej klatce wygrywa pierwszy,
    który da wiarygodne dopasowanie. Działa też po przesunięciu lub obrocie
    kamery. Zwraca None, gdy obraz jest zbyt słaby na wiarygodne dopasowanie.
    """
    views = _load_references()
    if not views:
        return None
    sift = cv2.SIFT_create(REFERENCE_FEATURES)
    matcher = cv2.BFMatcher()
    estimates: list[tuple[float, float]] = []
    sample_count = 0

    for frame in frames:
        sample_count += 1
        keypoints, descriptors = sift.detectAndCompute(_prepare_for_matching(frame), None)
        if descriptors is None or len(keypoints) < REFERENCE_MIN_INLIERS:
            continue
        for view in views:
            estimate = _estimate_opening_from_view(
                view, keypoints, descriptors, frame.shape, matcher
            )
            if estimate is not None:
                estimates.append(estimate)
                break

    if sample_count == 0 or len(estimates) < max(min_estimates, sample_count // 2):
        return None
    xs, ys = zip(*estimates)
    median_x, median_y = float(np.median(xs)), float(np.median(ys))
    spread = max(
        float(np.hypot(x - median_x, y - median_y)) for x, y in estimates
    )
    if spread > REFERENCE_MAX_SPREAD_PX:
        return None
    return OpeningCandidate(median_x, median_y, float(len(estimates)))


def _find_opening(
    capture: cv2.VideoCapture,
    fps: float,
    frame_count: int,
    start_frame: int,
    retry_frames: int,
    near: Optional[OpeningCandidate] = None,
) -> Optional[tuple[OpeningCandidate, np.ndarray, int]]:
    """Szuka momentu, od którego otwór jest wiarygodnie widoczny.

    1. Rzadka siatka (co `SEARCH_GRID_SECONDS`): w każdym punkcie jedna klatka
       dopasowywana do wzorców (kilka sekund zamiast ok. minuty na 13 klatek).
    2. Po trafieniu bisekcja między ostatnim punktem bez otworu a trafionym, do
       dokładności `retry_frames`; wynik potwierdza dopasowanie 13 klatek.
    3. Gdy wzorce nie działają w żadnym punkcie, rzadka siatka z wolniejszą
       metodą bez wzorca (szukanie samego otworu; po utracie ogranicza je `near`).
    """
    grid = max(retry_frames, int(round(fps * SEARCH_GRID_SECONDS)))
    _log(
        f"[{_format_elapsed(start_frame / fps)}] Szukam otworu: siatka co "
        f"{grid / fps:.0f} s, potem bisekcja.",
        flush=True,
    )

    hidden: Optional[int] = None
    index = start_frame
    while index < frame_count:
        if not _probe_reference(capture, index):
            hidden = index
            index += grid
            continue
        first = index
        if hidden is not None:
            low, high = hidden, index
            while high - low > retry_frames:
                middle = (low + high) // 2
                if _probe_reference(capture, middle):
                    high = middle
                else:
                    low = middle
            first = high
        frames = _sample_calibration_frames(capture, fps, first)
        opening = locate_opening_by_reference(frames) if frames else None
        if opening is not None:
            _log(
                f"[{_format_elapsed(first / fps)}] Otwór wyznaczony ze wzorca: "
                f"({opening.x:.0f}, {opening.y:.0f}).",
                flush=True,
            )
            return opening, frames[0], first
        hidden = first
        index = first + grid

    fallback_step = max(grid, int(round(fps * SEARCH_FALLBACK_SECONDS)))
    for attempt_start in range(start_frame, frame_count, fallback_step):
        frames = _sample_calibration_frames(capture, fps, attempt_start)
        if not frames:
            break
        try:
            opening = locate_opening(frames, near=near)
        except RuntimeError as error:
            reason = str(error).split(";")[0].split(":")[0]
            _log(
                f"[{_format_elapsed(attempt_start / fps)}] Otwór niewidoczny ({reason}).",
                flush=True,
            )
            continue
        return opening, frames[0], attempt_start
    return None


def track_opening(
    frame: np.ndarray, reference_gray: np.ndarray, opening: OpeningCandidate
) -> TrackResult:
    height, width = reference_gray.shape
    center_x, center_y = int(round(opening.x)), int(round(opening.y))
    radius = TEMPLATE_RADIUS
    if (
        center_x - radius < 0
        or center_y - radius < 0
        or center_x + radius > width
        or center_y + radius > height
    ):
        return TrackResult(center_x, center_y, 0.0)

    template = reference_gray[
        center_y - radius:center_y + radius,
        center_x - radius:center_x + radius,
    ]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    search = SEARCH_RADIUS
    x1, y1 = max(0, center_x - radius - search), max(0, center_y - radius - search)
    x2 = min(width, center_x + radius + search)
    y2 = min(height, center_y + radius + search)
    search_area = gray[y1:y2, x1:x2]
    match = cv2.matchTemplate(search_area, template, cv2.TM_CCOEFF_NORMED)
    _, confidence, _, location = cv2.minMaxLoc(match)
    return TrackResult(
        x1 + location[0] + radius,
        y1 + location[1] + radius,
        float(confidence),
    )


@dataclass
class AdaptiveBackground:
    image: np.ndarray
    changed_frames: np.ndarray

    @property
    def shape(self) -> tuple[int, ...]:
        return self.image.shape


def _reference_window(
    reference_gray: np.ndarray, opening: OpeningCandidate
) -> Optional[AdaptiveBackground]:
    """Okno wokół otworu o stałym rozmiarze; None, gdy wychodzi poza obraz."""
    center_x, center_y = int(round(opening.x)), int(round(opening.y))
    x1, x2 = center_x - ROI_HALF_WIDTH, center_x + ROI_HALF_WIDTH
    y1, y2 = center_y - ROI_TOP, center_y + ROI_BOTTOM
    height, width = reference_gray.shape
    if x1 < 0 or y1 < 0 or x2 > width or y2 > height:
        return None
    image = reference_gray[y1:y2, x1:x2].astype(np.float32)
    return AdaptiveBackground(image, np.zeros(image.shape, dtype=np.uint16))


def _update_background(
    background: AdaptiveBackground,
    frame: np.ndarray,
    bounds: tuple[int, int, int, int],
    mask: np.ndarray,
    fps: float,
) -> None:
    """Powoli wtapia obraz w tło; zmiany (wiewiórka) są chwilowo zamrożone.

    Zmiana trwająca dłużej niż BACKGROUND_FREEZE_SECONDS jest uznawana za trwałą
    (np. oświetlenie) i też trafia do tła.
    """
    x1, y1, x2, y2 = bounds
    if (y2 - y1, x2 - x1) != background.shape:
        return
    gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    changed = cv2.dilate(mask, np.ones((9, 9), dtype=np.uint8)) > 0
    counts = background.changed_frames
    counts[changed] = np.minimum(counts[changed], 65534) + 1
    counts[~changed] = 0
    freeze_frames = int(BACKGROUND_FREEZE_SECONDS * fps)
    allowed = ((~changed) | (counts > freeze_frames)).astype(np.uint8) * 255
    cv2.accumulateWeighted(gray, background.image, BACKGROUND_ALPHA, allowed)


def _activity_mask(
    frame: np.ndarray,
    reference_gray: np.ndarray,
    opening: OpeningCandidate,
    tracked: TrackResult,
    background: Optional[AdaptiveBackground] = None,
) -> tuple[Optional[np.ndarray], Optional[tuple[int, int, int, int]], float]:
    if tracked.confidence < MIN_TRACK_CONFIDENCE:
        return None, None, 0.0

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    x1 = max(0, tracked.x - ROI_HALF_WIDTH)
    x2 = min(gray.shape[1], tracked.x + ROI_HALF_WIDTH)
    y1 = max(0, tracked.y - ROI_TOP)
    y2 = min(gray.shape[0], tracked.y + ROI_BOTTOM)
    ref_x1 = x1 + int(round(opening.x - tracked.x))
    ref_y1 = y1 + int(round(opening.y - tracked.y))
    reference_roi = reference_gray[ref_y1:ref_y1 + y2 - y1, ref_x1:ref_x1 + x2 - x1]
    current_roi = gray[y1:y2, x1:x2]
    if background is not None and background.shape == current_roi.shape:
        reference_roi = cv2.convertScaleAbs(background.image)
    if reference_roi.shape != current_roi.shape or current_roi.size == 0:
        return None, None, 0.0

    # Wiewiórka jest ciemniejsza od tła; rozjaśnienia (światło, kompresja) pomijamy.
    # Globalna zmiana ekspozycji (mediana różnicy) jest odejmowana, bo mała
    # wiewiórka jej nie zmienia.
    exposure_shift = float(np.median(current_roi.astype(np.int16) - reference_roi))
    reference_roi = cv2.convertScaleAbs(reference_roi, alpha=1.0, beta=exposure_shift)
    difference = cv2.subtract(reference_roi, current_roi)
    difference = cv2.GaussianBlur(difference, (3, 3), 0)
    _, mask = cv2.threshold(difference, 25, 255, cv2.THRESH_BINARY)
    # Cień, słońce i zmiana ekspozycji tylko nieco przyciemniają jasną ścianę;
    # wiewiórka jest wielokrotnie ciemniejsza niż tło.
    much_darker = current_roi < reference_roi * MIN_DARKENING_RATIO
    mask[~much_darker] = 0
    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    ratio = float(cv2.countNonZero(mask) / mask.size)
    return mask, (x1, y1, x2, y2), ratio


def activity_regions(
    mask: np.ndarray,
    min_diagonal_px: float,
    min_side_px: int = MIN_REGION_SIDE_PX,
    max_aspect: float = MAX_REGION_ASPECT,
) -> tuple[list[ActivityRegion], float]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    accepted: list[ActivityRegion] = []
    largest_diagonal = 0.0

    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        diagonal = math.hypot(width, height)
        largest_diagonal = max(largest_diagonal, diagonal)
        if (
            diagonal > min_diagonal_px
            and min(width, height) >= min_side_px
            and max(width, height) / max(1, min(width, height)) <= max_aspect
        ):
            accepted.append(ActivityRegion(x, y, width, height, diagonal))

    accepted.sort(key=lambda region: region.diagonal, reverse=True)
    return accepted, largest_diagonal


class AppearanceDetector:
    def __init__(self, fps: float, frame_step: int = 1):
        # Przy rzadszej analizie okno liczone w próbkach jest krótsze (3 z 5
        # klatek co 1 klatkę odpowiada ok. 2 z 3 próbek co 4. klatkę).
        sparse = frame_step > 1
        self._window = 3 if sparse else CONFIRM_WINDOW_FRAMES
        self._hits = 2 if sparse else CONFIRM_HITS
        self._history: deque[tuple[int, bool]] = deque(maxlen=self._window)
        self._release_gap_frames = max(1, int(round(fps * RELEASE_GAP_SECONDS)))
        self._active = False
        self._last_seen_frame: Optional[int] = None

    def update(self, frame_index: int, has_large_region: bool) -> Optional[Appearance]:
        if self._active:
            if has_large_region:
                self._last_seen_frame = frame_index
            if (
                self._last_seen_frame is not None
                and frame_index - self._last_seen_frame >= self._release_gap_frames
            ):
                self._active = False
                self._last_seen_frame = None
                self._history.clear()
            return None

        self._history.append((frame_index, has_large_region))
        if len(self._history) < self._window:
            return None

        positive_frames = [index for index, detected in self._history if detected]
        if len(positive_frames) < self._hits:
            return None

        self._active = True
        self._last_seen_frame = positive_frames[-1]
        return Appearance(start_frame=positive_frames[0])


def _format_elapsed(seconds: float) -> str:
    total_milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}-{minutes:02d}-{whole_seconds:02d}-{milliseconds:03d}"


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
        if candidate.with_suffix(".csv").exists():
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
    clip_seconds: float,
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

    for appearance in appearances:
        start_seconds = appearance.start_frame / fps
        duration = min(clip_seconds, video_duration - start_seconds)
        if duration <= 0:
            continue

        clip_start = recording_start + timedelta(seconds=start_seconds)
        clip_path = clips_dir / (
            f"{video_path.stem}_{clip_start.strftime('%Y%m%d%H%M%S')}.mkv"
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
                    f"{video_path.stem}_{clip_start.strftime('%Y%m%d%H%M%S')}"
                    f"_{suffix:02d}.mkv"
                )
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-ss",
            f"{start_seconds:.6f}",
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
            str(clip_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            clip_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg nie zapisał klipu {clip_path}: {result.stderr.strip()}"
            )
        clip_paths.append(clip_path)

    return clip_paths


def analyze_video(
    video_path: Path,
    output_path: Optional[Path] = None,
    mode: str = "first",
    min_diagonal_px: float = DEFAULT_MIN_DIAGONAL_PX,
    clip_seconds: float = DEFAULT_CLIP_SECONDS,
    save_preview: bool = False,
    frame_step: int = DEFAULT_FRAME_STEP,
) -> Path:
    if mode not in {"first", "all"}:
        raise ValueError("Tryb musi mieć wartość 'first' albo 'all'.")
    if min_diagonal_px < 0:
        raise ValueError("Minimalna przekątna nie może być ujemna.")
    if frame_step < 1:
        raise ValueError("Krok klatek musi być co najmniej 1.")
    if clip_seconds <= 0:
        raise ValueError("Długość klipu musi być większa od zera.")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Nie można otworzyć pliku: {video_path}")

    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise RuntimeError("Nie udało się odczytać parametrów nagrania.")

    recording_start = _recording_start_time(video_path, frame_count / fps)
    output_path = _reserve_output_path(
        _timestamped_output_path(video_path, output_path, recording_start)
    )
    output_size = (max(2, width // 2), max(2, height // 2))
    writer: Optional[cv2.VideoWriter] = None
    if save_preview:
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps / frame_step,
            output_size,
        )
        if not writer.isOpened():
            capture.release()
            output_path.unlink(missing_ok=True)
            raise RuntimeError(f"Nie można utworzyć podglądu: {output_path}")

    csv_path = output_path.with_suffix(".csv")
    frame_index = 0
    detector = AppearanceDetector(fps, frame_step)
    appearances: list[Appearance] = []
    retry_frames = max(1, int(round(fps * SEARCH_RETRY_SECONDS)))
    analyzed_fps = fps / frame_step
    lost_limit = max(1, int(round(analyzed_fps * LOST_TRACKING_SECONDS)))
    opening: Optional[OpeningCandidate] = None
    last_opening: Optional[OpeningCandidate] = None
    reference_gray: Optional[np.ndarray] = None
    background: Optional[AdaptiveBackground] = None
    lost_frames = 0
    try:
        with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
            rows = csv.writer(csv_file)
            rows.writerow(
                [
                    "czas_s",
                    "x_otworu",
                    "y_otworu",
                    "pewnosc_sledzenia",
                    "zmieniony_obszar",
                    "najwieksza_przekatna_px",
                    "duze_obszary",
                    "zdarzenie",
                ]
            )

            while True:
                if opening is None:
                    found = _find_opening(
                        capture,
                        fps,
                        frame_count,
                        frame_index,
                        retry_frames,
                        near=last_opening,
                    )
                    if found is None:
                        break
                    opening, found_frame, frame_index = found
                    last_opening = opening
                    reference_gray = cv2.cvtColor(found_frame, cv2.COLOR_BGR2GRAY)
                    background = _reference_window(reference_gray, opening)
                    lost_frames = 0
                    detector = AppearanceDetector(fps, frame_step)
                    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                    _log(
                        f"[{_format_elapsed(frame_index / fps)}] Otwór widoczny: "
                        f"({opening.x:.1f}, {opening.y:.1f}); analiza.",
                        flush=True,
                    )
                    continue

                ok, frame = capture.read()
                if not ok:
                    break

                tracked = track_opening(frame, reference_gray, opening)
                lost_frames = (
                    lost_frames + 1 if tracked.confidence < MIN_TRACK_CONFIDENCE else 0
                )
                mask, bounds, changed_ratio = _activity_mask(
                    frame, reference_gray, opening, tracked, background
                )
                regions: list[ActivityRegion] = []
                largest_diagonal = 0.0
                if mask is not None:
                    regions, largest_diagonal = activity_regions(mask, min_diagonal_px)
                    if background is not None and bounds is not None:
                        _update_background(background, frame, bounds, mask, analyzed_fps)
                appearance = detector.update(frame_index, bool(regions))
                if appearance is not None:
                    appearances.append(appearance)
                timestamp = frame_index / fps
                rows.writerow(
                    [
                        f"{timestamp:.3f}",
                        tracked.x,
                        tracked.y,
                        f"{tracked.confidence:.4f}",
                        f"{changed_ratio:.5f}",
                        f"{largest_diagonal:.2f}",
                        len(regions),
                        "wykryto" if appearance is not None else "",
                    ]
                )

                annotated = frame.copy()
                if tracked.confidence >= MIN_TRACK_CONFIDENCE and bounds is not None:
                    x1, y1, x2, y2 = bounds
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 220, 255), 2)
                    cv2.drawMarker(
                        annotated,
                        (tracked.x, tracked.y),
                        (0, 255, 0),
                        cv2.MARKER_CROSS,
                        18,
                        2,
                    )
                    for region in regions:
                        top_left = (x1 + region.x, y1 + region.y)
                        bottom_right = (
                            top_left[0] + region.width,
                            top_left[1] + region.height,
                        )
                        cv2.rectangle(annotated, top_left, bottom_right, (255, 80, 0), 2)
                        cv2.putText(
                            annotated,
                            f"{region.diagonal:.0f}px",
                            (top_left[0], max(20, top_left[1] - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            (255, 80, 0),
                            2,
                            cv2.LINE_AA,
                        )
                    state = (
                        f"obszary {len(regions)} > {min_diagonal_px:.0f}px"
                        f" / bok >= {MIN_REGION_SIDE_PX}px"
                        f" / proporcje <= {MAX_REGION_ASPECT:g}:1"
                        f" | zmiana {changed_ratio:.1%}"
                    )
                else:
                    state = "POMINIETO: slabe dopasowanie"

                if appearance is not None:
                    state += " | WYKRYTO"
                cv2.putText(
                    annotated,
                    f"otwor {opening.x:.0f},{opening.y:.0f} | "
                    f"sledzenie {tracked.confidence:.2f} | {state}",
                    (24, 72),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    (0, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                if writer is not None:
                    preview = cv2.resize(
                        annotated, output_size, interpolation=cv2.INTER_AREA
                    )
                    writer.write(preview)
                frame_index += frame_step
                if appearance is not None and mode == "first":
                    break
                for _ in range(frame_step - 1):
                    capture.grab()
                if lost_frames >= lost_limit:
                    _log(
                        f"[{_format_elapsed(frame_index / fps)}] Otwór przestał być "
                        "widoczny; czekam, aż się pojawi.",
                        flush=True,
                    )
                    opening = None
    except (KeyboardInterrupt, SystemExit):
        # Przerwana analiza nie zostawia niepełnych wyników.
        csv_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise
    finally:
        capture.release()
        if writer is not None:
            writer.release()

    if last_opening is None:
        output_path.unlink(missing_ok=True)
        csv_path.unlink(missing_ok=True)
        _log("Nie znaleziono widocznego otworu w całym nagraniu; brak wyników.")
        return output_path

    clip_paths = _save_appearance_clips(
        video_path,
        output_path,
        appearances,
        fps,
        frame_count,
        clip_seconds,
        recording_start,
    )
    _log(
        f"Ostatni otwór: ({last_opening.x:.1f}, {last_opening.y:.1f}), "
        f"wynik: {last_opening.score:.1f}; "
        f"doszedł do klatki {min(frame_index, frame_count)}/{frame_count} "
        f"(analiza co {frame_step}. klatkę)."
    )
    if save_preview:
        _log(f"Podgląd: {output_path}")
    else:
        output_path.unlink(missing_ok=True)
    if save_preview or clip_paths:
        _log(f"Wyniki CSV: {csv_path}")
    else:
        csv_path.unlink(missing_ok=True)
    if clip_paths:
        _log("Zapisane klipy:")
        for clip_path in clip_paths:
            _log(f"  {clip_path}")
    else:
        _log("Nie wykryto pojawienia się przekraczającego zadany próg.")
    return output_path


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


def _analyzed_list_path(video_path: Path, analysis_dir: Path) -> Path:
    return analysis_dir / video_path.parent.name / ANALYZED_LIST_NAME


def _load_analyzed(list_path: Path) -> set[str]:
    try:
        lines = list_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    return {line.split("\t")[0] for line in lines if line}


def _mark_analyzed(
    video_path: Path, status: str, analysis_dir: Path = DEFAULT_ANALYSIS_OUTPUT_DIR
) -> None:
    list_path = _analyzed_list_path(video_path, analysis_dir)
    list_path.parent.mkdir(parents=True, exist_ok=True)
    with list_path.open("a", encoding="utf-8") as list_file:
        list_file.write(f"{video_path.name}\t{status}\n")


def next_pending_recording(
    camera_dir: Path,
    first_date: date,
    last_date: date,
    now_timestamp: float,
    start: Optional[time] = None,
    end: Optional[time] = None,
    settle_seconds: float = WATCH_SETTLE_SECONDS,
    analysis_dir: Path = DEFAULT_ANALYSIS_OUTPUT_DIR,
) -> Optional[Path]:
    """Zwraca najstarsze zakończone, jeszcze nieanalizowane nagranie.

    Plik jest zakończony, gdy istnieje nowszy plik z tego dnia (rejestrator
    zamknął poprzedni segment) albo gdy nie był zapisywany od `settle_seconds`
    (nagrywanie zatrzymane, np. wieczorem przez astro).
    """
    current = first_date
    while current <= last_date:
        directory = camera_dir / current.isoformat()
        current += timedelta(days=1)
        if not directory.is_dir():
            continue
        everything = select_recordings(directory)
        if not everything:
            continue
        analyzed = _load_analyzed(_analyzed_list_path(everything[0], analysis_dir))
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
) -> None:
    """Analizuje kolejno każde nowe nagranie zaraz po jego zakończeniu.

    Śledzi dni od uruchomienia do bieżącego, więc zaległości po północy też
    zostaną nadrobione. Błąd jednego pliku nie zatrzymuje kolejki.
    """
    first_date = clock().date()
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
        )
        if video is None:
            sleep(poll_seconds)
            continue
        _log(f"=== {video.parent.name}/{video.name} ===")
        try:
            analyze(video)
        except (RuntimeError, OSError, ValueError) as error:
            _log(f"Błąd analizy {video.name}: {error}")
            _mark_analyzed(video, "blad", analysis_dir)
        else:
            _mark_analyzed(video, "ok", analysis_dir)


def _terminate(_signum: int, _frame: object) -> None:
    raise SystemExit(0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wykrywanie dużych zmian przy otworze i zapis klipów."
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
        "(dziś i kolejne dni), także zaległe; kończy się sygnałem SIGTERM/Ctrl+C",
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
        help="Opcjonalna ścieżka bazowa podglądu MP4; domyślnie katalog analizy",
    )
    parser.add_argument(
        "--mode",
        choices=("first", "all"),
        default="first",
        help="first: zatrzymaj analizę po pierwszym wykryciu (domyślnie); "
        "all: analizuj cały film i zapisz wszystkie wykrycia",
    )
    parser.add_argument(
        "--min-diagonal-px",
        type=float,
        default=DEFAULT_MIN_DIAGONAL_PX,
        help=f"Minimalna przekątna prostokąta zmiany w pikselach "
        f"(domyślnie {DEFAULT_MIN_DIAGONAL_PX:g})",
    )
    parser.add_argument(
        "--clip-seconds",
        type=float,
        default=DEFAULT_CLIP_SECONDS,
        help="Długość klipu od wykrytego początku w sekundach "
        f"(domyślnie {DEFAULT_CLIP_SECONDS:g})",
    )
    parser.add_argument(
        "--frame-step",
        type=int,
        default=DEFAULT_FRAME_STEP,
        help="Analizuj co N-tą klatkę (domyślnie "
        f"{DEFAULT_FRAME_STEP}; 1 = każdą, wolniej)",
    )
    parser.add_argument(
        "--save-preview",
        action="store_true",
        help="Zapisz też pełny podgląd MP4 z oznaczeniami i CSV, nawet bez wykrycia "
        "(domyślnie powstają tylko klipy z wykryciem i ich CSV)",
    )
    args = parser.parse_args()

    if args.watch:
        if args.video is not None or args.date is not None:
            parser.error("--watch nie łączy się z plikiem ani z --date.")
    elif (args.video is None) == (args.date is None):
        parser.error("Podaj plik wideo, --date albo --watch.")
    if args.hours and args.date is None and not args.watch:
        parser.error("--hours wymaga --date lub --watch.")
    if args.hours and len(args.hours) > 2:
        parser.error("--hours przyjmuje najwyżej dwie godziny (od i do).")

    try:
        if args.date is not None:
            datetime.strptime(args.date, "%Y-%m-%d")
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
                min_diagonal_px=args.min_diagonal_px,
                clip_seconds=args.clip_seconds,
                save_preview=args.save_preview,
                frame_step=args.frame_step,
            )

        try:
            watch_recordings(
                camera_dir,
                analyze_watched,
                start,
                end,
                poll_seconds=args.poll_seconds,
            )
        except KeyboardInterrupt:
            _log("Przerwano.")
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
                min_diagonal_px=args.min_diagonal_px,
                clip_seconds=args.clip_seconds,
                save_preview=args.save_preview,
                frame_step=args.frame_step,
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
