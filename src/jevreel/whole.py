"""Whole-video questions: one request that sees the entire video at once.

Raw sheets for every minute would be slow and, per TypeSafe, less accurate in long context. The
request instead carries a storyboard (one representative frame per sheet, picked by Jev at index
time), the typed timeline from indexing, and the full transcript.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel
from typesafe_sdk import Choice, Noul, Question, Score

from jevreel.judge import Judge, is_context_overflow
from jevreel.sheets import storyboard
from jevreel.states import Meter, image_asset, stamp
from jevreel.store import Video
from jevreel.timefmt import clock, span

Kind = Literal["noul", "choice", "score"]

# Rough text budget before switching on long context: Jev's default limit is 64K tokens per request,
# and images need room too. A request that still overflows is retried with long context on.
LONG_CONTEXT_CHARS = 120_000


class WholeAnswer(BaseModel):
    video: str
    question: str
    kind: Kind
    answer: float | str
    """Noul: probability of yes. Choice: the chosen option. Score: expected level, from 0."""
    probabilities: dict[str, float] | None
    confidence: float | None
    storyboard_frames: int
    long_context: bool
    requests: int
    cached: int
    """Requests answered from jevreel's cache, at no cost."""
    input_tokens: int
    estimated_usd: float


def _keyframes(video: Video) -> list[tuple[float, Any]]:
    layout = video.manifest.layout
    picks = {row.sheet: row.keyframe for row in video.timeline}
    frames = []
    for sheet in video.manifest.sheets:
        at = picks.get(sheet.index, sheet.tiles[len(sheet.tiles) // 2][1])
        slot = next((i for i, (_, t) in enumerate(sheet.tiles) if abs(t - at) < 1e-6), len(sheet.tiles) // 2)
        frames.append((at, video.sheet_image(sheet.index).crop(layout.slot_box(slot))))
    return frames


def whole_state(video: Video) -> dict[str, Any]:
    boards = storyboard(_keyframes(video), video.manifest.layout, per_board=video.manifest.tiles_per_sheet)
    legend = {label: stamp(t) for _, labels in boards for label, t in labels}
    state: dict[str, Any] = {
        "video": video.manifest.title,
        "duration": clock(video.manifest.duration),
        "storyboard": [image_asset(image) for image, _ in boards],
        "about_storyboard": (
            "One representative frame per stretch of video, labelled K01, K02, ... in time order. "
            "`storyboard_times` maps each label to its time."
        ),
        "storyboard_times": legend,
    }
    if video.timeline:
        state["timeline"] = [
            {
                "window": span(row.start, row.end),
                "people": row.people,
                "talking_to_camera": row.talking_to_camera,
                "screen_or_slides": row.screen_or_slides,
                "text_on_screen": row.text_on_screen,
                "cut": row.cut,
                "setting": row.setting,
                "activity_0_to_3": row.activity,
            }
            for row in video.timeline
        ]
        state["about_timeline"] = "Per-window yes probabilities and labels from looking at every second."
    if video.transcript is not None and video.transcript.segments:
        state["transcript"] = video.transcript.text()
    return state


def whole_question(question: str, kind: Kind, options: list[str] | None) -> Question:
    if kind == "noul":
        return Noul(instructions=question)
    if not options:
        raise ValueError(f"A {kind} question needs options.")
    if kind == "choice":
        return Choice(instructions=question, criteria=dict.fromkeys(options))
    if len(options) > 10:
        raise ValueError("A score question takes at most 10 levels, lowest first.")
    return Score(instructions=question, criteria=options)


async def whole(
    video: Video, judge: Judge, *, question: str, kind: Kind = "noul", options: list[str] | None = None
) -> WholeAnswer:
    state = whole_state(video)
    questions = {"answer": whole_question(question, kind, options)}
    text_size = len(json.dumps({k: v for k, v in state.items() if k != "storyboard"}))
    long_context = text_size > LONG_CONTEXT_CHARS
    meter = Meter(judge)
    try:
        result = await meter.ask(state, questions, long_context=long_context)
    except Exception as error:
        if long_context or not is_context_overflow(error):
            raise
        long_context = True
        result = await meter.ask(state, questions, long_context=True)

    probabilities: dict[str, float] | None
    if kind == "noul":
        answer: float | str = round(result.nouls["answer"].noul, 4)
        probabilities, confidence = None, None
    elif kind == "choice":
        chosen = result.choices["answer"]
        answer, confidence = chosen.choice, chosen.confidence
        probabilities = {k: round(v, 4) for k, v in chosen.probabilities.items()}
    else:
        scored = result.scores["answer"]
        answer, confidence = round(scored.score, 4), scored.confidence
        probabilities = {
            options[level] if options else str(level): round(p, 4)
            for level, p in scored.probabilities.items()
        }
    return WholeAnswer(
        video=video.id,
        question=question,
        kind=kind,
        answer=answer,
        probabilities=probabilities,
        confidence=confidence,
        storyboard_frames=len(state["storyboard_times"]),
        long_context=long_context,
        **meter.spent(),
    )
