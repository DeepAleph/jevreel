"""The CLI and the MCP server, wired to the same Reel."""

import json
from pathlib import Path

import pytest
from fastmcp import Client
from typer.testing import CliRunner

from jevreel import Reel, Settings, __version__, server
from jevreel.cli import app, parse_time
from tests.fakes import FakeJudge

runner = CliRunner()


def test_parse_time() -> None:
    assert parse_time("75") == 75
    assert parse_time("1:15") == 75
    assert parse_time("1:02:03") == 3723
    assert parse_time(None) is None


def test_version_and_help() -> None:
    assert runner.invoke(app, ["--version"]).output.strip() == __version__
    help_text = runner.invoke(app, ["--help"]).output
    for command in ("index", "ask", "find", "locate", "zoom", "transcript", "whole", "mcp"):
        assert command in help_text


@pytest.mark.ffmpeg
def test_cli_index_and_transcript(clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEVREEL_HOME", str(tmp_path / "home"))
    indexed = runner.invoke(app, ["index", str(clip), "--no-timeline"])
    assert indexed.exit_code == 0, indexed.output
    video_id = json.loads(indexed.stdout)["id"]
    spoken = runner.invoke(app, ["transcript", video_id, "--start", "1:00", "--end", "1:10"])
    assert json.loads(spoken.stdout)["segments"][0]["text"] == "Now we talk about caching."
    missing = runner.invoke(app, ["transcript", "nope-nothing"])
    assert missing.exit_code == 1 and "No indexed video" in missing.stderr


@pytest.mark.ffmpeg
async def test_mcp_tools(clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeJudge()
    monkeypatch.setattr(server, "_reel", Reel(Settings(home=tmp_path / "home"), judge=fake))
    async with Client(server.mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert names == {
            "index_video",
            "ask_video",
            "find_in_video",
            "locate_in_frame",
            "zoom_video",
            "video_transcript",
            "ask_whole_video",
            "list_videos",
        }
        indexed = await client.call_tool("index_video", {"source": str(clip)})
        assert indexed.structured_content["sheets"] == 3
        asked = await client.call_tool("ask_video", {"video": "clip", "query": "anything", "mode": "visual"})
        assert asked.structured_content["windows_checked"] == 3
        zoomed = await client.call_tool("zoom_video", {"video": "clip", "start": 5, "end": 6, "count": 2})
        assert [block.type for block in zoomed.content] == ["text", "image", "image"]
