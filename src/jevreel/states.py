"""The state Jev sees for one contact sheet, and a meter that counts what each operation spends.

TypeSafe's guidance is to send only what the questions need: unrelated detail in the state costs
accuracy. So a sheet's state carries the image, its time window, how to read the tiles, and the
transcript only when a question is about speech. The tile-to-time map stays in code.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PIL import Image
from typesafe_sdk import Image as JevImage
from typesafe_sdk import Question, SystemOneResponse

from jevreel.config import estimated_cost_usd
from jevreel.judge import Judge
from jevreel.media import fit_for_jev, jpeg_bytes
from jevreel.sheets import SheetInfo, tile_ids
from jevreel.store import Video
from jevreel.timefmt import clock, span
from jevreel.transcript import Segment

MAX_LINES = 254  # a Choice takes at most 255 options, and one is "none"


def stamp(seconds: float) -> str:
    """A tile's time, with tenths only when frames are less than a second apart."""
    whole = clock(seconds)
    tenths = round((seconds % 1) * 10)
    return f"{whole}.{tenths}" if 0 < tenths < 10 else whole


def layout_note(fps: float, *, in_time_order: bool = True) -> str:
    interval = 1 / fps
    gap = f"{interval:g} second" + ("" if interval == 1 else "s")
    order = (
        f"Tiles are frames {gap} apart, in time order: left to right, then top to bottom."
        if in_time_order
        else "Tiles are in shuffled order, not time order."
    )
    return f"{order} Each tile's ID is printed in its top-left corner."


def numbered_lines(video: Video, start: float, end: float) -> dict[str, Segment]:
    """Transcript segments in a window, keyed L01, L02, ... so a Choice can point at one."""
    if video.transcript is None:
        return {}
    segments = video.transcript.window(start, end)[:MAX_LINES]
    return dict(zip(tile_ids(len(segments), prefix="L"), segments, strict=True))


def lines_state(lines: Mapping[str, Segment]) -> dict[str, str]:
    return {line_id: f"[{clock(s.start)}] {s.text}" for line_id, s in lines.items()}


def sheet_state(
    video: Video,
    sheet: SheetInfo,
    image: JevImage,
    *,
    lines: Mapping[str, Segment] | None = None,
    in_time_order: bool = True,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "window": span(sheet.start, sheet.end),
        "sheet": image,
        "layout": layout_note(video.manifest.fps, in_time_order=in_time_order),
    }
    if lines:
        state["transcript"] = lines_state(lines)
    return state


def sheet_asset(video: Video, sheet: SheetInfo) -> JevImage:
    return JevImage(video.sheet_path(sheet.index))


def image_asset(image: Image.Image) -> JevImage:
    return JevImage(jpeg_bytes(fit_for_jev(image)))


def reference_asset(path: Path) -> JevImage:
    with Image.open(path) as image:
        return image_asset(image)


class Meter:
    """Counts the requests and input tokens of one operation, even while other operations share the judge."""

    def __init__(self, judge: Judge) -> None:
        self.judge = judge
        self.requests = 0
        self.cached = 0
        self.input_tokens = 0

    async def ask(
        self, state: Any, questions: Mapping[str, Question], *, long_context: bool = False
    ) -> SystemOneResponse:
        result = await self.judge.ask(state, questions, long_context=long_context)
        if result.usage.input_tokens:
            self.requests += 1
            self.input_tokens += result.usage.input_tokens
        else:
            self.cached += 1  # answered from jevreel's cache, free
        return result

    def spent(self) -> dict[str, int | float]:
        """The cost fields every result reports."""
        return {
            "requests": self.requests,
            "cached": self.cached,
            "input_tokens": self.input_tokens,
            "estimated_usd": round(estimated_cost_usd(self.input_tokens), 6),
        }
