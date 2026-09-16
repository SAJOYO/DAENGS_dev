"""Title projection, actual orchestration boundary and saved body binding; no live calls."""

import json
from copy import deepcopy

import pytest

from daengs_backend.orchestration.relational_diary import generate_prepared_relational_diary
from daengs_backend.services.walk_diary.storage.relational import read_skeleton, save_skeleton
from daengs_backend.services.walk_diary.writing.relational_title import write_relational_title
from daengs_walk.diary.relational.scene_title_context import scene_title_context
from daengs_walk.diary.relational.scene_title_writer_view import scene_title_writer_view
from daengs_walk.diary.relational.title_context import (
    TITLE_CONTRACT,
    TitleReadModel,
    title_context,
    title_request_revision,
    validate_title_publication,
)
from daengs_walk.value_contracts import digest
from tests.walk.diary.test_relational_orchestration import send as body_sender
from tests.walk.diary.test_relational_takeover import assessment
from tests.walk.diary.test_scene_snapshot_assembly import prepared, public_collector  # noqa: F401


def cards():
    rows = []
    for i in range(3):
        space = f"장소 {i}." if i != 1 else ""
        action = "보리가 냄새를 맡았다." if i == 2 else ""
        rows.append(
            {
                "scene_id": f"original:{i}",
                "anchor": {"event_at": f"2026-09-15T09:0{i}:00+09:00"},
                "parts": {
                    "space": {"status": "returned" if space else "failed", "text": space},
                    "action": {
                        "status": "returned" if action else "not_requested",
                        "text": action,
                    },
                },
                "body": "\n".join(x for x in (space, action) if x),
                "movement_observations": [],
                "originals": [{"text": "메모 원문 금지"}],
                "standalone_context": {"text": "채택하지 않은 공원"},
                "header": {"weather": "비", "dong": "양재동"},
                "candidate": {"text": "거절된 경험"},
            }
        )
    rows[2]["movement_observations"] = [
        {"id": "motion1", "text": "현재 기록 20초 전: 방향 전환", "source_claim": "원자료"}
    ]
    return {"cards": rows}


async def title_sender(stage, request, schema):
    if stage == "title":
        return json.dumps({"title": "보리와 남긴 산책"})
    return json.dumps(assessment(request["candidate"]["evidence_ids"]))


def test_projection_preserves_scene_ownership_and_excludes_unpublished_sources():
    receipt = cards()
    value = title_context(receipt)
    assert [s.scene_id for s in value.scenes] == ["original:0", "original:2"]
    assert [s.order for s in value.scenes] == [1, 3]
    assert value.scenes[-1].space == "장소 2."
    assert value.scenes[-1].action == "보리가 냄새를 맡았다."
    assert value.scenes[-1].movement_observations[0].id == "motion1"
    assert value.scenes[-1].recorded_at.isoformat() == "2026-09-15T00:02:00+00:00"
    text = value.model_dump_json()
    for excluded in ("메모", "공원", "양재동", "weather", "원자료", "거절된"):
        assert excluded not in text
    receipt["cards"][2]["parts"]["action"]["text"] = "외부 변경"
    assert value.scenes[-1].action == "보리가 냄새를 맡았다."


@pytest.mark.parametrize("change", ["duplicate", "reverse", "extra", "rejected"])
def test_input_contract_rejects_ambiguous_or_unaccepted_content(change):
    receipt = cards()
    if change == "rejected":
        receipt["cards"][1]["parts"]["space"]["text"] = "거절된 후보"
        with pytest.raises(ValueError, match="unaccepted"):
            title_context(receipt)
        return
    value = title_context(receipt).model_dump(mode="json")
    if change == "duplicate":
        value["scenes"][1]["scene_id"] = value["scenes"][0]["scene_id"]
    elif change == "reverse":
        value["scenes"].reverse()
    else:
        value["scenes"][0]["raw_space"] = "unadopted"
    with pytest.raises(ValueError):
        TitleReadModel.model_validate(value)


async def test_writer_and_reviewer_read_the_same_frozen_input():
    receipt = cards()
    requests = []

    async def sender(stage, request, schema):
        requests.append((stage, deepcopy(request)))
        raw = await title_sender(stage, request, schema)
        if stage == "title":
            request["scenes"].clear()
        return raw

    title = await write_relational_title(receipt, send=sender, review=True)
    receipt.update(title=title, title_contract=TITLE_CONTRACT)
    validate_title_publication(receipt)
    assert [stage for stage, _ in requests] == ["title", "review"]
    assert requests[0][1] == requests[1][1]["evidence"] == title["request"]
    wire = json.dumps(title["request"], ensure_ascii=False)
    assert "현재 기록" not in wire and "movement_observations" not in wire
    assert "보리가 냄새를 맡았다." in wire
    assert "action" not in title["request"]["scenes"][0]
    assert requests[1][1]["candidate"]["evidence_ids"] == ["original:0", "original:2"]


@pytest.mark.parametrize("case", ["empty", "motion_only", "bad_json", "extra", "reject", "timeout"])
async def test_empty_body_or_failed_title_does_not_trigger_rewriting(case):
    receipt = cards()
    if case in {"empty", "motion_only"}:
        for card in receipt["cards"]:
            for part in card["parts"].values():
                part.update(status="not_requested", text="")
            card["body"] = ""
            if case == "empty":
                card["movement_observations"] = []
    seen = []

    async def sender(stage, request, schema):
        seen.append(stage)
        if case == "timeout":
            raise TimeoutError("unavailable")
        if case == "bad_json":
            return "not json"
        if case == "extra":
            return json.dumps({"title": "제목", "invented": "extra"})
        if case == "reject" and stage == "review":
            value = assessment(request["candidate"]["evidence_ids"])
            value.update(supported=False, issues=["근거 없는 경험"])
            return json.dumps(value)
        return await title_sender(stage, request, schema)

    receipt.update(
        title=await write_relational_title(receipt, send=sender, review=True),
        title_contract=TITLE_CONTRACT,
    )
    validate_title_publication(receipt)
    title = receipt["title"]
    if case in {"empty", "motion_only"}:
        assert seen == [] and title["status"] == "not_requested"
    else:
        assert title["status"] == "failed"
        assert seen == (["title", "review"] if case == "reject" else ["title"])


@pytest.mark.parametrize("change", ["body", "review", "candidate", "marker", "status", "bypass"])
async def test_saved_title_cannot_be_rebound_by_only_rehashing_request(change):
    receipt = cards()
    receipt.update(
        title=await write_relational_title(receipt, send=title_sender, review=True),
        title_contract=TITLE_CONTRACT,
    )
    title = receipt["title"]
    if change == "body":
        title["request"]["scenes"][0]["space"] = "다른 본문"
        title["content_revision"] = digest(title["request"])
        title["request_revision"] = title_request_revision(
            title["prompt_revision"], title["request"], title["response_schema"]
        )
    elif change == "review":
        title["semantic_review"]["request"]["evidence"]["scenes"].clear()
    elif change == "candidate":
        title["text"] = "교체된 제목"
    elif change == "marker":
        del receipt["title_contract"]
    elif change == "bypass":
        title["semantic_status"] = "unverified"
    else:
        title["semantic_review"]["status"] = "rejected"
    with pytest.raises(ValueError):
        validate_title_publication(receipt)


async def test_real_orchestration_and_local_saved_title_binding(prepared, tmp_path):  # noqa: F811
    result = await generate_prepared_relational_diary(prepared, send=body_sender)
    scene_id = result["receipt"]["cards"][0]["scene_id"]
    title = result["receipt"]["scene_titles"][scene_id]
    assert title["status"] == "returned"
    assert title["request"] == scene_title_writer_view(
        scene_title_context(result["receipt"]["cards"][0])
    )
    path = tmp_path / "title.json"
    save_skeleton(path, result)
    assert read_skeleton(path) == result["receipt"]
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["receipt"]["scene_titles"][scene_id]["text"] = "교체된 제목"
    document["digest"] = digest(document["payload"])
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="candidate"):
        read_skeleton(path)
    result["receipt"]["scene_titles"][scene_id]["request"]["space"] = "다른 본문"
    with pytest.raises(ValueError, match="adopted"):
        save_skeleton(tmp_path / "wrong.json", result)


def test_legacy_title_keeps_its_original_contract():
    validate_title_publication({"title": {"request": {"scenes": [{"body": "기존 본문"}]}}})
