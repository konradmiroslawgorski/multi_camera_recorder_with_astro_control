import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
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
            script = f"touch {out}/10-00-00.mkv; sleep 1.2; touch {out}/10-30-00.mkv; sleep 30"
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

    def test_no_file_callback_after_timeout(self) -> None:
        recorder.stop_requested.clear()
        with tempfile.TemporaryDirectory() as tmp:
            end = datetime.now(ZoneInfo("Europe/Warsaw")) + timedelta(seconds=3)
            fired: list[bool] = []
            result = recorder._record_until(
                end,
                ["bash", "-c", "sleep 30"],
                Path(tmp),
                start_timeout=1,
                on_no_file=lambda: fired.append(True),
            )

        self.assertEqual(fired, [True])
        self.assertTrue(result.no_file_created)


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
