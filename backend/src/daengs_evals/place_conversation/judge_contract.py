"""File contracts for offline facility judgments; importing needs no app or API key."""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Axis = Literal["intent_alignment", "scope_fit", "result_faithfulness"]
AXES: tuple[Axis, ...] = ("intent_alignment", "scope_fit", "result_faithfulness")
Decision = Literal["pass", "fail", "uncertain", "not_applicable"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Contract):
    path: str = Field(min_length=1, max_length=400)
    observation: str = Field(min_length=1, max_length=400)


class Verdict(Contract):
    evidence: list[Evidence] = Field(max_length=8)
    rationale: str = Field(min_length=1, max_length=800)
    status: Decision

    @model_validator(mode="after")
    def evidence_required(self) -> Self:
        if self.status in {"pass", "fail"} and not self.evidence:
            raise ValueError("pass/fail requires observed evidence")
        return self


class TurnKey(Contract):
    case_id: str
    variant: str = "production-baseline"
    repetition: int = Field(ge=1)
    turn: int = Field(ge=1)

    @classmethod
    def of(cls, row: dict) -> Self:
        return cls.model_validate({k: row[k] for k in cls.model_fields if k in row})

    def identity(self) -> tuple:
        return self.case_id, self.variant, self.repetition, self.turn


class JudgeInput(Contract):
    key: TurnKey
    axis: Axis
    payload: dict[str, Any]
    unavailable_reason: str | None = None


class Judgment(Contract):
    key: TurnKey
    axis: Axis
    input_sha256: str
    status: Literal["judged", "judge_error", "unmeasured", "budget_exhausted"]
    verdict: Verdict | None = None
    reason: str | None = None
    attempts: int = Field(ge=0)

    @model_validator(mode="after")
    def matches_status(self) -> Self:
        if (self.status == "judged") != (self.verdict is not None):
            raise ValueError("only a judged row has a verdict")
        return self


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    # Replace only our own derived metadata/report; raw observations are never written here.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(dumps(value) + "\n")
        output.flush()


def resolve_pointer(payload: Any, pointer: str) -> Any:
    """Require a real JSON Pointer into the exact payload shown to the judge."""
    if not pointer.startswith("/"):
        raise ValueError("evidence path must be a JSON Pointer")
    value = payload
    try:
        for part in pointer[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list) and (not part.isdigit() or str(int(part)) != part):
                raise ValueError("invalid array evidence index")
            value = value[int(part)] if isinstance(value, list) else value[part]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError("evidence points outside observed payload") from error
    return value


def validate_evidence(verdict: Verdict, payload: dict) -> None:
    for evidence in verdict.evidence:
        resolve_pointer(payload, evidence.path)
