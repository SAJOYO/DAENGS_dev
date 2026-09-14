"""Freeze adopted card bodies and validate completed cards against prepared input."""

from daengs_backend.services.walk_diary.contracts import CardWritingResult
from daengs_backend.services.walk_diary.writing.policy import writing_version
from daengs_walk.diary_board_output import publish_board
from daengs_walk.diary_card_narrative import (
    CardNarrative,
    CardPart,
    content_revision,
    observation_content,
)
from daengs_walk.diary_input import digest
from daengs_walk.diary_output import BackgroundPiece


def places_for(stamp, previous):
    reference = stamp.location_reference
    if reference is None:
        return previous
    # Reuse the existing SGIS normalization. APP deliberately displays facts.dong only.
    return (
        BackgroundPiece(
            id=reference.id,
            background_id=reference.source_id,
            kind="place_reference",
            schema_version="sgis-dong-v1",
            facts=reference.facts,
        ),
    )


def frozen_card(scene, stamp, space_result, action_result):
    observation = observation_content(scene.core, scene.observation)
    places = places_for(stamp, scene.place_reference)
    dong = next((p.facts.get("dong") for p in places if p.facts.get("dong")), None)
    default = (
        f"{dong}에 남긴 산책 기록이다."
        if dong
        else ("산책 중 기록한 장소다." if scene.anchor.point else "위치 정보가 없는 산책 기록이다.")
    )
    generated = space_result.accepted if space_result else None
    space = CardPart(
        text=generated["text"].strip() if generated and generated["text"].strip() else default,
        origin="generated" if generated and generated["text"].strip() else "fallback",
    )
    actions = ()
    if action_result:
        record = action_result.request["action"]
        accepted = action_result.accepted
        actions = (
            CardPart(
                text=accepted["text"].strip()
                if accepted
                else f"{record['material']['무엇을']} 행동을 기록했다.",
                origin="generated" if accepted else "fallback",
                action_id=record["id"],
                actor_id=record["actor"]["id"],
            ),
        )
    original = (
        scene.body if scene.user_record and scene.user_record.kind in {"note", "photo"} else None
    )
    revision = content_revision(
        scene.id,
        scene.anchor.model_dump(mode="json"),
        [p.model_dump(mode="json") for p in places],
        space.model_dump(mode="json"),
        [a.model_dump(mode="json") for a in actions],
        observation.model_dump(mode="json") if observation else None,
    )
    narrative = CardNarrative(
        content_revision=revision,
        observation=observation,
        space=space,
        actions=actions,
        original_text=original,
        title_origin="fallback",
        title_based_on_content_revision=revision,
    )
    return scene.model_copy(
        update={"body": narrative.body(), "writing": narrative, "place_reference": places}
    )


def complete_cards(prepared, output):
    value = CardWritingResult.model_validate(
        output.model_dump(mode="json") if isinstance(output, CardWritingResult) else output
    )
    base = prepared.board
    if (
        value.input_revision != base.board.input_revision
        or value.plan_revision != base.plan.revision()
        or value.slot_revision != base.slots.revision()
        or value.writer_version != digest(writing_version())
    ):
        raise ValueError("card writing returned another snapshot")
    public = publish_board(base.board, base.plan)
    if [s.id for s in value.bundle.scenes] != [s.id for s in public.scenes]:
        raise ValueError("writer changed selected cards")
    for old, new in zip(public.scenes, value.bundle.scenes, strict=True):
        unchanged = {"title", "body", "writing", "place_reference"}
        if old.model_dump(exclude=unchanged) != new.model_dump(exclude=unchanged):
            raise ValueError("writer changed record identity")
        expected_original = (
            old.body if old.user_record and old.user_record.kind in {"note", "photo"} else None
        )
        if new.writing is None or new.writing.original_text != expected_original:
            raise ValueError("writer changed original text")
        if new.writing.observation != observation_content(old.core, old.observation):
            raise ValueError("writer changed the confirmed observation")
    return value.bundle
