import itertools
from pathlib import Path

import pytest
from PIL import Image

from jevreel.media import (
    MAX_SIDE,
    MIN_PIXELS,
    MIN_SIDE,
    fit_for_jev,
    fit_with_content,
    grab_frame,
    probe,
    sample_frames,
)


@pytest.mark.parametrize(
    "size", [(40, 30), (4000, 3000), (2000, 100), (256, 256), (1024, 256), (279, 1600), (1568, 248)]
)
def test_fit_for_jev_lands_in_range(size: tuple[int, int]) -> None:
    # Measured 2026-10-15: the API rejects 1024x256 and 279x1024 although its docs allow 256 per side.
    fitted = fit_for_jev(Image.new("RGB", size))
    width, height = fitted.size
    assert MIN_SIDE <= width <= MAX_SIDE
    assert MIN_SIDE <= height <= MAX_SIDE
    assert width * height >= MIN_PIXELS


def test_fit_keeps_content_at_top_left() -> None:
    fitted, content = fit_with_content(Image.new("RGB", (2000, 100)))
    assert content[0] == fitted.width  # scaled to the long-side limit
    assert content[1] < fitted.height  # padded below


@pytest.mark.ffmpeg
def test_probe_and_sample(clip: Path) -> None:
    info = probe(clip)
    assert (info.width, info.height) == (640, 360)
    assert info.has_audio
    assert info.duration == pytest.approx(150, abs=0.5)
    first = list(itertools.islice(sample_frames(clip, 1.0, (160, 90)), 5))
    assert len(first) == 5
    assert all(frame.size == (160, 90) for frame in first)


@pytest.mark.ffmpeg
def test_grab_frame_respects_max_side(clip: Path) -> None:
    frame = grab_frame(clip, 12.0, max_side=320)
    assert frame.size == (320, 180)


@pytest.mark.ffmpeg
def test_a_tile_and_a_grab_at_the_same_second_are_the_same_frame(tmp_path: Path) -> None:
    """Sheets and full-frame grabs must agree on what "the frame at 3 s" is.

    With ffmpeg's default fps rounding the tile labelled n showed the frame at n + 0.47 s.
    """
    import subprocess

    from PIL import ImageChops, ImageStat

    ramp = tmp_path / "ramp.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
            "-i", "nullsrc=s=64x64:r=30:d=5,format=yuvj444p,geq=lum='mod(N*7,256)':cb='mod(N*13,256)':cr=128",
            "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuvj444p", str(ramp),
        ],
        check=True,
    )  # fmt: skip
    # Ground truth: every frame, decoded without any frame-rate filter. Frame k is at k / 30 s.
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(ramp),
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    size = 64 * 64 * 3
    truth = [Image.frombytes("RGB", (64, 64), raw[i : i + size]) for i in range(0, len(raw) - size + 1, size)]

    def at(image: Image.Image) -> float:
        diffs = [
            sum(ImageStat.Stat(ImageChops.difference(image.resize((64, 64)), ref)).mean) for ref in truth
        ]
        return round(diffs.index(min(diffs)) / 30, 3)

    assert [at(tile) for tile in list(sample_frames(ramp, 1.0, (64, 64)))[:5]] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert [at(grab_frame(ramp, float(t), 64)) for t in range(4)] == [0.0, 1.0, 2.0, 3.0]
