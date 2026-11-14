import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from mcrwac.core import recorder
from mcrwac.core.time_window import RecordingWindow
from mcrwac.notify.notifier import Notifier, build_notifier


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []
        self.lock = threading.Lock()

    def __call__(self, title: str, message: str, priority: str, tags: str) -> None:
        with self.lock:
            self.calls.append((title, message, priority, tags))


class NotifierTests(unittest.TestCase):
    def test_message_format_has_date_time_camera_and_text(self) -> None:
        transport = Recorder()
        notifier = Notifier(transport, "POE1", "Europe/Warsaw")
        notifier.event("start nagrywania filmu [12-00-00.mkv]", title="Start")
        notifier.close()

        title, message, _, _ = transport.calls[0]
        self.assertEqual(title, "Start POE1")
        self.assertRegex(
            message,
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} POE1 "
            r"start nagrywania filmu \[12-00-00\.mkv\]$",
        )

    def test_errors_are_grouped_within_the_hour(self) -> None:
        transport = Recorder()
        clock = FakeClock()
        notifier = Notifier(transport, "POE1", error_group_seconds=3600, clock=clock)

        notifier.error("pierwszy")
        clock.now = 60
        notifier.error("drugi")
        clock.now = 120
        notifier.error("trzeci")
        notifier.close()

        self.assertEqual(len(transport.calls), 2)
        self.assertIn("pierwszy", transport.calls[0][1])
        summary = transport.calls[1][1]
        self.assertIn("2 dodatkowych bledow", summary)
        self.assertIn("drugi", summary)
        self.assertIn("trzeci", summary)

    def test_error_after_the_group_window_is_sent_immediately(self) -> None:
        transport = Recorder()
        clock = FakeClock()
        notifier = Notifier(transport, "POE1", error_group_seconds=3600, clock=clock)

        notifier.error("a")
        clock.now = 3600
        notifier.error("b")
        notifier.close()

        self.assertEqual([c[1].endswith(x) for c, x in zip(transport.calls, "ab")], [True, True])

    def test_transport_failure_does_not_raise(self) -> None:
        def broken(*_args: str) -> None:
            raise OSError("brak sieci")

        notifier = Notifier(broken, "POE1")
        notifier.event("x")
        notifier.close()

    def test_send_is_retried_until_network_is_up(self) -> None:
        attempts: list[int] = []
        delivered = Recorder()

        def flaky(*args: str) -> None:
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError("siec jeszcze niegotowa")
            delivered(*args)

        notifier = Notifier(flaky, "POE1", retry_delays=(0.01, 0.01, 0.01))
        notifier.event("start systemu")
        for _ in range(100):
            if delivered.calls:
                break
            threading.Event().wait(0.05)
        notifier.close()

        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(delivered.calls), 1)

    def test_disabled_without_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("NTFY_TOPIC", None)
            config = {"notifications": {"ntfy": {"enabled": True}}}
            notifier = build_notifier(config, "POE1", "UTC", Path(tmp))
            self.assertFalse(notifier.enabled)

    def test_topic_read_from_notify_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("NTFY_TOPIC", None)
            (Path(tmp) / "notify.env").write_text("NTFY_TOPIC=sekretny-temat\n")
            config = {"notifications": {"ntfy": {"enabled": True}}}
            notifier = build_notifier(config, "POE1", "UTC", Path(tmp))
            self.assertTrue(notifier.enabled)
            self.assertTrue(notifier.transport.url.endswith("/sekretny-temat"))
            notifier.close()


class RecordUntilTests(unittest.TestCase):
    def test_reports_first_and_last_file_at_window_end(self) -> None:
        recorder.stop_requested.clear()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            script = f"printf x > {out}/10-00-00.mkv; sleep 1.2; printf x > {out}/10-30-00.mkv; sleep 30"
            seen: list[str] = []
            end = datetime.now(ZoneInfo("Europe/Warsaw")) + timedelta(seconds=3)

            result = recorder._record_until(
                end, ["bash", "-c", script], out, 60, seen.append
            )

        self.assertEqual(seen, ["10-00-00.mkv"])
        self.assertEqual(result.first_file, "10-00-00.mkv")
        self.assertEqual(result.last_file, "10-30-00.mkv")
        self.assertFalse(result.exited_unexpectedly)

    def test_unexpected_exit_is_reported(self) -> None:
        recorder.stop_requested.clear()
        with tempfile.TemporaryDirectory() as tmp:
            end = datetime.now(ZoneInfo("Europe/Warsaw")) + timedelta(seconds=10)
            result = recorder._record_until(end, ["bash", "-c", "exit 3"], Path(tmp))

        self.assertTrue(result.exited_unexpectedly)
        self.assertEqual(result.returncode, 3)

    def test_exit_during_service_stop_is_not_an_error(self) -> None:
        recorder.stop_requested.clear()
        threading.Timer(0.3, recorder.stop_requested.set).start()
        with tempfile.TemporaryDirectory() as tmp:
            end = datetime.now(ZoneInfo("Europe/Warsaw")) + timedelta(seconds=10)
            result = recorder._record_until(
                end, ["bash", "-c", "sleep 0.2; exit 255"], Path(tmp)
            )
        recorder.stop_requested.clear()

        self.assertFalse(result.exited_unexpectedly)

    def test_stops_ffmpeg_if_first_file_never_appears(self) -> None:
        recorder.stop_requested.clear()
        with tempfile.TemporaryDirectory() as tmp:
            end = datetime.now(ZoneInfo("Europe/Warsaw")) + timedelta(seconds=10)
            result = recorder._record_until(
                end,
                [sys.executable, "-c", "import time; time.sleep(30)"],
                Path(tmp),
                start_timeout=1,
            )

        self.assertTrue(result.stalled)
        self.assertIsNone(result.first_file)
        self.assertIsNotNone(result.returncode)

    def test_stops_ffmpeg_after_file_growth_and_segment_rotation_stop(self) -> None:
        recorder.stop_requested.clear()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            script = (
                "import pathlib, signal, sys, time; "
                "signal.signal(signal.SIGINT, lambda *_: sys.exit(0)); "
                f"p = pathlib.Path({str(out)!r}); "
                "(p / '10-00-00.mkv').write_bytes(b'a'); time.sleep(1.2); "
                "(p / '10-30-00.mkv').write_bytes(b'b'); time.sleep(30)"
            )
            progress: list[bool] = []
            seen: list[str] = []
            end = datetime.now(ZoneInfo("Europe/Warsaw")) + timedelta(seconds=10)
            started = time.monotonic()
            result = recorder._record_until(
                end, [sys.executable, "-c", script], out, 2, seen.append,
                lambda: progress.append(True),
            )
            elapsed = time.monotonic() - started

        self.assertTrue(result.stalled)
        self.assertGreaterEqual(elapsed, 3.5)
        self.assertEqual(seen, ["10-00-00.mkv"])
        self.assertEqual(result.last_file, "10-30-00.mkv")
        self.assertEqual(progress, [True])

    def test_progress_is_detected_even_when_ffmpeg_exits_quickly(self) -> None:
        recorder.stop_requested.clear()
        with tempfile.TemporaryDirectory() as tmp:
            script = (
                "import pathlib, sys; "
                f"pathlib.Path({str(Path(tmp) / 'recording.mkv')!r}).write_bytes(b'x'); "
                "sys.exit(3)"
            )
            progress: list[bool] = []
            end = datetime.now(ZoneInfo("Europe/Warsaw")) + timedelta(seconds=10)
            result = recorder._record_until(
                end, [sys.executable, "-c", script], Path(tmp),
                on_progress=lambda: progress.append(True),
            )

        self.assertTrue(result.exited_unexpectedly)
        self.assertEqual(progress, [True])

    def test_empty_file_is_not_recording_progress(self) -> None:
        recorder.stop_requested.clear()
        with tempfile.TemporaryDirectory() as tmp:
            script = (
                "import pathlib, time; "
                f"pathlib.Path({str(Path(tmp) / 'empty.mkv')!r}).touch(); "
                "time.sleep(30)"
            )
            progress: list[bool] = []
            end = datetime.now(ZoneInfo("Europe/Warsaw")) + timedelta(seconds=10)
            result = recorder._record_until(
                end, [sys.executable, "-c", script], Path(tmp), 2,
                on_progress=lambda: progress.append(True),
            )

        self.assertTrue(result.stalled)
        self.assertIsNone(result.first_file)
        self.assertEqual(progress, [])


class FailureNotificationTests(unittest.TestCase):
    def test_emergency_escalation_and_recovery(self) -> None:
        clock = FakeClock()
        notifier = Mock()
        failures = recorder.FailureNotifications(notifier, clock)

        for _ in range(3):
            failures.failure("brak nagrania")
        self.assertEqual(notifier.event.call_count, 3)
        self.assertIn("tryb awaryjny", notifier.event.call_args.args[0])

        for deadline in (300, 1200, 3000, 6600, 10200):
            failures.failure("brak nagrania")
            clock.now = deadline - 1
            previous = notifier.event.call_count
            failures.tick()
            self.assertEqual(notifier.event.call_count, previous)
            clock.now = deadline
            failures.tick()
            self.assertEqual(notifier.event.call_count, previous + 1)
            self.assertIn("1 kolejnych awarii", notifier.event.call_args.args[0])

        failures.failure("brak nagrania")
        failures.progress()
        self.assertIn("nagrywanie wznowione", notifier.event.call_args.args[0])
        self.assertIn("1 od ostatniego powiadomienia", notifier.event.call_args.args[0])
        count = notifier.event.call_count
        clock.now += 4000
        failures.tick()
        self.assertEqual(notifier.event.call_count, count)
        failures.failure("nowa awaria")
        self.assertIn("awaria 1:", notifier.event.call_args.args[0])

    def test_progress_before_emergency_resets_failure_count(self) -> None:
        notifier = Mock()
        failures = recorder.FailureNotifications(notifier, FakeClock())
        failures.failure("pierwsza")
        failures.failure("druga")
        failures.progress()
        failures.failure("nowa")
        self.assertEqual(notifier.event.call_count, 3)
        self.assertIn("awaria 1:", notifier.event.call_args.args[0])


class StatusTextTests(unittest.TestCase):
    def test_status_contains_astro_and_config(self) -> None:
        config = Path(__file__).resolve().parents[1] / "config" / "recording_config.yaml"
        window = RecordingWindow(str(config))
        now = datetime(2026, 7, 15, 12, tzinfo=ZoneInfo("Europe/Warsaw"))

        text = recorder._describe_status("start", window, now, "1800", "lower_half")

        self.assertIn("wschod 04:32", text)
        self.assertIn("zachod 20:50", text)
        self.assertIn("sunrise - 1h", text)
        self.assertIn("segment 1800s", text)
        self.assertIn("crop lower_half", text)


if __name__ == "__main__":
    unittest.main()
