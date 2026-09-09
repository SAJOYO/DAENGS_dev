import pytest

from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.edits import EditProposal, compile_edits


def base(**changes):
    return FilterState.model_validate(
        {
            "candidate_kinds": ["cafe", "restaurant"],
            "spatial": {"lat": 37.5, "lng": 127, "radius_m": 3000},
            **changes,
        }
    )


def atom(id="parking", capability="operations.parking", value=True):
    return {
        "id": id,
        "capability": capability,
        "op": "in" if capability == "purpose.kind" else "eq",
        "value": value,
    }


def edit(query, type="upsert_all", origin="explicit", **payload):
    return {
        "type": type,
        "evidence": {"quote": query, "start": 0, "end": len(query), "origin": origin},
        **payload,
    }


def proposal(*edits, unresolved=()):
    return EditProposal.model_validate({"edits": edits, "unresolved": unresolved})


def test_added_intersection_preserves_every_unmentioned_input():
    state = base(name_query="댕스", hard={"all": [atom()]}, dogs=[{"ref": "dog", "revision": "v1"}])
    result = compile_edits(
        state,
        "전용인 곳",
        proposal(edit("전용인 곳", atom=atom("exclusive", "pet_access.exclusive"))),
    )
    assert result.status == "ready"
    assert len(result.proposed_state.hard.all) == 2
    for field in (
        "dogs",
        "spatial",
        "name_query",
        "unknown_policy",
        "result_policy",
        "candidate_kinds",
    ):
        assert getattr(result.proposed_state, field) == getattr(state, field)


def test_or_branches_keep_and_conditions_and_not_in():
    query = "카페는 주차되거나 카페 아닌 전용 장소"
    branch1 = {"id": "b1", "all": [atom("cafe", "purpose.kind", ["cafe"]), atom()]}
    branch2 = {
        "id": "b2",
        "all": [
            {**atom("other", "purpose.kind", ["cafe"]), "op": "not_in"},
            atom("exclusive", "pet_access.exclusive"),
        ],
    }
    result = compile_edits(
        base(),
        query,
        proposal(*(edit(query, "upsert_branch", branch=b) for b in [branch1, branch2])),
    )
    # Both branches are a single new disjunction, not a relaxation of an existing one.
    assert result.proposed_state.hard.any[1].all[0].op == "not_in"
    assert result.status == "ready"


@pytest.mark.parametrize("query", ["아까 주차 필수는 빼줘", "주차 조건 빼주세요", "주차 상관없어"])
def test_explicit_clear_is_not_false_and_needs_no_confirmation(query):
    result = compile_edits(
        base(hard={"all": [atom()]}),
        query,
        proposal(edit(query, "remove_all", target_id="parking")),
    )
    assert result.status == "ready" and result.proposed_state.hard.all == ()


@pytest.mark.parametrize(
    "query",
    ["근처 다른 데", "주차 필수는 빼줘 라고 하지 않았어", "'주차 조건 빼줘'라는 문장을 해석해"],
)
def test_model_explicit_flag_cannot_unlock_existing_conditions(query):
    result = compile_edits(
        base(hard={"all": [atom()]}),
        query,
        proposal(edit(query, "remove_all", target_id="parking")),
    )
    assert result.requires_confirmation and result.status == "needs_resolution"


def test_inference_can_only_add_parking_preference():
    query = "차로 갈 거야"
    result = compile_edits(
        base(),
        query,
        proposal(
            edit(
                query,
                "upsert_preference",
                origin="inferred",
                preference={**atom(), "scope_kinds": ["cafe"]},
            )
        ),
    )
    assert not result.proposed_state.hard.all and result.proposed_state.preferences
    with pytest.raises(ValueError, match="inference_cannot"):
        compile_edits(base(), query, proposal(edit(query, origin="inferred", atom=atom())))


def test_unsupported_clause_prevents_partial_search_plan():
    query = "주차되고 조용한 곳"
    result = compile_edits(
        base(),
        query,
        proposal(
            edit(query, atom=atom()),
            unresolved=[
                {
                    "evidence": {"quote": "조용한", "start": 5, "end": 8, "origin": "explicit"},
                    "reason": "unsupported",
                }
            ],
        ),
    )
    assert result.proposed_state is None and result.issues[0].code == "unsupported"


def test_false_is_strict_and_contradiction_is_not_relaxed():
    result = compile_edits(
        base(hard={"all": [atom()]}),
        "주차 없는 곳",
        proposal(edit("주차 없는 곳", atom=atom("no", value=False))),
    )
    assert result.proposed_state is None and result.issues[0].code == "invalid_combination"
    with pytest.raises(ValueError):
        proposal(edit("주차", atom=atom(value="false")))


@pytest.mark.parametrize("span", [(0, 20), (1, 3)])
def test_evidence_rejects_out_of_bounds_and_utf16_offsets(span):
    query = "🐕주차"
    raw = edit(query, atom=atom())
    raw["evidence"].update(start=span[0], end=span[1])
    with pytest.raises(ValueError, match="invalid_evidence"):
        compile_edits(base(), query, proposal(raw))
    assert compile_edits(base(), query, proposal(edit(query, atom=atom()))).status == "ready"


def test_duplicate_target_unknown_target_and_invented_state_fields_are_rejected():
    with pytest.raises(ValueError, match="duplicate_edit_target"):
        compile_edits(
            base(), "주차", proposal(edit("주차", atom=atom()), edit("주차", atom=atom()))
        )
    with pytest.raises(ValueError, match="unknown_edit_target"):
        compile_edits(
            base(), "주차 빼줘", proposal(edit("주차 빼줘", "remove_all", target_id="missing"))
        )
    with pytest.raises(ValueError):
        proposal({**edit("주차", atom=atom()), "locked": False})
