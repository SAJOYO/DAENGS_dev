"""Research-only alternatives. They never change production contracts or handlers."""

import copy
import json
from dataclasses import dataclass

from daengs_place.place.conversation.contract import TurnPlan
from daengs_place.place.conversation.service import fingerprint
from daengs_place.place.tools.changes import apply_changes

from .provider import ObservedGemini

POLICY_HINT = """
추가 정책:
- question은 사용자 결정을 기다리는 질문이다. 질문이 필요하면 goal=clarify이며 changes는 비운다.
  확인을 묻는 문장과 실행 명령을 동시에 내지 않는다.
- 검색 중심 좌표는 이 도구로 바꿀 수 없다. 지역 이동 요청은 지도에서 검색 지역 변경을
  안내하는 clarify다. 지역명을 name_query에 넣지 않는다. 명시적으로 말한 상호명만 이름 검색한다.
- 여기/거기/선택한 장소의 속성을 묻는 질문은 explain이다. 검색 조건 변경 명령과 구별한다.
- 원문을 정정한다는 명확한 표현 없이 상반된 조건을 동시에 요구하면 clarify한다.
- 모든 분기 ID와 조건 ID는 서로 달라야 한다.
- pending_request는 아직 실행하지 않은 원문과 실제 확인 질문이다. 현재 질문의 동의가
  명확하면 제안했던 범위만 적용한다. 거절이면 실행하지 않고 조건을 보존한다.
"""


@dataclass(frozen=True)
class PendingProposal:
    """Research-only transaction: bind explicit consent to a validated proposal.

    The API must additionally bind owner/session/pending ID and reject stale revisions.
    This object accepts a structured decision, never guesses consent from free text.
    """

    question: str
    plan: TurnPlan
    base_fingerprint: str
    base_revision: int
    candidate_fingerprint: str

    @classmethod
    def capture(cls, raw_plan, state, revision):
        if not raw_plan.question.strip() or raw_plan.goal not in {"show", "pick_one", "edit_only"}:
            raise ValueError("requires a question and executable proposal")
        # Revalidate the whole candidate before offering it. Do not retain arbitrary raw patches.
        plan = TurnPlan.model_validate({**raw_plan.model_dump(exclude_unset=True), "question": ""})
        candidate = apply_changes(state.filters, plan.changes)
        return cls(
            raw_plan.question, plan, fingerprint(state.filters), revision, fingerprint(candidate)
        )

    def resolve(self, decision, state, revision):
        if revision != self.base_revision or fingerprint(state.filters) != self.base_fingerprint:
            raise ValueError("stale_pending_proposal")
        if decision == "reject":
            return None
        if decision != "accept":
            raise ValueError("a changed request must be planned separately")
        candidate = apply_changes(state.filters, self.plan.changes)
        if fingerprint(candidate) != self.candidate_fingerprint:
            raise ValueError("pending_candidate_changed")
        return self.plan


class PolicyGemini(ObservedGemini):
    """Ablatable prompt, deterministic question gate and one pending request per case."""

    def __init__(self, *args, pending_context=False, question_gate=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending_context = pending_context
        self.question_gate = question_gate
        self.pending = None
        self.policy_events = []

    async def _call(self, payload):
        if "tools" in payload:
            payload = copy.deepcopy(payload)
            payload["system_instruction"] += POLICY_HINT
            if self.pending_context and self.pending:
                context = json.loads(payload["input"])
                context["pending_request"] = self.pending
                payload["input"] = json.dumps(context, ensure_ascii=False)
        return await super()._call(payload)

    async def plan(self, request):
        if self.pending and self.pending["base_filter_fingerprint"] != fingerprint(
            request.previous.filters
        ):
            self.pending = None
        plan = await super().plan(request)
        if self.question_gate and plan.question.strip() and plan.goal != "clarify":
            self.policy_events.append(
                {"code": "question_blocks_execution", "original_plan": plan.model_dump(mode="json")}
            )
            plan = TurnPlan(goal="clarify", question=plan.question)
        if plan.goal == "clarify":
            original = self.pending["original_query"] if self.pending else request.query
            self.pending = {
                "original_query": original,
                "question": plan.question,
                "base_filter_fingerprint": fingerprint(request.previous.filters),
            }
        else:
            self.pending = None
        return plan


def compile_replacement_branches(branches, occupied_ids=()):
    """Diagnostic compiler for a COMPLETE OR replacement, not incremental ID repair.

    The caller must intentionally choose replacement semantics. Existing filter IDs must
    be reserved; assigning new IDs to incremental upserts could silently change meaning.
    """
    occupied = set(occupied_ids)

    def identifier(stem):
        value = stem
        index = 1
        while value in occupied:
            value = f"{stem}-{index}"
            index += 1
        occupied.add(value)
        return value

    compiled = []
    for index, branch in enumerate(branches):
        if set(branch) != {"all"}:
            raise ValueError("anonymous branch must contain only all")
        atoms = []
        for ordinal, atom in enumerate(branch["all"]):
            if set(atom) != {"capability", "op", "value"}:
                raise ValueError("anonymous atom must contain only capability/op/value")
            atoms.append({"id": identifier(f"expr-{index}-atom-{ordinal}"), **atom})
        compiled.append({"id": identifier(f"expr-branch-{index}"), "all": atoms})
    return compiled
