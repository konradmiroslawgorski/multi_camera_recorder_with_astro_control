from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import yaml
from astral import LocationInfo
from astral.sun import sun


class RecordingWindow:
    """
    Liczy granice nagrywania na podstawie sunrise/sunset
    oraz offsetów z pliku YAML.
    """

    def __init__(self, config_path: str):
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Brak pliku config: {config_path}")

        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        self.segment_duration = self.config.get("segment", {}).get("duration_minutes", 15)
        
        loc = self.config["location"]

        self.latitude = loc["latitude"]
        self.longitude = loc["longitude"]
        self.timezone = loc["timezone"]

        self.start_expr = self.config["recording_window"]["start"]
        self.stop_expr = self.config["recording_window"]["stop"]

    def _apply_offset(self, base_time: datetime, expr: str) -> datetime:
        """
        Obsługuje:
        'sunrise - 1h'
        'sunset + 2h'
        """

        expr = expr.strip().lower()

        if "-" in expr:
            hours = int(expr.split("-")[1].replace("h", "").strip())
            return base_time - timedelta(hours=hours)

        if "+" in expr:
            hours = int(expr.split("+")[1].replace("h", "").strip())
            return base_time + timedelta(hours=hours)

        return base_time

    def get_window(self, reference_date: datetime):
        """
        Zwraca:
        (lower_boundary, upper_boundary)
        """

        tz = ZoneInfo(self.timezone)

        location = LocationInfo(
            name="local",
            region="local",
            timezone=self.timezone,
            latitude=self.latitude,
            longitude=self.longitude,
        )

        s = sun(location.observer, date=reference_date.date(), tzinfo=tz)

        sunrise = s["sunrise"]
        sunset = s["sunset"]

        lower = self._apply_offset(sunrise, self.start_expr)
        upper = self._apply_offset(sunset, self.stop_expr)

        return sunrise, sunset, lower, upper
