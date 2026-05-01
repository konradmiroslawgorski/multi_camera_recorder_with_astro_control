from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass
class Segment:
    path: Path
    start_time: datetime

    def end_time(self) -> datetime:
        """
        Zwraca przewidywany czas zakończenia segmentu.
        Zakładamy długość segmentu 15 minut.
        """
        return self.start_time + timedelta(minutes=15)


def parse_segment(path: Path, date: str) -> Segment:
    """
    Parsuje nazwę pliku w formacie HH-MM-SS.mkv
    """

    name = path.stem  # np. 14-30-00
    hour, minute, second = map(int, name.split("-"))

    tz = ZoneInfo("Europe/Warsaw")

    dt = datetime.strptime(date, "%Y-%m-%d").replace(
        hour=hour,
        minute=minute,
        second=second,
        tzinfo=tz
    )

    return Segment(path=path, start_time=dt)