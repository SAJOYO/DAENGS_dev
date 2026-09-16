"""Frozen v8 save/read boundaries, with provider responses injected only at transport."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from daengs_backend.services.walk_diary.relational_execution import RelationalExecutionPolicy
from daengs_backend.services.walk_diary.runtime import write_relational_board
from daengs_backend.services.walk_diary.storage.relational import read_skeleton, save_skeleton
from daengs_backend.services.walk_diary.storage.relational_db import read_result, store_result
from daengs_walk.value_contracts import digest
from tests.walk.diary.test_brief_execution import prepare, public_collector  # noqa: F401
from tests.walk.diary.test_diary_activity import prepared as activity
from tests.walk.diary.test_relational_http import brief_send


@pytest.fixture
def base():
    from daengs_backend.services.walk_diary.preparation.relational_base import (
        assemble_relational_base,
    )

    assembled = activity()[0].input
    source = assembled.source.model_copy(
        update={"client_session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}
    )
    return assemble_relational_base(replace(assembled, source=source), 3)


@pytest.fixture
async def written(base, prepare):  # noqa: F811
    return await write_relational_board(
        base.input.source,
        base,
        prepare=prepare,
        send=brief_send,
        execution_policy=RelationalExecutionPolicy(minimum_interval_s=0),
    )


def load(raw):
    body = raw["payload"]
    return read_result(
        raw,
        walk_id=body["walk_id"],
        session_id=body["session_id"],
        revision=body["source_revision"],
        generation=body["generation"],
    )


def store(written, base):
    return store_result(written, base, revision="user-source-revision", generation=2, target=3)


def test_v8_json_and_db_envelope_read_without_current_planning(
    written, base, tmp_path, monkeypatch
):
    from daengs_backend.services.walk_diary.writing import brief_prompts, relational
    from daengs_walk.diary.relational import (
        brief_binding,
        comparison_writing,
        narrative_space,
        scene_requests,
    )

    raw = store(written, base)
    # The JSON serialization boundary is the same as JSONB; no in-memory object assumptions.
    raw = json.loads(json.dumps(raw))
    path = tmp_path / "v8.json"
    save_skeleton(path, {"prepared": written.prepared, "receipt": written.receipt})

    def forbidden(*args, **kwargs):
        raise AssertionError("read must not plan, rebuild meanings, or call a model")

    for module, name in (
        (relational, "validate_prepared"),
        (relational, "generate_relation_part"),
        (scene_requests, "assemble_scene_requests"),
        (brief_binding, "validate_brief_plans"),
        (comparison_writing, "comparison_input"),
        (narrative_space, "build_space_context"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setitem(brief_prompts.BRIEF_PROMPTS, "space", "a future prompt")
    assert load(raw).model_dump(mode="json") == raw["payload"]["public"]
    assert read_skeleton(path) == written.receipt
    assert "raw_text" not in json.dumps(raw["payload"]["public"])


@pytest.mark.parametrize(
    "field",
    [
        "actor",
        "phase",
        "memory",
        "request",
        "schema",
        "raw",
        "review",
        "model",
        "header",
        "original",
        "body",
        "prepared",
        "version",
    ],
)
def test_read_rejects_broken_binding_even_if_outer_hash_is_replaced(written, base, field):
    raw = store(written, base)
    saved = raw["payload"]
    receipt = saved["receipt"]
    card = receipt["cards"][0]
    row = receipt["writing"]["results"][0]
    if field == "actor":
        card = next(c for c in receipt["cards"] if c["action_brief"])
        card["action_brief"]["required_event"]["actor"]["name"] = "다른 강아지"
    elif field == "phase":
        card["space_brief"]["context"]["current"]["position"]["selected_scene_number"] = 99
    elif field == "memory":
        card["delivery_after"]["recent"] = []
    elif field == "request":
        row["request"]["available_facts"] = []
    elif field == "schema":
        row["response_schema"]["properties"]["evidence_ids"]["items"]["enum"] = ["forged"]
    elif field == "raw":
        row["raw_text"] = json.dumps({**row["answer"], "text": "다른 문장"})
    elif field == "review":
        row["semantic_status"] = "model_reviewed"
    elif field == "model":
        receipt["execution"]["model"] = "another-model"
    elif field == "header":
        card["comparison"]["header"]["dong"] = "다른동"
    elif field == "original":
        card["originals"].append({"record": {"kind": "memo", "text": "바뀐 원문"}})
    elif field == "body":
        card["body"] = "채택되지 않은 글"
    elif field == "prepared":
        saved["prepared"]["snapshot"]["frames"].reverse()
    else:
        receipt["version"] = "relational-diary-skeleton-v99"
    raw["digest"] = digest(saved)
    with pytest.raises((ValueError, KeyError)):
        load(raw)


def test_store_rejects_changed_result_and_other_reservation(written, base):
    changed = deepcopy(written)
    changed.receipt["cards"][0]["body"] = "바뀐 문장"
    with pytest.raises(ValueError, match="canonical publication"):
        store(changed, base)
    with pytest.raises(ValueError, match="binding"):
        raw = store(written, base)
        body = raw["payload"]
        read_result(
            raw,
            walk_id=body["walk_id"],
            session_id=body["session_id"],
            revision=body["source_revision"],
            generation=3,
        )


async def test_scene_titles_survive_public_roundtrip_and_legacy_defaults(written, base):
    from daengs_backend.services.walk_diary.storage.relational_db import project
    from daengs_backend.services.walk_diary.writing.relational_title import write_relational_title
    from daengs_walk.diary.relational.title_context import TITLE_CONTRACT

    raw = store(written, base)
    public = load(json.loads(json.dumps(raw)))
    assert public.title is None and public.title_status == "not_requested"
    assert [c.title for c in public.cards] == [
        written.receipt["scene_titles"][c.scene_id]["text"] for c in public.cards
    ]
    assert all(c.title_status == "returned" for c in public.cards)
    changed = deepcopy(raw)
    changed["payload"]["public"]["cards"][0]["title"] = "다른 제목"
    changed["digest"] = digest(changed["payload"])
    with pytest.raises(ValueError, match="projection"):
        load(changed)

    # Reconstruct the historical writer contract and its title-less public cards.
    old_receipt = deepcopy(written.receipt)
    del old_receipt["scene_titles"]
    old_receipt["title_contract"] = TITLE_CONTRACT
    old_receipt["title"] = await write_relational_title(old_receipt, send=brief_send)
    old = deepcopy(raw)
    old["payload"]["receipt"] = old_receipt
    old["payload"]["public"] = project(old_receipt, base.input.source.client_session_id).model_dump(
        mode="json"
    )
    for card in old["payload"]["public"]["cards"]:
        del card["title"], card["title_status"]
    old["digest"] = digest(old["payload"])
    restored = load(json.loads(json.dumps(old)))
    assert restored.title == old_receipt["title"]["text"]
    assert all(c.title is None and c.title_status == "not_requested" for c in restored.cards)
