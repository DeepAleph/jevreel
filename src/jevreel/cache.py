"""Reusing Jev's answers: the same model, images and questions get the same answer from disk.

Agents ask the same thing more than once, and every ask sends every sheet in range again. A cached
answer costs nothing. The key covers the model, every image's bytes and every question's wording,
so any change (a re-indexed sheet, a reworded question, a new model) is a new request.
"""

import hashlib
import json
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from typesafe_sdk import Image as JevImage
from typesafe_sdk import Question, SystemOneResponse, Usage

from jevreel.judge import Judge


def _image_digest(image: JevImage, memo: dict[int, str]) -> str:
    if id(image) not in memo:
        data = image.data
        raw = Path(data).read_bytes() if isinstance(data, PathLike) else bytes(memoryview(data))
        memo[id(image)] = hashlib.sha256(raw).hexdigest()
    return memo[id(image)]


def _canonical(value: Any, memo: dict[int, str]) -> Any:
    if isinstance(value, JevImage):
        return {"image": _image_digest(value, memo)}
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(), memo)
    if isinstance(value, Mapping):
        return {str(k): _canonical(v, memo) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v, memo) for v in value]
    return value


def cache_key(model: str, state: Any, questions: Mapping[str, Question], long_context: bool) -> str:
    memo: dict[int, str] = {}
    payload = {
        "model": model,
        "long_context": long_context,
        "state": _canonical(state, memo),
        # Choice option order is part of the question: Jev leans toward options that come first.
        "questions": [[name, _canonical(q, memo)] for name, q in questions.items()],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=False).encode()).hexdigest()


FREE = Usage(input_tokens=0, output_tokens=0)


def is_cached(result: SystemOneResponse) -> bool:
    """Cached answers carry zero usage; a real request always reports input tokens."""
    return not result.usage.input_tokens


class CachedJudge:
    """Wraps a judge with an on-disk answer cache under `folder`."""

    def __init__(self, inner: Judge, folder: Path, model: str) -> None:
        self.inner = inner
        self.folder = folder
        self.model = model

    @property
    def input_tokens(self) -> int:
        return self.inner.input_tokens

    @property
    def requests(self) -> int:
        return self.inner.requests

    def _path(self, key: str) -> Path:
        return self.folder / key[:2] / f"{key}.json"

    async def ask(
        self, state: Any, questions: Mapping[str, Question], *, long_context: bool = False
    ) -> SystemOneResponse:
        path = self._path(cache_key(self.model, state, questions, long_context))
        if path.is_file():
            stored = SystemOneResponse.model_validate_json(path.read_text(encoding="utf-8"))
            return stored.model_copy(update={"usage": FREE})
        result = await self.inner.ask(state, questions, long_context=long_context)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(), encoding="utf-8")
        return result

    async def aclose(self) -> None:
        await self.inner.aclose()
