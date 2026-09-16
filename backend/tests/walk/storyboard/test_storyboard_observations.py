import json
import uuid
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services.walk_legacy.titles import title_input, title_storyboard
from daengs_walk import analyze_walk
from daengs_walk.contracts import WalkEvidencePoint
from daengs_walk.storyboard import StoryboardBundleV4, build_storyboard, compatible_bundle
from daengs_walk.storyboard_input import scene_inputs
from tests.walk.support.paths import WALK_FIXTURES
from tests.walk.support.storyboard import headings


@pytest.fixture
def recorded():
    return json.loads((WALK_FIXTURES / "v4-observations.json").read_text(encoding="utf-8"))


def reconstruct(recorded):
    start, end = (datetime.fromisoformat(recorded[k]) for k in ("started_at", "ended_at"))
    points = [WalkEvidencePoint.model_validate(p) for p in recorded["observations"]]
    evidence = analyze_walk(uuid.UUID(int=1), start, end, points)
    entries, selection = scene_inputs(evidence, [], session_id="anchor-fixture")
    return build_storyboard(
        "anchor-fixture",
        start,
        end,
        evidence.facts.moving_distance_m,
        entries,
        selection,
        {},
        evidence.gaps,
        include_observations=True,
    )


def test_shared_app_fixture_comes_from_real_measurement_and_selection(recorded):
    bundle = reconstruct(recorded)
    assert bundle.model_dump(mode="json") == recorded["bundle"]
    fixes = {p["client_seq"]: p for p in recorded["observations"]}
    located = [s for s in bundle.scenes if s.observation]
    assert len(located) >= 4
    for scene in located:
        anchor = scene.observation.model_dump(mode="json")
        fix = fixes[anchor["client_seq"]]
        assert anchor == {k: fix[k] for k in anchor}
    start, end = bundle.scenes[0].observation, bundle.scenes[-1].observation
    assert start.lat == end.lat and start.lng == end.lng
    assert start.client_seq != end.client_seq and start.chain_index != end.chain_index
    gaps = [s for s in bundle.scenes if "observation_gap" in s.reasons]
    assert gaps and all(s.observation is None for s in gaps)


@pytest.mark.parametrize("version", ["v1", "v2", "v3"])
def test_legacy_representation_strips_new_scene_field(recorded, version):
    value = compatible_bundle(recorded["bundle"], "walk-storyboard-candidates-" + version)
    assert value.format.endswith(version)
    assert all("observation" not in s for s in value.model_dump(mode="json")["scenes"])


@pytest.mark.parametrize("invalid", ["time", "coordinate", "sequence", "gap"])
def test_invalid_observation_contract_is_rejected(recorded, invalid):
    value = recorded["bundle"]
    scene = next(s for s in value["scenes"] if s["route"] and s["observation"])
    if invalid == "time":
        scene["observation"]["at"] = recorded["started_at"]
    elif invalid == "coordinate":
        scene["observation"]["lat"] = 91
    elif invalid == "sequence":
        scene["observation"]["client_seq"] = -1
    else:
        gap = next(s for s in value["scenes"] if "observation_gap" in s["reasons"])
        gap["observation"] = scene["observation"]
    with pytest.raises(ValueError):
        StoryboardBundleV4.model_validate(value)


async def test_title_generation_preserves_anchors_without_sending_them_to_llm(recorded):
    bundle = StoryboardBundleV4.model_validate(recorded["bundle"])
    generate = AsyncMock(side_effect=headings)
    titled = await title_storyboard(bundle, generate)
    assert isinstance(titled, StoryboardBundleV4) and titled.title
    assert [s.observation for s in titled.scenes] == [s.observation for s in bundle.scenes]
    assert all(set(s) == {"id", "title", "facts"} for s in title_input(bundle)["scenes"])
    failed = await title_storyboard(bundle, AsyncMock(side_effect=ValueError("offline")))
    assert isinstance(failed, StoryboardBundleV4) and failed.scenes == bundle.scenes
