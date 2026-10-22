import cv2
import numpy as np
from pathlib import Path


print("### LOADED adaptive_dark_detector (VERBOSE EVENT VERSION) ###")


class AdaptiveDarkDetector:

    def __init__(
        self,
        roi_width: int,
        roi_height: int,
        canvas_width: int,
        canvas_height: int,
        window_seconds: int,
        sigma_threshold: float,
        debug: bool = False
    ):
        self.roi_width = roi_width
        self.roi_height = roi_height
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.window_seconds = window_seconds
        self.sigma_threshold = sigma_threshold
        self.debug = debug

        self.roi_x = (canvas_width - roi_width) // 2
        self.roi_y = (canvas_height - roi_height) // 2

    # ---------------------------------------------------------

    def _extract_roi(self, frame):
        return frame[
            self.roi_y:self.roi_y + self.roi_height,
            self.roi_x:self.roi_x + self.roi_width
        ]

    # ---------------------------------------------------------

    def _compute_darkness(self, gray):
        return 255.0 - float(np.mean(gray))

    # ---------------------------------------------------------

    def detect_all_events(
        self,
        video_path: Path,
        output_dir: Path
    ):

        print(f"\n--- ANALIZA: {video_path.name} ---")

        cap = cv2.VideoCapture(str(video_path))

        if not cap.isOpened():
            raise RuntimeError(f"Nie można otworzyć: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 10

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        window_size = int(self.window_seconds * fps)
        if window_size < 2:
            window_size = 2

        values = [0.0] * window_size
        index = 0
        count = 0

        S = 0.0
        Q = 0.0

        frame_index = 0
        detected_frames = []

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_index % 200 == 0:
                percent = (frame_index / total_frames) * 100
                print(f"Progress: {percent:.1f}% ({frame_index}/{total_frames})")

            roi = self._extract_roi(frame)

            if len(roi.shape) == 3:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            else:
                gray = roi

            v_new = self._compute_darkness(gray)

            if count < window_size:
                values[index] = v_new
                S += v_new
                Q += v_new * v_new
                count += 1
            else:
                v_old = values[index]
                S = S + v_new - v_old
                Q = Q + v_new * v_new - v_old * v_old
                values[index] = v_new

            index = (index + 1) % window_size

            N = count

            if N >= 2:
                mean = S / N
                variance = (Q / N) - (mean * mean)
                if variance < 0:
                    variance = 0.0

                std = np.sqrt(variance)
                if std < 1e-9:
                    std = 1e-9

                z = (v_new - mean) / std

                if z > self.sigma_threshold:
                    print(f"EVENT at frame {frame_index}  Z={z:.2f}")
                    detected_frames.append(frame_index)

            frame_index += 1

        cap.release()

        print(f"Znaleziono {len(detected_frames)} eventów w pliku.")

        if not detected_frames:
            return 0

        output_dir.mkdir(parents=True, exist_ok=True)

        base_name = video_path.stem.replace("_analysis", "")

        for i, event_frame in enumerate(detected_frames):

            event_file = output_dir / f"{base_name}_event_{i+1}.mp4"

            if event_file.exists():
                continue

            self._save_event_clip(
                video_path,
                event_file,
                event_frame,
                fps,
                total_frames
            )

        return len(detected_frames)

    # ---------------------------------------------------------

    def _save_event_clip(
        self,
        video_path,
        output_path,
        event_frame,
        fps,
        total_frames
    ):

        cap = cv2.VideoCapture(str(video_path))

        start_frame = max(event_frame - 5, 0)
        end_frame = min(event_frame + 15, total_frames)

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(
            str(output_path),
            fourcc,
            fps,
            (self.canvas_width, self.canvas_height)
        )

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        current = start_frame

        while current < end_frame:
            ret, frame = cap.read()
            if not ret:
                break

            out.write(frame)
            current += 1

        cap.release()
        out.release()