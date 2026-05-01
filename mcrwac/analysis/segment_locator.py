from pathlib import Path
from typing import List, Optional
import subprocess


class SegmentLocator:
    def __init__(self, base_path: Path):
        self.base_path = base_path

    def list_segments(self, camera: str, date: str) -> List[Path]:
        directory = self.base_path / camera / date

        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        return sorted(directory.glob("*.mkv"))

    def get_active_segment(self, camera: str, date: str) -> Optional[str]:
        """
        Zwraca nazwę pliku .mkv aktualnie otwartego do zapisu (jeśli istnieje).
        Wykorzystuje lsof.
        """

        directory = self.base_path / camera / date

        if not directory.exists():
            return None

        try:
            result = subprocess.run(
                ["lsof", "+D", str(directory)],
                capture_output=True,
                text=True,
                check=False
            )

            for line in result.stdout.splitlines():
                if ".mkv" in line:
                    parts = line.split()
                    return Path(parts[-1]).name

        except Exception:
            pass

        return None