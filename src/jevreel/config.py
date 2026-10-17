"""Settings shared by every jevreel operation, read from the environment with explicit overrides."""

import os
from dataclasses import dataclass, replace
from pathlib import Path

from platformdirs import user_cache_path, user_config_path

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
    # Screen-then-verify: how many top-ranked windows get their candidate frames re-checked at a
    # larger size, and the lowest screening probability worth re-checking. On the first live test,
    # windows holding a one-second event screened at 0.27-0.57 while empty ones stayed at 0.03-0.07.
    verify_top: int = 6
    verify_floor: float = 0.1
    # Reuse Jev's answer when the same model gets the same images and questions again.
    cache: bool = True

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
        if verify_top := env.get("JEVREEL_VERIFY_TOP"):
            values["verify_top"] = int(verify_top)
        if cache := env.get("JEVREEL_CACHE"):
            values["cache"] = cache.strip().lower() not in {"0", "false", "no", "off"}
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
        if self.verify_top < 0:
            raise ValueError("verify_top must be 0 (off) or more.")
        return self


def key_path() -> Path:
    """Where `jevreel login` saves the TypeSafe API key."""
    return Path(os.environ.get("JEVREEL_CONFIG") or user_config_path("jevreel", appauthor=False)) / "api_key"


def api_key() -> str | None:
    """TYPESAFE_API_KEY, else the key `jevreel login` saved.

    The saved key exists for agents that start MCP servers with a trimmed environment: Codex passes
    plugin servers only an allowlist of variables, and a plugin can't add to it.
    A literal `${...}` is a placeholder the host didn't fill in, not a key: Codex 0.144 reads the
    Claude plugin manifest and passes `${user_config.typesafe_api_key}` through unexpanded.
    """
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key and not key.startswith("${"):
        return key
    try:
        return key_path().read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def save_api_key(key: str) -> Path:
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.strip() + "\n", encoding="utf-8")
    path.chmod(0o600)  # owner-only on POSIX; Windows keeps the profile folder's own ACL
    return path


def estimated_cost_usd(input_tokens: int) -> float:
    return input_tokens * USD_PER_MILLION_INPUT_TOKENS / 1_000_000
