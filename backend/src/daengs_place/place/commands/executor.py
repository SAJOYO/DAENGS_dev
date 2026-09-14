"""Deterministic commands. No model interpretation, answer rendering or session writes."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from daengs_place.place.commands.contract import (
    ARGUMENTS,
    CommandResult,
    FacilityState,
    NamedReference,
    Proposal,
)
from daengs_place.place.commands.view import references, state_changes
from daengs_place.place.conversation.compiler import compile_changes, fingerprint
from daengs_place.place.filters.service import search_filtered_places


def changed(state, **values):
    return FacilityState.model_validate(
        {**state.model_dump(), **values, "revision": state.revision + 1}
    )


def result(status, before, after=None, *, code="", data=None):
    after = after or before
    return CommandResult(
        status=status, state=after, changes=state_changes(before, after), code=code, data=data or {}
    )


def unique_keys(keys):
    return tuple({(k.source, k.ref): k for k in keys}.values())


class FacilityCommands:
    def __init__(self, searcher=search_filtered_places, *, now=None):
        self.searcher = searcher
        self.now = now or (lambda: datetime.now(UTC))

    async def execute(self, db, state, name, arguments, *, expected_revision):
        if expected_revision != state.revision:
            return result("conflict", state, code="view_changed")
        if name not in ARGUMENTS:
            return result("unsupported", state, code="unknown_tool")
        args = ARGUMENTS[name].model_validate(arguments)
        if name == "search_places":
            candidate = compile_changes(state.filters, args.semantic())
            if args.unavailable:
                return self._propose(state, candidate, args.unavailable, args.apply_to)
            if args.apply_to == "filters_only":
                if candidate == state.filters and state.proposal is None:
                    return result("unchanged", state)
                return result("applied", state, changed(state, filters=candidate, proposal=None))
            return await self._search(db, state, candidate)
        if name == "next_places":
            if not state.result or fingerprint(state.result.applied_state) != fingerprint(
                state.filters
            ):
                return result("conflict", state, code="results_do_not_match_filters")
            return await self._search(db, state, state.filters, next_page=True)
        if name == "propose_search_change":
            return self._propose(state, compile_changes(state.filters, args.semantic()))
        if name == "resolve_search_proposal":
            proposal = state.proposal
            if (
                not proposal
                or proposal.revision != state.revision
                or proposal.expires_at <= self.now()
            ):
                return result("conflict", state, code="proposal_expired")
            if not args.accept:
                return result(
                    "applied", state, changed(state, proposal=None), code="proposal_cancelled"
                )
            if proposal.apply_to == "filters_only":
                return result(
                    "applied", state, changed(state, filters=proposal.candidate, proposal=None)
                )
            return await self._search(db, state, proposal.candidate)
        refs = references(state)
        requested = [args.place_ref] if name == "select_place" else list(args.place_refs)
        if len(requested) != len(set(requested)):
            return result("failed", state, code="duplicate_reference")
        if name == "set_place_excluded" and not args.excluded:
            removed = [p for p in state.excluded if p.ref in requested]
            if len(removed) != len(requested):
                return result("conflict", state, code="unknown_excluded_reference")
            return await self._search(
                db,
                state,
                state.filters,
                excluded=tuple(p for p in state.excluded if p.ref not in requested),
            )
        if any(ref not in refs for ref in requested):
            return result("conflict", state, code="stale_place_reference")
        if name == "get_place_details":
            return result(
                "unchanged",
                state,
                data={
                    "places": [self._details(ref, refs[ref], args.attributes) for ref in requested]
                },
            )
        if name == "select_place":
            key = refs[args.place_ref].key
            return (
                result("unchanged", state)
                if key == state.selected
                else result("applied", state, changed(state, selected=key, proposal=None))
            )
        targets = tuple(
            NamedReference(ref=ref, key=refs[ref].key, name=refs[ref].name) for ref in requested
        )
        if name == "mark_places_known":
            existing = {p.key for p in state.known}
            added = tuple(p for p in targets if p.key not in existing)
            if len(state.known) + len(added) > 120:
                return result("failed", state, code="known_limit")
            return (
                result(
                    "applied", state, changed(state, known=(*state.known, *added), proposal=None)
                )
                if added
                else result("unchanged", state)
            )
        if name == "set_place_excluded":
            existing = {p.key for p in state.excluded}
            excluded = (*state.excluded, *(p for p in targets if p.key not in existing))
            if len(excluded) > 120:
                return result("failed", state, code="exclusion_limit")
            return await self._search(db, state, state.filters, excluded=excluded)
        return result("unsupported", state, code="unknown_tool")

    def _propose(self, state, candidate, unavailable=(), apply_to="results"):
        proposal = Proposal(
            id=uuid4(),
            revision=state.revision + 1,
            expires_at=self.now() + timedelta(minutes=5),
            candidate=candidate,
            unavailable=unavailable,
            apply_to=apply_to,
        )
        return result(
            "needs_confirmation",
            state,
            changed(state, proposal=proposal),
            code="awaiting_user_confirmation",
            data={"applied": False, "requires_user_reply": True},
        )

    async def _search(self, db, state, candidate, *, next_page=False, excluded=None):
        excluded = state.excluded if excluded is None else tuple(excluded)
        same_filters = fingerprint(candidate) == fingerprint(state.filters)
        presented = state.presented if same_filters else ()
        omitted = unique_keys((*(p.key for p in excluded), *(presented if next_page else ())))
        if (
            len(presented) + len(candidate.candidate_kinds) * candidate.result_policy.limit_per_kind
            > 1200
        ):
            return result("failed", state, code="exploration_limit")
        try:
            found = await self.searcher(db, candidate, omitted=omitted)
        except (SQLAlchemyError, TimeoutError):
            return result("failed", state, code="search_failed")
        if found.applied_state != candidate:
            raise RuntimeError("search returned different filters")
        keys = [h.place.key for g in found.groups for h in g.matched]
        if len(keys) != len(set(keys)) or any(k in omitted for k in keys):
            raise RuntimeError("search returned duplicate or omitted results")
        after = changed(
            state,
            filters=candidate,
            result=found,
            snapshot_id=uuid4(),
            presented=unique_keys((*presented, *keys)),
            excluded=excluded,
            selected=state.selected if state.selected in keys else None,
            proposal=None,
        )
        return result(
            "applied" if keys else "empty",
            state,
            after,
            data={"count": len(keys), "more": any(g.matched_truncated for g in found.groups)},
        )

    @staticmethod
    def _details(ref, place, attributes):
        values = {
            "parking": place.facts.parking,
            "exclusive": place.facts.pet_access.exclusive,
            "pet_allowed": place.facts.pet_access.allowed,
            "address": place.facts.address,
            "distance": place.distance_m,
            "hours": place.facts.hours_text,
        }
        facts = {}
        for name in attributes:
            value = values.get(name)
            facts[name] = {
                "status": "unsupported"
                if name not in values
                else "unknown"
                if value is None
                else "known",
                "value": value,
            }
        return {"ref": ref, "name": place.name, "basis": "등록된 정보", "facts": facts}
