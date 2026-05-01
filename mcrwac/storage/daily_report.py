import json
from pathlib import Path
from typing import List
from datetime import datetime
import os
import tempfile

from mcrwac.analysis.segment import Segment


def save_daily_report(
    base_output_path: Path,
    camera: str,
    date: str,
    sunrise,
    sunset,
    lower_boundary,
    upper_boundary,
    segment_duration: int,
    segments_total: int,
    segments_filtered: List[Segment],
):
    """
    Zapisuje (nadpisuje) dzienny raport analizy w sposób atomowy.
    """

    output_dir = base_output_path / camera
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"{date}.json"

    report = {
        "date": date,
        "camera": camera,
        "sunrise": str(sunrise),
        "sunset": str(sunset),
        "lower_boundary": str(lower_boundary),
        "upper_boundary": str(upper_boundary),
        "segment_duration_minutes": segment_duration,
        "segments_total": segments_total,
        "segments_filtered_count": len(segments_filtered),
        "segments": [
            {
                "file": s.path.name,
                "start_time": str(s.start_time),
            }
            for s in segments_filtered
        ],
        "generated_at": str(datetime.now()),
    }

    # 🔒 zapis atomowy
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=output_dir,
        delete=False
    ) as tmp_file:
        json.dump(report, tmp_file, indent=4)
        tmp_path = Path(tmp_file.name)

    os.replace(tmp_path, output_file)

    return output_file