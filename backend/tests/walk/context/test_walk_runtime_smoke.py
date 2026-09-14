"""The operational probe must clean only its newly created account, including failures."""

import importlib.util
import json
import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkUpload
from daengs_backend.schemas.walk_entry_v2 import EntryWriteV2
from daengs_backend.services.walk_diary.lifecycle.snapshot import result as publication_result
from daengs_backend.services.walk_diary.preparation.board import assemble_saved_base_board
from daengs_backend.services.walk_diary.preparation.diary import PreparedWalkDiary
from daengs_backend.services.walk_diary.runtime import write_cards
from daengs_backend.services.walk_diary.storage.board import store_board
from daengs_backend.services.walk_records import pins as walk_entry_pin
from daengs_backend.services.walk_session.chunk import encode_chunk
from daengs_walk.diary.contracts.input import DiaryInput, digest
from tests.walk.diary.test_diary_card_writing import prepared, prose
from tests.walk.support.base_board import policy
from tests.walk.support.paths import REPO


async def test_card_probe_reads_current_receipt_through_packaged_storage(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "walk_card_smoke_test", REPO / "tools/walk_runtime_smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = prepared()
    source = base.input.source.model_dump(mode="json")
    source["client_session_id"] = str(uuid.uuid4())
    note = deepcopy(source["records"][0])
    note["ref"]["id"] = "probe-note"
    note["content"] = {"kind": "note", "text": "  점검용 원문\n"}
    source["records"].append(note)
    base = assemble_saved_base_board(
        replace(base.input, source=DiaryInput.model_validate(source)), policy(3)
    )
    output = await write_cards(base.input.source, base, generate=prose)
    assert any(
        a.action_id is None and a.movement_ids
        for scene in output.bundle.scenes
        for a in scene.writing.actions
    )
    value = PreparedWalkDiary(base.input, base.plan.intermediate, base)
    revision = digest("probe-generation")
    stored = store_board(value, output.bundle, revision, writing=output)
    row = SimpleNamespace(
        bundle=stored, input_revision=revision, status="ready", generation=1, error_code=None
    )
    response = publication_result(value, row, revision).model_dump(mode="json")
    connection = AsyncMock()
    connection.scalar.return_value = stored
    context = AsyncMock()
    context.__aenter__.return_value = connection
    monkeypatch.setattr(module, "engine", SimpleNamespace(connect=lambda: context))
    request = AsyncMock(return_value=response)
    notes = [note["content"]["text"]]
    owner, walk = uuid.uuid4(), uuid.uuid4()
    result = await module.card_publication(request, owner, walk, [], notes)
    assert result["receipt_valid"] and result["original_action_preserved"]
    assert result["original_notes_preserved"] and result["model_status"] == "accepted"
    assert result["synthetic_response"] == response
    assert [call.args[0] for call in request.await_args_list] == ["POST", "GET", "POST"]
    assert connection.scalar.await_args.args[1] == {"walk": walk, "owner": owner}


@pytest.mark.parametrize("failure", [None, "cycle", "insert"])
@pytest.mark.parametrize("backfill", [False, True])
async def test_probe_cleanup_is_owned_and_errors_are_redacted(
    monkeypatch, capsys, failure, backfill
):
    spec = importlib.util.spec_from_file_location(
        "walk_smoke_test", REPO / "tools/walk_runtime_smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connection = AsyncMock()
    connection.execute.return_value = SimpleNamespace(rowcount=1)
    transaction = AsyncMock()
    transaction.__aenter__.return_value = connection
    engine = SimpleNamespace(begin=lambda: transaction, dispose=AsyncMock())
    monkeypatch.setattr(module, "engine", engine)
    run = AsyncMock(return_value={"same_readback": True})
    monkeypatch.setattr(module, "cycle", run)
    if failure == "cycle":
        run.side_effect = RuntimeError("private-connection-value")
    elif failure == "insert":
        connection.execute.side_effect = RuntimeError("private-connection-value")
    code = await module.main(backfill=backfill)
    output = capsys.readouterr().out
    report = json.loads(output)
    assert "private-connection-value" not in output
    assert report["ok"] is (failure is None)
    assert (code == 0) is (failure is None)
    calls = connection.execute.call_args_list
    inserted = calls[0].args[1]
    assert inserted["kakao"] < 0
    assert str(inserted["id"]) not in output
    if failure == "insert":
        assert len(calls) == 1
        run.assert_not_called()
        assert not report["cleaned"]
    else:
        assert report["cleaned"]
        assert str(calls[1].args[0]) == "DELETE FROM app_users WHERE id=:id AND kakao_id=:kakao"
        assert calls[1].args[1] == inserted
        if backfill:
            assert run.call_args.kwargs == {"backfill": True}
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("note_change", [None, "missing", "changed", "wrong_region", "backfill"])
async def test_probe_entries_match_gps_and_diary_source_contract(monkeypatch, note_change):
    """A real clock's submillisecond precision must not break GPS source verification."""
    spec = importlib.util.spec_from_file_location(
        "walk_smoke_contract_test", REPO / "tools/walk_runtime_smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recovery = AsyncMock(return_value={"previous": "saved"})
    verify = AsyncMock(return_value={"prior_envelopes_preserved": True})
    monkeypatch.setattr(module, "prepare_backfill", recovery)
    monkeypatch.setattr(module, "verify_backfill", verify)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 10, 2, 0, 0, 123456, tzinfo=UTC)

    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(module, "create_access_token", lambda *args: "synthetic-token")
    chunks = []
    raw_chunks = AsyncMock(side_effect=lambda *args: chunks)
    monkeypatch.setattr(walk_entry_pin.repo, "raw_chunks", raw_chunks)
    walk_id = uuid.uuid4()
    checked = []
    scenes = []
    diary = {
        "status": "ready",
        "generation": 1,
        "bundle": {"model_status": "accepted", "scenes": scenes},
    }

    async def respond(request):
        if request.method == "POST" and request.url.path == "/app/walks":
            upload = WalkUpload.model_validate_json(request.content)
            chunks.append(SimpleNamespace(payload=encode_chunk(upload.points)))
            return httpx.Response(200, json={"id": str(walk_id)})
        if request.url.path.endswith("/finalize"):
            WalkFinalizeRequest.model_validate_json(request.content)
            return httpx.Response(200, json={})
        if request.method == "PUT":
            entry = EntryWriteV2.model_validate_json(request.content)
            walk_entry_pin.validate_new_pin(entry.content, entry.pin)
            await walk_entry_pin.validate_sources(None, walk_id, entry.content, entry.pin)
            checked.append(entry.content.kind)
            entry_id = request.url.path.split("/")[-1]
            record = {"kind": entry.content.kind}
            record.update({"code": "sniffing"} if entry.pin else {"text": entry.content.note})
            if entry.content.kind == "note" and note_change == "changed":
                record["text"] = "modified"
            if entry.content.kind != "note" or note_change != "missing":
                scenes.append(
                    {
                        "core": {"identity": "walk_entry:" + entry_id},
                        "user_record": record,
                        "place_reference": [
                            {"schema_version": "sgis-dong-v1", "facts": {"dong": "도곡동"}}
                        ],
                        # Background prose deliberately does not duplicate the user's note.
                        "narration": {
                            "status": "generated",
                            "text": "등록된 상가들이 가까이 있었다.",
                        },
                    }
                )
            return httpx.Response(200, json={"id": entry_id, "revision": 1})
        if request.url.path.endswith("/contexts"):
            return httpx.Response(
                200,
                json={
                    "sources": [
                        {
                            "tag": tag,
                            "state": "completed",
                            "envelope": {
                                "status": "known",
                                "payload": {
                                    "catalog_area": {
                                        "center": {"lat": 0, "lng": 0}
                                        if note_change == "wrong_region"
                                        else {"lat": 37.4878, "lng": 127.052}
                                    }
                                },
                            },
                        }
                        for tag in (
                            "space.address",
                            "space.park",
                            "space.commerce",
                            "space.river",
                            "movement",
                            "place",
                        )
                    ]
                },
            )
        assert request.url.path.endswith("/storyboard")
        return httpx.Response(200, json=diary)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    if note_change and note_change != "backfill":
        reason = (
            "managed regional catalog"
            if note_change == "wrong_region"
            else "original note missing or changed"
        )
        with pytest.raises(module.SmokeFailure, match=reason):
            await module.cycle(uuid.uuid4(), require_regional=True)
    else:
        result = await module.cycle(
            uuid.uuid4(), require_regional=True, backfill=note_change == "backfill"
        )
        assert result["managed_region_verified"]
        assert result["user_notes_preserved"] and result["user_action_preserved"]
        assert result["same_readback"] and result["addressed_scene_count"] == 3
        assert result["generated_background_count"] == 3
        if note_change == "backfill":
            recovery.assert_awaited_once()
            verify.assert_awaited_once()
            assert verify.call_args.args[2] == recovery.return_value
            assert result["backfill"]["prior_envelopes_preserved"]
    assert checked == ["behavior", "note", "note"]
    assert raw_chunks.await_count == 3
