"""ffmpeg and ffprobe: probing a video, sampling it at a fixed rate, and grabbing single stills."""

import json
import math
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

# Jev's image limits. Out-of-range images are rejected rather than resized, and one bad image fails
# the whole request. TypeSafe's docs say each side may be 256-1568 px, but the API measured on
# 2026-10-15 (jev-1.14.0) rejects any side below 280 ("Invalid image dimensions"): 1024x280 passes,
# 1024x256 and 279x1024 fail. 280 matches the docs' own 280x280 minimum pixel count.
MAX_SIDE = 1568
MIN_SIDE = 280
MIN_PIXELS = 78_400


class MediaError(RuntimeError):
    """ffmpeg or ffprobe is missing, or could not read the video."""


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise MediaError(f"{name} was not found on PATH. Install FFmpeg (https://ffmpeg.org/download.html).")
    return path


@dataclass(frozen=True)
class Probe:
    duration: float
    width: int
    height: int
    has_audio: bool

    @property
    def aspect(self) -> float:
        return self.width / self.height


def probe(path: Path) -> Probe:
    command = [
        _tool("ffprobe"),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0:
        raise MediaError(
            f"ffprobe could not read {path}: {completed.stderr.decode(errors='replace').strip()}"
        )
    info = json.loads(completed.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError(f"{path} has no video stream.")
    width, height = int(video["width"]), int(video["height"])
    # Phone videos store portrait frames as landscape plus a rotation; ffmpeg applies it when decoding,
    # so the layout has to be chosen for the rotated shape.
    rotation = next(
        (int(d.get("rotation", 0)) for d in video.get("side_data_list", []) if "rotation" in d),
        int(video.get("tags", {}).get("rotate", 0)),
    )
    if abs(rotation) % 180 == 90:
        width, height = height, width
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise MediaError(f"{path} reports no duration.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return Probe(duration=duration, width=width, height=height, has_audio=has_audio)


def sample_frames(path: Path, fps: float, size: tuple[int, int]) -> Iterator[Image.Image]:
    """Decode `fps` frames per second, each letterboxed to exactly `size`. Frame i is at i / fps seconds.

    `round=up` makes slot i the last source frame at or before i / fps. With ffmpeg's default
    (`near`) it is the last frame before (i + 0.5) / fps instead: measured on a 30 fps source, the
    slot labelled 8 s showed 8.47 s, so a sub-second event on a tile was not where its label said.
    """
    width, height = size
    command = [
        _tool("ffmpeg"),
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-vf",
        (
            f"fps={fps}:round=up,scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        ),
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "-",
    ]
    frame_bytes = width * height * 3
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        assert process.stdout is not None
        while True:
            buffer = process.stdout.read(frame_bytes)
            if len(buffer) < frame_bytes:
                break
            yield Image.frombytes("RGB", (width, height), buffer)
        stderr = process.stderr.read() if process.stderr else b""
        process.wait()
    if process.returncode != 0:
        raise MediaError(f"ffmpeg could not decode {path}: {stderr.decode(errors='replace').strip()}")


def grab_frame(path: Path, at: float, max_side: int = MAX_SIDE) -> Image.Image:
    """One full-resolution still at `at` seconds, shrunk so its longest side is at most `max_side`."""
    command = [
        _tool("ffmpeg"),
        "-v",
        "error",
        "-nostdin",
        "-ss",
        # An accurate seek returns the frame at t: the same one sample_frames puts on the tile labelled t.
        f"{max(at, 0):.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    if completed.returncode != 0 or not completed.stdout:
        detail = completed.stderr.decode(errors="replace").strip() or "no frame at that time"
        raise MediaError(f"ffmpeg could not grab a frame at {at:.2f}s from {path}: {detail}")
    image = Image.open(BytesIO(completed.stdout)).convert("RGB")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return image


def fit_for_jev(image: Image.Image) -> Image.Image:
    """Resize or pad an image into Jev's accepted range; the API itself never resizes."""
    return fit_with_content(image)[0]


def fit_with_content(image: Image.Image) -> tuple[Image.Image, tuple[int, int]]:
    """Like fit_for_jev, also returning the size the original content occupies at the top-left."""
    image = image.convert("RGB")
    width, height = image.size
    scale = 1.0
    if max(width, height) > MAX_SIDE:
        scale = MAX_SIDE / max(width, height)
    elif min(width, height) < MIN_SIDE or width * height < MIN_PIXELS:
        # Upscale small crops toward the minimum, but never past the maximum on the long side.
        wanted = max(MIN_SIDE / min(width, height), math.sqrt(MIN_PIXELS / (width * height)))
        scale = min(wanted, MAX_SIDE / max(width, height))
    if scale != 1.0:
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS
        )
    return pad_to_minimum(image), image.size


def pad_to_minimum(image: Image.Image) -> Image.Image:
    """Pad on the right and bottom only, so pixel coordinates inside the image stay valid."""
    width, height = image.size
    target_w, target_h = max(width, MIN_SIDE), max(height, MIN_SIDE)
    if target_w * target_h < MIN_PIXELS:
        target_h = math.ceil(MIN_PIXELS / target_w)
    if (target_w, target_h) == (width, height):
        return image
    canvas = Image.new("RGB", (target_w, target_h))
    canvas.paste(image, (0, 0))
    return canvas


def jpeg_bytes(image: Image.Image, quality: int = 90) -> bytes:
    # Baseline 8-bit JPEG is the format TypeSafe calls the safest; Pillow writes baseline by default.
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
