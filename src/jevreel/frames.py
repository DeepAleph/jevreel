"""Full-resolution stills: what the main agent looks at once Jev has found the moment.

Contact-sheet tiles are sized for Jev to screen every second cheaply, not for reading a slide or a
line of code. These stills are.
"""

from pathlib import Path

from pydantic import BaseModel

from jevreel.media import grab_frame
from jevreel.states import stamp
from jevreel.store import Video


class Still(BaseModel):
    at: float
    time: str
    path: str


def still(video: Video, at: float, max_side: int) -> Path:
    at = min(max(at, 0.0), max(video.manifest.duration - 0.05, 0.0))
    path = video.subdir("frames") / f"frame-{at:010.3f}-{max_side}.jpg"
    if not path.is_file():
        grab_frame(video.media, at, max_side).save(path, format="JPEG", quality=90)
    return path


def zoom(video: Video, start: float, end: float, *, count: int = 4, max_side: int = 1024) -> list[Still]:
    """`count` evenly spaced stills from start to end (one from the middle when count is 1)."""
    if end < start:
        start, end = end, start
    count = max(1, min(count, 16))
    if count == 1:
        times = [(start + end) / 2]
    else:
        step = (end - start) / (count - 1)
        times = [start + i * step for i in range(count)]
    return [Still(at=round(t, 3), time=stamp(t), path=str(still(video, t, max_side))) for t in times]
