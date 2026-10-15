# jevreel

**Search and understand video without embeddings.**

jevreel lays a video out on labelled contact sheets, one frame per second and sixty frames to an
image, and has [Jev](https://docs.typesafe.ai), TypeSafe's fast decision model, judge every sheet in
parallel. The best windows are re-checked on full frames. Your agent (Claude Code, Codex, Cursor,
Claude Desktop, anything that speaks MCP) gets back moments, times and probabilities, then looks
closer itself.

A real run on 10 minutes of 1080p gameplay, summarized (the CLI prints JSON):

```text
$ jevreel ask gta "the game's pause menu is open"
hits      0:08.5 (p=0.99)   2:19.5 (p=1.00)   10:12.5 (p=1.00)    every appearance, each under a second long
spent     15 requests · 103k input tokens · $0.0043
again     0 requests · $0 (answered from cache)
```

[![CI](https://github.com/Madhav2609/jevreel/actions/workflows/ci.yml/badge.svg)](https://github.com/Madhav2609/jevreel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)

## Why no embeddings

- **Your question, not a fixed notion of "similar".** An embedding index decides at index time what
  counts as close. A decision model judges the question you ask now, and returns a probability your
  code can threshold, sort and gate on.
- **Every second, cheaply.** Jev charges for input tokens only, and output is free. A contact sheet
  is sent once and every question about it rides along in the same request, so jevreel screens the
  whole video at one frame per second instead of a handful of keyframes.
- **Questions are nearly free, so ask many.** Lenses add your own typed questions to every minute
  at almost no extra cost, then filter them as often as you like for nothing.
- **The expensive model only sees what passed.** Your agent gets the few moments that matter, as
  full-resolution frames, rather than a pile of thumbnails.

## How it works

```mermaid
flowchart LR
    V[video file or URL] --> F["ffmpeg: 1 frame/s"]
    F --> S["contact sheets<br/>60 labelled tiles, 1 minute each"]
    V --> T["captions, or local<br/>speech-to-text"]
    S --> J{{"screen: one Jev request<br/>per sheet, in parallel"}}
    T --> J
    J --> R["verify: top-ranked windows<br/>re-checked on full frames,<br/>every 0.5 s around the pick"]
    R --> H["hits: time, probability,<br/>frame, nearby speech"]
    H --> A["your agent: zoom, locate,<br/>read, cite 12:04"]
```

1. **Index.** Sample the video at 1 fps and tile the frames onto 1568 px contact sheets, each tile
   labelled `T01`, `T02`, and so on. Fetch captions, or transcribe locally. Ask every sheet a fixed
   set of questions once (people, on-screen text, cuts, setting, activity, a representative frame)
   plus any **lenses** you add, which gives a typed timeline.
2. **Screen.** One request per sheet: a yes/no (Noul) for "is it in this minute" plus a Choice over
   tile IDs for "which second". Screen and speech are separate questions combined in code.
3. **Verify.** The top-ranked windows (by rank, not threshold) are re-checked with one yes/no per full
   frame, every half second around the pick. Screening ranks a one-second event correctly even when
   it can't confirm it; verification confirms it, or throws out a false lead.
4. **Look closer.** `zoom` re-renders full-resolution frames; `locate` puts a Box around a target in
   one frame and crops it; `whole` answers one question about the entire video from a storyboard,
   the timeline and the transcript.

Answers are cached: the same images and questions to the same model never cost twice.

This follows TypeSafe's own patterns. Their line-search cookbook pairs a Choice over labelled units
with a Noul for "is it there at all". Their images guide searches a large image by Choice over tiles
and looks closer only where needed. Their Box guide gates every box behind a Noul and checks its edges.

## Quickstart

You need Python 3.11+, [FFmpeg](https://ffmpeg.org/download.html) on your PATH, and a
[TypeSafe API key](https://console.typesafe.ai).

```bash
uv tool install "jevreel[all] @ git+https://github.com/Madhav2609/jevreel"
export TYPESAFE_API_KEY=...

jevreel index talk.mp4                       # prints the video id
jevreel ask talk "a slide with a bar chart"  # id, id prefix, path or title all work
jevreel zoom talk 12:00 12:10                # full-resolution frames to look at
```

`[all]` adds URL support (yt-dlp) and local speech-to-text (faster-whisper). Without them, jevreel
indexes local files and uses `.srt` or `.vtt` captions saved next to the video.

## Lenses: your own questions on every minute

A lens is a set of typed questions asked of every sheet and kept as timeline columns. Questions can
be `noul` (yes/no, plus the second it happens), `choice` (one of your options) or `score` (levels,
lowest first).

```toml
# examples/lenses/driving.toml
name = "driving"

[[questions]]
name = "on_bridge"
type = "noul"
ask = "Is the car driving on a bridge?"

[[questions]]
name = "weather"
type = "choice"
ask = "What is the weather mostly like?"
options = ["clear", "cloudy", "rain", "snow", "fog"]

[[questions]]
name = "traffic"
type = "score"
ask = "How much other traffic is on the road?"
levels = ["None", "Light", "Moderate", "Heavy"]
```

```bash
jevreel index drive.mp4 --lens driving.toml     # rides in the indexing requests: almost free
jevreel lens drive driving.toml                 # or run it later: one request per minute of video
jevreel query drive "on_bridge > 0.7"           # filter as often as you like: no requests
jevreel query drive "weather == snow and traffic >= 2"
```

## Use it from your agent

The MCP server exposes ten tools. The skill teaches agents the workflow: ask, look before claiming
anything, cite times.

**Claude Code**

```bash
claude mcp add jevreel -e TYPESAFE_API_KEY=your-key -- uvx --from "jevreel[all] @ git+https://github.com/Madhav2609/jevreel" jevreel mcp
```

**Codex**

```bash
codex mcp add jevreel --env TYPESAFE_API_KEY=your-key -- uvx --from "jevreel[all] @ git+https://github.com/Madhav2609/jevreel" jevreel mcp
```

**Claude Desktop, Cursor and other `mcpServers` clients**

```json
{
  "mcpServers": {
    "jevreel": {
      "command": "uvx",
      "args": ["--from", "jevreel[all] @ git+https://github.com/Madhav2609/jevreel", "jevreel", "mcp"],
      "env": { "TYPESAFE_API_KEY": "your-key" }
    }
  }
}
```

VS Code uses the same entry under `"servers"` with `"type": "stdio"`.

**The skill** (Claude Code, Codex and other agents that read Agent Skills):

```bash
npx skills add Madhav2609/jevreel
```

Agents without MCP can use the skill with the CLI alone; every command prints JSON.

## Tools

| MCP tool | CLI | What it does |
| - | - | - |
| `index_video` | `jevreel index` | Sheets, transcript, typed timeline and any lenses. Runs as an MCP background task; repeat calls return the index and only run new lenses. |
| `ask_video` | `jevreel ask` | Moments matching a description: screened, verified on full frames, with times, probabilities, nearby speech and the frames themselves. `mode`: visual, speech, both. |
| `find_in_video` | `jevreel find` | Every moment the subject of a reference photo appears. |
| `locate_in_frame` | `jevreel locate` | Box and crop a target in one full-resolution frame. |
| `zoom_video` | `jevreel zoom` | Evenly spaced full-resolution frames between two times. |
| `video_transcript` | `jevreel transcript` | What is said between two times. |
| `ask_whole_video` | `jevreel whole` | One typed answer (yes/no, choice, or score) about the entire video. |
| `run_lens` | `jevreel lens` | Ask your own typed questions of every minute of an indexed video. |
| `query_timeline` | `jevreel query` | Filter the timeline (default answers and lenses). No requests. |
| `list_videos` | `jevreel list` | Videos indexed on this machine. |

`jevreel measure <video>` reports what one contact sheet costs at several grid sizes on your account.

## Python

```python
import asyncio

from jevreel import Reel


async def main() -> None:
    async with Reel() as reel:
        video = await reel.index("talk.mp4")
        result = await reel.ask(video.id, "a slide with a bar chart")
        for hit in result.hits:
            print(hit.time, hit.probability, hit.frame)
        print(reel.query(video.id, "text_on_screen > 0.8").rows)


asyncio.run(main())
```

## Configuration

| Variable | Default | Meaning |
| - | - | - |
| `TYPESAFE_API_KEY` | (required for Jev) | Your TypeSafe key. Indexing without a timeline, zoom, transcripts and queries work without it. |
| `JEVREEL_HOME` | your user cache folder | Where indexed videos, sheets, frames and the answer cache are kept. `jevreel home` prints it. |
| `JEVREEL_MODEL` | `jev-1.14.0` | Pinned on purpose: Box and image tiles need 1.14, and aliases move. |
| `JEVREEL_FPS` | `1` | Frames per second sampled onto sheets (up to 5). |
| `JEVREEL_TILES_PER_SHEET` | `60` | Frames per sheet. Fewer means larger tiles and more requests. |
| `JEVREEL_VERIFY_TOP` | `6` | How many top-ranked windows each ask verifies on full frames; `0` turns verification off. |
| `JEVREEL_CACHE` | `1` | Set to `0` to always ask Jev again. |
| `JEVREEL_CONCURRENCY` | `16` | Parallel Jev requests. Rate limits are per TypeSafe account. |
| `JEVREEL_ASR_MODEL` | `small` | faster-whisper model for videos without captions. |
| `JEVREEL_CAPTION_LANGUAGES` | `en,en-orig,en.*` | Caption languages to fetch for URLs, in order. |

## What it costs

Measured on 2026-10-15 with `jev-1.14.0` at $0.042 per million input tokens, on a 10-minute 1080p video:

| | Requests | Input tokens | Cost |
| - | - | - | - |
| One full contact sheet (any grid from 4×7 to 8×14) | 1 | about 6,000 | $0.00025 |
| Indexing (11 sheets, 8 default questions each, 14 s wall clock) | 11 | 78,000 | $0.0033 |
| A lens of 5 questions, run afterwards | 11 | 84,000 | $0.0035 |
| One `ask`, screened and verified | 13–15 | 97,000–115,000 | $0.004–0.005 |
| The same `ask` again | 0 | 0 | $0 (cache) |
| `locate` (Box plus edge check) | 2 | 5,000 | $0.0002 |
| `whole` (one storyboard request) | 1 | 7,600 | $0.0003 |

One question across an hour of video comes to about $0.02–0.03. Asked at index time, lens questions
ride along with the default ones, which is where the "nearly free" comes from: an 8-question request
cost about the same as a 2-question one. TypeSafe doesn't document image token counts yet. These
figures are what the API reported. Check yours with `jevreel measure`.

## Measured on a real video

A 10-minute 1080p gameplay capture, no speech, every result checked against the frames by hand:

| Query | Result |
| - | - |
| "the game's pause menu is open" | All three appearances, each under a second: 0:08.5, 2:19.5, 10:12.5 (p 0.99–1.00). Screening alone scored them 0.36, 0.50, 0.95. Before verification, two of the three were missed. |
| "the full-screen city map is shown" | 0:10 and 2:21, both map screens. No false hits. |
| "the car drives across a big steel truss bridge" | 2:07 and 5:05.5, both real bridge crossings. |
| Lens `driving`: `query "on_bridge > 0.7"` | Exactly the two bridge minutes, at 2:14 and 5:02. |
| Lens `driving`: `query "menu_open > 0.7"` | Exactly the three menu minutes. |
| `locate` "the location name caption" at 5:02 | Boxed and cropped; the crop reads "Fathers Bridge". |
| `whole` "What is this video mostly?" | "a driving sequence in a video game", 0.98. |

## Limits

- **Brief events need screening to rank their window.** Verification looks every half second, but
  only in the top-ranked windows (6 by default). An event that screening ranks below that is not
  checked. Raise `JEVREEL_VERIFY_TOP`, or lower `JEVREEL_TILES_PER_SHEET` for larger tiles.
- **No sound beyond speech.** Audio reaches Jev only as transcript text.
- **Jev's image support is experimental.** TypeSafe calls images and Box "dreamer-ready", not
  production-ready, and says limits and accuracy can change between releases.
- **Jev's real image minimum is 280 px per side.** The docs say 256, but the API rejects anything
  smaller than 280 (measured 2026-10-15). jevreel pads every image itself.
- **Thresholds need tuning on your footage.** 0.5 to report and 0.7 for "strong" follow TypeSafe's
  examples. They held up on the test video, which is one video.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Coding agents should read
[AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE). jevreel is an independent project, not affiliated with TypeSafe AI. Jev is
TypeSafe's model and is used through its public API under TypeSafe's terms.
