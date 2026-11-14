import logging
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from time import monotonic
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from mcrwac.core.time_window import RecordingWindow
from mcrwac.notify import Notifier, build_notifier


logger = logging.getLogger("camera-recorder")
stop_requested = threading.Event()


def _handle_shutdown(_signum: int, _frame: object) -> None:
    stop_requested.set()


def _next_start(now: datetime, window: RecordingWindow) -> datetime:
    _, _, start, _ = window.get_window(now)
    if now < start:
        return start

    tomorrow = now.date() + timedelta(days=1)
    next_day = datetime.combine(tomorrow, time.min, tzinfo=now.tzinfo)
    return window.get_window(next_day)[2]


def _recording_bounds(
    now: datetime, window: RecordingWindow, always: bool
) -> tuple[datetime, datetime]:
    if always:
        start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        end = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo)
        return start, end
    _, _, start, end = window.get_window(now)
    return start, end


# Kamera z dwoma obrazami w jednej klatce: po przycięciu do dolnej połowy znika jej
# zegar, więc czas serwera jest wypalany w lewym dolnym rogu (zamiast napisu CAMO1).
TIMESTAMP_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
TIMESTAMP_FILTER = (
    "drawbox=x=0:y=ih-70:w=435:h=70:color=black@1:t=fill,"
    f"drawtext=fontfile={TIMESTAMP_FONT}:"
    r"text='%{localtime\:%Y-%m-%d %H\\\:%M\\\:%S}':"
    "x=12:y=h-52:fontsize=36:fontcolor=white"
)
LOWER_HALF_FILTER = f"crop=iw:ih/2:0:ih/2,{TIMESTAMP_FILTER}"


def _ffmpeg_command(
    rtsp_url: str, segment_time: str, output_template: str, pre_crop: str
) -> list[str]:
    command = [
        "/usr/bin/ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "+genpts",
        "-rtsp_transport",
        "tcp",
        "-timeout",
        "5000000",
        "-i",
        rtsp_url,
    ]

    if pre_crop == "lower_half":
        command.extend(
            [
                "-vf",
                LOWER_HALF_FILTER,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-c:a",
                "copy",
            ]
        )
    else:
        command.extend(["-c", "copy"])

    command.extend(
        [
            "-f",
            "segment",
            "-segment_time",
            segment_time,
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            output_template,
        ]
    )
    return command


def _stop_ffmpeg(process: subprocess.Popen, already_signalled: bool = False) -> None:
    if process.poll() is not None:
        return

    if not already_signalled:
        try:
            process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=2 if already_signalled else 3)
    except subprocess.TimeoutExpired:
        if already_signalled:
            try:
                process.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@dataclass
class RecordingResult:
    returncode: Optional[int] = None
    exited_unexpectedly: bool = False
    first_file: Optional[str] = None
    last_file: Optional[str] = None
    stalled: bool = False


def _segment_sizes(output_dir: Path) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for path in output_dir.glob("*.mkv"):
        try:
            sizes[path.name] = path.stat().st_size
        except FileNotFoundError:
            continue
    return sizes


class FailureNotifications:
    intervals = (5 * 60, 15 * 60, 30 * 60, 60 * 60)

    def __init__(self, notifier: Notifier, clock: Callable[[], float] = monotonic) -> None:
        self.notifier = notifier
        self.clock = clock
        self.failures = 0
        self.pending = 0
        self.interval_index = 0
        self.next_notice: Optional[float] = None
        self.last_error = ""

    def _send(self, text: str) -> None:
        self.notifier.event(text, title="Blad", priority="high", tags="warning")

    def failure(self, text: str) -> None:
        logger.error("%s", text)
        self.failures += 1
        self.last_error = text
        if self.failures <= 3:
            message = f"awaria {self.failures}: {text}"
            if self.failures == 3:
                message += "; tryb awaryjny, proby wznowienia trwaja"
                self.next_notice = self.clock() + self.intervals[0]
            self._send(message)
        else:
            self.pending += 1
        self.tick()

    def tick(self) -> None:
        if self.next_notice is None or self.clock() < self.next_notice:
            return
        self._send(
            f"tryb awaryjny: {self.pending} kolejnych awarii od ostatniego "
            f"powiadomienia, lacznie {self.failures}; ostatnia: {self.last_error}"
        )
        self.pending = 0
        self.interval_index = min(self.interval_index + 1, len(self.intervals) - 1)
        self.next_notice = self.clock() + self.intervals[self.interval_index]

    def progress(self) -> None:
        if self.failures >= 3:
            self.notifier.event(
                f"nagrywanie wznowione po {self.failures} awariach "
                f"({self.pending} od ostatniego powiadomienia)",
                title="Wznowiono",
            )
            logger.info("Recording recovered after %s failures", self.failures)
        self.failures = 0
        self.pending = 0
        self.interval_index = 0
        self.next_notice = None
        self.last_error = ""


def _record_until(
    end: datetime,
    command: list[str],
    output_dir: Path,
    start_timeout: float = 60,
    on_first_file: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[], None]] = None,
    on_poll: Optional[Callable[[], None]] = None,
) -> RecordingResult:
    result = RecordingResult()
    known = _segment_sizes(output_dir)
    last_progress = monotonic()
    process = subprocess.Popen(command)
    try:
        while not stop_requested.is_set():
            remaining = (end - datetime.now(end.tzinfo)).total_seconds()
            if remaining <= 0:
                break

            try:
                result.returncode = process.wait(timeout=min(remaining, 1))
            except subprocess.TimeoutExpired:
                result.returncode = None

            if on_poll:
                on_poll()
            sizes = _segment_sizes(output_dir)
            new_files = sorted(sizes.keys() - known.keys())
            if new_files:
                result.last_file = new_files[-1]
            growing = sorted(
                name for name, size in sizes.items() if size > known.get(name, 0)
            )
            if growing:
                last_progress = monotonic()
                result.last_file = growing[-1]
                if result.first_file is None:
                    result.first_file = growing[0]
                    if on_progress:
                        on_progress()
                    if on_first_file:
                        on_first_file(result.first_file)
            known = sizes
            if result.returncode is not None:
                # FFmpeg może zakończyć się przed odebraniem SIGTERM przez rejestrator.
                if not stop_requested.wait(1) and datetime.now(end.tzinfo) < end:
                    result.exited_unexpectedly = True
                    logger.warning(
                        "FFmpeg exited unexpectedly with status %s", result.returncode
                    )
                break
            if stop_requested.is_set() or datetime.now(end.tzinfo) >= end:
                break
            if monotonic() - last_progress >= start_timeout:
                result.stalled = True
                break
    finally:
        _stop_ffmpeg(process, already_signalled=stop_requested.is_set())

    if result.returncode is None:
        result.returncode = process.returncode

    # Segment utworzony tuż przed zatrzymaniem FFmpeg.
    new_files = sorted(_segment_sizes(output_dir).keys() - known.keys())
    if new_files:
        result.last_file = new_files[-1]
    return result


def _describe_status(
    state: str,
    window: RecordingWindow,
    now: datetime,
    segment_time: str,
    pre_crop: str,
) -> str:
    sunrise, sunset, start, end = window.get_window(now)
    return (
        f"{state}\n"
        f"astro OK: wschod {sunrise:%H:%M} ({window.start_expr} = {start:%H:%M}), "
        f"zachod {sunset:%H:%M} ({window.stop_expr} = {end:%H:%M})\n"
        f"okno: {start:%H:%M}-{end:%H:%M} ({window.timezone})\n"
        f"konfiguracja: segment {segment_time}s, crop {pre_crop or 'brak'}"
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    camera = os.environ["CAMERA"]
    rtsp_url = os.environ["RTSP_URL"]
    segment_time = os.environ["SEGMENT_TIME"]
    pre_crop = os.environ.get("PRE_CROP", "")
    always = os.environ.get("MCRWAC_RECORDING_ALWAYS") == "1"

    project_dir = Path(__file__).resolve().parents[2]
    config_path = Path(
        os.environ.get(
            "RECORDING_CONFIG", str(project_dir / "config" / "recording_config.yaml")
        )
    )
    window = RecordingWindow(str(config_path))
    timezone = ZoneInfo(window.timezone)
    base_dir = Path("/opt/cameras/recordings") / camera
    notifications = window.config.get("notifications") or {}
    start_timeout = float(notifications.get("start_timeout_seconds", 60))
    if not 0 < start_timeout < float("inf"):
        raise ValueError("notifications.start_timeout_seconds must be finite and positive")
    notifier = build_notifier(window.config, camera, window.timezone, config_path.parent)
    failures = FailureNotifications(notifier)
    logger.info(
        "Recorder started for %s using %s (always=%s)", camera, config_path, always
    )

    def send_status(state: str) -> None:
        notifier.status(
            _describe_status(
                state, window, datetime.now(timezone), segment_time, pre_crop
            )
        )

    # Status poranny tylko wtedy, gdy to astro otworzyło okno (nie restart w dzień).
    window_opened_by_astro = False

    while not stop_requested.is_set():
        now = datetime.now(timezone)
        start, end = _recording_bounds(now, window, always)

        if not start <= now < end:
            window_opened_by_astro = True
            next_start = _next_start(now, window)
            delay = max(0, (next_start - now).total_seconds())
            logger.info(
                "Recording inactive for %s; next window starts at %s",
                camera,
                next_start.isoformat(),
            )
            stop_requested.wait(min(delay, 30))
            continue

        output_dir = base_dir / now.date().isoformat()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(base_dir / "%Y-%m-%d" / "%H-%M-%S.mkv")
        command = _ffmpeg_command(rtsp_url, segment_time, output_template, pre_crop)

        logger.info(
            "Recording %s until %s in %s",
            camera,
            end.isoformat(),
            output_dir,
        )
        send_morning_status = window_opened_by_astro
        window_opened_by_astro = False

        def on_first_file(name: str) -> None:
            notifier.event(
                f"start nagrywania filmu [{name}]", title="Start", tags="red_circle"
            )
            if send_morning_status:
                send_status("status poranny: nagrywanie wystartowalo wg astro")

        result = _record_until(
            end, command, output_dir, start_timeout, on_first_file,
            failures.progress, failures.tick,
        )

        last_file = result.last_file or "brak pliku"
        if stop_requested.is_set():
            notifier.event(
                f"stop nagrywania filmu [{last_file}] (zatrzymanie uslugi)",
                title="Stop",
                tags="black_square_for_stop",
            )
        elif result.stalled:
            failures.failure(
                f"brak przyrostu nagrania przez {start_timeout:.0f}s, "
                f"restart FFmpeg; ostatni plik [{last_file}]"
            )
        elif result.exited_unexpectedly:
            failures.failure(
                f"FFmpeg zakonczyl prace nieoczekiwanie (kod {result.returncode}), "
                f"ostatni plik [{last_file}]"
            )
        else:
            reason = "koniec dnia" if always else "koniec okna"
            notifier.event(
                f"stop nagrywania filmu [{last_file}] ({reason})",
                title="Stop",
                tags="black_square_for_stop",
            )
            if not always:
                send_status("status wieczorny: nagrywanie zatrzymane wg astro")

        if not stop_requested.is_set() and datetime.now(timezone) < end:
            logger.info("Retrying FFmpeg for %s in 5 seconds", camera)
            stop_requested.wait(5)
            failures.tick()

    logger.info("Recorder stopped for %s", camera)
    notifier.close()


if __name__ == "__main__":
    main()
