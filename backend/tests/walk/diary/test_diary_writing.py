"""Writing dictionary and one bounded provider call; no real LLM or private records."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services import walk_diary_writing as writer
from daengs_walk.diary_output import assemble_diary
from daengs_walk.diary_stamps import StampPolicy, prepare_stamps
from daengs_walk.diary_writing import prepare_writing
from tests.walk.support.diary import nearby, prose, record, with_backgrounds


def materials(count=2):
    records = [record(f"private-note-{i}", f"{i + 5:02}") for i in range(count)]
    backgrounds = [nearby(r, id=f"private-bg-{i}") for i, r in enumerate(records)]
    source = with_backgrounds(
        *records, record("private-photo", "30", photo=True), backgrounds=backgrounds
    )
    return source, prepare_stamps(source, StampPolicy(target_scene_count=3))


async def test_compact_dictionary_generates_only_backgrounds_and_preserves_originals():
    source, prepared = materials()
    call = AsyncMock(side_effect=prose)
    output = await writer.write_diary(source, prepared, call)
    fallback = assemble_diary(source, prepared.plan, None)
    assert output.model_status == "accepted" and output.title_origin == "model"
    assert output.title == "함께 남긴 산책"
    for before, after in zip(fallback.scenes, output.scenes, strict=True):
        assert before.model_dump(exclude={"narration"}) == after.model_dump(exclude={"narration"})
    assert output.scenes[-1].narration.status == "no_background"
    payload, schema = call.call_args.args
    serialized = json.dumps(payload, ensure_ascii=False)
    for private in (
        "private-note",
        "private-bg",
        "private-photo",
        "app-private-photo",
        '"lat"',
        '"lng"',
        "pet_id",
        "source_ref",
    ):
        assert private not in serialized
    assert payload["title_context"][0]["record"]["text"] == source.records[0].content.text
    assert list(payload["background_dictionary"]) == ["e1", "e2"]
    assert payload["background_dictionary"]["e1"]["retrieved_at"].startswith("2026-09-09T02:")
    assert payload["background_dictionary"]["e1"]["location_at"].startswith("2026-09-09T00:04:55")
    assert schema["$defs"]["SceneText"]["properties"]["scene_id"]["enum"] == ["s1", "s2"]
    assert schema["properties"]["scenes"]["minItems"] == 2
    call.assert_awaited_once()


@pytest.mark.parametrize(
    "invalid", ["cross_scene", "unknown", "missing", "duplicate", "action", "malformed", "blank"]
)
async def test_invalid_model_output_keeps_originals(invalid):
    source, prepared = materials()
    request = prepare_writing(source, prepared)
    raw = prose(request.payload)
    if invalid == "cross_scene":
        raw["scenes"][0]["evidence_ids"] = ["e2"]
    elif invalid == "unknown":
        raw["scenes"][0]["evidence_ids"] = ["invented"]
    elif invalid == "missing":
        raw["scenes"].pop()
    elif invalid == "duplicate":
        raw["scenes"].append(raw["scenes"][0])
    elif invalid == "action":
        raw["scenes"][0]["user_record"] = {"text": "rewritten"}
    elif invalid == "blank":
        raw["title"] = " "
    else:
        raw = "{"
    output = await writer.write_diary(source, prepared, AsyncMock(return_value=raw))
    assert output.failure_code == "invalid_response" and output.title_origin == "system"
    assert (
        output.scenes
        == assemble_diary(source, prepared.plan, None, failure_code="invalid_response").scenes
    )


async def test_no_background_does_not_spend_a_title_or_writing_call():
    source = with_backgrounds(record())
    prepared = prepare_stamps(source, StampPolicy(target_scene_count=3))
    call = AsyncMock()
    result = await writer.write_diary(source, prepared, call)
    assert result.model_status == "not_requested" and result.scenes[0].user_record is not None
    call.assert_not_awaited()


@pytest.mark.parametrize("limit", ["bytes", "scenes"])
async def test_budget_skips_the_whole_call_without_dropping_user_records(monkeypatch, limit):
    source, prepared = materials()
    monkeypatch.setattr(writer, "MAX_INPUT_BYTES" if limit == "bytes" else "MAX_WRITABLE_SCENES", 1)
    call = AsyncMock()
    result = await writer.write_diary(source, prepared, call)
    assert result.failure_code == "budget_exceeded" and len(result.scenes) == len(source.records)
    call.assert_not_awaited()


async def test_timeout_provider_failure_omission_and_cancellation(monkeypatch):
    source, prepared = materials()
    output = await writer.write_diary(
        source, prepared, AsyncMock(side_effect=ValueError("private"))
    )
    assert output.failure_code == "provider_failed"
    monkeypatch.setattr(writer, "TIMEOUT_SECONDS", 0.01)
    closed = []

    async def slow(*args):
        try:
            await asyncio.sleep(10)
        finally:
            closed.append(True)

    assert (await writer.write_diary(source, prepared, slow)).failure_code == "provider_failed"
    assert closed == [True]
    with pytest.raises(asyncio.CancelledError):
        await writer.write_diary(source, prepared, AsyncMock(side_effect=asyncio.CancelledError))
    raw = prose(prepare_writing(source, prepared).payload)
    raw["scenes"][0].update(text=None, evidence_ids=[])
    result = await writer.write_diary(source, prepared, AsyncMock(return_value=raw))
    assert result.model_status == "accepted" and result.scenes[0].narration.status == "omitted"
    monkeypatch.setattr(writer, "MAX_RESPONSE_BYTES", 1)
    assert (
        await writer.write_diary(source, prepared, AsyncMock(return_value=raw))
    ).failure_code == "invalid_response"


async def test_sdk_one_async_structured_call_has_no_retries_and_closes(monkeypatch):
    from google import genai
    from pydantic import SecretStr

    generate = AsyncMock(return_value=SimpleNamespace(text="{}"))
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate)
    )
    captured = []

    def construct(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(aio=client)

    monkeypatch.setattr(writer.settings, "gemini_api_key", SecretStr("synthetic-test-key"))
    monkeypatch.setattr(genai, "Client", construct)
    assert await writer.generate_background({"test": "fixture"}, {"type": "object"}) == "{}"
    assert captured[0]["http_options"].retry_options.attempts == 1
    assert captured[0]["http_options"].timeout == writer.TIMEOUT_SECONDS * 1000
    config = generate.call_args.kwargs["config"]
    assert config.max_output_tokens == writer.MAX_OUTPUT_TOKENS
    generate.assert_awaited_once()
    client.__aexit__.assert_awaited_once()
