"""Filtering a video's timeline: the default answers from indexing plus every lens, with no requests.

    jevreel query <video> "people > 0.8 and setting == outdoors"
    jevreel query <video> "safety.helmet_off >= 0.7"

Conditions are `column op value`, joined with `and`. Operators: > >= < <= == !=. Lens columns are
`lens.question`; a bare question name works when only one lens has it. Quote a value that contains
"and": place == 'books and a desk'.
"""

import operator
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from jevreel.lenses import load_lens_rows
from jevreel.states import stamp
from jevreel.store import Video
from jevreel.timefmt import span

_CONDITION = re.compile(r"^\s*([A-Za-z_][\w.\-]*)\s*(>=|<=|==|!=|>|<|=)\s*(.+?)\s*$")
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "=": operator.eq,
    "!=": operator.ne,
}


class QueryResult(BaseModel):
    video: str
    where: str | None
    columns: list[str]
    rows: list[dict[str, Any]]


def timeline_rows(video: Video) -> list[dict[str, Any]]:
    """One row per sheet: window, default facets, and `lens.column` for every lens run."""
    rows: dict[int, dict[str, Any]] = {
        sheet.index: {"sheet": sheet.index, "window": span(sheet.start, sheet.end), "start": sheet.start}
        for sheet in video.manifest.sheets
    }
    for facets in video.timeline:
        row = rows[facets.sheet]
        row.update(facets.model_dump(exclude={"sheet", "start", "end"}))
        row["keyframe"] = stamp(facets.keyframe)
    for lens, lens_rows in load_lens_rows(video).items():
        for lens_row in lens_rows:
            row = rows[lens_row["sheet"]]
            for column, value in lens_row.items():
                if column == "sheet":
                    continue
                row[f"{lens}.{column}"] = (
                    stamp(value) if column.endswith("_at") and value is not None else value
                )
    return [rows[index] for index in sorted(rows)]


def _value(text: str) -> Any:
    text = text.strip().strip("'\"")
    try:
        return float(text)
    except ValueError:
        return text


def _resolve(column: str, columns: list[str]) -> str:
    if column in columns:
        return column
    matches = [c for c in columns if c.endswith(f".{column}")]
    if len(matches) == 1:
        return matches[0]
    known = ", ".join(columns)
    raise ValueError(f"Unknown or ambiguous column {column!r}. Columns: {known}")


def query(video: Video, where: str | None = None) -> QueryResult:
    rows = timeline_rows(video)
    columns = sorted({c for row in rows for c in row} - {"sheet", "start"})
    if where:
        for clause in split_conditions(where):
            match = _CONDITION.match(clause)
            if match is None:
                raise ValueError(
                    f"Can't read condition {clause!r}; use `column op value`, e.g. people > 0.8."
                )
            column, op, raw = _resolve(match[1], columns), _OPS[match[2]], _value(match[3])
            rows = [row for row in rows if _compare(row.get(column), op, raw)]
    return QueryResult(video=video.id, where=where, columns=columns, rows=rows)


def split_conditions(where: str) -> list[str]:
    """Split on `and`, except inside a quoted value (an even number of quotes must follow)."""
    return re.split(r"\s+and\s+(?=(?:[^'\"]*['\"][^'\"]*['\"])*[^'\"]*$)", where.strip(), flags=re.IGNORECASE)


def _compare(cell: Any, op: Callable[[Any, Any], bool], value: Any) -> bool:
    if cell is None:
        return False
    if isinstance(value, float) and isinstance(cell, int | float):
        return op(float(cell), value)
    return op(str(cell).casefold(), str(value).casefold())
