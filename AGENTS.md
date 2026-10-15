# AGENTS.md

jevreel searches videos without embeddings: frames are tiled onto labelled contact sheets and Jev
(TypeSafe's decision model) answers typed questions about them. The README explains the design;
this file is for working on the code.

## Commands

```bash
uv sync --all-extras --group dev   # install everything
uv run pytest -q                   # tests; needs ffmpeg + ffprobe on PATH, never calls Jev
uv run ruff format . && uv run ruff check .
uv run jevreel --help              # the CLI
uv run jevreel mcp                 # the MCP server on stdio
```

## Layout

- `src/jevreel/judge.py` is the only module that talks to Jev; `cache.py` wraps it with the on-disk answer cache.
  Everything else builds state and questions.
- `sheets.py` (contact sheets, tile IDs), `media.py` (ffmpeg, Jev's image limits), `transcript.py`, `sources.py`.
- `states.py` builds what Jev sees for a sheet. Keep it lean: unrelated state costs accuracy.
- One module per operation: `indexing.py`, `search.py` (ask and find: screen, then verify), `lenses.py`,
  `query.py` (timeline filters, no requests), `locate.py`, `frames.py` (zoom), `whole.py`.
- `reel.py` is the Python API; `cli.py` and `server.py` are thin wrappers over it.
- `skills/jevreel/SKILL.md` teaches agents to use the tools; keep it in step with tool behaviour.
- `tests/fakes.py` scripts Jev's answers. New operations get a test there, not a live call.

## Rules

- No embeddings or vector search. That is the point of the project.
- Read TypeSafe's docs raw (`https://docs.typesafe.ai/<page>.md`, index at `/llms.txt`), not through a summarizer.
- One judgment per question; counting and arithmetic stay in code. Reorder Choice options when re-asking: Jev leans toward the first option.
- Every image sent to Jev goes through `media.fit_for_jev`. The API rejects sides under 280 px (the docs say 256) and never resizes.
- A timestamp means one frame everywhere: tiles (`fps` with `round=up`) and grabs (`-ss t`) must agree. `test_media.py` checks it.
- Box answers are only trusted behind a Noul in the same request; Box has no "not found".
- The model stays pinned (`config.DEFAULT_MODEL`); don't switch to an alias.
- Tests must not need an API key or the network. Live checks are manual: `jevreel measure` first.
- Never commit `.env`, API keys, videos, or anything under `.jevreel/`.
