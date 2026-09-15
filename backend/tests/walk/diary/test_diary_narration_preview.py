"""Saved narration attempts must not spend another model request."""

import gzip
import importlib
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("status", ["started", "accepted", "failed"])
@pytest.mark.parametrize("compressed", [False, True])
async def test_any_saved_attempt_is_reused_without_entering_the_provider(
    monkeypatch, tmp_path, status, compressed
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "tools"))
    preview = importlib.import_module("compare_diary_narration")
    from daengs_backend.services.walk_diary.writing import provider

    async def unexpected(*args, **kwargs):
        raise AssertionError("saved attempt reached provider")

    monkeypatch.setattr(provider, "generate_card_prose", unexpected)
    path = tmp_path / "attempt.json"
    stored = {"status": status}
    if compressed:
        path.with_suffix(".json.gz").write_bytes(gzip.compress(json.dumps(stored).encode()))
    else:
        preview.save(path, stored)
    assert await preview.attempt(None, path) == stored
