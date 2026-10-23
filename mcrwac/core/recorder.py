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
from mcrwac.notify import build_notifier


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
        "-stimeout",
        "5000000",
        "-i",
        rtsp_url,
    ]

    if pre_crop == "lower_half":
        command.extend(
            [
                "-vf",
                "crop=iw:ih/2:0:ih/2",
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
    no_file_created: bool = False


def _list_segments(output_dir: Path) -> set[str]:
    return {path.name for path in output_dir.glob("*.mkv")}


def _record_until(
    end: datetime,
    command: list[str],
    output_dir: Path,
    start_timeout: float = 60,
    on_first_file: Optional[Callable[[str], None]] = None,
    on_no_file: Optional[Callable[[], None]] = None,
) -> RecordingResult:
    result = RecordingResult()
    known = _list_segments(output_dir)
    started = monotonic()
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
            else:
                # systemd wysyła SIGTERM do całej grupy, więc FFmpeg może
                # zakończyć się zanim nasz handler ustawi stop_requested.
                if not stop_requested.wait(1):
                    result.exited_unexpectedly = True
                    logger.warning(
                        "FFmpeg exited unexpectedly with status %s", result.returncode
                    )
                break

            new_files = sorted(_list_segments(output_dir) - known)
            if new_files:
                known.update(new_files)
                result.last_file = new_files[-1]
                if result.first_file is None:
                    result.first_file = new_files[0]
                    if on_first_file:
                        on_first_file(result.first_file)
            elif (
                result.first_file is None
                and not result.no_file_created
                and monotonic() - started > start_timeout
            ):
                result.no_file_created = True
                if on_no_file:
                    on_no_file()
    finally:
        _stop_ffmpeg(process, already_signalled=stop_requested.is_set())

    if result.returncode is None:
        result.returncode = process.returncode

    # Segment utworzony tuż przed zatrzymaniem FFmpeg.
    new_files = sorted(_list_segments(output_dir) - known)
    if new_files:
        result.last_file = new_files[-1]
        if result.first_file is None:
            result.first_file = new_files[0]
            if on_first_file:
                on_first_file(result.first_file)
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
    notifier = build_notifier(window.config, camera, window.timezone, config_path.parent)
    logger.info("Recorder started for %s using %s", camera, config_path)

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
        _, _, start, end = window.get_window(now)

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

        def on_no_file() -> None:
            notifier.error(
                f"FFmpeg dziala, ale od {start_timeout:.0f}s nie powstal zaden plik .mkv"
            )

        result = _record_until(
            end, command, output_dir, start_timeout, on_first_file, on_no_file
        )

        last_file = result.last_file or "brak pliku"
        if result.exited_unexpectedly:
            notifier.error(
                f"FFmpeg zakonczyl prace nieoczekiwanie (kod {result.returncode}), "
                f"ostatni plik [{last_file}]"
            )
        elif stop_requested.is_set():
            notifier.event(
                f"stop nagrywania filmu [{last_file}] (zatrzymanie uslugi)",
                title="Stop",
                tags="black_square_for_stop",
            )
        else:
            notifier.event(
                f"stop nagrywania filmu [{last_file}] (koniec okna)",
                title="Stop",
                tags="black_square_for_stop",
            )
            send_status("status wieczorny: nagrywanie zatrzymane wg astro")

        if not stop_requested.is_set() and datetime.now(timezone) < end:
            logger.info("Retrying FFmpeg for %s in 5 seconds", camera)
            stop_requested.wait(5)

    logger.info("Recorder stopped for %s", camera)
    notifier.close()


if __name__ == "__main__":
    main()
