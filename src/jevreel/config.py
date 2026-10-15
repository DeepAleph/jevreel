"""Settings shared by every jevreel operation, read from the environment with explicit overrides."""

import os
from dataclasses import dataclass, replace
from pathlib import Path

from platformdirs import user_cache_path

# Pinned rather than an alias: Box questions and image input need Jev 1.14, and `jev-latest`
# and `jev-preview` move between releases without notice to us.
DEFAULT_MODEL = "jev-1.14.0"

# Jev 1.14's launch price in USD. Output tokens are free, so input is the whole bill.
USD_PER_MILLION_INPUT_TOKENS = 0.042

# TypeSafe's own line-search cookbook treats >= 0.7 as found and <= 0.35 as absent;
# anything between is re-asked once before it is trusted.
FOUND = 0.7
ABSENT = 0.35


@dataclass(frozen=True)
class Settings:
    home: Path
    model: str = DEFAULT_MODEL
    # One frame per second: most footage changes slower than that, and it keeps tiles large.
    fps: float = 1.0
    # 60 tiles fill a 1568 px sheet with 261x146 tiles for 16:9 video: one minute per request.
    tiles_per_sheet: int = 60
    # Consecutive sheets share this many tiles, so an event on a boundary is whole on one of them.
    sheet_overlap: int = 2
    # Parallel Jev requests. The SDK keeps up to 100 connections; rate limits are per account.
    concurrency: int = 16
    found: float = FOUND
    absent: float = ABSENT
    # Speech-to-text model for videos without captions ("tiny" .. "large-v3", or a local path).
    asr_model: str = "small"
    # Caption languages to ask yt-dlp for, in order of preference.
    caption_languages: tuple[str, ...] = ("en", "en-orig", "en.*")

    @classmethod
    def from_env(cls, **overrides: object) -> "Settings":
        env = os.environ
        values: dict[str, object] = {
            "home": Path(env.get("JEVREEL_HOME") or user_cache_path("jevreel", appauthor=False)),
        }
        if model := env.get("JEVREEL_MODEL"):
            values["model"] = model
        if fps := env.get("JEVREEL_FPS"):
            values["fps"] = float(fps)
        if tiles := env.get("JEVREEL_TILES_PER_SHEET"):
            values["tiles_per_sheet"] = int(tiles)
        if concurrency := env.get("JEVREEL_CONCURRENCY"):
            values["concurrency"] = int(concurrency)
        if asr_model := env.get("JEVREEL_ASR_MODEL"):
            values["asr_model"] = asr_model
        if languages := env.get("JEVREEL_CAPTION_LANGUAGES"):
            values["caption_languages"] = tuple(lang.strip() for lang in languages.split(",") if lang.strip())
        settings = cls(**values)  # type: ignore[arg-type]
        return replace(settings, **{key: value for key, value in overrides.items() if value is not None})

    def validate(self) -> "Settings":
        if not 0 < self.fps <= 5:
            raise ValueError("fps must be above 0 and at most 5.")
        if not 2 <= self.tiles_per_sheet <= 254:
            # A tile Choice needs one option per tile plus "none", and Choice allows 255 options.
            raise ValueError("tiles_per_sheet must be between 2 and 254.")
        if not 0 <= self.sheet_overlap < self.tiles_per_sheet:
            raise ValueError("sheet_overlap must be at least 0 and below tiles_per_sheet.")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1.")
        return self


def estimated_cost_usd(input_tokens: int) -> float:
    return input_tokens * USD_PER_MILLION_INPUT_TOKENS / 1_000_000
