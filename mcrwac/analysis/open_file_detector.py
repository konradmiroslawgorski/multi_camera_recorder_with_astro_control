import subprocess
from pathlib import Path


def is_file_open(path: Path) -> bool:
    """
    Sprawdza czy plik jest aktualnie otwarty (np. przez ffmpeg)
    """
    try:
        result = subprocess.run(
            ["lsof", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False
