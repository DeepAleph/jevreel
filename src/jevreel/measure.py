"""Measuring what a contact sheet costs before committing to a grid.

TypeSafe publishes no formula for image tokens, so the only honest number is the one the API
reports. For each grid size this sends one sheet with one tiny question, plus one text-only
request with the same question as a baseline, and reports the difference.
"""

import itertools
from pathlib import Path

from pydantic import BaseModel
from typesafe_sdk import Noul

from jevreel.config import USD_PER_MILLION_INPUT_TOKENS
from jevreel.judge import Judge
from jevreel.media import probe, sample_frames
from jevreel.sheets import choose_layout, compose, tile_ids
from jevreel.states import image_asset

QUESTION = {"probe": Noul(instructions="Is any tile of `sheet` mostly black?")}


class GridCost(BaseModel):
    tiles_per_sheet: int
    layout: str
    tile_size: str
    sheet_tokens: int
    """Input tokens the sheet adds to a request, after subtracting the text-only baseline."""
    sheets_per_hour: float
    usd_per_hour_per_pass: float
    """One request per sheet across an hour of video at 1 fps, one question each."""


async def measure(path: Path, judge: Judge, grids: list[int], fps: float = 1.0) -> list[GridCost]:
    info = probe(path)
    baseline = await judge.ask({"sheet": "(no image)"}, QUESTION)
    base_tokens = baseline.usage.input_tokens or 0
    results = []
    for tiles in grids:
        layout = choose_layout(info.aspect, tiles)
        frames = list(itertools.islice(sample_frames(path, fps, (layout.tile_w, layout.tile_h)), tiles))
        sheet = compose(frames, tile_ids(tiles)[: len(frames)], layout)
        answer = await judge.ask({"sheet": image_asset(sheet)}, QUESTION)
        tokens = max(0, (answer.usage.input_tokens or 0) - base_tokens)
        per_hour = 3600 * fps / tiles
        results.append(
            GridCost(
                tiles_per_sheet=tiles,
                layout=f"{layout.cols}x{layout.rows}",
                tile_size=f"{layout.tile_w}x{layout.tile_h}",
                sheet_tokens=tokens,
                sheets_per_hour=round(per_hour, 1),
                usd_per_hour_per_pass=round(
                    per_hour * (answer.usage.input_tokens or 0) * USD_PER_MILLION_INPUT_TOKENS / 1e6, 6
                ),
            )
        )
    return results
