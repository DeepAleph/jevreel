"""Contact sheets: many seconds of video tiled into one labelled image that Jev judges in one request.

Every tile carries its ID (T01, T02, ...) in the top-left corner, and the request's state maps each
ID to its timestamp. A Choice over tile IDs then turns Jev's answer into a moment in the video,
the same way TypeSafe's line-search cookbook turns a Choice over line IDs into a line.
"""

import math
import random
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

from jevreel.media import MAX_SIDE, pad_to_minimum


class Layout(BaseModel):
    cols: int
    rows: int
    tile_w: int
    tile_h: int

    @property
    def capacity(self) -> int:
        return self.cols * self.rows

    def slot_box(self, slot: int) -> tuple[int, int, int, int]:
        x = (slot % self.cols) * self.tile_w
        y = (slot // self.cols) * self.tile_h
        return (x, y, x + self.tile_w, y + self.tile_h)


def choose_layout(aspect: float, tiles: int, max_side: int = MAX_SIDE) -> Layout:
    """The grid with the largest tiles that holds `tiles` frames of this aspect ratio in a max_side square."""
    best: Layout | None = None
    for cols in range(1, tiles + 1):
        rows = math.ceil(tiles / cols)
        tile_w = math.floor(min(max_side / cols, max_side * aspect / rows))
        tile_h = math.floor(tile_w / aspect)
        if tile_w < 1 or tile_h < 1:
            continue
        candidate = Layout(cols=cols, rows=rows, tile_w=tile_w, tile_h=tile_h)
        if best is None or _rank(candidate, tiles) > _rank(best, tiles):
            best = candidate
    if best is None:
        raise ValueError(f"No layout fits {tiles} tiles of aspect {aspect:.3f} in {max_side} px.")
    return best


def _rank(layout: Layout, tiles: int) -> tuple[int, int]:
    # Larger tiles first; among equal tiles, fewer empty slots.
    return (layout.tile_w * layout.tile_h, -(layout.capacity - tiles))


def tile_ids(count: int, prefix: str = "T") -> list[str]:
    width = max(2, len(str(count)))
    return [f"{prefix}{index + 1:0{width}d}" for index in range(count)]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def compose(frames: Sequence[Image.Image], ids: Sequence[str], layout: Layout) -> Image.Image:
    """Tile frames left to right, top to bottom, each labelled with its ID."""
    if len(frames) != len(ids) or len(frames) > layout.capacity:
        raise ValueError("compose needs one ID per frame and no more frames than the layout holds.")
    canvas = Image.new("RGB", (layout.cols * layout.tile_w, layout.rows * layout.tile_h))
    draw = ImageDraw.Draw(canvas)
    font = _font(max(12, layout.tile_h // 8))
    for slot, (frame, tile_id) in enumerate(zip(frames, ids, strict=True)):
        x0, y0, _, _ = layout.slot_box(slot)
        tile = frame if frame.size == (layout.tile_w, layout.tile_h) else _fit_tile(frame, layout)
        canvas.paste(tile, (x0, y0))
        left, top, right, bottom = draw.textbbox((x0 + 3, y0 + 2), tile_id, font=font)
        draw.rectangle((left - 3, top - 2, right + 3, bottom + 2), fill=(0, 0, 0))
        draw.text((x0 + 3, y0 + 2), tile_id, fill=(255, 255, 255), font=font)
    return pad_to_minimum(canvas)


def _fit_tile(frame: Image.Image, layout: Layout) -> Image.Image:
    copy = frame.convert("RGB")
    copy.thumbnail((layout.tile_w, layout.tile_h), Image.Resampling.LANCZOS)
    tile = Image.new("RGB", (layout.tile_w, layout.tile_h))
    tile.paste(copy, ((layout.tile_w - copy.width) // 2, (layout.tile_h - copy.height) // 2))
    return tile


def cut_tiles(sheet: Image.Image, layout: Layout, count: int) -> list[Image.Image]:
    """The labelled tiles back out of a sheet, in slot order."""
    return [sheet.crop(layout.slot_box(slot)) for slot in range(count)]


def shuffled(sheet: Image.Image, layout: Layout, count: int, seed: int) -> Image.Image:
    """The same labelled tiles in a different order, to re-ask a borderline sheet without position bias."""
    tiles = cut_tiles(sheet, layout, count)
    order = list(range(count))
    random.Random(seed).shuffle(order)
    canvas = Image.new("RGB", sheet.size)
    for slot, source in enumerate(order):
        canvas.paste(tiles[source], layout.slot_box(slot)[:2])
    return canvas


class SheetInfo(BaseModel):
    index: int
    file: str
    start: float
    end: float
    # (tile ID, seconds into the video), in slot order.
    tiles: list[tuple[str, float]]

    def time_of(self, tile_id: str) -> float | None:
        return next((t for tid, t in self.tiles if tid == tile_id), None)


def build_sheets(
    frames: Iterable[Image.Image],
    *,
    fps: float,
    layout: Layout,
    tiles_per_sheet: int,
    overlap: int,
    out_dir: Path,
    on_sheet: Callable[[SheetInfo], None] | None = None,
) -> list[SheetInfo]:
    """Stream frames into sheets of `tiles_per_sheet`, consecutive sheets sharing `overlap` frames."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = tile_ids(tiles_per_sheet)
    sheets: list[SheetInfo] = []
    buffer: list[tuple[float, Image.Image]] = []
    fresh = 0  # frames in the buffer not yet written on any sheet

    def flush() -> None:
        index = len(sheets)
        name = f"sheet-{index:04d}.jpg"
        image = compose([frame for _, frame in buffer], ids[: len(buffer)], layout)
        image.save(out_dir / name, format="JPEG", quality=90)
        info = SheetInfo(
            index=index,
            file=name,
            start=buffer[0][0],
            end=buffer[-1][0] + 1 / fps,
            tiles=[(ids[slot], round(t, 3)) for slot, (t, _) in enumerate(buffer)],
        )
        sheets.append(info)
        if on_sheet is not None:
            on_sheet(info)

    for frame_index, frame in enumerate(frames):
        buffer.append((frame_index / fps, frame))
        fresh += 1
        if len(buffer) == tiles_per_sheet:
            flush()
            buffer = buffer[len(buffer) - overlap :] if overlap else []
            fresh = 0
    if fresh:
        flush()
    return sheets


def storyboard(
    keyframes: Sequence[tuple[float, Image.Image]], layout: Layout, per_board: int
) -> list[tuple[Image.Image, list[tuple[str, float]]]]:
    """Representative frames from the whole video on as few boards as possible, labelled K01, K02, ..."""
    ids = tile_ids(len(keyframes), prefix="K")
    boards = []
    for start in range(0, len(keyframes), per_board):
        chunk = keyframes[start : start + per_board]
        chunk_ids = ids[start : start + per_board]
        image = compose([frame for _, frame in chunk], chunk_ids, layout)
        boards.append((image, [(tid, t) for tid, (t, _) in zip(chunk_ids, chunk, strict=True)]))
    return boards
