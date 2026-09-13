"""Freeze the exact cited materials when publishing, without copying rejected candidates."""

from daengs_backend.services.walk_diary_board_slot_writing import complete_slot_board
from daengs_backend.services.walk_diary_slot_writing import SlotWritingResult, writing_version
from daengs_walk.diary_board_output import publish_board
from daengs_walk.diary_board_receipt import CitedEvidence, StoredSceneWriting, StoredSlotWriting
from daengs_walk.diary_input import digest
from daengs_walk.diary_scene_input import preserve_original


def writing_receipt(prepared, bundle, revision, output=None):
    from daengs_backend.services.walk_diary_card_receipt import StoredCardWriting
    from daengs_backend.services.walk_diary_card_writing import CardWritingResult
    from daengs_backend.services.walk_diary_card_writing import writing_version as card_version

    if isinstance(output, CardWritingResult):
        expected = complete_slot_board(prepared, output)
        if expected != bundle:
            raise ValueError("stored board differs from the adopted card result")
        receipt = StoredCardWriting(
            generation_revision=revision, writer=card_version(), result=output
        )
        receipt.require_bundle(bundle, revision)
        return receipt
    base = prepared.board
    if output is None:
        if bundle.model_status == "accepted":
            raise ValueError("accepted board requires its slot writing result")
        expected = publish_board(
            base.board.model_copy(
                update={
                    "model_status": bundle.model_status,
                    "failure_code": bundle.failure_code,
                }
            ),
            base.plan,
        )
        written = {}
    else:
        output = SlotWritingResult.model_validate(output)
        expected = complete_slot_board(prepared, output)
        written = {s.scene_id: s for s in output.writing.scenes} if output.writing else {}
    if expected != bundle:
        raise ValueError("stored board differs from the accepted writing result")
    originals = {s.id: s for s in base.board.scenes}
    scenes = []
    for stamp in base.slots.stamps:
        prose = written.get(stamp.scene_id)
        available = {e.id: e for e in stamp.materials()}
        scenes.append(
            StoredSceneWriting(
                scene_id=stamp.scene_id,
                original_body_sha256=digest(originals[stamp.scene_id].body),
                background=prose.text.strip() if prose else "",
                composition=(
                    "replace"
                    if prose
                    and prose.text.strip()
                    and not preserve_original(originals[stamp.scene_id])
                    else "prepend"
                ),
                action_id=prose.action_id if prose else None,
                evidence=tuple(
                    CitedEvidence(
                        id=available[ref].id,
                        part=available[ref].part,
                        role=available[ref].role,
                        facts=available[ref].facts,
                        sources=available[ref].sources,
                    )
                    for ref in (prose.evidence_ids if prose else ())
                ),
            )
        )
    writer = writing_version()
    receipt = StoredSlotWriting(
        generation_revision=revision,
        input_revision=bundle.input_revision,
        plan_revision=bundle.plan_revision,
        bundle_sha256=digest(bundle),
        slot_revision=base.slots.revision(),
        slot_policy=base.slots.policy.model_dump(mode="json"),
        writer=writer,
        writer_version=digest(writer),
        scenes=tuple(scenes),
    )
    receipt.require_bundle(bundle, revision)
    return receipt
