"""LLM proposals are bounded edits, never a replacement execution state or authority."""

import re
from typing import Literal, Self

from pydantic import Field, StrictInt, model_validator

from daengs_place.place.filters.contract import (
    Atom,
    Branch,
    FilterState,
    Identifier,
    KindList,
    Preference,
    guard_filter_state,
)
from daengs_place.place.name_query import PlaceNameQuery
from daengs_place.place.planning.contract import PlanningModel


class Evidence(PlanningModel):
    quote: str = Field(min_length=1, max_length=1000)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    origin: Literal["explicit", "inferred"]

    def verify(self, query: str):
        if (
            self.start >= self.end
            or self.end > len(query)
            or query[self.start : self.end] != self.quote
        ):
            raise ValueError("invalid_evidence")


class Edit(PlanningModel):
    type: Literal[
        "upsert_all",
        "remove_all",
        "upsert_branch",
        "remove_branch",
        "upsert_preference",
        "remove_preference",
        "set_kinds",
        "set_name_query",
    ]
    evidence: Evidence
    target_id: Identifier | None = None
    atom: Atom | None = None
    branch: Branch | None = None
    preference: Preference | None = None
    kinds: KindList | None = None
    name_query: PlaceNameQuery | None = None

    @model_validator(mode="after")
    def exactly_one_payload(self) -> Self:
        expected = {
            "upsert_all": "atom",
            "upsert_branch": "branch",
            "upsert_preference": "preference",
            "set_kinds": "kinds",
            "set_name_query": "name_query",
        }.get(self.type, "target_id")
        present = {
            k
            for k in ("target_id", "atom", "branch", "preference", "kinds", "name_query")
            if getattr(self, k) is not None
        }
        if present != {expected}:
            raise ValueError("edit_payload_mismatch")
        return self


class Unresolved(PlanningModel):
    evidence: Evidence
    reason: Literal["unsupported", "ambiguous"]


class EditProposal(PlanningModel):
    edits: tuple[Edit, ...] = Field(max_length=12)
    unresolved: tuple[Unresolved, ...] = Field(max_length=8)


class EditIssue(PlanningModel):
    code: str
    message: str
    target_ids: tuple[str, ...] = ()


class CompiledEdit(PlanningModel):
    status: Literal["ready", "needs_resolution"]
    base_state: FilterState
    proposed_state: FilterState | None
    proposal: EditProposal
    requires_confirmation: bool = False
    issues: tuple[EditIssue, ...] = ()


def _clear_is_explicit(query: str, item) -> bool:
    """Independent narrow user-event recognizer; model 'explicit' cannot unlock existing facts.

    Complex corrections remain reviewable. Full-input matching avoids attributing quoted or negated
    removal text to the user's request. Scope is global only; branch changes always need review.
    """
    if isinstance(item, Branch):
        return False
    noun = {"operations.parking": "주차", "pet_access.exclusive": r"(?:반려동물\s*)?전용"}.get(
        item.capability
    )
    if noun is None:
        return False
    normalized = query.strip().rstrip(".!。")
    return (
        re.fullmatch(
            rf"(?:아까\s*|기존\s*)?{noun}\s*(?:(?:조건|필수|선호|우선)\s*)?(?:은|는|을|를)?\s*"
            r"(?:빼\s*(?:줘|주세요)|(?:해제|제거)(?:해\s*줘|해\s*주세요|해줘|해주세요)|상관\s*없어(?:요)?)",
            normalized,
        )
        is not None
    )


def compile_edits(base: FilterState, query: str, proposal: EditProposal) -> CompiledEdit:
    base = guard_filter_state(base)
    proposal = EditProposal.model_validate(proposal.model_dump(mode="json"))
    for evidence in [
        *(e.evidence for e in proposal.edits),
        *(u.evidence for u in proposal.unresolved),
    ]:
        evidence.verify(query)
    if proposal.unresolved:
        return CompiledEdit(
            status="needs_resolution",
            base_state=base,
            proposed_state=None,
            proposal=proposal,
            issues=tuple(
                EditIssue(
                    code=u.reason,
                    message=f"‘{u.evidence.quote}’ 조건을 현재 필터로 확정할 수 없어요.",
                )
                for u in proposal.unresolved
            ),
        )
    groups = {
        "all": list(base.hard.all),
        "branch": list(base.hard.any),
        "preference": list(base.preferences),
    }
    kinds, name = base.candidate_kinds, base.name_query
    protected, touched = [], set()
    for edit in proposal.edits:
        if edit.evidence.origin == "inferred" and edit.type != "upsert_preference":
            raise ValueError("inference_cannot_change_hard_filters")
        if edit.type in ("set_kinds", "set_name_query"):
            key = edit.type
            if key in touched:
                raise ValueError("duplicate_edit_target")
            touched.add(key)
            if edit.type == "set_kinds":
                # Candidate scope itself can be a manually selected constraint.
                if edit.kinds != kinds:
                    protected.append("candidate_kinds")
                kinds = edit.kinds
            else:
                if name and name != edit.name_query:
                    protected.append("name_query")
                name = edit.name_query
            continue
        action, group = edit.type.split("_", 1)
        item = {"all": edit.atom, "branch": edit.branch, "preference": edit.preference}[group]
        target = edit.target_id if action == "remove" else item.id
        if target in touched:
            raise ValueError("duplicate_edit_target")
        touched.add(target)
        previous = next((v for v in groups[group] if v.id == target), None)
        if action == "remove":
            if previous is None:
                raise ValueError("unknown_edit_target")
            has_scoped_condition = not isinstance(previous, Branch) and any(
                atom.capability == previous.capability
                for branch in base.hard.any
                for atom in branch.all
            )
            if has_scoped_condition or not _clear_is_explicit(query, previous):
                protected.append(target)
            groups[group] = [v for v in groups[group] if v.id != target]
        else:
            # Adding another OR alternative broadens an existing disjunction.
            if (previous is not None and previous != item) or (
                group == "branch" and previous is None and base.hard.any
            ):
                protected.append(target)
            if edit.evidence.origin == "inferred" and previous is not None and previous != item:
                raise ValueError("inference_cannot_replace_existing_preference")
            groups[group] = (
                [item if v.id == target else v for v in groups[group]]
                if previous
                else [*groups[group], item]
            )
    try:
        proposed = FilterState.model_validate(
            {
                **base.model_dump(mode="json"),
                "candidate_kinds": kinds,
                "name_query": name,
                "hard": {"all": groups["all"], "any": groups["branch"]},
                "preferences": groups["preference"],
            }
        )
    except ValueError:
        return CompiledEdit(
            status="needs_resolution",
            base_state=base,
            proposed_state=None,
            proposal=proposal,
            issues=(
                EditIssue(
                    code="invalid_combination",
                    message="기존 조건과 함께 만족할 수 없는 조합이에요. 조건 범위를 구체적으로 말해 주세요.",
                ),
            ),
        )
    return CompiledEdit(
        status="needs_resolution" if protected else "ready",
        base_state=base,
        proposed_state=proposed,
        proposal=proposal,
        requires_confirmation=bool(protected),
        issues=(
            EditIssue(
                code="protected_change",
                target_ids=tuple(protected),
                message="기존 조건을 바꾸는 범위를 확인해 주세요.",
            ),
        )
        if protected
        else (),
    )
