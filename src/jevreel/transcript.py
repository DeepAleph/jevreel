"""What is said in a video: captions when they exist, local speech-to-text when they don't."""

import html
import re
from pathlib import Path

from pydantic import BaseModel

from jevreel.timefmt import clock


class Segment(BaseModel):
    start: float
    end: float
    text: str


class Transcript(BaseModel):
    source: str
    language: str | None = None
    segments: list[Segment]

    def window(self, start: float | None = None, end: float | None = None) -> list[Segment]:
        lo = float("-inf") if start is None else start
        hi = float("inf") if end is None else end
        return [s for s in self.segments if s.end > lo and s.start < hi]

    def text(self, start: float | None = None, end: float | None = None) -> str:
        return "\n".join(f"[{clock(s.start)}] {s.text}" for s in self.window(start, end))


_TIMING = re.compile(
    r"(?P<start>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*(?P<end>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})"
)
_TAG = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def _seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    seconds = float(parts[-1])
    minutes = int(parts[-2]) if len(parts) >= 2 else 0
    hours = int(parts[-3]) if len(parts) >= 3 else 0
    return hours * 3600 + minutes * 60 + seconds


def parse_captions(text: str) -> list[Segment]:
    """Parse SRT or WebVTT, dropping markup and the repeated lines of rolling auto-captions."""
    segments: list[Segment] = []
    recent: list[str] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.lstrip("﻿")):
        lines = [line.strip() for line in block.strip().splitlines()]
        timing_index = next((i for i, line in enumerate(lines) if _TIMING.search(line)), None)
        if timing_index is None:
            continue  # WEBVTT header, NOTE, STYLE, or REGION block
        match = _TIMING.search(lines[timing_index])
        assert match is not None
        start, end = _seconds(match["start"]), _seconds(match["end"])
        for raw in lines[timing_index + 1 :]:
            line = html.unescape(_TAG.sub("", raw)).strip()
            # Auto-captions repeat the previous line above each new one; keep each line once.
            if not line or line in recent:
                continue
            segments.append(Segment(start=start, end=end, text=line))
            recent = [*recent[-1:], line]
    return segments


CAPTION_SUFFIXES = (".vtt", ".srt")


def find_sidecar(media: Path) -> Path | None:
    """Captions saved next to the video: `talk.srt`, `talk.vtt`, or `talk.<lang>.vtt`."""
    for suffix in CAPTION_SUFFIXES:
        exact = media.with_suffix(suffix)
        if exact.is_file():
            return exact
    for candidate in sorted(media.parent.glob(f"{glob_escape(media.stem)}.*")):
        if candidate.suffix.lower() in CAPTION_SUFFIXES and candidate.is_file():
            return candidate
    return None


def glob_escape(text: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", text)


def load_captions(path: Path) -> Transcript:
    text = path.read_text(encoding="utf-8", errors="replace")
    return Transcript(source=f"captions:{path.name}", segments=parse_captions(text))


class ASRUnavailable(RuntimeError):
    """faster-whisper is not installed."""


def transcribe(media: Path, model: str = "small", language: str | None = None) -> Transcript:
    """Local speech-to-text with faster-whisper; segment timing is plenty at one frame per second."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise ASRUnavailable(
            "This video has no captions. Install local speech-to-text with: pip install 'jevreel[asr]'"
        ) from error
    whisper = WhisperModel(model, device="auto", compute_type="default")
    pieces, info = whisper.transcribe(str(media), language=language, vad_filter=True)
    segments = [Segment(start=p.start, end=p.end, text=p.text.strip()) for p in pieces if p.text.strip()]
    return Transcript(source=f"asr:faster-whisper-{model}", language=info.language, segments=segments)
