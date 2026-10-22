import sys
from pathlib import Path
from mcrwac.analysis.blob_detector import DarkBlobDetector


def main():

    if len(sys.argv) < 2:
        print("Usage: python -m scripts.test_blob PATH_TO_ANALYSIS_FILE")
        return

    video_path = Path(sys.argv[1])

    detector = DarkBlobDetector(
        min_area=300,
        threshold_value=60,
        debug=True
    )

    result = detector.detect(video_path)

    if result:
        print("OBIEKT WYKRYTY")
    else:
        print("BRAK OBIEKTU")


if __name__ == "__main__":
    main()
