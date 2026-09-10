"""The operational probe must clean only its newly created account, including failures."""

import importlib.util
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkUpload
from daengs_backend.schemas.walk_entry_v2 import EntryWriteV2
from daengs_backend.services import walk_entry_pin
from daengs_backend.services.walk_chunk import encode_chunk
from tests.walk.support.paths import REPO


@pytest.mark.parametrize("failure", [None, "cycle", "insert"])
async def test_probe_cleanup_is_owned_and_errors_are_redacted(monkeypatch, capsys, failure):
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
    code = await module.main()
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
    engine.dispose.assert_awaited_once()


async def test_probe_entries_match_gps_after_storage_roundtrip(monkeypatch):
    """A real clock's submillisecond precision must not break GPS source verification."""
    spec = importlib.util.spec_from_file_location(
        "walk_smoke_contract_test", REPO / "tools/walk_runtime_smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 10, 2, 0, 0, 123456, tzinfo=UTC)

    class EntriesChecked(Exception):
        pass

    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(module, "create_access_token", lambda *args: "synthetic-token")
    chunks = []
    raw_chunks = AsyncMock(side_effect=lambda *args: chunks)
    monkeypatch.setattr(walk_entry_pin.repo, "raw_chunks", raw_chunks)
    walk_id = uuid.uuid4()
    checked = []

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
            return httpx.Response(200, json={"id": request.url.path.split("/")[-1], "revision": 1})
        assert request.url.path.endswith("/contexts")
        raise EntriesChecked

    client = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(EntriesChecked):
        await module.cycle(uuid.uuid4())
    assert checked == ["behavior", "note", "note"]
    assert raw_chunks.await_count == 3
