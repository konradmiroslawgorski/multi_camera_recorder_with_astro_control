import sys
from pathlib import Path
import logging

from mcrwac.analysis.adaptive_dark_detector import AdaptiveDarkDetector
from mcrwac.core.analysis_config import AnalysisConfig


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger("adaptive-detector")

ANALYSIS_BASE = Path("/opt/cameras/analysis")


def main():

    if len(sys.argv) < 3:
        print("Usage:")
        print("python -m scripts.test_adaptive_detector CAMERA DATE")
        return

    camera = sys.argv[1]
    date = sys.argv[2]

    analysis_dir = ANALYSIS_BASE / camera / date
    events_dir = analysis_dir / "events"

    cfg = AnalysisConfig("config/analysis_config.yaml")
    camera_cfg = cfg.get_camera_config(camera)

    roi = camera_cfg["roi"]
    canvas = camera_cfg["canvas"]
    detection = camera_cfg["detection"]

    detector = AdaptiveDarkDetector(
        roi_width=roi["width"],
        roi_height=roi["height"],
        canvas_width=canvas["width"],
        canvas_height=canvas["height"],
        window_seconds=detection["window_seconds"],
        sigma_threshold=detection["sigma_threshold"],
        debug=False
    )

    files = sorted(analysis_dir.glob("*_analysis.avi"))

    logger.info(f"Plików do analizy: {len(files)}")

    total_events = 0

    for video_path in files:

        count = detector.detect_all_events(
            video_path,
            events_dir
        )

        if count > 0:
            logger.info(f"{video_path.name} → {count} eventów")

        total_events += count

    logger.info("=====================================")
    logger.info(f"Łącznie wykrytych eventów: {total_events}")
    logger.info("===== KONIEC ANALIZY DNIA =====")


if __name__ == "__main__":
    main()