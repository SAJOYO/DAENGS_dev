"""Adapt the versioned scenario specification without accessing a database."""

from datetime import UTC, datetime, timedelta

from daengs_place.place.contracts import PlaceResult
from daengs_place.place.conversation.contract import PrepareRequest
from daengs_place.place.conversation.service import ConversationService
from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.evaluation import evaluate
from daengs_place.place.filters.service import FilterGroup, FilterResponse, explain_hit

PARKING = "operations.parking"


def initial_filters(setup):
    state = {
        "candidate_kinds": setup["candidate_kinds"],
        "spatial": {**setup["origin"], "radius_m": setup["radius_m"]},
        "name_query": setup["name_query"],
        "dogs": setup["dogs"],
    }
    mode = setup["parking"]
    atom = {"id": "initial-parking", "capability": PARKING, "op": "eq", "value": True}
    if mode in {"required_true", "required_false"}:
        state["hard"] = {"all": [{**atom, "value": mode == "required_true"}]}
    elif mode == "preferred_true":
        state["preferences"] = [{**atom, "scope_kinds": setup["candidate_kinds"]}]
    elif mode != "none":
        raise ValueError("unknown setup parking mode")
    return FilterState.model_validate(state)


class FixtureSearcher:
    """Full synthetic candidate pool, filtered before limiting. Not a PostGIS oracle."""

    def __init__(self, setup, fixtures, now=None):
        self.now = now or datetime.now(UTC)
        self.calls = []
        variant = fixtures[setup.get("fixture", "standard")]
        rows = variant if isinstance(variant, list) else variant["rows"]
        defaults = fixtures["defaults"]
        self.rows = []
        for index, row in enumerate(rows, 1):
            key = {"source": defaults["source"], "ref": row["ref"]}
            self.rows.append(
                PlaceResult.model_validate(
                    {
                        "key": key,
                        "name": row.get("name", "테스트 " + row["ref"]),
                        **setup["origin"],
                        "distance_m": row.get("distance_m", index * 100),
                        "match": {"source": key, "kind": row["kind"]},
                        "classifications": [
                            {
                                "source": key,
                                "source_category": row["kind"],
                                "kind": row["kind"],
                                "mapping_version": "conversation-eval/1",
                            }
                        ],
                        "facts": {
                            "parking": row.get("parking"),
                            "pet_access": {"exclusive": row.get("exclusive")},
                            "address": defaults["address"],
                        },
                    }
                )
            )
        self.rows.sort(key=lambda row: (row.distance_m, row.key.source, row.key.ref))

    async def __call__(self, db, state, *, omitted=()):
        assert db is None, "the evaluation must not receive a real database"
        self.calls.append(state)
        groups = []
        for kind in state.candidate_kinds:
            matched, uncertain = [], []
            for row in self.rows:
                if (
                    row.key in omitted
                    or row.match.kind != kind
                    or row.distance_m > state.spatial.radius_m
                    or state.name_query.casefold() not in row.name.casefold()
                ):
                    continue
                verdict = evaluate(state, kind, row.facts.parking, row.facts.pet_access.exclusive)
                if verdict is True:
                    matched.append(explain_hit(row, state, uncertain=False))
                elif verdict is None and state.unknown_policy == "separate":
                    uncertain.append(explain_hit(row, state, uncertain=True))
            limit = state.result_policy.limit_per_kind
            uncertain_limit = state.result_policy.uncertain_limit_per_kind
            groups.append(
                FilterGroup(
                    kind=kind,
                    matched=tuple(matched[:limit]),
                    uncertain=tuple(uncertain[:uncertain_limit]),
                    matched_truncated=len(matched) > limit,
                    uncertain_truncated=len(uncertain) > uncertain_limit,
                )
            )
        return FilterResponse(applied_state=state, evaluated_at=self.now, groups=tuple(groups))


async def initial_state(setup, searcher):
    prepared = await ConversationService(searcher=searcher, now=lambda: searcher.now).prepare(
        None, PrepareRequest(mode="restore", restore_filters=initial_filters(setup))
    )
    state = prepared.state
    snapshot = state.snapshot.model_copy(
        update={
            "created_at": searcher.now - timedelta(seconds=setup["snapshot_age_seconds"]),
        }
    )
    selected = next(
        (key for key in snapshot.display_order if key.ref == setup.get("selected")), None
    )
    if setup.get("selected") and selected is None:
        raise ValueError("selected fixture is not in the initial snapshot")
    searcher.calls.clear()
    return state.model_copy(update={"snapshot": snapshot, "selected": selected})
