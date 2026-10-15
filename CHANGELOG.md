# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0]

### Added

- Contact sheets at 1 frame per second, with labelled tiles; tile timestamps kept in code.
- `index`: sheets, transcript (sidecar captions, URL captions via yt-dlp, or local faster-whisper),
  and a typed per-minute timeline from Jev.
- `ask`: parallel per-sheet search, visual and speech as separate questions, with borderline sheets
  re-asked with tiles moved and Choice options reordered; near misses reported as `weak`.
- `find`: search by reference photo.
- `locate`: Box on a full-resolution frame, gated by a Noul, with TypeSafe's edge check.
- `zoom`, `transcript`, `whole` (one typed answer about the entire video), `measure`.
- MCP server (FastMCP 4, background indexing through the tasks extension) and an Agent Skill.
