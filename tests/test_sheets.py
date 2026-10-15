from pathlib import Path

from PIL import Image

from jevreel.media import MAX_SIDE, MIN_SIDE
from jevreel.sheets import build_sheets, choose_layout, compose, cut_tiles, shuffled, tile_ids


def frames(count: int, size: tuple[int, int]) -> list[Image.Image]:
    return [Image.new("RGB", size, (i % 256, (i * 7) % 256, (i * 13) % 256)) for i in range(count)]


def test_landscape_layout_fills_the_sheet() -> None:
    layout = choose_layout(16 / 9, 60)
    assert (layout.cols, layout.rows) == (6, 10)
    assert (layout.tile_w, layout.tile_h) == (261, 146)
    assert layout.cols * layout.tile_w <= MAX_SIDE
    assert layout.rows * layout.tile_h <= MAX_SIDE


def test_portrait_layout_uses_more_columns() -> None:
    layout = choose_layout(9 / 16, 60)
    assert layout.cols > layout.rows
    assert layout.capacity >= 60
    assert layout.cols * layout.tile_w <= MAX_SIDE
    assert layout.rows * layout.tile_h <= MAX_SIDE


def test_tile_ids_are_zero_padded_and_widen_past_99() -> None:
    assert tile_ids(3) == ["T01", "T02", "T03"]
    assert tile_ids(120)[-1] == "T120"
    assert tile_ids(2, prefix="K") == ["K01", "K02"]


def test_sheets_overlap_and_carry_tile_times(tmp_path: Path) -> None:
    layout = choose_layout(16 / 9, 60)
    sheets = build_sheets(
        frames(150, (layout.tile_w, layout.tile_h)),
        fps=1.0,
        layout=layout,
        tiles_per_sheet=60,
        overlap=2,
        out_dir=tmp_path,
    )
    assert [s.start for s in sheets] == [0.0, 58.0, 116.0]
    assert len(sheets[-1].tiles) == 34
    assert sheets[1].time_of("T01") == 58.0
    assert sheets[1].time_of("T10") == 67.0
    assert sheets[0].end == 60.0
    for sheet in sheets:
        with Image.open(tmp_path / sheet.file) as image:
            assert max(image.size) <= MAX_SIDE
            assert min(image.size) >= MIN_SIDE


def test_no_trailing_sheet_of_only_overlap(tmp_path: Path) -> None:
    layout = choose_layout(16 / 9, 10)
    sheets = build_sheets(
        frames(18, (layout.tile_w, layout.tile_h)),
        fps=1,
        layout=layout,
        tiles_per_sheet=10,
        overlap=2,
        out_dir=tmp_path,
    )
    # 0-9, then 8-17: the last two frames of sheet 2 are not repeated on a third sheet.
    assert [s.start for s in sheets] == [0.0, 8.0]


def test_shuffle_moves_tiles_but_keeps_them_all() -> None:
    layout = choose_layout(16 / 9, 12)
    sheet = compose(frames(12, (layout.tile_w, layout.tile_h)), tile_ids(12), layout)
    mixed = shuffled(sheet, layout, 12, seed=3)
    before = sorted(t.tobytes() for t in cut_tiles(sheet, layout, 12))
    after = sorted(t.tobytes() for t in cut_tiles(mixed, layout, 12))
    assert before == after
    assert mixed.tobytes() != sheet.tobytes()
