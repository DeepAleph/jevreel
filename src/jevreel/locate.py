"""Where in the frame: Jev's Box question on a full-resolution still, never on a contact-sheet tile.

Box always returns a box, even when the target isn't there, so a Noul in the same request decides
whether to trust it. TypeSafe's edge check follows: crop to the box, ask once per edge whether the
target continues past it, and widen the edges that say yes.
"""

import hashlib
from pathlib import Path

from PIL import Image
from pydantic import BaseModel
from typesafe_sdk import Box, Noul, Question
from typesafe_sdk import Image as JevImage

from jevreel.config import estimated_cost_usd
from jevreel.frames import still
from jevreel.judge import Judge
from jevreel.media import MAX_SIDE, fit_with_content, jpeg_bytes
from jevreel.states import Meter, image_asset, reference_asset, stamp
from jevreel.store import Video

EDGES = ("left", "right", "top", "bottom")
PAD = 0.05  # TypeSafe's example pads the box by 5% before cropping
GROW = 0.25  # widen a cut-off edge by a quarter of the box's size


class BoxEdges(BaseModel):
    left: float
    top: float
    right: float
    bottom: float


class Located(BaseModel):
    at: float
    time: str
    target: str
    found: bool
    presence: float
    box: BoxEdges | None
    """Edges as fractions of the frame's width and height, from its top-left corner."""
    box_pixels: tuple[int, int, int, int] | None
    """(left, top, right, bottom) in the saved frame's pixels."""
    confidence: float | None
    widened_edges: list[str]
    frame: str
    crop: str | None
    requests: int
    input_tokens: int
    estimated_usd: float


async def locate(
    video: Video,
    judge: Judge,
    *,
    at: float,
    target: str | None = None,
    reference: Path | None = None,
    edge_check: bool = True,
) -> Located:
    if target is None and reference is None:
        raise ValueError("Say what to locate, give a reference image, or both.")
    what = target or "the person or object shown in `reference`"
    if target and reference is not None:
        what = f"{target} (the one shown in `reference`)"
    frame_path = still(video, at, MAX_SIDE)
    with Image.open(frame_path) as opened:
        frame = opened.convert("RGB")
    ref = reference_asset(reference) if reference is not None else None
    meter = Meter(judge)

    sent, (content_w, content_h) = fit_with_content(frame)
    state = {"frame": JevImage(jpeg_bytes(sent))}
    if ref is not None:
        state["reference"] = ref
    questions: dict[str, Question] = {
        "present": Noul(instructions=f"Is {what} visible in `frame`?"),
        "box": Box(instructions=f"{what} in `frame`"),
    }
    answer = await meter.ask(state, questions)
    presence = answer.nouls["present"].noul
    result = dict(
        at=at,
        time=stamp(at),
        target=target or (reference.name if reference else ""),
        presence=round(presence, 3),
        frame=str(frame_path),
    )
    if presence < 0.5:
        return _done(result, meter, found=False)

    raw = answer.boxes["box"]
    # Coordinates are fractions of the image Jev saw, which may be scaled and padded on the right and
    # bottom; convert them to fractions of the frame's own content.
    sx, sy = sent.width / content_w, sent.height / content_h
    edges = {
        "left": _clamp(raw.box.left * sx),
        "right": _clamp(raw.box.right * sx),
        "top": _clamp(raw.box.top * sy),
        "bottom": _clamp(raw.box.bottom * sy),
    }
    widened: list[str] = []
    if edge_check:
        crop = frame.crop(_pixels(_padded(edges, PAD), frame))
        check_state = {"crop": image_asset(crop)}
        if ref is not None:
            check_state["reference"] = ref
        checks: dict[str, Question] = {
            edge: Noul(
                instructions=f"Does {what} continue past the {edge} edge of `crop`, so part of it is cut off?"
            )
            for edge in EDGES
        }
        verdict = await meter.ask(check_state, checks)
        width, height = edges["right"] - edges["left"], edges["bottom"] - edges["top"]
        for edge in EDGES:
            if verdict.nouls[edge].noul >= 0.5:
                widened.append(edge)
                grow = GROW * (width if edge in ("left", "right") else height)
                edges[edge] = _clamp(edges[edge] - grow if edge in ("left", "top") else edges[edge] + grow)

    final = _padded(edges, PAD)
    pixels = _pixels(final, frame)
    digest = hashlib.sha1(f"{at}|{what}|{pixels}".encode()).hexdigest()[:8]
    crop_path = video.subdir("crops") / f"crop-{at:010.3f}-{digest}.jpg"
    frame.crop(pixels).save(crop_path, format="JPEG", quality=92)
    return _done(
        result,
        meter,
        found=True,
        box=BoxEdges(**{k: round(v, 4) for k, v in final.items()}),
        box_pixels=pixels,
        confidence=raw.confidence,
        widened_edges=widened,
        crop=str(crop_path),
    )


def _done(base: dict, meter: Meter, **fields: object) -> Located:
    defaults = {"box": None, "box_pixels": None, "confidence": None, "widened_edges": [], "crop": None}
    return Located(
        **base,
        **(defaults | fields),
        requests=meter.requests,
        input_tokens=meter.input_tokens,
        estimated_usd=round(estimated_cost_usd(meter.input_tokens), 6),
    )


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def _padded(edges: dict[str, float], pad: float) -> dict[str, float]:
    return {
        "left": _clamp(edges["left"] - pad),
        "top": _clamp(edges["top"] - pad),
        "right": _clamp(edges["right"] + pad),
        "bottom": _clamp(edges["bottom"] + pad),
    }


def _pixels(edges: dict[str, float], frame: Image.Image) -> tuple[int, int, int, int]:
    left = int(edges["left"] * frame.width)
    top = int(edges["top"] * frame.height)
    right = max(left + 1, round(edges["right"] * frame.width))
    bottom = max(top + 1, round(edges["bottom"] * frame.height))
    return (left, top, right, bottom)
