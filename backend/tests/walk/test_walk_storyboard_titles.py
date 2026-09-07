import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services import walk_storyboard_titles as service
from daengs_walk.storyboard import StoryboardBundleV2, compatible_bundle


@pytest.fixture
def bundle():
    return StoryboardBundleV2.model_validate_json(
        (Path(__file__).parent / "fixtures/v2-short.json").read_text(encoding="utf-8")
    )


def headings(payload):
    scenes = payload["scenes"]
    return {
        "title": {"text": "함께 남긴 산책 기록", "fact_ids": [scenes[0]["facts"][0]["id"]]},
        "scenes": [
            {"scene_id": s["id"], "text": "산책 관측 기록", "fact_ids": [s["facts"][0]["id"]]}
            for s in scenes
        ],
    }


async def test_one_call_generates_both_titles_preserving_observations_and_legacy(bundle):
    generate = AsyncMock(side_effect=headings)
    titled = await service.title_storyboard(bundle, generate)
    assert titled.title == "함께 남긴 산책 기록"
    assert titled.source_revision != bundle.source_revision
    generate.assert_awaited_once()
    for old, new in zip(bundle.scenes, titled.scenes, strict=True):
        assert old.model_dump(exclude={"title", "revision"}) == new.model_dump(
            exclude={"title", "revision"}
        )
        if old.title != new.title:
            assert old.revision != new.revision
    for target in ("walk-storyboard-candidates-v1", "walk-storyboard-candidates-v2"):
        legacy = compatible_bundle(titled.model_dump(mode="json"), target).model_dump(mode="json")
        assert (
            legacy["format"] == target and "title" not in legacy and "title_fact_ids" not in legacy
        )
    supplied = generate.call_args.args[0]
    assert all(set(s) == {"id", "title", "facts"} for s in supplied["scenes"])
    assert all(set(f) == {"id", "kind", "text"} for s in supplied["scenes"] for f in s["facts"])


@pytest.mark.parametrize(
    "invalid", ["unknown_fact", "missing_scene", "duplicate_scene", "newline", "long", "malformed"]
)
async def test_invalid_generation_keeps_original_scenes_without_title(bundle, invalid):
    value = headings(service.title_input(bundle))
    if invalid == "unknown_fact":
        value["title"]["fact_ids"] = ["invented-fact"]
    elif invalid == "missing_scene":
        value["scenes"].pop()
    elif invalid == "duplicate_scene":
        value["scenes"].append(value["scenes"][0])
    elif invalid == "newline":
        value["title"]["text"] = "산책\n기록"
    elif invalid == "long":
        value["title"]["text"] = "가" * 41
    else:
        value = "{"
    result = await service.title_storyboard(bundle, AsyncMock(return_value=value))
    assert result.title is None and result.title_fact_ids == []
    assert result.scenes == bundle.scenes


async def test_provider_failure_deadline_and_input_budget_do_not_discard_facts(bundle, monkeypatch):
    failed = await service.title_storyboard(
        bundle, AsyncMock(side_effect=ValueError("private error"))
    )
    assert failed.title is None and failed.scenes == bundle.scenes
    monkeypatch.setattr(service, "TIMEOUT_SECONDS", 0.01)
    cancelled = []

    async def slow(_):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)

    assert (await service.title_storyboard(bundle, slow)).title is None
    assert cancelled == [True]
    with pytest.raises(asyncio.CancelledError):
        await service.title_storyboard(bundle, AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(service, "MAX_INPUT_BYTES", 1)
    unused = AsyncMock()
    assert (await service.title_storyboard(bundle, unused)).scenes == bundle.scenes
    unused.assert_not_awaited()


async def test_sdk_uses_single_async_structured_request_and_closes(monkeypatch, bundle):
    from types import SimpleNamespace

    from google import genai
    from pydantic import SecretStr

    generate = AsyncMock(return_value=SimpleNamespace(text="{}"))
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate)
    )
    seen = []

    def construct(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(aio=client)

    monkeypatch.setattr(service.settings, "gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(genai, "Client", construct)
    assert await service.generate_headings(service.title_input(bundle)) == "{}"
    assert seen[0]["http_options"].retry_options.attempts == 1
    assert generate.call_args.kwargs["config"].response_json_schema["properties"].keys() == {
        "title",
        "scenes",
    }
    generate.assert_awaited_once()
    client.__aexit__.assert_awaited_once()
