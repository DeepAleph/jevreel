"""Lenses: your own questions, asked of every sheet, kept as columns of the video's timeline.

A lens is a named set of typed questions ("is a worker without a helmet visible", "which vehicle is
most visible", "how crowded is it, 0-3"). Asked at index time they ride in the same request as the
default questions, so the sheet image, which is nearly all of the cost, is paid for once. Afterwards
the timeline can be filtered with `query` as often as you like, with no further requests.

Lens files are TOML or JSON:

    name = "site-safety"

    [[questions]]
    name = "helmet_off"
    type = "noul"
    ask = "Is a worker without a helmet visible?"

    [[questions]]
    name = "vehicle"
    type = "choice"
    ask = "Which vehicle is most visible?"
    options = ["car", "truck", "forklift", "none"]

    [[questions]]
    name = "crowd"
    type = "score"
    ask = "How crowded is the site?"
    levels = ["Empty", "A few people", "Busy", "Packed"]
"""

import asyncio
import json
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from typesafe_sdk import Choice, Noul, Question, Score, SystemOneResponse

from jevreel.judge import Judge
from jevreel.sheets import SheetInfo
from jevreel.states import Meter, numbered_lines, sheet_asset, sheet_state
from jevreel.store import Video

NAME = r"^[a-z][a-z0-9_]*$"
LENS_NAME = r"^[a-z][a-z0-9_-]*$"
WHERE = "the tiles of `sheet`"


class LensQuestion(BaseModel):
    name: str = Field(pattern=NAME)
    type: Literal["noul", "choice", "score"]
    ask: str = Field(min_length=3)
    options: list[str] | dict[str, str | None] | None = None
    """Choice options: a list of names, or names mapped to descriptions."""
    levels: list[str] | None = None
    """Score levels, lowest first, at most 10."""
    locate: bool = True
    """For yes/no questions, also ask which tile shows it, so matches come with a time."""

    @model_validator(mode="after")
    def _shape(self) -> "LensQuestion":
        if self.type == "choice" and not self.options:
            raise ValueError(f"Choice question {self.name!r} needs options.")
        if self.type == "score" and not (self.levels and 2 <= len(self.levels) <= 10):
            raise ValueError(f"Score question {self.name!r} needs 2 to 10 levels, lowest first.")
        return self

    def criteria(self) -> dict[str, str | None]:
        if isinstance(self.options, dict):
            return dict(self.options)
        return dict.fromkeys(self.options or [])


class Lens(BaseModel):
    name: str = Field(pattern=LENS_NAME)
    questions: list[LensQuestion] = Field(min_length=1)

    @field_validator("questions")
    @classmethod
    def _unique(cls, questions: list[LensQuestion]) -> list[LensQuestion]:
        names = [q.name for q in questions]
        if len(names) != len(set(names)):
            raise ValueError("Question names in a lens must be unique.")
        return questions

    @classmethod
    def load(cls, path: Path) -> "Lens":
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text) if path.suffix.lower() == ".toml" else json.loads(text)
        data.setdefault("name", path.stem)
        return cls.model_validate(data)


def _key(lens: Lens, question: LensQuestion, suffix: str = "") -> str:
    # Question IDs are never sent to the model, so they can carry the lens name.
    return f"{lens.name}:{question.name}{suffix}"


def lens_questions(lens: Lens, tile_ids: list[str]) -> dict[str, Question]:
    """The lens as Jev questions about one sheet, following TypeSafe's structured instructions."""
    questions: dict[str, Question] = {}
    for q in lens.questions:
        instructions = {"question": q.ask, "inspect": WHERE}
        if q.type == "noul":
            questions[_key(lens, q)] = Noul(instructions=instructions)
            if q.locate:
                criteria: dict[str, str | None] = dict.fromkeys(tile_ids)
                criteria["none"] = "No tile shows it."
                questions[_key(lens, q, "@tile")] = Choice(
                    instructions={
                        "question": f"Which tile shows this most clearly? {q.ask}",
                        "inspect": WHERE,
                    },
                    criteria=criteria,
                )
        elif q.type == "choice":
            questions[_key(lens, q)] = Choice(instructions=instructions, criteria=q.criteria())
        else:
            questions[_key(lens, q)] = Score(instructions=instructions, criteria=q.levels or [])
    return questions


def read_lens(lens: Lens, sheet: SheetInfo, answer: SystemOneResponse) -> dict[str, Any]:
    """One sheet's answers as timeline columns: `name` (and `name_at` for located yes/no questions)."""
    row: dict[str, Any] = {}
    for q in lens.questions:
        key = _key(lens, q)
        if q.type == "noul":
            row[q.name] = round(answer.nouls[key].noul, 3)
            tile = answer.choices.get(_key(lens, q, "@tile"))
            if tile is not None:
                row[f"{q.name}_at"] = sheet.time_of(tile.choice) if tile.choice != "none" else None
        elif q.type == "choice":
            row[q.name] = answer.choices[key].choice
        else:
            row[q.name] = round(answer.scores[key].score, 3)
    return row


class LensResult(BaseModel):
    video: str
    lens: str
    columns: list[str]
    windows: int
    requests: int
    cached: int
    """Requests answered from jevreel's cache, at no cost."""
    input_tokens: int
    estimated_usd: float


def lens_path(video: Video, name: str) -> Path:
    return video.root / "lenses" / f"{name}.json"


def save_lens(video: Video, lens: Lens, rows: list[dict[str, Any]]) -> None:
    path = lens_path(video, lens.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lens": lens.model_dump(), "rows": rows}, indent=1), encoding="utf-8")


def load_lens_rows(video: Video) -> dict[str, list[dict[str, Any]]]:
    """Every lens run on this video: lens name -> one row per sheet."""
    folder = video.root / "lenses"
    if not folder.is_dir():
        return {}
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))["rows"]
        for path in sorted(folder.glob("*.json"))
    }


async def run_lens(video: Video, judge: Judge, lens: Lens) -> LensResult:
    """Ask a lens of every sheet of an already indexed video: one request per sheet."""
    meter = Meter(judge)

    async def one(sheet: SheetInfo) -> dict[str, Any]:
        ids = [tile_id for tile_id, _ in sheet.tiles]
        lines = numbered_lines(video, sheet.start, sheet.end)
        state = sheet_state(video, sheet, sheet_asset(video, sheet), lines=lines)
        answer = await meter.ask(state, lens_questions(lens, ids))
        return {"sheet": sheet.index, **read_lens(lens, sheet, answer)}

    rows = await asyncio.gather(*(one(sheet) for sheet in video.manifest.sheets))
    save_lens(video, lens, list(rows))
    columns = [c for c in rows[0] if c != "sheet"] if rows else []
    return LensResult(
        video=video.id,
        lens=lens.name,
        columns=[f"{lens.name}.{c}" for c in columns],
        windows=len(rows),
        **meter.spent(),
    )
