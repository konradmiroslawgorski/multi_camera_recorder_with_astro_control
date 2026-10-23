import logging
import os
import queue
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("notifier")

Transport = Callable[[str, str, str, str], None]

MAX_GROUPED_LINES = 10
RETRY_DELAYS = (5, 15, 30, 60, 120)


class NtfyTransport:
    """Wysyła wiadomość do ntfy (https://ntfy.sh lub własny serwer)."""

    def __init__(self, server: str, topic: str, timeout: float = 10.0):
        self.url = f"{server.rstrip('/')}/{topic}"
        self.timeout = timeout

    def __call__(self, title: str, message: str, priority: str, tags: str) -> None:
        headers = {"Title": title, "Priority": priority}
        if tags:
            headers["Tags"] = tags
        request = urllib.request.Request(
            self.url, data=message.encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=self.timeout):
            pass


def load_env_file(path: Path) -> dict[str, str]:
    """Prosty parser KEY=VALUE, żeby sekrety nie trafiały do repozytorium."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


class Notifier:
    """
    Asynchroniczne powiadomienia: nie blokują nagrywania i nie rzucają wyjątków.
    Błędy są grupowane: pierwszy idzie od razu, kolejne w oknie
    `error_group_seconds` trafiają w jednym podsumowaniu.
    """

    def __init__(
        self,
        transport: Optional[Transport],
        camera: str,
        timezone: str = "UTC",
        error_group_seconds: float = 3600,
        clock: Callable[[], float] = time.monotonic,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
    ):
        self.transport = transport
        self.camera = camera
        self.tz = ZoneInfo(timezone)
        self.error_group_seconds = error_group_seconds
        self.clock = clock
        self.retry_delays = retry_delays

        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending_errors: list[str] = []
        self._last_error_sent: Optional[float] = None
        self._closed = False
        self._closing = threading.Event()
        self._worker: Optional[threading.Thread] = None

        if transport is not None:
            self._worker = threading.Thread(
                target=self._run, name="notifier", daemon=True
            )
            self._worker.start()

    @property
    def enabled(self) -> bool:
        return self.transport is not None

    def _stamp(self, text: str) -> str:
        now = datetime.now(self.tz)
        return f"{now:%Y-%m-%d %H:%M:%S} {self.camera} {text}"

    def event(
        self, text: str, title: str = "Info", priority: str = "default", tags: str = ""
    ) -> None:
        self._enqueue(f"{title} {self.camera}", self._stamp(text), priority, tags)

    def status(self, text: str) -> None:
        self.event(text, title="Status", tags="information_source")

    def error(self, text: str) -> None:
        now = self.clock()
        with self._lock:
            due = (
                self._last_error_sent is None
                or now - self._last_error_sent >= self.error_group_seconds
            )
            if not due:
                self._pending_errors.append(self._stamp(text))
                return
            self._last_error_sent = now
        self._enqueue(f"Blad {self.camera}", self._stamp(text), "high", "warning")

    def flush_due_errors(self, force: bool = False) -> None:
        now = self.clock()
        with self._lock:
            if not self._pending_errors:
                return
            if (
                not force
                and self._last_error_sent is not None
                and now - self._last_error_sent < self.error_group_seconds
            ):
                return
            lines = self._pending_errors
            self._pending_errors = []
            self._last_error_sent = now

        shown = lines[-MAX_GROUPED_LINES:]
        header = self._stamp(f"{len(lines)} dodatkowych bledow od ostatniego powiadomienia")
        if len(lines) > len(shown):
            header += f" (pokazano ostatnie {len(shown)})"
        self._enqueue(
            f"Bledy {self.camera}", "\n".join([header, *shown]), "high", "warning"
        )

    def _enqueue(self, title: str, message: str, priority: str, tags: str) -> None:
        if self.transport is not None:
            self._queue.put((title, message, priority, tags))

    def _send(self, item: tuple[str, str, str, str]) -> None:
        # Po starcie systemu sieć bywa jeszcze niegotowa, więc ponawiamy.
        delays = iter(self.retry_delays)
        while True:
            try:
                self.transport(*item)  # type: ignore[misc]
                return
            except Exception as exc:
                delay = next(delays, None)
                if delay is None or self._closing.is_set():
                    logger.warning("Nie udalo sie wyslac powiadomienia: %s", exc)
                    return
                logger.info("Powiadomienie nieudane (%s), ponowie za %ss", exc, delay)
                self._closing.wait(delay)

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=1)
            except queue.Empty:
                self.flush_due_errors()
                continue
            if item is None:
                return
            self._send(item)

    def close(self, timeout: float = 10.0) -> None:
        """Wysyła zaległe błędy i opróżnia kolejkę przed zakończeniem procesu."""
        if self._worker is None or self._closed:
            return
        self._closed = True
        self.flush_due_errors(force=True)
        self._closing.set()
        self._queue.put(None)
        self._worker.join(timeout)


def build_notifier(
    config: dict, camera: str, timezone: str, config_dir: Path
) -> Notifier:
    notifications = (config or {}).get("notifications") or {}
    ntfy = notifications.get("ntfy") or {}
    group_seconds = float(notifications.get("error_group_minutes", 60)) * 60

    if not ntfy.get("enabled", False):
        return Notifier(None, camera, timezone, group_seconds)

    topic_key = ntfy.get("topic_env", "NTFY_TOPIC")
    topic = os.environ.get(topic_key) or load_env_file(config_dir / "notify.env").get(
        topic_key
    )
    if not topic:
        logger.warning(
            "Powiadomienia ntfy wlaczone, ale brak %s (env lub config/notify.env)",
            topic_key,
        )
        return Notifier(None, camera, timezone, group_seconds)

    transport = NtfyTransport(
        ntfy.get("server", "https://ntfy.sh"),
        topic,
        float(ntfy.get("timeout_seconds", 10)),
    )
    return Notifier(transport, camera, timezone, group_seconds)
