import cv2
import numpy as np
from pathlib import Path


class DarkHoleDetector:

    def __init__(
        self,
        roi_width: int,
        roi_height: int,
        canvas_width: int,
        canvas_height: int,
        threshold_value: int = 60,
        debug: bool = False
    ):
        self.roi_width = roi_width
        self.roi_height = roi_height
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.threshold_value = threshold_value
        self.debug = debug

        self.roi_x = (canvas_width - roi_width) // 2
        self.roi_y = (canvas_height - roi_height) // 2

    # ---------------------------------------------------------
    # WYCIĘCIE ROI
    # ---------------------------------------------------------

    def _extract_roi(self, frame):
        return frame[
            self.roi_y:self.roi_y + self.roi_height,
            self.roi_x:self.roi_x + self.roi_width
        ]

    # ---------------------------------------------------------
    # OBLICZENIE CIEMNEJ POWIERZCHNI
    # ---------------------------------------------------------

    def _dark_area(self, gray):
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)

        _, thresh = cv2.threshold(
            blurred,
            self.threshold_value,
            255,
            cv2.THRESH_BINARY_INV
        )

        area = cv2.countNonZero(thresh)

        return area, thresh

    # ---------------------------------------------------------
    # WYLICZENIE BASELINE (pusta dziura)
    # ---------------------------------------------------------

    def compute_baseline(self, video_path: Path, frames_to_sample: int = 30):

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Nie można otworzyć: {video_path}")

        areas = []
        count = 0

        while count < frames_to_sample:
            ret, frame = cap.read()
            if not ret:
                break

            roi = self._extract_roi(frame)

            if len(roi.shape) == 3:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            else:
                gray = roi

            area, _ = self._dark_area(gray)
            areas.append(area)

            count += 1

        cap.release()

        if not areas:
            raise RuntimeError("Nie udało się policzyć baseline.")

        baseline = int(np.median(areas))

        if self.debug:
            print("Baseline (ciemna powierzchnia):", baseline)

        return baseline

    # ---------------------------------------------------------
    # DETEKCJA OBIEKTU
    # ---------------------------------------------------------

    def detect(self, video_path: Path, baseline: int, sensitivity: float = 1.4):

        """
        sensitivity:
            1.0  → równe baseline
            1.3  → 30% większa powierzchnia
            1.5  → 50% większa powierzchnia
        """

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Nie można otworzyć: {video_path}")

        detected = False

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            roi = self._extract_roi(frame)

            if len(roi.shape) == 3:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            else:
                gray = roi

            area, _ = self._dark_area(gray)

            if self.debug:
                print("Aktualna powierzchnia:", area)

            if area > baseline * sensitivity:
                detected = True
                break

        cap.release()
        return detected