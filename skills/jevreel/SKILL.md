---
name: jevreel
description: Search and understand videos without embeddings. Use when the user asks about the content of a video file or video URL - finding moments ("when does X happen"), finding a person or object from a photo, reading what is on screen at a moment, getting what is said, tracking the same things across a whole video, or one answer about the whole video. Works through the jevreel MCP tools or the `jevreel` CLI.
---

# jevreel: ask questions about videos

jevreel lays a video out on contact sheets at one frame per second and has Jev, TypeSafe's fast
decision model, screen every sheet in parallel, then re-check the best windows on full frames. You
(the main agent) then look at what it found. Jev gives typed answers and probabilities, never
descriptions: reading, describing and concluding is your job.

## Setup check

- `TYPESAFE_API_KEY` must be set for anything that asks Jev (indexing with a timeline, ask, find,
  locate, whole, lenses). Zoom, transcripts and timeline queries work without it.
- `ffmpeg` and `ffprobe` must be on PATH.
- URLs need `jevreel[url]`; videos without captions need `jevreel[asr]` for a transcript.

## Workflow

1. **Index once.** MCP `index_video(source)` or `jevreel index <file-or-url>`. Note the returned `id`.
   Re-indexing an indexed video returns at once.
2. **Find moments.** `ask_video(video, query)` / `jevreel ask <id> "<query>"`.
   - Write the query as a plain description of what would be visible or said:
     "a bar chart on a slide", "someone opens a laptop", "the speaker mentions pricing".
   - One thing per query. Split "a red car and a dog" into two asks; Jev is most accurate on
     simple questions.
   - `mode`: `visual` (on screen only), `speech` (transcript only, cheapest), `both`, or `auto`.
   - Narrow with `start`/`end` when the user names a part of the video.
3. **Find from a photo.** `find_in_video(video, image_path, note="person")` /
   `jevreel find <id> photo.jpg --note person`.
4. **Look before you answer.** Hits are leads, not facts. Verified hits come with `frame`, a
   full-resolution image of the moment: look at it. `zoom_video(video, start, end)` /
   `jevreel zoom <id> 12:00 12:10` gives more frames. `locate_in_frame(video, at, target)` boxes and
   crops one thing in one frame for close reading.
5. **What is said.** `video_transcript(video, start, end)` / `jevreel transcript <id> --start 12:00 --end 13:00`.
6. **Whole-video questions.** `ask_whole_video(video, question, kind, options)` /
   `jevreel whole <id> "Is this a tutorial?"`. Use `kind=choice` with options, or `kind=score` with
   levels lowest first (at most 10).

## Lenses: when the same questions matter across the whole video

When the user cares about several things throughout a video (who is on screen, safety rules,
brands, weather, whether a slide is up), don't ask each one separately. Run a lens: every minute
answers all of its questions in one request. Then filter the answers as often as needed, for free.

- MCP `run_lens(video, lens)` with
  `{"name": "site", "questions": [{"name": "helmet_off", "type": "noul", "ask": "Is a worker without a helmet visible?"}, {"name": "vehicle", "type": "choice", "ask": "Which vehicle is most visible?", "options": ["car", "truck", "none"]}, {"name": "crowd", "type": "score", "ask": "How crowded is it?", "levels": ["Empty", "Some", "Busy"]}]}`.
  CLI: `jevreel lens <id> lens.toml`. To include a lens while indexing, pass it to `index_video`
  (or `jevreel index --lens`); it then costs almost nothing extra.
- `query_timeline(video, "helmet_off > 0.7")` / `jevreel query <id> "helmet_off > 0.7 and vehicle == truck"`.
  Conditions are `column op value` joined by `and`; omit `where` to see every minute and its columns.
  Yes/no questions also give `<name>_at`, the second it is most visible.
- The default timeline columns are there too: `people`, `talking_to_camera`, `screen_or_slides`,
  `text_on_screen`, `cut`, `setting`, `activity` (0-3), `keyframe`.

## Reading results

- `probability` is Jev's yes-probability; `strong` means at least 0.7. For `verified` hits it comes
  from the full frames, `at` / `time` is the frame that showed it best, and `screened` is the
  contact-sheet probability before. Unverified hits come from the sheet alone.
- `shown` and `said` are the screen and transcript questions, asked separately.
- `also` lists other likely moments in the same window.
- Probabilities rank well but are not guaranteed calibrated. Look before stating anything you have
  not seen, and say so when you have only a weak hit.
- No hits above the threshold is a real answer, but check `weak` first: windows Jev did not rule
  out, including ones verification overturned. Zoom into them before saying "not found".
- Cite moments by their times (for example "at 12:04"), so the user can jump there.

## Costs

Jev bills input tokens only, and repeated identical requests are answered from jevreel's cache for
free (`cached` in results). Every ask sends each sheet in range once (one sheet per minute of
video), plus one request per verified window, so narrowing with `start`/`end` makes asks cheaper and
faster. Results report `requests`, `cached`, `input_tokens` and `estimated_usd`.
