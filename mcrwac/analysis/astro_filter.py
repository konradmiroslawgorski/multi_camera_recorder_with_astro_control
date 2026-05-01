from datetime import datetime, timedelta
from typing import List
import logging

from mcrwac.analysis.segment import Segment
from mcrwac.core.time_window import RecordingWindow


logger = logging.getLogger("astro-filter")


def filter_segments_by_astronomy(
    segments: List[Segment],
    date: str,
    config_path: str
):

    rw = RecordingWindow(config_path)

    reference_date = datetime.strptime(date, "%Y-%m-%d")

    sunrise, sunset, lower_boundary, upper_boundary = rw.get_window(reference_date)

    logger.info(f"Wschód słońca: {sunrise}")
    logger.info(f"Zachód słońca: {sunset}")
    logger.info(f"Granica dolna (po offsetach): {lower_boundary}")
    logger.info(f"Granica górna (po offsetach): {upper_boundary}")
    logger.info(f"Długość segmentu (min): {rw.segment_duration}")

    filtered = [
        s for s in segments
        if s.start_time <= upper_boundary
        and (s.start_time + timedelta(minutes=rw.segment_duration)) >= lower_boundary
    ]

    logger.info(f"Segmenty po filtrze astronomicznym: {len(filtered)}")

    return (
        filtered,
        sunrise,
        sunset,
        lower_boundary,
        upper_boundary,
        rw.segment_duration,
    )