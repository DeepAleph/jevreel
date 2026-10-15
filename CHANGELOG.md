# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Demo clips in `docs/demo/clips`: five recorded Claude Code sessions using jevreel on
  *Tears of Steel*.
- Videos can be named by their title's words or a unique title prefix: "tears of steel" or "tears"
  finds `tears_of_steel.mov`.
- `query` values that contain "and" can be quoted: `place == 'books and a desk'`.

### Fixed

- A missing video or reference photo now says which path was tried and where relative paths are
  resolved from. On Windows the bare OS error read like a missing program, and an agent gave up on it.
- The answer cache treated requests that differed only in the order of a Choice's options as the
  same request. Order is part of what Jev reads, so it now counts.

## [0.1.0]

### Added

- Contact sheets at 1 frame per second, with labelled tiles; tile timestamps kept in code.
- `index`: sheets, transcript (sidecar captions, URL captions via yt-dlp, or local faster-whisper),
  and a typed per-minute timeline from Jev.
- `ask`: parallel per-sheet screening, visual and speech as separate questions, then verification of
  the top-ranked windows with one yes/no per full frame every half second around the pick. Near
  misses, and leads verification overturned, are reported as `weak`.
- Lenses: your own typed questions (yes/no, choice, score) asked of every sheet, either inside the
  indexing requests or as a later pass, and kept as timeline columns.
- `query`: filter the timeline (default answers and lenses) with no requests.
- An on-disk answer cache: the same model, images and questions are never paid for twice.
- `find`: search by reference photo, verified the same way.
- `locate`: Box on a full-resolution frame, gated by a Noul, with TypeSafe's edge check.
- `zoom`, `transcript`, `whole` (one typed answer about the entire video), `measure`.
- MCP server (FastMCP 4, background indexing through the tasks extension) with ten tools, and an
  Agent Skill.

### Fixed during development

- Sheet tiles and full-frame grabs disagreed by about half a second: ffmpeg's default `fps` rounding
  put the frame at n + 0.47 s on the tile labelled n. Sampling now uses `round=up`, and a test checks
  tiles and grabs against a filter-free decode.
