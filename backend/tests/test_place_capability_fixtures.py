"""The committed Android-facing fixtures are the wire contract — keep them honest.

`tests/fixtures/place_capability/*.json` is what the Android client (a separate
repository, PR SAJOYO/DAENGS_APP#114) writes its parser against. The risk these tests
exist to remove is a silent one: the backend changes the projection or the aggregation
truth table, the committed fixtures keep describing the old shape, and the divergence is
discovered in the other repository — after that parser is already written.

So the fixtures are not treated as data files here. They are rebuilt from the production
code on every run and compared byte for byte, and the invariants the client is being
asked to rely on are asserted directly.
"""

from __future__ import annotations

import json

import pytest

from tools.place_fixtures import CONTRACT_VERSION, FIXTURE_DIR, build_fixtures, serialize

EXPECTED = {
    "place_success",
    "place_plus_walk_success",
    "place_abstention",
    "partial_success",
    "missing_location",
    "no_candidates",
    "refinement_required",
}


def _committed(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_every_expected_fixture_is_committed_and_nothing_else_is() -> None:
    on_disk = {path.stem for path in FIXTURE_DIR.glob("*.json")}
    assert on_disk == EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_committed_fixture_matches_the_production_path_byte_for_byte(name: str) -> None:
    """Regenerating must be a no-op. If this fails, the wire shape moved: rerun
    `uv run python -m tools.place_fixtures --write`, read the diff, and tell the
    Android session what changed before committing it."""
    rebuilt = serialize(build_fixtures()[name])
    assert (FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8") == rebuilt


# ----------------------------------------------------------------- envelope invariants


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("place_success", "ANSWERED"),
        ("place_plus_walk_success", "ANSWERED"),
        ("place_abstention", "UNCERTAIN"),
        ("partial_success", "PARTIAL"),
        ("missing_location", "CLARIFY"),
        ("no_candidates", "UNCERTAIN"),
        ("refinement_required", "UNCERTAIN"),
    ],
)
def test_top_level_status_is_what_the_truth_table_produces(name: str, status: str) -> None:
    assert _committed(name)["status"] == status


def test_clarify_fixture_executed_nothing() -> None:
    """O-8 on the wire: the client can rely on `results` being empty under CLARIFY."""
    body = _committed("missing_location")
    assert body["results"] == [] and body["handoffs"] == []
    assert body["clarify"]["missing"] == ["location.lat", "location.lon"]


def test_mixed_fixture_keeps_both_capabilities_and_the_canonical_order() -> None:
    body = _committed("place_plus_walk_success")
    assert [r["capability"] for r in body["results"]] == ["walk", "place"]
    # Both structured payloads survive aggregation — the client renders from these,
    # not by re-parsing `message`.
    assert all(r["data"] is not None for r in body["results"])
    assert "[산책]" in body["message"] and "[장소]" in body["message"]


def test_partial_fixture_keeps_the_successful_half(name: str = "partial_success") -> None:
    """A failed Walk must not erase an answered Place, and must not be papered over."""
    body = _committed(name)
    by_capability = {r["capability"]: r for r in body["results"]}
    assert by_capability["walk"]["status"] == "ERROR"
    assert by_capability["walk"]["data"] is None
    assert by_capability["place"]["status"] == "OK"
    assert by_capability["place"]["data"]["groups"][0]["candidates"]


# ------------------------------------------------------------------ Place data contract


def _place_data(name: str) -> dict:
    return next(
        r["data"] for r in _committed(name)["results"] if r["capability"] == "place"
    )


@pytest.mark.parametrize(
    "name",
    [
        "place_success",
        "place_plus_walk_success",
        "place_abstention",
        "partial_success",
        "no_candidates",
        "refinement_required",
    ],
)
def test_every_place_result_carries_the_contract_version_and_the_search_frame(
    name: str,
) -> None:
    """Option B (D-051) on the wire, including when Place abstains.

    A client that only shows the frame on success would hide it exactly when the user
    is most likely to wonder why nothing was found.
    """
    data = _place_data(name)
    assert data["contract_version"] == CONTRACT_VERSION
    frame = data["notices"][0]
    assert frame["code"] == "place.searched_around_current_location"
    assert frame["lens_id"] is None
    assert "현재 기기 위치를 기준으로" in frame["message"]
    assert "반영하지 않았습니다" in frame["message"]


def test_abstaining_place_still_carries_its_data() -> None:
    """ABSTAINED is not empty: the interpretation and any refinements still ship, which
    is what lets the client show why nothing came back."""
    for name in ("place_abstention", "no_candidates", "refinement_required"):
        assert _place_data(name) is not None
    assert _place_data("refinement_required")["refinements"][0]["options"]


def test_unknown_facts_are_preserved_as_unknown_not_as_negatives() -> None:
    """The distinction the client must not collapse: "not asked" is not "no"."""
    candidates = _place_data("place_success")["groups"][0]["candidates"]
    unknown = [
        fact
        for candidate in candidates
        for fact in candidate["facts"]
        if fact["source_state"] != "known"
    ]
    assert unknown, "fixture must exercise at least one unknown fact"
    for fact in unknown:
        assert fact["evaluation_state"] in {"unknown", "not_evaluated"}
        assert fact["text"]


def test_borrowed_facts_keep_their_provenance_and_link_state() -> None:
    """A value taken from another source record must stay attributable.

    `link_state == "candidate"` means the two records were only guessed to be the same
    place — a client that renders that as a confirmed fact about this place is
    inventing one.
    """
    candidates = _place_data("place_success")["groups"][0]["candidates"]
    borrowed = [
        fact
        for candidate in candidates
        for fact in candidate["facts"]
        if fact["provenance"]["value_origin"] == "borrowed"
    ]
    assert len(borrowed) == 2
    assert {fact["provenance"]["link_state"] for fact in borrowed} == {"verified", "candidate"}
    for fact in borrowed:
        assert fact["provenance"]["role"] == "supporting"
        assert fact["provenance"]["source"]


def test_candidate_fields_the_client_renders_are_all_present() -> None:
    candidate = _place_data("place_success")["groups"][0]["candidates"][0]
    for field in ("place_id", "title", "summary", "kind", "location", "address", "facts",
                  "notices", "why_matched"):
        assert field in candidate, field
    assert set(candidate["kind"]) == {"id", "label"}
    assert set(candidate["location"]) == {"lat", "lon", "distance_m"}
    assert set(candidate["place_id"]) == {"source", "ref"}


def test_place_candidate_budget_is_within_the_projection_contract() -> None:
    """The projection's own bounds, asserted on the wire rather than trusted."""
    for name in ("place_success", "place_plus_walk_success", "partial_success"):
        data = _place_data(name)
        assert len(data["groups"]) <= 3
        total = 0
        for group in data["groups"]:
            assert len(group["candidates"]) <= 3
            total += len(group["candidates"])
            for candidate in group["candidates"]:
                assert len(candidate["facts"]) <= 5
                assert len(candidate["notices"]) <= 2
                assert len(candidate["why_matched"]) <= 2
        assert total <= 9
        assert len(json.dumps(data, ensure_ascii=False).encode("utf-8")) <= 48 * 1024


def test_no_fixture_claims_a_named_region_was_resolved() -> None:
    """Nothing on the wire may suggest geocoding happened — it never does."""
    for path in FIXTURE_DIR.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        for forbidden in ("지오코딩", "좌표로 변환", "성수동", "해운대"):
            assert forbidden not in text, f"{path.name} mentions {forbidden}"
