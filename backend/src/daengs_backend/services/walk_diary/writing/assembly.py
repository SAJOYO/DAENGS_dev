"""Freeze adopted card bodies and validate completed cards against prepared input."""

from daengs_backend.services.walk_diary.contracts import CardWritingResult
from daengs_backend.services.walk_diary.writing.jobs import action_job
from daengs_backend.services.walk_diary.writing.policy import writing_version
from daengs_walk.diary.board.action_context import require_scene_action
from daengs_walk.diary.board.activity import activity_fallback, covers_observation
from daengs_walk.diary.board.output import publish_board
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.contracts.narrative import (
    CardNarrative,
    CardPart,
    content_revision,
    observation_content,
)
from daengs_walk.diary.contracts.output import BackgroundPiece


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
    if action_result is not None:
        require_scene_action(scene, action_result.request)
    observation = observation_content(scene.core, scene.observation, modern=True)
    places = places_for(stamp, scene.place_reference)
    dong = next((p.facts.get("dong") for p in places if p.facts.get("dong")), None)
    default = (
        f"{dong}에 남긴 산책 기록이다."
        if dong
        else ("산책 중 기록한 장소다." if scene.anchor.point else "위치 정보가 없는 산책 기록이다.")
    )
    if action_result or (scene.user_record and scene.user_record.kind in {"note", "photo"}):
        default = ""
    generated = space_result.accepted if space_result else None
    space = CardPart(
        text=generated["text"].strip() if generated and generated["text"].strip() else default,
        origin="generated" if generated and generated["text"].strip() else "fallback",
    )
    actions = ()
    if action_result:
        record = action_result.request["action"]
        accepted = action_result.accepted
        fallback, fallback_refs = activity_fallback(action_result.request)
        actions = (
            CardPart(
                text=accepted["text"].strip() if accepted else fallback,
                origin="generated" if accepted else "fallback",
                action_id=record["id"] if record else None,
                actor_id=record["actor"]["id"] if record else None,
                movement_ids=tuple(accepted.get("movement_ids", ())) if accepted else fallback_refs,
            ),
        )
    original = (
        scene.body if scene.user_record and scene.user_record.kind in {"note", "photo"} else None
    )
    observation_in_activity = bool(
        action_result
        and actions
        and covers_observation(action_result.request, actions[0].movement_ids, scene.observation)
    )
    # APP's existing reader accepts 2400 characters. Never trim the user's original.
    prospective = [space.text, *(a.text for a in actions)]
    if observation and not observation_in_activity:
        prospective.append(observation.text)
    if original is not None:
        prospective.append(original)
    if len("\n".join(p for p in prospective if p)) > 2400:
        space = CardPart(text="", origin="fallback")
    revision = content_revision(
        scene.id,
        scene.anchor.model_dump(mode="json"),
        [p.model_dump(mode="json") for p in places],
        space.model_dump(mode="json"),
        [a.model_dump(mode="json") for a in actions],
        observation.model_dump(mode="json") if observation else None,
        original_text=original,
        modern=True,
        observation_in_activity=observation_in_activity,
    )
    narrative = CardNarrative(
        format="diary-card-narrative-v2",
        content_revision=revision,
        observation=observation,
        space=space,
        actions=actions,
        original_text=original,
        observation_in_activity=observation_in_activity,
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
    public = publish_board(base.board, base.plan, base.slots)
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
        if new.writing.observation != observation_content(old.core, old.observation, modern=True):
            raise ValueError("writer changed the confirmed observation")
        internal = next(s for s in base.board.scenes if s.id == old.id)
        stamp = next(s for s in base.slots.stamps if s.scene_id == old.id)
        expected_action = action_job(base, internal, stamp)
        actual_action = next(
            (j for j in value.jobs if j.stage == "action" and j.request["card_id"] == old.id), None
        )
        if (expected_action is None) != (actual_action is None) or (
            expected_action is not None
            and (
                expected_action.request != actual_action.request
                or expected_action.evidence != actual_action.evidence
            )
        ):
            raise ValueError("activity did not consume the frozen movement stamp")
        space_job_result = next(
            j for j in value.jobs if j.stage == "space" and j.request["card_id"] == old.id
        )
        rebuilt = frozen_card(old, stamp, space_job_result, actual_action)
        if rebuilt.writing.model_dump(exclude={"title_origin"}) != new.writing.model_dump(
            exclude={"title_origin"}
        ):
            raise ValueError("activity parts differ from their accepted jobs")
    return value.bundle
