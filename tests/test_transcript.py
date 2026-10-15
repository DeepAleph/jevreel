from pathlib import Path

from jevreel.transcript import Segment, Transcript, find_sidecar, parse_captions

SRT = """1
00:00:01,000 --> 00:00:04,000
Hello &amp; welcome.

2
01:02:03,500 --> 01:02:05,000
<i>Second</i> line
"""

ROLLING_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000 align:start position:0%
so<00:00:00.500><c> today</c>

00:00:02.000 --> 00:00:02.010
so today

00:00:02.010 --> 00:00:04.000
so today
we look at caching

00:00:04.000 --> 00:00:06.000
we look at caching
and why it matters
"""


def test_srt_times_markup_and_entities() -> None:
    segments = parse_captions(SRT)
    assert segments == [
        Segment(start=1.0, end=4.0, text="Hello & welcome."),
        Segment(start=3723.5, end=3725.0, text="Second line"),
    ]


def test_rolling_auto_captions_keep_each_line_once() -> None:
    texts = [s.text for s in parse_captions(ROLLING_VTT)]
    assert texts == ["so today", "we look at caching", "and why it matters"]


def test_window_and_text() -> None:
    transcript = Transcript(
        source="test",
        segments=[Segment(start=0, end=5, text="a"), Segment(start=60, end=65, text="b")],
    )
    assert [s.text for s in transcript.window(50, 70)] == ["b"]
    assert transcript.text(50, 70) == "[1:00] b"
    assert transcript.text() == "[0:00] a\n[1:00] b"


def test_sidecar_lookup(tmp_path: Path) -> None:
    media = tmp_path / "talk [final].mp4"
    media.write_bytes(b"")
    assert find_sidecar(media) is None
    lang = tmp_path / "talk [final].en.vtt"
    lang.write_text("WEBVTT\n", encoding="utf-8")
    assert find_sidecar(media) == lang
    exact = tmp_path / "talk [final].srt"
    exact.write_text("", encoding="utf-8")
    assert find_sidecar(media) == exact
