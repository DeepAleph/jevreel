"""A stand-in for Jev that answers from rules, so tests run offline and deterministically."""

import json
from collections.abc import Callable, Mapping
from typing import Any

from typesafe_sdk import Box, Choice, Noul, Question, Score, SystemOneResponse

Rule = Callable[[str, Question, Mapping[str, Any]], dict[str, Any] | None]
"""(question name, question, state) -> raw answer, or None for the default answer."""


def noul(p: float) -> dict[str, Any]:
    return {"type": "noul", "noul": p}


def choice(options: Mapping[str, float]) -> dict[str, Any]:
    best = max(options, key=lambda k: options[k])
    return {"type": "choice", "choice": best, "confidence": options[best], "probabilities": dict(options)}


def box(left: float, top: float, right: float, bottom: float, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "type": "box",
        "box": {"left": left, "top": top, "right": right, "bottom": bottom},
        "confidence": confidence,
    }


def default_answer(question: Question) -> dict[str, Any]:
    if isinstance(question, Noul):
        return noul(0.05)
    if isinstance(question, Choice):
        labels = list(question.criteria)
        if "none" in labels:
            return choice({label: (0.9 if label == "none" else 0.1 / (len(labels) - 1)) for label in labels})
        return choice(
            {label: (0.9 if i == 0 else 0.1 / max(1, len(labels) - 1)) for i, label in enumerate(labels)}
        )
    if isinstance(question, Score):
        levels = len(question.criteria)
        return {
            "type": "score",
            "score": 1.0,
            "confidence": 0.8,
            "legend": {str(i): str(c) for i, c in enumerate(question.criteria)},
            "probabilities": {str(i): (1.0 if i == 1 else 0.0) for i in range(levels)},
        }
    if isinstance(question, Box):
        return box(0.25, 0.25, 0.75, 0.75)
    raise TypeError(f"Unexpected question {question!r}")


class FakeJudge:
    def __init__(self, rule: Rule | None = None, tokens_per_request: int = 1000) -> None:
        self.rule = rule
        self.tokens_per_request = tokens_per_request
        self.calls: list[tuple[Mapping[str, Any], Mapping[str, Question], bool]] = []
        self.input_tokens = 0
        self.requests = 0
        self.fail_next: Exception | None = None

    async def ask(
        self, state: Any, questions: Mapping[str, Question], *, long_context: bool = False
    ) -> SystemOneResponse:
        self.calls.append((state, questions, long_context))
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        answers = {}
        for name, question in questions.items():
            raw = self.rule(name, question, state) if self.rule else None
            answers[name] = raw if raw is not None else default_answer(question)
        self.requests += 1
        self.input_tokens += self.tokens_per_request
        body = {
            "model": "fake-jev",
            "usage": {"input_tokens": self.tokens_per_request, "output_tokens": 0},
            "answers": answers,
        }
        return SystemOneResponse.model_validate_json(json.dumps(body))

    async def aclose(self) -> None:
        pass
