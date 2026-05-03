import logging
import subprocess
import sys
from pathlib import Path
import re

from mcrwac.analysis.segment_locator import SegmentLocator
from mcrwac.analysis.segment import parse_segment
from mcrwac.analysis.astro_filter import filter_segments_by_astronomy
from mcrwac.core.analysis_config import AnalysisConfig


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger("segment-runner")

BASE_RECORDINGS = Path("/opt/cameras/recordings")
BASE_RESULTS = Path("/opt/cameras/analysis")

CONFIG_PATH = "config/recording_config.yaml"
ANALYSIS_CONFIG_PATH = "config/analysis_config.yaml"

DATE_REGEX = re.compile(r"\d{4}-\d{2}-\d{2}")


# ============================================================
# CLI
# ============================================================

def parse_cli():

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m scripts.test_segments CAMERA")
        print("  python -m scripts.test_segments CAMERA DATE")
        print("  python -m scripts.test_segments CAMERA FILE.mkv")
        print("  python -m scripts.test_segments CAMERA DATE FILE.mkv")
        print("  python -m scripts.test_segments CAMERA FILE.mkv -force")
        print("  python -m scripts.test_segments CAMERA DATE FILE.mkv -force")
        sys.exit(1)

    camera = sys.argv[1]
    date = None
    file = None
    force = "-force" in sys.argv

    args = [a for a in sys.argv[2:] if a != "-force"]

    for arg in args:
        if arg.endswith(".mkv"):
            file = arg
        elif DATE_REGEX.fullmatch(arg):
            date = arg
        else:
            raise RuntimeError(f"Nieznany argument: {arg}")

    return camera, date, file, force


# ============================================================
# UTILS
# ============================================================

def build_time_offset_seconds(filename: str) -> int:
    stem = Path(filename).stem
    h, m, s = map(int, stem.split("-"))
    return h * 3600 + m * 60 + s


def run_ffmpeg_analysis(input_file, output_file, roi, canvas, overlay, time_window):

    offset = build_time_offset_seconds(input_file.name)

    canvas_w = max(canvas["width"], roi["width"])
    canvas_h = max(canvas["height"], roi["height"])

    start_second = time_window.get("start_second", 0)
    end_second = time_window.get("end_second", None)

    vf = (
        f"crop={roi['width']}:{roi['height']}:{roi['left']}:{roi['top']},"
        f"format=gray,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"drawtext=fontcolor=white:fontsize={overlay['font_size']}:"
        f"box=1:boxcolor=black@0.5:"
        f"text='%{{eif\\:(t+{offset})/3600\\:d\\:2}}\\:"
        f"%{{eif\\:mod((t+{offset})/60\\,60)\\:d\\:2}}\\:"
        f"%{{eif\\:mod(t+{offset}\\,60)\\:d\\:2}}':"
        f"x=(w-tw)/2:y=h-th-15"
    )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_second),
        "-i", str(input_file),
    ]

    if end_second is not None:
        duration = end_second - start_second
        cmd += ["-t", str(duration)]

    cmd += [
        "-vf", vf,
        "-c:v", "mjpeg",          # <-- szybkie kodowanie intra
        "-q:v", "5",              # jakość (2=wysoka, 31=najgorsza)
        "-an",                    # brak audio (niepotrzebne)
        str(output_file),
    ]

    logger.info(f"Analiza (grayscale+mjpeg): {input_file}")
    subprocess.run(cmd, check=True)


# ============================================================
# MAIN
# ============================================================

def main():

    camera, date, specific_file, force = parse_cli()

    camera_dir = BASE_RECORDINGS / camera
    if not camera_dir.exists():
        raise RuntimeError(f"Kamera nie istnieje: {camera}")

    analysis_cfg = AnalysisConfig(ANALYSIS_CONFIG_PATH)
    camera_cfg = analysis_cfg.get_camera_config(camera)

    roi = camera_cfg["roi"]
    canvas = camera_cfg["canvas"]
    overlay = camera_cfg["overlay"]
    time_window = camera_cfg["time_window"]

    # ========================================================
    # TRYB POJEDYNCZY PLIK
    # ========================================================
    if specific_file:

        found_file = None

        if date:
            candidate = camera_dir / date / specific_file
            if candidate.exists():
                found_file = candidate

        if not found_file:
            for day_dir in camera_dir.iterdir():
                if day_dir.is_dir():
                    candidate = day_dir / specific_file
                    if candidate.exists():
                        found_file = candidate
                        date = day_dir.name
                        break

        if not found_file:
            raise RuntimeError("Plik nie istnieje w katalogu kamery.")

        result_file = BASE_RESULTS / camera / date / f"{found_file.stem}_analysis.avi"

        if result_file.exists() and not force:
            logger.info("Plik już przetworzony.")
            return

        run_ffmpeg_analysis(found_file, result_file, roi, canvas, overlay, time_window)
        return

    # ========================================================
    # TRYB DZIEŃ / WSZYSTKO
    # ========================================================
    locator = SegmentLocator(BASE_RECORDINGS)

    if not date:
        dates = sorted(d.name for d in camera_dir.iterdir() if d.is_dir())
    else:
        dates = [date]

    for DATE in dates:

        logger.info(f"=== Kamera: {camera} | Data: {DATE} ===")

        segment_paths = locator.list_segments(camera, DATE)

        if not segment_paths:
            continue

        segments = [parse_segment(p, DATE) for p in segment_paths]

        active = locator.get_active_segment(camera, DATE)
        if active:
            segments = [s for s in segments if s.path.name != active]

        filtered, sunrise, sunset, lower, upper, duration = \
            filter_segments_by_astronomy(segments, DATE, CONFIG_PATH)

        logger.info(f"After astro filter: {len(filtered)}")

        for segment in filtered:

            result_file = BASE_RESULTS / camera / DATE / f"{segment.path.stem}_analysis.avi"

            if result_file.exists():
                continue

            run_ffmpeg_analysis(segment.path, result_file, roi, canvas, overlay, time_window)


if __name__ == "__main__":
    main()