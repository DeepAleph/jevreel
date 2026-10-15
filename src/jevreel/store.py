"""Where indexed videos live: one folder per video under the jevreel home.

<home>/videos/<id>/
    manifest.json     what was indexed and how (layout, sheets, tile timestamps)
    sheets/           the contact sheets Jev judges
    transcript.json   captions or speech-to-text, if any
    timeline.json     default per-sheet answers from indexing
    frames/ crops/    full-resolution stills and box crops made on demand
    source/           the downloaded video, for URLs
"""

import json
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path

from PIL import Image
from pydantic import BaseModel

from jevreel.sheets import Layout, SheetInfo
from jevreel.sources import is_url, video_id
from jevreel.transcript import Transcript


class WindowFacets(BaseModel):
    """Indexing's default answers for one sheet: the typed timeline used by whole-video questions."""

    sheet: int
    start: float
    end: float
    keyframe: float
    people: float
    talking_to_camera: float
    screen_or_slides: float
    text_on_screen: float
    cut: float
    setting: str
    activity: float


class Manifest(BaseModel):
    id: str
    source: str
    title: str
    media: str
    duration: float
    width: int
    height: int
    has_audio: bool
    fps: float
    tiles_per_sheet: int
    layout: Layout
    sheets: list[SheetInfo]
    transcript_source: str | None = None
    has_timeline: bool = False
    model: str
    created: str
    jevreel_version: str
    input_tokens: int = 0


class VideoNotFound(LookupError):
    pass


class Video:
    """An indexed video and lazy access to its files."""

    def __init__(self, root: Path, manifest: Manifest) -> None:
        self.root = root
        self.manifest = manifest
        self._sheet_images: dict[int, Image.Image] = {}

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def media(self) -> Path:
        return Path(self.manifest.media)

    def sheet_path(self, index: int) -> Path:
        return self.root / "sheets" / self.manifest.sheets[index].file

    def sheet_image(self, index: int) -> Image.Image:
        if index not in self._sheet_images:
            with Image.open(self.sheet_path(index)) as image:
                self._sheet_images[index] = image.convert("RGB")
        return self._sheet_images[index]

    @cached_property
    def transcript(self) -> Transcript | None:
        path = self.root / "transcript.json"
        return Transcript.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    @cached_property
    def timeline(self) -> list[WindowFacets]:
        path = self.root / "timeline.json"
        if not path.is_file():
            return []
        return [WindowFacets.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]

    def subdir(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return path


class Store:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.videos = home / "videos"

    def dir_for(self, vid: str) -> Path:
        return self.videos / vid

    def save_manifest(self, root: Path, manifest: Manifest) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    def load(self, vid: str) -> Video | None:
        path = self.dir_for(vid) / "manifest.json"
        if not path.is_file():
            return None
        return Video(path.parent, Manifest.model_validate_json(path.read_text(encoding="utf-8")))

    def open(self, ref: str) -> Video:
        """Find a video by ID, unique ID prefix, file path, URL or exact title."""
        candidates = [ref]
        if is_url(ref):
            candidates.insert(0, video_id(ref))
        elif Path(ref).expanduser().is_file():
            candidates.insert(0, video_id(str(Path(ref).expanduser().resolve())))
        for candidate in candidates:
            if video := self.load(candidate):
                return video
        if len(ref) >= 4 and self.videos.is_dir():
            matches = [p.name for p in self.videos.iterdir() if p.name.startswith(ref)]
            if len(matches) == 1 and (video := self.load(matches[0])):
                return video
        titled = [m.id for m in self.list() if m.title.casefold() == ref.casefold()]
        if len(titled) == 1 and (video := self.load(titled[0])):
            return video
        raise VideoNotFound(f"No indexed video matches {ref!r}. Index it first with `jevreel index`.")

    def list(self) -> list[Manifest]:
        if not self.videos.is_dir():
            return []
        found = []
        for root in sorted(self.videos.iterdir()):
            if (root / "manifest.json").is_file():
                found.append(
                    Manifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
                )
        return sorted(found, key=lambda m: m.created, reverse=True)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
