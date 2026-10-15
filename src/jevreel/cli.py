"""The `jevreel` command. Every command prints JSON on stdout, so agents and scripts can read it;
progress and errors go to stderr."""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer
from pydantic import BaseModel
from typesafe_sdk import TypeSafeError

from jevreel import __version__
from jevreel.config import Settings
from jevreel.lenses import Lens
from jevreel.media import MediaError
from jevreel.reel import Reel
from jevreel.search import Mode
from jevreel.sources import URLSupportUnavailable
from jevreel.store import VideoNotFound
from jevreel.whole import Kind

app = typer.Typer(
    name="jevreel",
    help="Zero-embedding video intelligence: contact sheets + Jev typed decisions.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

T = TypeVar("T")
Video = Annotated[str, typer.Argument(help="Video ID, unique ID prefix, or the indexed file path or URL.")]


def parse_time(value: str | None) -> float | None:
    """Seconds, or a clock time like 12:04 or 1:02:03."""
    if value is None:
        return None
    seconds = 0.0
    for part in value.strip().split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def emit(value: Any) -> None:
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json")
    elif isinstance(value, list):
        data = [v.model_dump(mode="json") if isinstance(v, BaseModel) else v for v in value]
    else:
        data = value
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def run(make: Callable[[Reel], Awaitable[T]]) -> T:
    async def go() -> T:
        async with Reel() as reel:
            return await make(reel)

    try:
        return asyncio.run(go())
    except (
        VideoNotFound,
        MediaError,
        URLSupportUnavailable,
        FileNotFoundError,
        ValueError,
        TypeSafeError,
    ) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None


def load_lens(path: Path) -> Lens:
    try:
        return Lens.load(path)
    except (OSError, ValueError) as error:  # missing file, bad TOML or JSON, or an invalid lens
        typer.echo(f"error: lens {path}: {error}", err=True)
        raise typer.Exit(1) from None


def version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=version_callback, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    """Index a video once, then ask Jev about it."""


@app.command()
def index(
    source: Annotated[str, typer.Argument(help="Local video file, or a video URL (needs jevreel[url]).")],
    force: Annotated[bool, typer.Option(help="Re-index even if already indexed.")] = False,
    timeline: Annotated[
        bool, typer.Option(help="Ask every sheet the default questions (needs an API key).")
    ] = True,
    asr: Annotated[bool, typer.Option(help="Use local speech-to-text when there are no captions.")] = True,
    lens: Annotated[
        list[Path] | None,
        typer.Option("--lens", "-l", help="A lens file (TOML or JSON) to ask while indexing; repeat it."),
    ] = None,
) -> None:
    """Build contact sheets, get the transcript, and record a typed timeline."""
    lenses = [load_lens(path) for path in lens or []]

    async def progress(stage: str, done: int, total: int) -> None:
        end = "\n" if done == total else ""
        print(f"\r{stage:<10} {done}/{total}", end=end, file=sys.stderr, flush=True)

    result = run(
        lambda reel: reel.index(
            source,
            force=force,
            timeline=timeline,
            transcription="auto" if asr else "never",
            lenses=lenses,
            on_progress=progress,
        )
    )
    emit(result)


@app.command()
def ask(
    video: Video,
    query: Annotated[str, typer.Argument(help="What to find.")],
    mode: Annotated[str, typer.Option(help="auto, visual, speech or both.")] = "auto",
    start: Annotated[str | None, typer.Option(help="Search from this time (seconds or m:ss).")] = None,
    end: Annotated[str | None, typer.Option(help="Search up to this time.")] = None,
    threshold: Annotated[float, typer.Option(help="Lowest yes-probability to report.")] = 0.5,
    limit: Annotated[int, typer.Option(help="Most hits to report.")] = 10,
    verify: Annotated[bool, typer.Option(help="Re-check the top-ranked windows on full frames.")] = True,
    recheck: Annotated[
        bool,
        typer.Option(
            help="Also re-ask borderline sheets with shuffled tiles (mostly useful with --no-verify)."
        ),
    ] = False,
) -> None:
    """Find the moments that match a description."""
    if mode not in ("auto", "visual", "speech", "both"):
        raise typer.BadParameter("mode must be auto, visual, speech or both")
    chosen: Mode = mode  # type: ignore[assignment]
    emit(
        run(
            lambda reel: reel.ask(
                video,
                query,
                mode=chosen,
                start=parse_time(start),
                end=parse_time(end),
                threshold=threshold,
                limit=limit,
                recheck=recheck,
                verify=verify,
            )
        )
    )


@app.command()
def find(
    video: Video,
    image: Annotated[Path, typer.Argument(help="Reference photo of the person, logo or object.")],
    note: Annotated[str | None, typer.Option(help="What the photo shows, e.g. 'person' or 'logo'.")] = None,
    start: Annotated[str | None, typer.Option()] = None,
    end: Annotated[str | None, typer.Option()] = None,
    threshold: Annotated[float, typer.Option()] = 0.5,
    limit: Annotated[int, typer.Option()] = 10,
    verify: Annotated[bool, typer.Option(help="Re-check the top-ranked windows on full frames.")] = True,
) -> None:
    """Find every moment the subject of a reference photo appears."""
    emit(
        run(
            lambda reel: reel.find(
                video,
                image,
                note=note,
                start=parse_time(start),
                end=parse_time(end),
                threshold=threshold,
                limit=limit,
                verify=verify,
            )
        )
    )


@app.command()
def locate(
    video: Video,
    at: Annotated[str, typer.Argument(help="Time of the frame (seconds or m:ss).")],
    target: Annotated[str | None, typer.Option(help="What to locate.")] = None,
    image: Annotated[Path | None, typer.Option(help="Reference photo of what to locate.")] = None,
) -> None:
    """Box a target in one full-resolution frame and save the crop."""
    seconds = parse_time(at) or 0.0
    emit(run(lambda reel: reel.locate(video, seconds, target=target, image=image)))


@app.command()
def zoom(
    video: Video,
    start: Annotated[str, typer.Argument(help="From (seconds or m:ss).")],
    end: Annotated[str, typer.Argument(help="To (seconds or m:ss).")],
    count: Annotated[int, typer.Option(help="Evenly spaced frames to save.")] = 4,
    max_side: Annotated[int, typer.Option(help="Longest side in pixels.")] = 1024,
) -> None:
    """Save full-resolution frames between two times."""

    async def go(reel: Reel) -> Any:
        return reel.zoom(
            video, parse_time(start) or 0.0, parse_time(end) or 0.0, count=count, max_side=max_side
        )

    emit(run(go))


@app.command()
def transcript(
    video: Video,
    start: Annotated[str | None, typer.Option()] = None,
    end: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Print what is said, with timestamps."""

    async def go(reel: Reel) -> Any:
        return reel.transcript(video, parse_time(start), parse_time(end))

    emit(run(go))


@app.command()
def whole(
    video: Video,
    question: Annotated[str, typer.Argument(help="A question about the entire video.")],
    kind: Annotated[str, typer.Option(help="noul (yes/no), choice or score.")] = "noul",
    option: Annotated[
        list[str] | None, typer.Option("--option", "-o", help="A choice option or score level; repeat it.")
    ] = None,
) -> None:
    """One typed answer about the whole video."""
    if kind not in ("noul", "choice", "score"):
        raise typer.BadParameter("kind must be noul, choice or score")
    chosen: Kind = kind  # type: ignore[assignment]
    emit(run(lambda reel: reel.whole(video, question, kind=chosen, options=option)))


@app.command("lens")
def run_lens(
    video: Video,
    lens_file: Annotated[Path, typer.Argument(help="Lens file (TOML or JSON) with the questions to ask.")],
) -> None:
    """Ask a lens of every sheet of an indexed video (one request per sheet)."""
    lens = load_lens(lens_file)
    emit(run(lambda reel: reel.lens(video, lens)))


@app.command()
def query(
    video: Video,
    where: Annotated[
        str | None,
        typer.Argument(help='e.g. "people > 0.8 and setting == outdoors" or "safety.helmet_off >= 0.7"'),
    ] = None,
) -> None:
    """Filter the per-minute timeline (default answers and lenses). No requests, no cost."""

    async def go(reel: Reel) -> Any:
        return reel.query(video, where)

    emit(run(go))


@app.command("list")
def list_videos() -> None:
    """Videos indexed on this machine."""

    async def go(reel: Reel) -> Any:
        return reel.videos()

    emit(run(go))


@app.command()
def measure(
    source: Annotated[Path, typer.Argument(help="A local video to sample.")],
    tiles: Annotated[str, typer.Option(help="Comma-separated tiles per sheet to compare.")] = "28,60,112",
) -> None:
    """Measure the input tokens one contact sheet costs at several grid sizes (1 + one request per size)."""
    from jevreel.measure import measure as run_measure

    sizes = [int(t) for t in tiles.split(",") if t.strip()]

    async def go(reel: Reel) -> Any:
        return await run_measure(source, reel.judge, sizes, fps=reel.settings.fps)

    emit(run(go))


@app.command()
def mcp() -> None:
    """Run the MCP server on stdio."""
    from jevreel.server import main as serve

    serve()


@app.command()
def home() -> None:
    """Print where jevreel keeps indexed videos."""
    typer.echo(str(Settings.from_env().home))
