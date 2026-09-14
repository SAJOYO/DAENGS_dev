"""Readable one-body cards, text-only writing and opt-in saved service boundary."""

from unittest.mock import AsyncMock

import pytest

from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.services.walk_diary.preparation import board as service
from daengs_backend.services.walk_diary.preparation.input import InputAssembly
from daengs_walk.diary.board.assembly import assemble_base_board
from daengs_walk.diary.board.models import BaseBoard
from daengs_walk.diary.contracts.input import DiaryInput
from daengs_walk.diary.contracts.output import WritingReceipt, assemble_diary
from daengs_walk.diary.selection.board import prepare_base_board
from daengs_walk.diary.selection.stamps import prepare_stamps
from tests.walk.support.base_board import policy, saved_case
from tests.walk.support.diary import nearby, record, source, with_backgrounds
from tests.walk.support.photo_input import OWNER, WALK


def grounded():
    note = record()
    snapshot = with_backgrounds(note, backgrounds=[nearby(note)])
    plan = prepare_base_board(snapshot, policy())
    stamp = plan.stamps[1]
    receipt = WritingReceipt(
        plan_revision=plan.revision(),
        writing={
            "title": "산책길에 남긴 기록",
            "scenes": [
                {
                    "scene_id": stamp.id,
                    "text": "가까이에 등록된 카페가 있었다.",
                    "evidence_ids": [stamp.background[0].id],
                }
            ],
        },
    )
    return snapshot, plan, receipt


def structure(board):
    return [(s.id, s.order, s.core_ref, s.core, s.anchor) for s in board.scenes]


@pytest.mark.parametrize(
    "failure", [None, "provider_failed", "invalid_response", "interrupted", "budget_exceeded"]
)
def test_no_ai_or_failed_writing_keeps_all_readable_cards(failure):
    assembled, route, _ = saved_case()
    plan = prepare_base_board(assembled.source, policy(), route=route)
    base = assemble_base_board(assembled.source, plan, route=route)
    result = assemble_base_board(assembled.source, plan, route=route, failure_code=failure)
    assert result.scenes == base.scenes and len(result.scenes) == 7
    assert all(s.title.strip() and s.body.strip() for s in result.scenes)
    assert result.failure_code == failure
    assert BaseBoard.model_validate_json(result.model_dump_json()) == result


def test_accepted_background_prose_changes_only_text_and_preserves_original_note():
    snapshot, plan, receipt = grounded()
    before = snapshot.model_dump(mode="json")
    base = assemble_base_board(snapshot, plan)
    enhanced = assemble_base_board(snapshot, plan, receipt=receipt)
    assert structure(base) == structure(enhanced)
    assert (
        enhanced.scenes[1].body
        == receipt.writing.scenes[0].text + "\n" + snapshot.records[0].content.text
    )
    assert enhanced.scenes[0] == base.scenes[0] and enhanced.scenes[-1] == base.scenes[-1]
    assert snapshot.model_dump(mode="json") == before
    assert enhanced.model_status == "accepted"


@pytest.mark.parametrize("change", ["plan", "scene", "citation", "duplicate", "failure"])
def test_writer_cannot_add_cards_borrow_citations_or_write_to_a_stale_plan(change):
    snapshot, plan, receipt = grounded()
    raw = receipt.model_dump(mode="json")
    kwargs = {}
    if change == "plan":
        raw["plan_revision"] = "b" * 64
    elif change == "scene":
        raw["writing"]["scenes"][0]["scene_id"] = plan.stamps[0].id
    elif change == "citation":
        raw["writing"]["scenes"][0]["evidence_ids"] = ["another-scene-background"]
    elif change == "duplicate":
        raw["writing"]["scenes"] *= 2
    else:
        kwargs["failure_code"] = "provider_failed"
    with pytest.raises(ValueError):
        assemble_base_board(snapshot, plan, receipt=WritingReceipt.model_validate(raw), **kwargs)


def test_base_policy_never_changes_v1_input_revision_selection_or_output():
    assembled, route, _ = saved_case()
    snapshot = assembled.source
    revision = snapshot.revision()
    old_plan = prepare_stamps(snapshot, policy().intermediate)
    old_bundle = assemble_diary(snapshot, old_plan.plan, None)
    for selected_policy in [policy(1), policy(5), policy(10, separation_m=120)]:
        plan = prepare_base_board(snapshot, selected_policy, route=route)
        assemble_base_board(snapshot, plan, route=route)
    assert snapshot.revision() == revision
    assert prepare_stamps(snapshot, policy().intermediate) == old_plan
    assert assemble_diary(snapshot, old_plan.plan, None) == old_bundle
    assert old_bundle.format == "walk-diary-bundle-v1" and old_bundle.scenes == ()


def test_missing_canonical_material_is_explicit_and_never_claims_route_points():
    assembled, _, _ = saved_case()
    plan = prepare_base_board(assembled.source, policy())
    assert len(plan.stamps) == 2
    assert all(s.core.anchor.point is None for s in plan.stamps)
    assert "canonical_route_not_supplied" in plan.limits


def test_tombstone_is_not_converted_into_a_checkpoint_or_fake_action():
    note = record().model_copy(update={"deleted": True, "content": None, "anchor": None})
    plan = prepare_base_board(source(note), policy())
    assert len(plan.stamps) == 2 and plan.counts["user_records"] == 0


async def test_saved_service_checks_owner_and_reuses_existing_replay_without_writes(monkeypatch):
    assembled, _, _ = saved_case()
    reader = AsyncMock(return_value=assembled)
    monkeypatch.setattr(service, "read_input", reader)
    session = object()  # No add/commit/provider methods.
    with pytest.raises(PermissionError):
        await service.prepare_saved_base_board(
            session, PrincipalContext(kind="ADMIN", subject="admin"), WALK, policy()
        )
    reader.assert_not_called()
    principal = PrincipalContext(kind="APP_USER", subject=str(OWNER))
    result = await service.prepare_saved_base_board(session, principal, WALK, policy())
    reader.assert_awaited_once_with(session, OWNER, WALK)
    assert result.plan.counts["checkpoints"] == 5
    assert result.input.observation_source.evidence is assembled.observation_source.evidence
    wrong = DiaryInput.model_validate(
        {**assembled.source.model_dump(mode="json"), "owner_id": "someone-else"}
    )
    reader.return_value = InputAssembly(wrong, ())
    with pytest.raises(PermissionError):
        await service.prepare_saved_base_board(session, principal, WALK, policy())
