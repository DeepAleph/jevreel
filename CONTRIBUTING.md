# Contributing

Thanks for helping. jevreel is small on purpose; changes that keep it that way are the easiest to merge.

## Set up

```bash
git clone https://github.com/DeepAleph/jevreel
cd jevreel
uv sync --all-extras --group dev
uv run pytest -q
```

You need FFmpeg on your PATH. The tests never call Jev, so no API key is needed for them.

## Making a change

1. Open an issue first for anything larger than a fix, so we can agree on the shape.
2. Keep the design rules in [AGENTS.md](AGENTS.md): no embeddings, one judgment per question, every
   image through `media.fit_for_jev`, Box behind a Noul.
3. Add or update a test in `tests/` using the scripted Jev in `tests/fakes.py`.
4. Run `uv run ruff format .`, `uv run ruff check .` and `uv run pytest -q`.
5. Update the README, `skills/jevreel/SKILL.md` and `CHANGELOG.md` when behaviour changes.

## Live checks

If your change affects what is sent to Jev, try it on a real video with your own key and say in the
pull request what you ran, how many requests it made, and what it found. Start with
`jevreel measure` so the cost is known before anything larger.

## Code of conduct

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
