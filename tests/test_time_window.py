import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mcrwac.core.recorder import _ffmpeg_command, _next_start
from mcrwac.core.time_window import RecordingWindow


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "recording_config.yaml"


class RecordingWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = RecordingWindow(str(CONFIG_PATH))

    def test_window_matches_configured_sunrise_and_sunset_offsets(self) -> None:
        sunrise, sunset, start, end = self.window.get_window(
            datetime(2026, 7, 15)
        )

        self.assertEqual(start, sunrise - timedelta(hours=1))
        self.assertEqual(end, sunset + timedelta(hours=1))
        self.assertEqual(sunrise.tzinfo, ZoneInfo("Europe/Warsaw"))
        self.assertEqual(sunset.tzinfo, ZoneInfo("Europe/Warsaw"))

    def test_window_uses_warsaw_offset_on_both_sides_of_dst(self) -> None:
        winter_sunrise, _, winter_start, _ = self.window.get_window(
            datetime(2026, 1, 15)
        )
        summer_sunrise, _, summer_start, _ = self.window.get_window(
            datetime(2026, 7, 15)
        )

        self.assertEqual(winter_sunrise.utcoffset(), timedelta(hours=1))
        self.assertEqual(summer_sunrise.utcoffset(), timedelta(hours=2))
        self.assertEqual(winter_start, winter_sunrise - timedelta(hours=1))
        self.assertEqual(summer_start, summer_sunrise - timedelta(hours=1))

    def test_next_start_is_tomorrow_when_today_window_is_over(self) -> None:
        now = datetime(2026, 7, 15, 23, tzinfo=ZoneInfo("Europe/Warsaw"))

        next_start = _next_start(now, self.window)

        self.assertEqual(next_start.date().isoformat(), "2026-07-16")
        self.assertEqual(next_start.tzinfo, ZoneInfo("Europe/Warsaw"))

    def test_ffmpeg_command_preserves_camera_crop_settings(self) -> None:
        command = _ffmpeg_command(
            "rtsp://camera/stream",
            "1800",
            "/recordings/%Y-%m-%d/%H-%M-%S.mkv",
            "lower_half",
        )

        self.assertIn("crop=iw:ih/2:0:ih/2", command)
        self.assertIn("libx264", command)
        self.assertEqual(command[command.index("-segment_time") + 1], "1800")
        self.assertEqual(command[-1], "/recordings/%Y-%m-%d/%H-%M-%S.mkv")


if __name__ == "__main__":
    unittest.main()
