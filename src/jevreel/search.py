"""Finding moments: one parallel Jev request per contact sheet (or transcript window), each asking
a yes/no question for the window and a Choice over tile IDs for the moment inside it.

This follows TypeSafe's own line-search pattern: a Noul says whether the thing is there at all, a
Choice over labelled units says where. What is on screen and what is said are separate questions,
combined in code, because one question should hold one judgment. Borderline sheets are asked again
with their tiles moved and the Choice options reordered, since Jev leans toward options that come
first and a tile's position shouldn't decide the answer alone.
"""

import asyncio
import random
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel
from typesafe_sdk import Choice, Noul, Question, SystemOneResponse
from typesafe_sdk import Image as JevImage

from jevreel.config import Settings
from jevreel.frames import still
from jevreel.judge import Judge
from jevreel.media import jpeg_bytes
from jevreel.sheets import SheetInfo, shuffled
from jevreel.states import (
    Meter,
    image_asset,
    layout_note,
    lines_state,
    numbered_lines,
    reference_asset,
    sheet_asset,
    sheet_state,
    stamp,
)
from jevreel.store import Video
from jevreel.timefmt import clock, span
from jevreel.transcript import Segment

Mode = Literal["auto", "visual", "speech", "both"]


class Moment(BaseModel):
    at: float
    time: str
    probability: float


class Hit(BaseModel):
    at: float
    time: str
    window: str
    sheet: int
    anchor: str
    """The tile (T..) or transcript line (L..) Jev picked."""
    probability: float
    """The stronger of `shown` and `said`; averaged over two asks when rechecked."""
    shown: float | None
    """Probability that it is on screen in this window."""
    said: float | None
    """Probability that it is said in this window, when the transcript was asked about."""
    strong: bool
    rechecked: bool
    verified: bool = False
    """Re-checked on full frames around the picked tile; `probability` and `at` then come from those frames."""
    screened: float | None = None
    """The contact-sheet probability before verification."""
    frame: str | None = None
    """Path of the verified full frame, for looking at directly."""
    also: list[Moment]
    """Other likely moments in the same window."""
    transcript: str | None


class SearchResult(BaseModel):
    video: str
    query: str
    mode: str
    hits: list[Hit]
    weak: list[Hit]
    """Windows below the threshold that Jev did not rule out: candidates to zoom into when hits are few."""
    windows_checked: int
    requests: int
    cached: int
    """Requests answered from jevreel's cache, at no cost."""
    input_tokens: int
    estimated_usd: float


def _in_range(sheet: SheetInfo, start: float | None, end: float | None) -> bool:
    return (start is None or sheet.end > start) and (end is None or sheet.start < end)


async def search(
    video: Video,
    judge: Judge,
    settings: Settings,
    *,
    query: str,
    mode: Mode = "auto",
    reference: Path | None = None,
    note: str | None = None,
    start: float | None = None,
    end: float | None = None,
    threshold: float = 0.5,
    limit: int = 10,
    recheck: bool = False,
    verify: bool = True,
) -> SearchResult:
    has_speech = video.transcript is not None and bool(video.transcript.segments)
    if reference is not None:
        mode = "visual"
    elif mode == "auto":
        mode = "both" if has_speech else "visual"
    if mode in ("speech", "both") and not has_speech:
        raise ValueError("This video has no transcript, so it can only be searched visually.")

    meter = Meter(judge)
    sheets = [s for s in video.manifest.sheets if _in_range(s, start, end)]
    ref = reference_asset(reference) if reference is not None else None
    what = note or "person or object"
    if mode == "speech":
        found = await asyncio.gather(*(_speech_window(video, meter, settings, s, query) for s in sheets))
    else:
        found = await asyncio.gather(
            *(
                _visual_sheet(
                    video, meter, settings, s, query=query, mode=mode, ref=ref, what=what, recheck=recheck
                )
                for s in sheets
            )
        )
    answered = _dedupe([hit for hit in found if hit is not None])
    answered.sort(key=lambda hit: hit.probability, reverse=True)
    if verify and mode != "speech" and settings.verify_top:
        # Screening ranks windows well even when it can't confirm a one-second event, so the top
        # windows by rank, not by threshold, get their candidate frames re-checked at a larger size.
        candidates = [
            hit for hit in answered if hit.anchor.startswith("T") and hit.probability >= settings.verify_floor
        ][: settings.verify_top]
        checked = await asyncio.gather(
            *(_verify(video, meter, settings, hit, query=query, ref=ref, what=what) for hit in candidates)
        )
        replaced = {id(old): new for old, new in zip(candidates, checked, strict=True)}
        answered = _dedupe([replaced.get(id(hit), hit) for hit in answered])
        answered.sort(key=lambda hit: hit.probability, reverse=True)
    hits = [hit for hit in answered if hit.probability >= threshold]
    weak = [
        hit
        for hit in answered
        if hit.probability < threshold and max(hit.probability, hit.screened or 0.0) >= settings.absent
    ]
    return SearchResult(
        video=video.id,
        query=query if reference is None else f"same {note or 'person or object'} as {reference.name}",
        mode=mode,
        hits=hits[:limit],
        weak=weak[:3],
        windows_checked=len(sheets),
        **meter.spent(),
    )


def _options(labels: list[str], none: str) -> dict[str, str | None]:
    criteria: dict[str, str | None] = dict.fromkeys(labels)
    criteria["none"] = none
    return criteria


def _sheet_questions(
    tile_order: list[str], line_order: list[str], *, query: str, reference_what: str | None
) -> dict[str, Question]:
    if reference_what is not None:
        shown = f"Does any tile of `sheet` show the same {reference_what} as `reference`?"
        pick = f"Which tile of `sheet` shows the same {reference_what} as `reference` most clearly?"
    else:
        shown = f"Does any tile of `sheet` show this: {query}"
        pick = f"Which tile of `sheet` shows this most clearly: {query}"
    questions: dict[str, Question] = {
        "shown": Noul(instructions=shown),
        "tile": Choice(instructions=pick, criteria=_options(tile_order, "No tile matches.")),
    }
    if line_order:
        questions["said"] = Noul(instructions=f"Does any line of `transcript` say or discuss this: {query}")
        questions["line"] = Choice(
            instructions=f"Which line of `transcript` says it most directly: {query}",
            criteria=_options(line_order, "No line says it."),
        )
    return questions


def _average(first: Mapping[str, float], second: Mapping[str, float]) -> dict[str, float]:
    return {key: (first.get(key, 0.0) + second.get(key, 0.0)) / 2 for key in first.keys() | second.keys()}


def _ranked(probabilities: Mapping[str, float]) -> list[tuple[str, float]]:
    return sorted(
        ((k, p) for k, p in probabilities.items() if k != "none"), key=lambda kp: kp[1], reverse=True
    )


async def _visual_sheet(
    video: Video,
    meter: Meter,
    settings: Settings,
    sheet: SheetInfo,
    *,
    query: str,
    mode: Mode,
    ref: JevImage | None,
    what: str,
    recheck: bool,
) -> Hit | None:
    ids = [tile_id for tile_id, _ in sheet.tiles]
    lines = numbered_lines(video, sheet.start, sheet.end) if mode == "both" else {}
    reference_what = what if ref is not None else None
    state = sheet_state(video, sheet, sheet_asset(video, sheet), lines=lines)
    if ref is not None:
        state["reference"] = ref
    answer = await meter.ask(
        state, _sheet_questions(ids, list(lines), query=query, reference_what=reference_what)
    )
    shown, said = answer.nouls["shown"].noul, _said(answer)
    tiles = dict(answer.choices["tile"].probabilities)
    line_probs = dict(answer.choices["line"].probabilities) if "line" in answer.choices else {}

    rechecked = False
    if recheck and settings.absent <= shown < settings.found:
        rng = random.Random(sheet.index + 1)
        tile_order, line_order = rng.sample(ids, len(ids)), rng.sample(list(lines), len(lines))
        image = shuffled(
            video.sheet_image(sheet.index), video.manifest.layout, len(ids), seed=sheet.index + 1
        )
        again = {
            **state,
            "sheet": JevImage(jpeg_bytes(image)),
            "layout": layout_note(video.manifest.fps, in_time_order=False),
        }
        second = await meter.ask(
            again, _sheet_questions(tile_order, line_order, query=query, reference_what=reference_what)
        )
        shown = (shown + second.nouls["shown"].noul) / 2
        if said is not None and (said_again := _said(second)) is not None:
            said = (said + said_again) / 2
        tiles = _average(tiles, second.choices["tile"].probabilities)
        if line_probs:
            line_probs = _average(line_probs, second.choices["line"].probabilities)
        rechecked = True

    probability = max(shown, said or 0.0)
    if said is not None and said > shown and (ranked_lines := _ranked(line_probs)):
        return _line_hit(video, settings, sheet, lines, ranked_lines, probability, shown, said, rechecked)
    ranked = _ranked(tiles)
    if not ranked or (at := sheet.time_of(ranked[0][0])) is None:
        return None
    also = [
        Moment(at=t, time=stamp(t), probability=round(p, 3))
        for tile_id, p in ranked[1:3]
        if p >= 0.15 and (t := sheet.time_of(tile_id)) is not None
    ]
    return Hit(
        at=at,
        time=stamp(at),
        window=span(sheet.start, sheet.end),
        sheet=sheet.index,
        anchor=ranked[0][0],
        probability=round(probability, 3),
        shown=round(shown, 3),
        said=None if said is None else round(said, 3),
        strong=probability >= settings.found,
        rechecked=rechecked,
        also=also,
        transcript=_nearby_speech(video, at),
    )


def _said(answer: SystemOneResponse) -> float | None:
    return answer.nouls["said"].noul if "said" in answer.nouls else None


def _line_hit(
    video: Video,
    settings: Settings,
    sheet: SheetInfo,
    lines: Mapping[str, Segment],
    ranked: list[tuple[str, float]],
    probability: float,
    shown: float | None,
    said: float,
    rechecked: bool,
) -> Hit:
    best = lines[ranked[0][0]]
    return Hit(
        at=best.start,
        time=clock(best.start),
        window=span(sheet.start, sheet.end),
        sheet=sheet.index,
        anchor=ranked[0][0],
        probability=round(probability, 3),
        shown=None if shown is None else round(shown, 3),
        said=round(said, 3),
        strong=probability >= settings.found,
        rechecked=rechecked,
        also=[
            Moment(at=lines[k].start, time=clock(lines[k].start), probability=round(p, 3))
            for k, p in ranked[1:3]
            if p >= 0.15
        ],
        transcript=_nearby_speech(video, best.start),
    )


async def _speech_window(
    video: Video, meter: Meter, settings: Settings, sheet: SheetInfo, query: str
) -> Hit | None:
    lines = numbered_lines(video, sheet.start, sheet.end)
    if not lines:
        return None
    state = {"window": span(sheet.start, sheet.end), "transcript": lines_state(lines)}
    questions: dict[str, Question] = {
        "said": Noul(instructions=f"Does any line of `transcript` say or discuss this: {query}"),
        "line": Choice(
            instructions=f"Which line of `transcript` says it most directly: {query}",
            criteria=_options(list(lines), "No line says it."),
        ),
    }
    answer = await meter.ask(state, questions)
    said = answer.nouls["said"].noul
    ranked = _ranked(answer.choices["line"].probabilities)
    if not ranked:
        return None
    return _line_hit(video, settings, sheet, lines, ranked, said, None, said, False)


VERIFY_SIDE = 784  # three times a tile's width: menu text and one-second events become legible
# Verification looks between the sampled seconds as well as around them: on the live test a
# pause menu was on screen for under a second, and the picked tile was a second off.
SPREAD = 1.5  # seconds either side of each pick
STEP = 0.5  # seconds between verification frames


async def _verify(
    video: Video, meter: Meter, settings: Settings, hit: Hit, *, query: str, ref: JevImage | None, what: str
) -> Hit:
    """Ask one yes/no per full frame around the screening pick(s), one judgment per question."""
    offsets = [i * STEP for i in range(-int(SPREAD / STEP), int(SPREAD / STEP) + 1)]
    last = max(video.manifest.duration - 0.05, 0.0)
    picks = [hit.at, *(moment.at for moment in hit.also[:1])]
    times = sorted({round(min(max(t + offset, 0.0), last), 3) for t in picks for offset in offsets})
    paths = await asyncio.gather(*(asyncio.to_thread(still, video, t, VERIFY_SIDE) for t in times))
    frames = [{"time": stamp(t), "frame": _frame_asset(path)} for t, path in zip(times, paths, strict=True)]
    state: dict[str, object] = {
        "about": "Single frames from one stretch of a video, in time order.",
        "frames": frames,
    }
    if ref is not None:
        state["reference"] = ref
    subject = f"the same {what} as `reference`" if ref is not None else f"this: {query}"
    questions: dict[str, Question] = {
        f"f{i}": Noul(instructions=f"Does `frames[{i}].frame` show {subject}") for i in range(len(times))
    }
    answer = await meter.ask(state, questions)
    scores = [answer.nouls[f"f{i}"].noul for i in range(len(times))]
    best = max(range(len(times)), key=scores.__getitem__)
    at, probability = times[best], scores[best]
    others = sorted(
        ((times[i], s) for i, s in enumerate(scores) if i != best and s >= 0.5), key=lambda ts: -ts[1]
    )
    return hit.model_copy(
        update={
            "at": at,
            "time": stamp(at),
            "probability": round(probability, 3),
            "screened": hit.probability,
            "verified": True,
            "strong": probability >= settings.found,
            "frame": str(paths[best]),
            "also": [Moment(at=t, time=stamp(t), probability=round(s, 3)) for t, s in others[:2]],
            "transcript": _nearby_speech(video, at),
        }
    )


def _frame_asset(path: Path) -> JevImage:
    with Image.open(path) as image:
        return image_asset(image)


def _nearby_speech(video: Video, at: float, radius: float = 8.0) -> str | None:
    if video.transcript is None:
        return None
    return video.transcript.text(at - radius, at + radius) or None


def _dedupe(hits: list[Hit]) -> list[Hit]:
    """Consecutive sheets share tiles, so one moment can be found twice; keep its stronger answer."""
    best: dict[float, Hit] = {}
    for hit in hits:
        key = round(hit.at, 2)
        if key not in best or hit.probability > best[key].probability:
            best[key] = hit
    return list(best.values())
