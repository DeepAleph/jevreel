"""Screen-then-verify, lenses with timeline queries, and the answer cache."""

from pathlib import Path
from typing import Any

import pytest
from typesafe_sdk import Choice, Question

from jevreel import Reel, Settings
from jevreel.cache import CachedJudge
from jevreel.lenses import Lens
from tests.fakes import FakeJudge, choice, noul
from tests.test_operations import tiles_rule

pytestmark = pytest.mark.ffmpeg

LENS = Lens.model_validate(
    {
        "name": "demo",
        "questions": [
            {"name": "pattern", "type": "noul", "ask": "Is a test pattern visible?"},
            {
                "name": "palette",
                "type": "choice",
                "ask": "Which colours dominate?",
                "options": ["warm", "cool"],
            },
            {"name": "busy", "type": "score", "ask": "How busy is it?", "levels": ["calm", "some", "busy"]},
        ],
    }
)

TOML_LENS = """
[[questions]]
name = "helmet_off"
type = "noul"
ask = "Is a worker without a helmet visible?"
"""


async def test_verify_rescues_a_low_screening_window(settings: Settings, clip: Path) -> None:
    # Screening says only 0.27, as a one-second event did on the live test; the full frame says yes.
    fake = FakeJudge(tiles_rule("0:00-1:00", "T10", 0.27, frame="0:08"))
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        result = await reel.ask(str(clip), "the pause menu", mode="visual")
    [hit] = result.hits
    assert hit.verified and hit.screened == 0.27 and hit.probability == 0.9
    assert (hit.at, hit.time) == (8.0, "0:08")  # one second before the tile screening picked
    assert hit.frame is not None and Path(hit.frame).is_file()
    verify_calls = [s for s, _, _ in fake.calls if "frames" in s]
    assert len(verify_calls) == 1 and len(verify_calls[0]["frames"]) == 7  # 7.5..10.5 s, every 0.5 s
    assert result.requests == 4


async def test_verify_can_overturn_a_screening_hit(settings: Settings, clip: Path) -> None:
    fake = FakeJudge(tiles_rule("0:58-1:58", "T10", 0.92, frame=None))
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        result = await reel.ask(str(clip), "something that isn't there", mode="visual")
    assert result.hits == []
    [weak] = result.weak  # still visible: screening thought it was there
    assert weak.verified and weak.screened == 0.92 and weak.probability == 0.05


async def test_verify_skips_windows_below_the_floor(settings: Settings, clip: Path) -> None:
    fake = FakeJudge()  # every window screens at 0.05, below the 0.1 floor
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        result = await reel.ask(str(clip), "nothing", mode="visual")
    assert result.hits == [] and result.requests == 3


async def test_lens_rides_in_the_index_request_and_can_be_queried(settings: Settings, clip: Path) -> None:
    def rule(name: str, question: Question, state: Any) -> dict[str, Any] | None:
        middle = state.get("window") == "0:58-1:58"
        if name == "demo:pattern":
            return noul(0.95 if middle else 0.2)
        if name == "demo:pattern@tile" and isinstance(question, Choice):
            return choice(dict.fromkeys(question.criteria, 0.0) | {"T03": 1.0})
        if name == "demo:palette" and isinstance(question, Choice):
            return choice({"warm": 0.7, "cool": 0.3} if middle else {"warm": 0.2, "cool": 0.8})
        return None

    fake = FakeJudge(rule)
    async with Reel(settings, judge=fake) as reel:
        indexed = await reel.index(str(clip), lenses=[LENS])
        assert indexed.lenses == ["demo"] and indexed.requests == 3  # no extra requests for the lens
        assert {"keyframe", "demo:pattern", "demo:pattern@tile", "demo:busy"} <= set(fake.calls[0][1])

        everything = reel.query(str(clip))
        assert {"people", "demo.pattern", "demo.pattern_at", "demo.palette", "demo.busy"} <= set(
            everything.columns
        )
        assert len(everything.rows) == 3

        matched = reel.query(str(clip), "pattern > 0.9 and demo.palette == warm")
        [row] = matched.rows
        assert row["window"] == "0:58-1:58" and row["demo.pattern_at"] == "1:00"

        with pytest.raises(ValueError, match="Unknown or ambiguous column"):
            reel.query(str(clip), "nonsense > 1")

        # Re-indexing with the same lens is free; a new lens on an indexed video is one request per sheet.
        again = await reel.index(str(clip), lenses=[LENS])
        assert again.reused and again.requests == 0
        other = LENS.model_copy(update={"name": "other"})
        ran = await reel.lens(str(clip), other)
        assert ran.requests == 3 and "other.pattern" in ran.columns


def test_lens_files_load_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "site-safety.toml"
    path.write_text(TOML_LENS, encoding="utf-8")
    lens = Lens.load(path)
    assert lens.name == "site-safety" and lens.questions[0].locate
    with pytest.raises(ValueError, match="needs 2 to 10 levels"):
        Lens.model_validate(
            {"name": "x", "questions": [{"name": "a", "type": "score", "ask": "How?", "levels": ["one"]}]}
        )


async def test_cache_answers_repeats_for_free(settings: Settings, clip: Path) -> None:
    fake = FakeJudge(tiles_rule("0:58-1:58", "T10", 0.92, frame="1:07"))
    cached = CachedJudge(fake, settings.home / "cache", settings.model)
    async with Reel(settings, judge=cached) as reel:
        await reel.index(str(clip), timeline=False)
        first = await reel.ask(str(clip), "a caching diagram", mode="visual")
        second = await reel.ask(str(clip), "a caching diagram", mode="visual")
        third = await reel.ask(str(clip), "a different question", mode="visual")
    assert first.requests == 4 and first.cached == 0
    assert second.requests == 0 and second.cached == 4 and second.input_tokens == 0
    assert [h.time for h in second.hits] == [h.time for h in first.hits] == ["1:07"]
    assert third.requests >= 3 and third.cached == 0
    assert len(fake.calls) == first.requests + third.requests
