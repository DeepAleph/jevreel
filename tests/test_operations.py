"""Indexing, ask, find, locate and whole-video questions against a scripted Jev."""

from pathlib import Path
from typing import Any

import httpx2
import pytest
from PIL import Image
from typesafe_sdk import Box, Choice, Noul, Question, Score, TypeSafeBadRequestError

from jevreel import Reel, Settings
from tests.fakes import FakeJudge, box, choice, noul

pytestmark = pytest.mark.ffmpeg


def tiles_rule(window: str, tile: str, p: float):
    """Say yes, with `tile` as the moment, only on the sheet whose window is `window`."""

    def rule(name: str, question: Question, state: Any) -> dict[str, Any] | None:
        if state.get("window") != window:
            return None
        if name == "shown":
            return noul(p)
        if name == "tile" and isinstance(question, Choice):
            labels = list(question.criteria)
            return choice({label: (0.8 if label == tile else 0.2 / (len(labels) - 1)) for label in labels})
        return None

    return rule


async def test_index_records_sheets_transcript_and_timeline(reel: Reel, fake: FakeJudge, clip: Path) -> None:
    result = await reel.index(str(clip))
    assert result.sheets == 3
    assert result.transcript == "captions:clip.srt"
    assert result.timeline
    assert result.requests == 3 == len(fake.calls)
    first_state, first_questions, _ = fake.calls[0]
    assert set(first_questions) >= {"keyframe", "people", "cut", "setting", "activity"}
    assert "tiles" not in first_state  # the tile-to-time map stays in code
    assert first_state["transcript"] == {"L01": "[0:01] Hello and welcome."}

    again = await reel.index(str(clip))
    assert again.cached and len(fake.calls) == 3


async def test_index_without_timeline_needs_no_judge(settings: Settings, clip: Path) -> None:
    async with Reel(settings) as reel:  # no judge: creating one would need an API key
        result = await reel.index(str(clip), timeline=False)
    assert not result.timeline and result.requests == 0


async def test_ask_finds_the_moment(settings: Settings, clip: Path) -> None:
    fake = FakeJudge(tiles_rule("0:58-1:58", "T10", 0.92))
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        result = await reel.ask(str(clip), "a caching diagram", mode="visual")
    assert result.windows_checked == 3 and result.requests == 3
    [hit] = result.hits
    assert (hit.at, hit.time, hit.anchor, hit.sheet) == (67.0, "1:07", "T10", 1)
    assert hit.strong and not hit.rechecked
    assert "caching" in (hit.transcript or "")


async def test_borderline_sheet_is_asked_again_shuffled(settings: Settings, clip: Path) -> None:
    fake = FakeJudge(tiles_rule("0:00-1:00", "T05", 0.5))
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        result = await reel.ask(str(clip), "something faint", mode="visual")
    assert result.requests == 4
    rechecks = [s for s, _, _ in fake.calls if "shuffled" in s.get("layout", "")]
    assert len(rechecks) == 1 and rechecks[0]["window"] == "0:00-1:00"
    [hit] = result.hits
    assert hit.rechecked and not hit.strong and hit.at == 4.0


async def test_threshold_filters_weak_windows(settings: Settings, clip: Path) -> None:
    fake = FakeJudge(tiles_rule("0:00-1:00", "T05", 0.3))
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        result = await reel.ask(str(clip), "not really there", mode="visual")
    assert result.hits == []


async def test_speech_mode_picks_a_line(settings: Settings, clip: Path) -> None:
    def rule(name: str, question: Question, state: Any) -> dict[str, Any] | None:
        if "sheet" in state:
            return None
        if name == "said":
            return noul(0.95 if "caching" in str(state["transcript"]) else 0.02)
        if name == "line" and isinstance(question, Choice):
            return choice(dict.fromkeys(question.criteria, 0.0) | {"L01": 1.0})
        return None

    fake = FakeJudge(rule)
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        result = await reel.ask(str(clip), "caching", mode="speech")
    assert [h.at for h in result.hits] == [65.5]
    assert all("sheet" not in state for state, _, _ in fake.calls)


async def test_find_sends_the_reference(settings: Settings, clip: Path, tmp_path: Path) -> None:
    photo = tmp_path / "logo.png"
    Image.new("RGB", (120, 80), (200, 30, 30)).save(photo)
    fake = FakeJudge(tiles_rule("1:56-2:30", "T03", 0.9))
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        result = await reel.find(str(clip), photo, note="logo")
    assert result.mode == "visual"
    assert all("reference" in state for state, _, _ in fake.calls)
    assert "same logo as `reference`" in str(fake.calls[0][1]["shown"].instructions)
    [hit] = result.hits
    assert hit.at == 118.0


async def test_locate_widens_cut_off_edges(settings: Settings, clip: Path) -> None:
    def rule(name: str, question: Question, state: Any) -> dict[str, Any] | None:
        if name == "present":
            return noul(0.9)
        if isinstance(question, Box):
            return box(0.4, 0.4, 0.6, 0.6)
        if name == "right":
            return noul(0.8)
        if isinstance(question, Noul):
            return noul(0.1)
        return None

    fake = FakeJudge(rule)
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        found = await reel.locate(str(clip), 30, target="the gray square")
    assert found.found and found.widened_edges == ["right"]
    assert found.box is not None
    assert found.box.left == pytest.approx(0.35)
    assert found.box.right == pytest.approx(0.6 + 0.25 * 0.2 + 0.05)
    assert found.crop is not None and Path(found.crop).is_file()
    assert found.requests == 2


async def test_locate_reports_absence(settings: Settings, clip: Path) -> None:
    fake = FakeJudge(lambda name, q, s: noul(0.1) if name == "present" else None)
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        found = await reel.locate(str(clip), 30, target="a giraffe")
    assert not found.found and found.box is None and found.requests == 1


async def test_whole_video_questions(reel: Reel, fake: FakeJudge, clip: Path) -> None:
    await reel.index(str(clip))
    yes = await reel.whole(str(clip), "Is this a test pattern?")
    assert yes.kind == "noul" and isinstance(yes.answer, float)
    state = fake.calls[-1][0]
    assert len(state["storyboard"]) == 1 and len(state["storyboard_times"]) == 3
    assert len(state["timeline"]) == 3 and "transcript" in state

    picked = await reel.whole(str(clip), "What genre?", kind="choice", options=["tutorial", "test", "vlog"])
    assert picked.answer == "tutorial"

    rated = await reel.whole(str(clip), "How busy?", kind="score", options=["calm", "some", "busy"])
    assert rated.answer == 1.0 and rated.probabilities == {"calm": 0.0, "some": 1.0, "busy": 0.0}
    assert isinstance(fake.calls[-1][1]["answer"], Score)


async def test_whole_retries_with_long_context_on_overflow(reel: Reel, fake: FakeJudge, clip: Path) -> None:
    await reel.index(str(clip))
    fake.fail_next = TypeSafeBadRequestError(
        400, {"error": {"code": "max_tokens_exceeded"}}, httpx2.Headers(), "context too long"
    )
    answer = await reel.whole(str(clip), "Anything?")
    assert answer.long_context and answer.requests == 1
    assert fake.calls[-1][2] is True


async def test_recheck_reorders_the_tile_options(settings: Settings, clip: Path) -> None:
    fake = FakeJudge(tiles_rule("0:00-1:00", "T05", 0.5))
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        await reel.ask(str(clip), "something faint", mode="visual")
    first, again = [q for s, q, _ in fake.calls if s["window"] == "0:00-1:00"]
    order = list(first["tile"].criteria)
    shuffled_order = list(again["tile"].criteria)
    assert order[0] == "T01" and shuffled_order != order  # Jev leans toward the first option
    assert sorted(order) == sorted(shuffled_order)


async def test_both_mode_asks_screen_and_speech_separately(settings: Settings, clip: Path) -> None:
    def rule(name: str, question: Question, state: Any) -> dict[str, Any] | None:
        if name == "said" and "caching" in str(state.get("transcript")):
            return noul(0.9)
        if name == "line" and isinstance(question, Choice) and "caching" in str(state.get("transcript")):
            lines = state["transcript"]
            target = next(k for k, v in lines.items() if "caching" in v)
            return choice(dict.fromkeys(question.criteria, 0.0) | {target: 1.0})
        return None

    fake = FakeJudge(rule)
    async with Reel(settings, judge=fake) as reel:
        await reel.index(str(clip), timeline=False)
        result = await reel.ask(str(clip), "caching")
    assert result.mode == "both"
    with_speech = [q for s, q, _ in fake.calls if "transcript" in s]
    without_speech = [q for s, q, _ in fake.calls if "transcript" not in s]
    assert with_speech and all({"shown", "tile", "said", "line"} <= set(q) for q in with_speech)
    assert without_speech and all(set(q) == {"shown", "tile"} for q in without_speech)
    [hit] = [h for h in result.hits if h.anchor.startswith("L")]
    assert hit.at == 65.5 and hit.said == 0.9 and hit.shown is not None and hit.shown < 0.5
