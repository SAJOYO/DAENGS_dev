"""Fact-checkable controls for the judge; expected labels never reach its prompt."""

from pathlib import Path

from .judge_contract import AXES, Axis, Contract, Decision, JudgeInput, TurnKey, read_jsonl

DEFAULT_ANCHORS = (
    Path(__file__).resolve().parents[3] / "evals/place_conversation/judge/anchors.v1.jsonl"
)


class Anchor(Contract):
    id: str
    axis: Axis
    payload: dict
    expected: Decision

    def judge_input(self) -> JudgeInput:
        return JudgeInput(
            key=TurnKey(case_id=self.id, variant="anchor", repetition=1, turn=1),
            axis=self.axis,
            payload=self.payload,
        )


def load_anchors(path: Path) -> list[Anchor]:
    anchors = [Anchor.model_validate(row) for row in read_jsonl(path)]
    if len({a.id for a in anchors}) != len(anchors):
        raise ValueError("duplicate anchor IDs")
    for axis in AXES:
        if not {"pass", "fail"} <= {a.expected for a in anchors if a.axis == axis}:
            raise ValueError("each axis needs both positive and negative anchors")
    return anchors
