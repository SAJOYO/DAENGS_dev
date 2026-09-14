"""Fixed source/provider cases captured before the writing-boundary refactor (#507)."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from daengs_backend.config import settings
from daengs_backend.services import walk_sgis
from daengs_backend.services.walk_diary import contracts as diary_contracts
from daengs_backend.services.walk_diary import runtime as writing
from daengs_backend.services.walk_diary.collection import service as collection
from daengs_backend.services.walk_diary.legacy.board_slots import (
    complete_slot_board,
    write_legacy_slot_board,
)
from daengs_backend.services.walk_diary.lifecycle.snapshot import generation_revision, result
from daengs_backend.services.walk_diary.preparation.board import (
    assemble_saved_base_board,
    with_scene_backgrounds,
)
from daengs_backend.services.walk_diary.storage.board import (
    LegacyStoredBoard,
    StoredBoard,
    store_board,
)
from daengs_backend.services.walk_diary.storage.card_receipt import StoredCardWriting
from daengs_backend.services.walk_diary.writing import policy as diary_policy
from daengs_backend.services.walk_sgis import SgisSource
from daengs_walk.diary_board_output import BOARD_FORMAT, publish_board
from daengs_walk.diary_input import digest
from tests.walk.diary.test_diary_board_slot_writing import prepared_case
from tests.walk.diary.test_diary_board_slot_writing import prose as slot_prose
from tests.walk.diary.test_diary_card_writing import collect_with_sgis, prose
from tests.walk.support.base_board import policy


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 9, 3, tzinfo=UTC)


async def fixed_cases(monkeypatch):
    monkeypatch.setattr(collection, "datetime", FixedDatetime)
    monkeypatch.setattr(walk_sgis, "datetime", FixedDatetime)
    monkeypatch.setattr(collection, "sgis", SgisSource())
    monkeypatch.setattr(settings, "walk_diary_route_patterns_enabled", False)
    prepared = prepared_case()
    assembled = replace(
        prepared.input,
        source=prepared.input.source.model_copy(
            update={"client_session_id": "00000000-0000-0000-0000-000000000507"}
        ),
    )
    base = assemble_saved_base_board(assembled, policy(3), slot_policy=prepared.board.slots.policy)
    prepared = replace(prepared, input=assembled, prepared=base.plan.intermediate, board=base)
    revision = generation_revision(prepared, BOARD_FORMAT)
    cases = {}

    def capture(name, bound, bundle, output=None):
        value = replace(prepared, board=bound)
        stored = store_board(value, bundle, revision, writing=output)
        row = SimpleNamespace(
            bundle=stored, input_revision=revision, status="ready", generation=1, error_code=None
        )
        cases[name] = {
            "public": bundle.model_dump(mode="json"),
            "stored": stored,
            "response": result(value, row, revision).model_dump(mode="json"),
        }

    accepted = await writing.write_cards(
        base.input.source, base, generate=prose, collector=collect_with_sgis
    )
    bound = with_scene_backgrounds(base, accepted.scene_backgrounds)
    capture("card", bound, accepted.bundle, accepted)
    cached = replace(bound, cached_jobs=tuple(j.model_dump(mode="json") for j in accepted.jobs))
    provider = AsyncMock(side_effect=AssertionError("cached jobs must not call a provider"))
    reused = await writing.write_cards(base.input.source, cached, generate=provider)
    provider.assert_not_awaited()
    capture("cached", cached, reused.bundle, reused)
    failed = await writing.write_cards(
        base.input.source, base, generate=AsyncMock(side_effect=OSError("synthetic outage"))
    )
    capture("provider_failed", base, failed.bundle, failed)
    public = publish_board(base.board, base.plan)
    capture("base", base, public)
    legacy = await write_legacy_slot_board(
        base.input.source, base, AsyncMock(side_effect=lambda payload, schema: slot_prose(payload))
    )
    capture("legacy_slot", base, complete_slot_board(prepared, legacy), legacy)
    old = cases["base"]["stored"]
    cases["legacy_v1"] = {
        "stored": LegacyStoredBoard(
            **{key: old[key] for key in LegacyStoredBoard.model_fields if key != "format"}
        ).model_dump(mode="json")
    }
    models = (
        diary_contracts.SpaceProse,
        diary_contracts.ActionProse,
        diary_contracts.CardTitle,
        diary_contracts.CardTitles,
        diary_contracts.WritingJob,
        diary_contracts.CardWritingResult,
        StoredCardWriting,
        LegacyStoredBoard,
        StoredBoard,
    )
    return {
        "source": prepared.input.source.model_dump(mode="json"),
        "policy": diary_policy.writing_version(),
        "schemas": {model.__name__: model.model_json_schema() for model in models},
        "cases": cases,
    }


def fingerprints(value):
    return {
        "policy": digest(value["policy"]),
        "schemas": {name: digest(schema) for name, schema in value["schemas"].items()},
        "cases": {
            name: {kind: digest(raw) for kind, raw in case.items()}
            for name, case in value["cases"].items()
        },
    }
