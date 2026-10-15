"""The Python API: one object that the CLI, the MCP server and scripts all use."""

from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import BaseModel

from jevreel.cache import CachedJudge
from jevreel.config import Settings
from jevreel.frames import Still, zoom
from jevreel.indexing import IndexResult, Progress, TranscribeMode, index_video
from jevreel.judge import JevJudge, Judge
from jevreel.lenses import Lens, LensResult, run_lens
from jevreel.locate import Located, locate
from jevreel.query import QueryResult, query
from jevreel.search import Mode, SearchResult, search
from jevreel.store import Store
from jevreel.timefmt import clock
from jevreel.transcript import Segment
from jevreel.whole import Kind, WholeAnswer, whole


class VideoSummary(BaseModel):
    id: str
    title: str
    duration: str
    source: str
    sheets: int
    transcript: str | None
    timeline: bool
    created: str


class TranscriptResult(BaseModel):
    video: str
    source: str | None
    segments: list[Segment]


class Reel:
    """Index videos and ask Jev about them.

    ```python
    async with Reel() as reel:
        video = await reel.index("talk.mp4")
        result = await reel.ask(video.id, "a slide about caching")
        for hit in result.hits:
            print(hit.time, hit.probability)
    ```
    """

    def __init__(self, settings: Settings | None = None, *, judge: Judge | None = None) -> None:
        self.settings = (settings or Settings.from_env()).validate()
        self.store = Store(self.settings.home)
        self._judge = judge

    @property
    def judge(self) -> Judge:
        # Created on first use, so indexing without a timeline, zoom and transcripts need no API key.
        if self._judge is None:
            judge: Judge = JevJudge(model=self.settings.model, concurrency=self.settings.concurrency)
            if self.settings.cache:
                judge = CachedJudge(judge, self.settings.home / "cache", self.settings.model)
            self._judge = judge
        return self._judge

    async def index(
        self,
        source: str,
        *,
        force: bool = False,
        timeline: bool = True,
        transcription: TranscribeMode = "auto",
        lenses: list[Lens] | None = None,
        on_progress: Progress | None = None,
    ) -> IndexResult:
        return await index_video(
            self.store,
            self.settings,
            source,
            judge=lambda: self.judge,
            force=force,
            timeline=timeline,
            transcription=transcription,
            lenses=lenses,
            on_progress=on_progress,
        )

    async def ask(
        self,
        video: str,
        query: str,
        *,
        mode: Mode = "auto",
        start: float | None = None,
        end: float | None = None,
        threshold: float = 0.5,
        limit: int = 10,
        recheck: bool = False,
        verify: bool = True,
    ) -> SearchResult:
        return await search(
            self.store.open(video),
            self.judge,
            self.settings,
            query=query,
            mode=mode,
            start=start,
            end=end,
            threshold=threshold,
            limit=limit,
            recheck=recheck,
            verify=verify,
        )

    async def find(
        self,
        video: str,
        image: str | Path,
        *,
        note: str | None = None,
        start: float | None = None,
        end: float | None = None,
        threshold: float = 0.5,
        limit: int = 10,
        recheck: bool = False,
        verify: bool = True,
    ) -> SearchResult:
        reference = Path(image).expanduser()
        if not reference.is_file():
            raise FileNotFoundError(f"Reference image not found: {reference}")
        return await search(
            self.store.open(video),
            self.judge,
            self.settings,
            query="",
            reference=reference,
            note=note,
            start=start,
            end=end,
            threshold=threshold,
            limit=limit,
            recheck=recheck,
            verify=verify,
        )

    async def locate(
        self, video: str, at: float, *, target: str | None = None, image: str | Path | None = None
    ) -> Located:
        reference = Path(image).expanduser() if image is not None else None
        if reference is not None and not reference.is_file():
            raise FileNotFoundError(f"Reference image not found: {reference}")
        return await locate(self.store.open(video), self.judge, at=at, target=target, reference=reference)

    def zoom(
        self, video: str, start: float, end: float, *, count: int = 4, max_side: int = 1024
    ) -> list[Still]:
        return zoom(self.store.open(video), start, end, count=count, max_side=max_side)

    def transcript(
        self, video: str, start: float | None = None, end: float | None = None
    ) -> TranscriptResult:
        opened = self.store.open(video)
        transcript = opened.transcript
        return TranscriptResult(
            video=opened.id,
            source=transcript.source if transcript else None,
            segments=transcript.window(start, end) if transcript else [],
        )

    async def whole(
        self, video: str, question: str, *, kind: Kind = "noul", options: list[str] | None = None
    ) -> WholeAnswer:
        return await whole(self.store.open(video), self.judge, question=question, kind=kind, options=options)

    async def lens(self, video: str, lens: Lens) -> LensResult:
        """Run a lens over an already indexed video: one request per sheet, answers kept on disk."""
        return await run_lens(self.store.open(video), self.judge, lens)

    def query(self, video: str, where: str | None = None) -> QueryResult:
        """Filter the timeline (default answers and lenses) without any requests."""
        return query(self.store.open(video), where)

    def videos(self) -> list[VideoSummary]:
        return [
            VideoSummary(
                id=m.id,
                title=m.title,
                duration=clock(m.duration),
                source=m.source,
                sheets=len(m.sheets),
                transcript=m.transcript_source,
                timeline=m.has_timeline,
                created=m.created,
            )
            for m in self.store.list()
        ]

    async def aclose(self) -> None:
        if self._judge is not None:
            await self._judge.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()
