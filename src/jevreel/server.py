"""The MCP server: jevreel's operations as tools for Claude Code, Codex, Claude Desktop, Cursor, VS Code
and any other MCP client. The client's own model is the main agent; Jev does the screening."""

from pathlib import Path
from typing import Annotated, Literal

from fastmcp import Context, FastMCP
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image as McpImage
from fastmcp_tasks import TasksExtension
from mcp_types import ToolAnnotations
from pydantic import Field

from jevreel.lenses import Lens
from jevreel.reel import Reel
from jevreel.search import SearchResult

INSTRUCTIONS = """\
jevreel answers questions about videos without embeddings. Jev, a fast decision model, screens
every second of a video on contact sheets; you look at what it finds.

Workflow:
1. index_video once per video (file path or URL). It returns a video ID.
2. ask_video to find moments ("a slide about caching", "someone opens the fridge").
   find_in_video does the same from a reference photo of a person, logo or object.
3. zoom_video on a hit to see full-resolution frames, and read slides, code or faces yourself.
   locate_in_frame boxes and crops a target in one frame for close reading.
4. video_transcript for what is said around a moment. ask_whole_video for one answer about the
   entire video (genre, structure, whether something ever happens).
5. Lenses: when the user cares about the same things across a video (safety, brands, who is on
   screen), run_lens asks your own typed questions of every minute once; query_timeline then filters
   the answers as often as needed with no further cost.

Hits marked verified were re-checked on full frames; their probability and time come from those.
When hits are few, look at `weak`: windows Jev did not rule out.

Jev's probabilities are tuned for ranking, not guaranteed calibrated: zoom in before stating
anything you have not seen. Cite moments with the times the tools return (e.g. 12:04)."""

mcp = FastMCP("jevreel", instructions=INSTRUCTIONS)
mcp.add_extension(TasksExtension())

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_reel: Reel | None = None

VideoRef = Annotated[
    str, Field(description="Video ID from index_video, or the file path or URL that was indexed.")
]
Seconds = Annotated[float, Field(description="Seconds from the start of the video.", ge=0)]


def reel() -> Reel:
    global _reel
    if _reel is None:
        _reel = Reel()
    return _reel


def _thumbnails(result: SearchResult, count: int) -> list[McpImage]:
    """Evidence for the top hits: the verified full frame, or else the tile cropped from its sheet."""
    video = reel().store.open(result.video)
    layout = video.manifest.layout
    images = []
    for hit in result.hits[:count]:
        if hit.frame:
            images.append(McpImage(path=Path(hit.frame)))
            continue
        sheet = video.manifest.sheets[hit.sheet]
        slot = next((i for i, (tile_id, _) in enumerate(sheet.tiles) if tile_id == hit.anchor), None)
        if slot is None:
            continue
        tile = video.sheet_image(hit.sheet).crop(layout.slot_box(slot))
        path = video.subdir("tiles") / f"hit-{hit.sheet:04d}-{hit.anchor}.jpg"
        tile.save(path, format="JPEG", quality=90)
        images.append(McpImage(path=path))
    return images


@mcp.tool(task=True, annotations=ToolAnnotations(read_only_hint=False, open_world_hint=True))
async def index_video(
    source: Annotated[str, Field(description="Path to a local video file, or a video URL.")],
    ctx: Context,
    force: Annotated[bool, Field(description="Re-index even if this video was indexed before.")] = False,
    lenses: Annotated[
        list[Lens] | None,
        Field(
            description="Your own questions to ask of every minute while indexing, at almost no extra cost."
        ),
    ] = None,
) -> ToolResult:
    """Prepare a video: contact sheets at 1 frame per second, the transcript (captions, or local
    speech-to-text if installed), and a timeline of default answers for every minute. Run once per video;
    later calls return the cached index immediately and only run lenses it has not seen."""

    async def progress(stage: str, done: int, total: int) -> None:
        await ctx.report_progress(done, total, f"{stage}: {done}/{total}")

    result = await reel().index(source, force=force, lenses=lenses, on_progress=progress)
    return ToolResult(content=result.model_dump_json(indent=1), structured_content=result.model_dump())


@mcp.tool(annotations=READ_ONLY)
async def ask_video(
    video: VideoRef,
    query: Annotated[
        str, Field(description="What to find, e.g. 'a whiteboard diagram' or 'the speaker mentions pricing'.")
    ],
    mode: Annotated[
        Literal["auto", "visual", "speech", "both"],
        Field(
            description="visual: on screen only. speech: transcript only. both: either. auto: both when there is a transcript."
        ),
    ] = "auto",
    start: Annotated[float | None, Field(description="Only search from this second.", ge=0)] = None,
    end: Annotated[float | None, Field(description="Only search up to this second.", ge=0)] = None,
    limit: Annotated[int, Field(description="Most hits to return.", ge=1, le=50)] = 8,
    thumbnails: Annotated[int, Field(description="How many top hits to show as images.", ge=0, le=12)] = 4,
    verify: Annotated[
        bool, Field(description="Re-check the top-ranked windows on full frames (recommended).")
    ] = True,
) -> ToolResult:
    """Find the moments in a video that match a description. Jev screens every contact sheet in parallel,
    re-checks the top-ranked windows on full frames, and returns times, probabilities and nearby speech."""
    result = await reel().ask(video, query, mode=mode, start=start, end=end, limit=limit, verify=verify)
    return ToolResult(
        content=[result.model_dump_json(indent=1), *_thumbnails(result, thumbnails)],
        structured_content=result.model_dump(),
    )


@mcp.tool(annotations=READ_ONLY)
async def find_in_video(
    video: VideoRef,
    image_path: Annotated[
        str, Field(description="Local path to a reference photo of the person, logo or object.")
    ],
    note: Annotated[
        str | None, Field(description="What the reference shows, e.g. 'person', 'logo', 'red backpack'.")
    ] = None,
    start: Annotated[float | None, Field(ge=0)] = None,
    end: Annotated[float | None, Field(ge=0)] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 8,
    thumbnails: Annotated[int, Field(ge=0, le=12)] = 4,
    verify: Annotated[bool, Field(description="Re-check the top-ranked windows on full frames.")] = True,
) -> ToolResult:
    """Find every moment the subject of a reference photo appears, without embeddings: Jev compares
    the photo with every contact sheet, then re-checks the best windows on full frames."""
    result = await reel().find(video, image_path, note=note, start=start, end=end, limit=limit, verify=verify)
    return ToolResult(
        content=[result.model_dump_json(indent=1), *_thumbnails(result, thumbnails)],
        structured_content=result.model_dump(),
    )


@mcp.tool(annotations=READ_ONLY)
async def locate_in_frame(
    video: VideoRef,
    at: Seconds,
    target: Annotated[str | None, Field(description="What to locate, e.g. 'the code editor'.")] = None,
    image_path: Annotated[str | None, Field(description="Reference photo of what to locate.")] = None,
) -> ToolResult:
    """Box a target in the full-resolution frame at a given second and return the crop, for reading small
    text or checking details. Says found=false when the target is not in that frame."""
    result = await reel().locate(video, at, target=target, image=image_path)
    images = [McpImage(path=Path(result.crop))] if result.crop else []
    return ToolResult(
        content=[result.model_dump_json(indent=1), *images], structured_content=result.model_dump()
    )


@mcp.tool(annotations=READ_ONLY)
async def zoom_video(
    video: VideoRef,
    start: Seconds,
    end: Seconds,
    count: Annotated[int, Field(description="Number of evenly spaced frames.", ge=1, le=8)] = 3,
    max_side: Annotated[
        int, Field(description="Longest side of each frame in pixels.", ge=256, le=1568)
    ] = 1024,
) -> ToolResult:
    """Full-resolution frames between two times, for you to look at directly."""
    stills = reel().zoom(video, start, end, count=count, max_side=max_side)
    listing = "\n".join(f"{s.time} ({s.at}s): {s.path}" for s in stills)
    return ToolResult(
        content=[listing, *(McpImage(path=Path(s.path)) for s in stills)],
        structured_content={"frames": [s.model_dump() for s in stills]},
    )


@mcp.tool(annotations=READ_ONLY)
def video_transcript(
    video: VideoRef,
    start: Annotated[float | None, Field(ge=0)] = None,
    end: Annotated[float | None, Field(ge=0)] = None,
) -> ToolResult:
    """What is said between two times (the whole video when both are omitted), with timestamps."""
    result = reel().transcript(video, start, end)
    if result.source is None:
        text = (
            "This video has no transcript: no captions were found and local speech-to-text is not installed."
        )
    else:
        text = (
            "\n".join(f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in result.segments) or "(no speech here)"
        )
    return ToolResult(content=text, structured_content=result.model_dump())


@mcp.tool(annotations=READ_ONLY)
async def ask_whole_video(
    video: VideoRef,
    question: Annotated[str, Field(description="A question about the entire video.")],
    kind: Annotated[
        Literal["noul", "choice", "score"],
        Field(
            description="noul: yes/no probability. choice: pick one option. score: rate on ordered levels."
        ),
    ] = "noul",
    options: Annotated[
        list[str] | None,
        Field(description="Options for choice, or levels lowest first for score (at most 10)."),
    ] = None,
) -> ToolResult:
    """One typed answer about the whole video, from a single request that sees a storyboard of the video,
    the per-minute timeline and the full transcript."""
    result = await reel().whole(video, question, kind=kind, options=options)
    return ToolResult(content=result.model_dump_json(indent=1), structured_content=result.model_dump())


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, open_world_hint=True))
async def run_lens(video: VideoRef, lens: Lens) -> ToolResult:
    """Ask your own typed questions of every minute of an indexed video and keep the answers as timeline
    columns. Questions: noul (yes/no; also finds the second it happens), choice (options), or score
    (levels, lowest first). One request per minute; filter the results afterwards with query_timeline."""
    result = await reel().lens(video, lens)
    return ToolResult(content=result.model_dump_json(indent=1), structured_content=result.model_dump())


@mcp.tool(annotations=READ_ONLY)
def query_timeline(
    video: VideoRef,
    where: Annotated[
        str | None,
        Field(
            description="Conditions joined by 'and', e.g. 'people > 0.8 and setting == outdoors' or "
            "'safety.helmet_off >= 0.7'. Omit to get every minute."
        ),
    ] = None,
) -> ToolResult:
    """Filter a video's per-minute timeline (default answers plus every lens) with no requests and no cost."""
    result = reel().query(video, where)
    return ToolResult(content=result.model_dump_json(indent=1), structured_content=result.model_dump())


@mcp.tool(annotations=READ_ONLY)
def list_videos() -> ToolResult:
    """Videos already indexed on this machine."""
    videos = reel().videos()
    return ToolResult(
        content="\n".join(f"{v.id}  {v.duration:>8}  {v.title}" for v in videos) or "No videos indexed yet.",
        structured_content={"videos": [v.model_dump() for v in videos]},
    )


def main() -> None:
    mcp.run()
