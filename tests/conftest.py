import shutil
import subprocess
from pathlib import Path

import pytest

from jevreel import Reel, Settings
from tests.fakes import FakeJudge

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

CAPTIONS = """1
00:00:01,000 --> 00:00:04,000
Hello and welcome.

2
00:01:05,500 --> 00:01:09,000
Now we talk about <i>caching</i>.
"""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if HAVE_FFMPEG:
        return
    skip = pytest.mark.skip(reason="ffmpeg and ffprobe are not on PATH")
    for item in items:
        if "ffmpeg" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 150-second 640x360 test pattern with a tone and an .srt next to it."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg and ffprobe are not on PATH")
    folder = tmp_path_factory.mktemp("media")
    path = folder / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=150",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=150",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )  # fmt: skip
    path.with_suffix(".srt").write_text(CAPTIONS, encoding="utf-8")
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(home=tmp_path / "home")


@pytest.fixture
def fake() -> FakeJudge:
    return FakeJudge()


@pytest.fixture
def reel(settings: Settings, fake: FakeJudge) -> Reel:
    return Reel(settings, judge=fake)
