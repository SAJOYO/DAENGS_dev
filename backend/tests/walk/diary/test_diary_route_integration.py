"""Route patterns reach the existing HTTP publication and survive persisted reuse."""

import json
import sys

import pytest

from daengs_backend.config import settings
from daengs_walk.diary_route_normalize import main
from daengs_walk.diary_scene_input import scene_materials
from tests.walk.support.diary_generation import PATH, body
from tests.walk.support.observations import stored, uploaded
from tests.walk.support.route_patterns import scenarios


def test_enabled_patterns_reach_writer_storage_and_reopen_without_regeneration(api, monkeypatch):
    client, state, _ = api
    state.walk, state.analysis, _ = stored(uploaded([(i * 10, i * 20) for i in range(30)]))
    monkeypatch.setattr(settings, "walk_diary_route_patterns_enabled", True)
    request = body(state, bundle_format="walk-diary-board-v1")
    response = client.post(PATH, json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "ready" and result["bundle"]["model_status"] == "accepted"
    receipt = state.row.bundle["writing_receipt"]
    patterns = [
        e
        for s in receipt["scenes"]
        for e in s["evidence"]
        if e["facts"].get("format") == "route-pattern-material-v1"
    ]
    assert patterns and all(e["facts"]["case_id"] == "straight_run" for e in patterns)
    payload = state.provider.call_args.args[0]
    projected = [
        e["facts"]
        for s in payload["scenes"]
        for e in scene_materials(s)
        if "경로 형태" in e["facts"].get("material", {})
    ]
    assert projected and all(
        set(f) == {"material", "relation", "subject", "action_meaning"} for f in projected
    )
    query = "?bundle_format=walk-diary-board-v1&target_scene_count=3"
    assert client.get(PATH + query).json() == result
    assert client.post(PATH, json=request).json() == result
    monkeypatch.setattr(settings, "walk_diary_route_patterns_enabled", False)
    changed_settings = result | {"background_update_available": True}
    assert client.get(PATH + query).json() == changed_settings
    assert client.post(PATH, json=request).json() == changed_settings
    state.provider.assert_awaited_once()


def test_enabling_patterns_does_not_replace_previously_published_diary(api, monkeypatch):
    client, state, _ = api
    monkeypatch.setattr(settings, "walk_diary_route_patterns_enabled", False)
    request = body(state, bundle_format="walk-diary-board-v1")
    response = client.post(PATH, json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    monkeypatch.setattr(settings, "walk_diary_route_patterns_enabled", True)
    query = "?bundle_format=walk-diary-board-v1&target_scene_count=3"
    changed_settings = result | {"background_update_available": True}
    assert client.get(PATH + query).json() == changed_settings
    assert client.post(PATH, json=request).json() == changed_settings
    state.provider.assert_awaited_once()


def test_cli_reads_relative_points_and_refuses_to_overwrite_result(tmp_path, monkeypatch):
    raw = scenarios()["out_back"]["source"].model_dump(mode="json")
    points = tmp_path / "points.json"
    points.write_text(json.dumps(raw.pop("points")), encoding="utf-8")
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps(raw | {"points": points.name}), encoding="utf-8")
    output = tmp_path / "normalized.json"
    monkeypatch.setattr(
        sys, "argv", ["normalize", "--input", str(manifest), "--output", str(output)]
    )
    main()
    first = output.read_bytes()
    assert "retrace" in {m["case_id"] for m in json.loads(first)["materials"]}
    with pytest.raises(FileExistsError):
        main()
    assert output.read_bytes() == first
