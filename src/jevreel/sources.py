"""Turning what the user passes (a file path or a URL) into a local video file, plus any captions."""

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Fetched:
    media: Path
    title: str
    captions: Path | None


def is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def local_file(source: str, what: str = "video file") -> Path:
    """The absolute path of a local file, or an error that says plainly it isn't there.

    The bare OS error on Windows, "[WinError 2] The system cannot find the file specified", reads like
    a missing program: an agent took it for a missing ffmpeg and gave up.
    """
    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(
            f"No {what} at {path.resolve()}. Relative paths are resolved from {Path.cwd()}."
        )
    return path.resolve()


def video_id(source: str) -> str:
    """Stable 12-character ID: a URL by its text, a file by its absolute path, size and modification time."""
    if is_url(source):
        key = source.strip()
    else:
        path = Path(source).expanduser().resolve()
        stat = path.stat()
        key = f"{path}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


class URLSupportUnavailable(RuntimeError):
    """yt-dlp is not installed."""


def fetch_url(url: str, dest: Path, languages: tuple[str, ...]) -> Fetched:
    """Download a video at up to 720p (plenty for 1 fps tiles) with its captions, if it has any."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:
        raise URLSupportUnavailable("Indexing URLs needs yt-dlp: pip install 'jevreel[url]'") from error
    dest.mkdir(parents=True, exist_ok=True)
    options = {
        "outtmpl": str(dest / "video.%(ext)s"),
        "format": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": list(languages),
        "subtitlesformat": "vtt/srt/best",
        "quiet": True,
        "noprogress": True,
    }
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
    files = sorted(p for p in dest.glob("video.*") if p.is_file())
    media = next((p for p in files if p.suffix.lower() not in {".vtt", ".srt", ".part", ".ytdl"}), None)
    if media is None:
        raise RuntimeError(f"yt-dlp finished without a video file for {url}.")
    captions = next((p for p in files if p.suffix.lower() in {".vtt", ".srt"}), None)
    return Fetched(media=media, title=str(info.get("title") or url), captions=captions)
