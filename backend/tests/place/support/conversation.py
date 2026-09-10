"""Synthetic facility records and workflow doubles shared by conversation tests."""

from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from daengs_place.place.contracts import PlaceResult
from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.intent import Interpretation as TurnPlan
from daengs_place.place.filters.evaluation import evaluate
from daengs_place.place.filters.service import FilterGroup, FilterResponse, explain_hit


def place(ref, kind="shopping", distance=100, parking=True):
    source = {"source": "test:facility", "ref": ref}
    return PlaceResult.model_validate(
        {
            "key": source,
            "name": "테스트 " + ref,
            "lat": 37.5,
            "lng": 127.0,
            "distance_m": distance,
            "match": {"source": source, "kind": kind},
            "classifications": [
                {
                    "source": source,
                    "source_category": kind,
                    "kind": kind,
                    "mapping_version": "test/1",
                }
            ],
            "facts": {"parking": parking, "address": "테스트 주소"},
        }
    )


class Searcher:
    def __init__(self):
        self.calls = []
        self.error = False
        self.rows = [place("first"), place("second", distance=200), place("cafe", kind="cafe")]

    async def __call__(self, db, state):
        self.calls.append(state)
        if self.error:
            raise SQLAlchemyError("private database address")
        groups = []
        for kind in state.candidate_kinds:
            rows = [
                row
                for row in self.rows
                if row.match.kind == kind and evaluate(state, kind, row.facts.parking, None) is True
            ]
            limit = state.result_policy.limit_per_kind
            groups.append(
                FilterGroup(
                    kind=kind,
                    matched=tuple(explain_hit(row, state, uncertain=False) for row in rows[:limit]),
                    matched_truncated=len(rows) > limit,
                )
            )
        return FilterResponse(
            applied_state=state, evaluated_at=datetime.now(UTC), groups=tuple(groups)
        )


class Planner:
    def __init__(self, plan=None):
        self.next = plan or TurnPlan(goal="pick_one")
        self.requests = []

    async def plan(self, request):
        self.requests.append(request)
        return self.next


def manual(previous=None, **changes):
    return PrepareRequest(
        mode="manual",
        previous=previous,
        manual={
            "lat": 37.5,
            "lng": 127.0,
            "radius_m": 3000,
            "kinds": ["shopping", "pet_shop"],
            **changes,
        },
    )
