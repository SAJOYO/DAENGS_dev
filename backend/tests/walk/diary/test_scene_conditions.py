"""Actual preparation -> publication -> receipt read; no live LLM or database."""

from types import SimpleNamespace

import pytest

from daengs_backend.services.walk_diary.lifecycle.publication import fallback
from daengs_backend.services.walk_diary.preparation.board import assemble_saved_base_board
from daengs_backend.services.walk_diary.preparation.input import InputAssembly
from daengs_backend.services.walk_diary.preparation.observations import ObservationSource
from daengs_backend.services.walk_diary.runtime import write_cards
from daengs_backend.services.walk_diary.storage.board import load_board, store_board
from daengs_walk.diary.board.models import BaseBoardPolicy
from daengs_walk.diary.board.output import PublishedBoard, publish_board
from daengs_walk.diary.contracts.input import DiaryInput, digest
from daengs_walk.diary.contracts.slots import SlotPolicy
from daengs_walk.diary.selection.stamps import StampPolicy
from tests.walk.diary.test_diary_card_writing import prose
from tests.walk.diary.test_diary_temperature import input_with_temperature


def prepared():
    raw, route, _ = input_with_temperature()
    source = DiaryInput.model_validate(raw)
    assembly = InputAssembly(source, (), ObservationSource(route.version, evidence=route.evidence))
    return assemble_saved_base_board(
        assembly,
        BaseBoardPolicy(intermediate=StampPolicy(target_scene_count=3)),
        slot_policy=SlotPolicy(environment_slots=0),
    )


def test_temperature_is_independent_of_prose_capacity_and_scene_bound():
    base = prepared()
    public = publish_board(base.board, base.plan, base.slots)
    cards = [s for s in public.scenes if s.temperature]
    assert len(cards) == 1
    assert cards[0].temperature.temperature_c == 22.5
    assert cards[0].temperature.scene_at == cards[0].anchor.event_at
    assert not any(e.part == "environment" for s in base.slots.stamps for e in s.evidence)
    assert PublishedBoard.model_validate_json(public.model_dump_json()) == public
    raw = public.model_dump(mode="json")
    target = next(s for s in raw["scenes"] if "temperature" in s)
    target["temperature"]["scene_at"] = "2000-01-01T00:00:00Z"
    with pytest.raises(ValueError):
        PublishedBoard.model_validate(raw)


async def test_written_and_failed_publications_preserve_temperature_in_saved_receipt():
    base = prepared()
    wrapper = SimpleNamespace(input=base.input, board=base)
    result = await write_cards(base.input.source, base, generate=prose)
    stored = store_board(wrapper, result.bundle, "a" * 64, writing=result)
    loaded = load_board(stored)
    assert [s.temperature for s in loaded.bundle.scenes] == [
        s.temperature for s in result.bundle.scenes
    ]
    assert any(s.temperature for s in loaded.bundle.scenes)
    failed = load_board(fallback(wrapper, "b" * 64))
    assert [s.temperature for s in failed.bundle.scenes] == [
        s.temperature for s in loaded.bundle.scenes
    ]
    stored["bundle"]["scenes"][1]["title"] = "tampered"
    with pytest.raises(ValueError):
        load_board(stored)


def test_legacy_publication_bytes_do_not_grow_a_null_field():
    base = prepared()
    old = publish_board(base.board, base.plan)
    assert all("temperature" not in s for s in old.model_dump(mode="json")["scenes"])
    assert digest(PublishedBoard.model_validate(old.model_dump(mode="json"))) == digest(old)
