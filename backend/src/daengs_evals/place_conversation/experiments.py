"""Research-only alternatives. They never change production contracts or handlers."""

from dataclasses import dataclass

from daengs_place.place.conversation.contract import TurnPlan
from daengs_place.place.conversation.service import fingerprint
from daengs_place.place.tools.changes import apply_changes


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
