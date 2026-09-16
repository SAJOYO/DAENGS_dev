"""Scene title ownership and failure isolation; no provider calls."""

import json
from copy import deepcopy

import pytest

from daengs_backend.services.walk_diary.writing.relational import writing_prompt
from daengs_backend.services.walk_diary.writing.relational_transport import (
    CallCoordinator,
    ProviderFailure,
)
from daengs_backend.services.walk_diary.writing.scene_titles import (
    SCENE_TITLE_PROMPT,
    write_scene_titles,
)
from daengs_walk.diary.relational.scene_title_context import (
    SCENE_TITLE_CONTRACT,
    SceneTitleInput,
    scene_title_context,
    scene_title_revision,
)
from daengs_walk.diary.relational.scene_title_writer_view import scene_title_writer_view
from daengs_walk.diary.relational.title_context import validate_title_publication
from tests.walk.diary.test_relational_title import cards


def test_single_scene_input_local_revision_and_delivery_policy():
    receipt = cards()
    context = scene_title_context(receipt["cards"][2])
    view = scene_title_writer_view(context)
    assert view["scene_id"] == "original:2"
    assert view["recorded_at"] == "2026-09-15T09:02:00+09:00"
    assert set(view) == {"version", "scene_id", "timezone", "recorded_at", "space", "action"}
    assert writing_prompt("title", view) == SCENE_TITLE_PROMPT
    revision = scene_title_revision(context)
    receipt["cards"][0]["body"] = "다른 장면 변경"
    receipt["cards"][2]["title"] = "별도 제목"
    receipt["cards"][2]["originals"].clear()
    assert scene_title_revision(scene_title_context(receipt["cards"][2])) == revision
    for field, value in (
        ("scene_id", "other"),
        ("space", "다른 본문"),
        ("recorded_at", "2026-09-16T00:00:00Z"),
    ):
        assert (
            scene_title_revision(
                SceneTitleInput.model_validate(context.model_dump() | {field: value})
            )
            != revision
        )
    for field in ("scenes", "delivery_memory", "originals", "content_revision"):
        with pytest.raises(ValueError):
            SceneTitleInput.model_validate(context.model_dump() | {field: []})


@pytest.mark.parametrize("bad", ["blank", "unaccepted", "body"])
def test_reject_invalid_body(bad):
    card = cards()["cards"][0]
    if bad == "blank":
        card["parts"]["space"]["text"] = " "
    elif bad == "unaccepted":
        card["parts"]["space"]["status"] = "failed"
    else:
        card["body"] = "다른 본문"
    with pytest.raises(ValueError):
        scene_title_context(card)


@pytest.mark.parametrize("failure", [None, "timeout", "bad_json", "rate_limit"])
async def test_frozen_inputs_failures_and_saved_binding(failure):
    receipt = cards()
    original, seen = deepcopy(receipt), []

    async def send(stage, request, schema):
        assert stage == "title" and "scenes" not in request
        seen.append(deepcopy(request))
        request["space"] = "전송 객체 변경"
        if len(seen) == 1:
            if failure == "timeout":
                raise TimeoutError()
            if failure == "bad_json":
                return "bad"
            if failure == "rate_limit":
                raise ProviderFailure(429)
        return json.dumps({"title": seen[-1]["scene_id"]})

    titles = await write_scene_titles(receipt, send=CallCoordinator(send))
    assert receipt == original
    assert titles["original:1"]["status"] == "not_requested"
    assert titles["original:1"]["request"] is None
    assert len(seen) == (1 if failure == "rate_limit" else 2)
    assert titles["original:0"]["status"] == ("failed" if failure else "returned")
    assert titles["original:2"]["status"] == ("failed" if failure == "rate_limit" else "returned")
    receipt.update(title_contract=SCENE_TITLE_CONTRACT, scene_titles=titles)
    validate_title_publication(receipt)
    for mutation in ("swap", "missing", "text", "marker", "body"):
        changed = deepcopy(receipt)
        if mutation == "swap":
            changed["scene_titles"]["original:0"] = titles["original:2"]
        elif mutation == "missing":
            del changed["scene_titles"]["original:0"]
        elif mutation == "marker":
            del changed["title_contract"]
        elif mutation == "text":
            changed["scene_titles"]["original:1"]["text"] = "가짜 제목"
        else:
            changed["cards"][0]["parts"]["space"]["text"] = "다른 본문"
            changed["cards"][0]["body"] = "다른 본문"
        with pytest.raises(ValueError):
            validate_title_publication(changed)
