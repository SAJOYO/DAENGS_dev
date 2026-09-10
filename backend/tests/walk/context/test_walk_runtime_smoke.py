"""The operational probe must clean only its newly created account, including failures."""

import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
