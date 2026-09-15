"""Frozen publication and bounded delivery memory; no provider or shared DB calls."""

import json
from copy import deepcopy

import pytest

from daengs_backend.services.walk_diary.storage.relational import read_skeleton, save_skeleton
from daengs_backend.services.walk_diary.writing.relational import validate_prepared
from daengs_backend.services.walk_diary.writing.short_memory import write_with_short_memory
from daengs_walk.diary.relational.delivery import DeliveryState, advance_delivery
from daengs_walk.diary.relational.publication import PUBLICATION_VERSION
from daengs_walk.value_contracts import digest
from tests.walk.diary.test_comparison_writer import response
from tests.walk.diary.test_relational_planning import review_double
from tests.walk.diary.test_scene_snapshot_assembly import prepared, public_collector  # noqa: F401


async def sender(stage, request, schema):
    return response(request)


async def test_saved_card_is_complete_and_reads_without_preparation(
    prepared,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    result = await write_with_short_memory(prepared, send=sender, review=False)
    receipt = result["receipt"]
    assert receipt["version"] == PUBLICATION_VERSION
    card = receipt["cards"][1]
    comparison = card["comparison"]
    assert comparison["context"]["earlier"] == prepared["snapshot"]["frames"][0]["scene_snapshot"]
    assert comparison["header"] == prepared["snapshot"]["frames"][1]["card_header"]
    assert comparison["context"]["connection"]["elapsed_seconds"] > 0
    assert comparison["context"]["route_evidence"]
    assert comparison["selection"]["text"] == card["parts"]["space"]["text"]
    assert comparison["selection"]["relation_ids"] == card["relation_selection"]["space"]
    assert comparison["semantic_status"] == "unverified"
    memory = receipt["cards"][-1]["comparison"]["delivery_after"]
    assert len(memory["recent_deliveries"]) == 2
    assert memory["all_context_facts_delivered"] is False
    assert "text" not in memory["active_introduction"]
    path = tmp_path / "v7.json"
    save_skeleton(path, result)
    with pytest.raises(FileExistsError):
        save_skeleton(path, result)
    # Neither caller mutations nor disabled current services can alter the saved read.
    result["prepared"]["snapshot"]["frames"].clear()
    from daengs_backend.services.walk_diary.writing import relational

    def forbidden(*args, **kwargs):
        raise AssertionError("reader must not invoke preparation or generation")

    monkeypatch.setattr(relational, "validate_prepared", forbidden)
    monkeypatch.setattr(relational, "generate_relation_part", forbidden)
    assert read_skeleton(path) == receipt


async def test_review_rejection_is_audit_only_and_clears_active_changed_context(prepared):  # noqa: F811
    reviews = 0

    async def send(stage, request, schema):
        nonlocal reviews
        if stage != "review":
            return response(request)
        reviews += 1
        if reviews == 2:
            raise TimeoutError("review unavailable")
        return review_double(request)

    result = await write_with_short_memory(prepared, send=send, review=True)
    failed = result["receipt"]["cards"][1]
    publication = failed["comparison"]
    assert failed["parts"]["space"]["status"] == "failed"
    assert failed["body"] == "" and publication["selection"] is None
    assert publication["semantic_status"] == "not_published"
    assert publication["delivery_after"]["active_introduction"] is None
    assert len(publication["delivery_after"]["recent_deliveries"]) == 1
    trace = result["receipt"]["writing"]["results"][1]
    assert trace["candidate"]["focus"] and trace["raw_text"]


async def test_save_rejects_context_changed_with_its_hash(prepared, tmp_path):  # noqa: F811
    result = await write_with_short_memory(prepared, send=sender, review=False)
    pub = result["receipt"]["cards"][1]["comparison"]
    pub["context"]["current"]["facts"][0]["value"] = {"name": "조작된 길"}
    pub["context_revision"] = digest(pub["context"])
    with pytest.raises(ValueError):
        save_skeleton(tmp_path / "bad.json", result)


async def test_read_rejects_selection_mutation_even_with_new_envelope_hash(prepared, tmp_path):  # noqa: F811
    result = await write_with_short_memory(prepared, send=sender, review=False)
    path = tmp_path / "v7.json"
    save_skeleton(path, result)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["receipt"]["cards"][0]["comparison"]["selection"]["focus"] = "교체"
    document["digest"] = digest(document["payload"])
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="selection"):
        read_skeleton(path)


@pytest.mark.parametrize("version", range(2, 7))
def test_old_publication_reads_without_new_fields(version, tmp_path):
    receipt = {"version": f"relational-diary-skeleton-v{version}", "cards": [{"body": "기존 원문"}]}
    payload = {"receipt": deepcopy(receipt)}
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps({"format": receipt["version"], "payload": payload, "digest": digest(payload)}),
        encoding="utf-8",
    )
    assert read_skeleton(path) == receipt


async def test_reversed_plans_rejected_before_sender(prepared):  # noqa: F811
    changed = deepcopy(prepared)
    changed["snapshot"]["plans"].reverse()
    changed["revision"] = digest(changed["snapshot"])

    async def forbidden(*args):
        raise AssertionError("must reject before sending")

    with pytest.raises(ValueError, match="chronological"):
        validate_prepared(changed)
    with pytest.raises(ValueError, match="chronological"):
        await write_with_short_memory(changed, send=forbidden, review=False)


async def test_reader_rejects_duplicate_cards(prepared, tmp_path):  # noqa: F811
    result = await write_with_short_memory(prepared, send=sender, review=False)
    path = tmp_path / "time.json"
    save_skeleton(path, result)
    document = json.loads(path.read_text(encoding="utf-8"))
    cards = document["payload"]["receipt"]["cards"]
    cards[1] = deepcopy(cards[0])
    document["digest"] = digest(document["payload"])
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate published scene"):
        read_skeleton(path)


@pytest.mark.parametrize("phase", ["serialization", "publication"])
async def test_failed_save_leaves_no_final_file_and_can_retry(
    prepared,  # noqa: F811
    tmp_path,
    monkeypatch,
    phase,
):
    from daengs_backend.services.walk_diary.storage import relational as storage

    result = await write_with_short_memory(prepared, send=sender, review=False)
    path = tmp_path / "retry.json"

    def interrupted_dump(document, stream, **kwargs):
        stream.write('{"format":')
        raise OSError("injected interrupted write")

    def interrupted_link(*args):
        raise OSError("injected publication failure")

    with monkeypatch.context() as patcher:
        if phase == "serialization":
            patcher.setattr(storage.json, "dump", interrupted_dump)
        else:
            patcher.setattr(storage.os, "link", interrupted_link)
        with pytest.raises(OSError):
            save_skeleton(path, result)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
    save_skeleton(path, result)
    assert read_skeleton(path) == result["receipt"]


def test_reviewed_current_introduction_uses_reviewed_citations(prepared):  # noqa: F811
    frame = prepared["snapshot"]["frames"][1]
    task = prepared["snapshot"]["plans"][1]["space_task"]
    current_id = frame["scene_snapshot"]["facts"][0]["id"]
    earlier_id = task["payload"]["earlier"]["facts"][0]["id"]
    result = {
        "status": "returned",
        "semantic_status": "model_reviewed",
        "answer": {
            "focus": "이전 장소",
            "text": "이전에 남긴 곳은 길이었다.",
            "evidence_ids": [earlier_id, current_id],
            "relation_ids": [],
        },
        "citation_map": {"e1": earlier_id, "e2": current_id},
        "semantic_review": {"assessment": {"used_evidence_ids": ["e1"]}},
    }
    state = advance_delivery(DeliveryState(), frame, task, result)
    assert state.active_introduction is None and len(state.recent_deliveries) == 1
    result["semantic_review"]["assessment"]["used_evidence_ids"] = ["e2"]
    assert advance_delivery(DeliveryState(), frame, task, result).active_introduction is not None


@pytest.mark.parametrize("family", ["road", "land_cover", "surrounding_object"])
def test_delivery_identity_preserves_family_meaning(prepared, family):  # noqa: F811
    from daengs_walk.diary.relational.comparison_writing import should_write_space
    from daengs_walk.diary.relational.delivery import context_signature
    from daengs_walk.diary.relational.relations.registry import collect_spatial_comparisons

    a = deepcopy(prepared["snapshot"]["frames"][0])
    a["scene_snapshot"]["facts"] = [
        f for f in a["scene_snapshot"]["facts"] if f["family"] == family
    ]
    b = deepcopy(a)
    b["scene_snapshot"]["scene_id"] += ":next"
    fact = b["scene_snapshot"]["facts"][0]
    fact["subject_key"] = "different-source-object"
    fact["id"] += ":new"
    b["spatial_comparison_slots"] = collect_spatial_comparisons(
        b["scene_snapshot"], a["scene_snapshot"]
    )
    changed = family == "surrounding_object"
    assert (context_signature(a) != context_signature(b)) == changed
    assert should_write_space(b, a) == changed
    if family != "surrounding_object":
        key = "name" if family == "road" else "classification"
        fact["value"][key] = "another-background"
        b["spatial_comparison_slots"] = collect_spatial_comparisons(
            b["scene_snapshot"], a["scene_snapshot"]
        )
        assert context_signature(a) != context_signature(b)
        assert should_write_space(b, a)


async def test_v7_legacy_delivery_still_reads(prepared, tmp_path):  # noqa: F811
    from daengs_backend.services.walk_diary.writing.relational import write_relational_diary
    from daengs_walk.diary.relational.assembly import assemble_receipt

    # A pre-policy v7 result is assembled and checked with the old signature.
    written = await write_relational_diary(prepared, send=sender, review=False)
    receipt = assemble_receipt(prepared, written)
    assert "delivery_policy" not in receipt
    path = tmp_path / "legacy-v7.json"
    save_skeleton(path, {"prepared": prepared, "receipt": receipt})
    assert read_skeleton(path) == receipt
