"""Local request instants preserve source facts and historical projections."""

from copy import deepcopy
from datetime import datetime

import pytest

from daengs_walk.diary.relational.writer_time import local_writer_times
from daengs_walk.diary.relational.writer_view import publication_writer_view, writer_view
from daengs_walk.diary.relational.writing_brief import build_space_brief
from tests.walk.diary.test_writing_brief import case, context, snapshot


def test_all_nested_request_instants_share_local_time_without_mutating_source():
    instant = "2026-09-08T18:30:00+00:00"
    source = {
        "walk": {"started_at": instant},
        "required_event": {"anchor": {"event_at": instant}},
        "route": {"gaps": [{"start": instant, "end": instant}]},
        "journey_relations": [{"support_ended_at": instant, "distance_profile": [{"at": instant}]}],
        "delivery_memory": [{"anchors": [{"position": {"recorded_at": instant}}]}],
        "material_time": {"as_of": "2026-09-08", "measured_at": instant},
        "text": instant, "id": instant, "seconds_since_start": 15,
    }
    before = deepcopy(source)
    view = local_writer_times(source)
    assert source == before
    assert view["walk"]["started_at"] == "2026-09-09T03:30:00+09:00"
    assert view["required_event"]["anchor"]["event_at"].endswith("+09:00")
    assert view["route"]["gaps"][0]["start"].endswith("+09:00")
    assert view["journey_relations"][0]["distance_profile"][0]["at"].endswith("+09:00")
    assert view["delivery_memory"][0]["anchors"][0]["position"]["recorded_at"].endswith("+09:00")
    assert view["material_time"]["as_of"] == source["material_time"]["as_of"]
    assert view["text"] == view["id"] == instant
    assert view["seconds_since_start"] == 15
    assert local_writer_times(view) == view


def test_naive_instant_does_not_silently_use_host_timezone():
    with pytest.raises(ValueError, match="offset"):
        local_writer_times({"recorded_at": "2026-09-08T06:00:00"})


def test_current_request_and_historical_v4_have_same_instant_and_citations():
    walk, scenes, positions = case()
    a = snapshot(scenes[0], walk)
    brief = build_space_brief(context(None, a, positions))
    before = brief.model_dump_json()
    old = publication_writer_view(brief, "single-writing-brief-v4")
    new = writer_view(brief)
    assert "timezone" not in old
    assert new["timezone"] == "Asia/Seoul"
    assert datetime.fromisoformat(old["walk"]["started_at"]) == datetime.fromisoformat(new["walk"]["started_at"])
    assert old["citation_ids"] == new["citation_ids"]
    assert brief.model_dump_json() == before


def test_title_local_time_preserves_v2_request_and_midnight_date():
    from daengs_walk.diary.relational.title_context import TitleReadModel, TitleScene
    from daengs_walk.diary.relational.title_writer_view import (
        title_publication_view,
        title_writer_view,
    )

    source = TitleReadModel(scenes=(TitleScene(
        scene_id="one", order=1, recorded_at=datetime.fromisoformat("2026-09-08T18:30:00+00:00"),
        space="길을 걸었다.", action=None, movement_observations=(),
    ),))
    old = title_publication_view(source, "adopted-prose-title-v2")
    new = title_writer_view(source)
    assert old["scenes"][0]["recorded_at"] == "2026-09-08T18:30:00+00:00"
    assert "timezone" not in old
    assert new["scenes"][0]["recorded_at"] == "2026-09-09T03:30:00+09:00"
    assert old["scenes"][0]["space"] == new["scenes"][0]["space"]
