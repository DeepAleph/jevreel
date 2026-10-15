---
name: jevreel
description: Search and understand videos without embeddings. Use when the user asks about the content of a video file or video URL - finding moments ("when does X happen"), finding a person or object from a photo, reading what is on screen at a moment, getting what is said, or one answer about the whole video. Works through the jevreel MCP tools or the `jevreel` CLI.
---

# jevreel: ask questions about videos

jevreel lays a video out on contact sheets at one frame per second and has Jev, TypeSafe's fast
decision model, screen every sheet in parallel. You (the main agent) then look at what it found.
Jev gives typed answers and probabilities, never descriptions: reading, describing and concluding
is your job.

## Setup check

- `TYPESAFE_API_KEY` must be set for anything that asks Jev (indexing with a timeline, ask, find,
  locate, whole). Zoom and transcripts work without it.
- `ffmpeg` and `ffprobe` must be on PATH.
- URLs need `jevreel[url]`; videos without captions need `jevreel[asr]` for a transcript.

## Workflow

1. **Index once.** MCP `index_video(source)` or `jevreel index <file-or-url>`. Note the returned `id`.
   Re-indexing an indexed video returns instantly from cache.
2. **Find moments.** `ask_video(video, query)` / `jevreel ask <id> "<query>"`.
   - Write the query as a plain description of what would be visible or said:
     "a bar chart on a slide", "someone opens a laptop", "the speaker mentions pricing".
   - One thing per query. Split "a red car and a dog" into two asks; Jev is most accurate on
     simple questions.
   - `mode`: `visual` (on screen only), `speech` (transcript only, cheapest), `both`, or `auto`.
   - Narrow with `start`/`end` when the user names a part of the video.
3. **Find from a photo.** `find_in_video(video, image_path, note="person")` /
   `jevreel find <id> photo.jpg --note person`.
4. **Look before you answer.** Hits are leads, not facts. `zoom_video(video, start, end)` /
   `jevreel zoom <id> 12:00 12:10` gives full-resolution frames; read slides, code and faces there.
   `locate_in_frame(video, at, target)` boxes and crops one thing in one frame for close reading.
5. **What is said.** `video_transcript(video, start, end)` / `jevreel transcript <id> --start 12:00 --end 13:00`.
6. **Whole-video questions.** `ask_whole_video(video, question, kind, options)` /
   `jevreel whole <id> "Is this a tutorial?"`. Use `kind=choice` with options, or `kind=score` with
   levels lowest first (at most 10).

## Reading results

- `probability` is Jev's yes-probability for the window; `strong` means at least 0.7. It is the
  larger of `shown` (on screen) and `said` (in the transcript), which are asked separately.
  `rechecked` means the sheet was borderline and asked twice with tiles and options reordered.
- `at` / `time` is the tile (or transcript line) Jev picked; `also` lists other likely moments in
  the same window. The pick can be a tile or two off: zoom a few seconds either side.
- Probabilities rank well but are not guaranteed calibrated. Zoom in before stating anything you
  have not seen, and say so when you have only a weak hit.
- No hits above the threshold is a real answer, but check `weak` first: windows Jev did not rule
  out. Events lasting about a second are the ones most often missed; for those, zoom into the weak
  windows, or ask again over a narrower `start`/`end`.
- Cite moments by their times (for example "at 12:04"), so the user can jump there.

## Costs

Jev bills input tokens only. Every ask sends each sheet in range once (one sheet per minute of
video), so narrowing with `start`/`end` makes asks cheaper and faster. Results report `requests`,
`input_tokens` and `estimated_usd`.
