"""Indexing: sample the video, lay it out on contact sheets, get the transcript, and ask every sheet
the same default questions once, so whole-video questions have a typed timeline to work from."""

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from typesafe_sdk import Choice, Noul, NoulCriteria, Question, Score

from jevreel.config import Settings, estimated_cost_usd
from jevreel.judge import Judge
from jevreel.media import probe, sample_frames
from jevreel.sheets import build_sheets, choose_layout
from jevreel.sources import fetch_url, is_url, video_id
from jevreel.states import Meter, numbered_lines, sheet_asset, sheet_state
from jevreel.store import Manifest, Store, Video, WindowFacets, now
from jevreel.timefmt import clock
from jevreel.transcript import ASRUnavailable, Transcript, find_sidecar, load_captions, transcribe

Progress = Callable[[str, int, int], Awaitable[None]]
TranscribeMode = Literal["auto", "never"]


class IndexResult(BaseModel):
    id: str
    title: str
    duration: str
    sheets: int
    layout: str
    transcript: str | None
    timeline: bool
    cached: bool
    requests: int
    input_tokens: int
    estimated_usd: float
    folder: str


def facet_questions(tile_ids: list[str]) -> dict[str, Question]:
    """The default questions every sheet answers at index time.

    Each is one literal judgment, per TypeSafe's guidance: Jev reads questions at face value and
    does not count reliably, so "is there a cut" is a yes/no rather than "how many cuts".
    """
    return {
        "keyframe": Choice(
            instructions="Which tile best represents what happens across `sheet`?",
            criteria=dict.fromkeys(tile_ids),
        ),
        "people": Noul(instructions="Is at least one person visible in any tile of `sheet`?"),
        "talking_to_camera": Noul(
            instructions="Does any tile of `sheet` show a person talking to the camera?"
        ),
        "screen_or_slides": Noul(
            instructions="Do the tiles of `sheet` show a computer screen, slides or a document?",
            criteria=NoulCriteria(
                true="Desktop or app screen recordings, presentation slides, or pages of a document.",
                false="Camera footage, animation, or video game footage.",
            ),
        ),
        "text_on_screen": Noul(
            instructions="Is readable text, such as titles, captions, slides or code, visible in any tile of `sheet`?"
        ),
        "cut": Noul(
            instructions="Does the video cut to a different shot or scene somewhere across the tiles of `sheet`?"
        ),
        "setting": Choice(
            instructions="What kind of footage is `sheet` mostly?",
            criteria={
                "indoors": "Filmed indoors with a camera.",
                "outdoors": "Filmed outdoors with a camera.",
                "screen": "A computer screen recording, slides or a document.",
                "game": "Video game footage.",
                "animation": "Animation or motion graphics.",
                "mixed": "A mix of these.",
            },
        ),
        "activity": Score(
            instructions="How much happens or moves across the tiles of `sheet`?",
            criteria=[
                "Nothing changes.",
                "Small changes.",
                "Steady movement or action.",
                "Fast, busy action.",
            ],
        ),
    }


async def index_video(
    store: Store,
    settings: Settings,
    source: str,
    *,
    judge: Callable[[], Judge],
    force: bool = False,
    timeline: bool = True,
    transcription: TranscribeMode = "auto",
    on_progress: Progress | None = None,
) -> IndexResult:
    if not is_url(source):
        source = str(Path(source).expanduser().resolve())
    vid = video_id(source)
    root = store.dir_for(vid)
    existing = store.load(vid)
    if existing is not None and not force and (existing.manifest.has_timeline or not timeline):
        return _result(existing, cached=True, requests=0, input_tokens=0)

    async def progress(stage: str, done: int, total: int) -> None:
        if on_progress is not None:
            await on_progress(stage, done, total)

    await progress("fetching", 0, 1)
    if is_url(source):
        fetched = await asyncio.to_thread(fetch_url, source, root / "source", settings.caption_languages)
        media, title, captions = fetched.media, fetched.title, fetched.captions
    else:
        media = Path(source)
        title, captions = media.stem, find_sidecar(media)
    info = await asyncio.to_thread(probe, media)

    layout = choose_layout(info.aspect, settings.tiles_per_sheet)
    sheets_dir = root / "sheets"
    if sheets_dir.exists():
        shutil.rmtree(sheets_dir)
    await progress("sheets", 0, 1)
    sheets = await asyncio.to_thread(
        build_sheets,
        sample_frames(media, settings.fps, (layout.tile_w, layout.tile_h)),
        fps=settings.fps,
        layout=layout,
        tiles_per_sheet=settings.tiles_per_sheet,
        overlap=settings.sheet_overlap,
        out_dir=sheets_dir,
    )
    if not sheets:
        raise RuntimeError(f"ffmpeg returned no frames for {source}.")
    await progress("sheets", 1, 1)

    await progress("transcript", 0, 1)
    transcript = await _transcript(media, captions, info.has_audio, settings, transcription)
    transcript_path = root / "transcript.json"
    if transcript is not None:
        transcript_path.write_text(transcript.model_dump_json(indent=1), encoding="utf-8")
    elif transcript_path.exists():
        transcript_path.unlink()
    await progress("transcript", 1, 1)

    manifest = Manifest(
        id=vid,
        source=source,
        title=title,
        media=str(media),
        duration=info.duration,
        width=info.width,
        height=info.height,
        has_audio=info.has_audio,
        fps=settings.fps,
        tiles_per_sheet=settings.tiles_per_sheet,
        layout=layout,
        sheets=sheets,
        transcript_source=transcript.source if transcript else None,
        model=settings.model,
        created=now(),
        jevreel_version=version("jevreel"),
    )
    store.save_manifest(root, manifest)
    video = Video(root, manifest)
    if not timeline:
        return _result(video, cached=False, requests=0, input_tokens=0)

    meter = Meter(judge())
    done = 0

    async def judge_sheet(index: int) -> WindowFacets:
        nonlocal done
        sheet = manifest.sheets[index]
        ids = [tile_id for tile_id, _ in sheet.tiles]
        lines = numbered_lines(video, sheet.start, sheet.end)
        state = sheet_state(video, sheet, sheet_asset(video, sheet), lines=lines)
        answer = await meter.ask(state, facet_questions(ids))
        nouls, choices = answer.nouls, answer.choices
        keyframe = sheet.time_of(choices["keyframe"].choice)
        done += 1
        await progress("timeline", done, len(manifest.sheets))
        return WindowFacets(
            sheet=index,
            start=sheet.start,
            end=sheet.end,
            keyframe=keyframe if keyframe is not None else sheet.tiles[len(sheet.tiles) // 2][1],
            people=round(nouls["people"].noul, 3),
            talking_to_camera=round(nouls["talking_to_camera"].noul, 3),
            screen_or_slides=round(nouls["screen_or_slides"].noul, 3),
            text_on_screen=round(nouls["text_on_screen"].noul, 3),
            cut=round(nouls["cut"].noul, 3),
            setting=choices["setting"].choice,
            activity=round(answer.scores["activity"].score, 3),
        )

    await progress("timeline", 0, len(manifest.sheets))
    rows = await asyncio.gather(*(judge_sheet(i) for i in range(len(manifest.sheets))))
    (root / "timeline.json").write_text(
        json.dumps([row.model_dump() for row in rows], indent=1), encoding="utf-8"
    )
    manifest = manifest.model_copy(update={"has_timeline": True, "input_tokens": meter.input_tokens})
    store.save_manifest(root, manifest)
    return _result(
        Video(root, manifest), cached=False, requests=meter.requests, input_tokens=meter.input_tokens
    )


async def _transcript(
    media: Path, captions: Path | None, has_audio: bool, settings: Settings, mode: TranscribeMode
) -> Transcript | None:
    if captions is not None:
        loaded = load_captions(captions)
        if loaded.segments:
            return loaded
    if mode == "never" or not has_audio:
        return None
    try:
        return await asyncio.to_thread(transcribe, media, settings.asr_model)
    except ASRUnavailable:
        # Captions first, local speech-to-text only when installed: a video without either is still
        # fully searchable by what is on screen.
        return None


def _result(video: Video, *, cached: bool, requests: int, input_tokens: int) -> IndexResult:
    m = video.manifest
    return IndexResult(
        id=m.id,
        title=m.title,
        duration=clock(m.duration),
        sheets=len(m.sheets),
        layout=f"{m.layout.cols}x{m.layout.rows} tiles of {m.layout.tile_w}x{m.layout.tile_h} px, "
        f"{m.tiles_per_sheet} frames per sheet at {m.fps:g} fps",
        transcript=m.transcript_source,
        timeline=m.has_timeline,
        cached=cached,
        requests=requests,
        input_tokens=input_tokens,
        estimated_usd=round(estimated_cost_usd(input_tokens), 6),
        folder=str(video.root),
    )
