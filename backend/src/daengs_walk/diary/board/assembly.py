"""Render a readable base board. Optional accepted prose never selects or relocates cards."""

from zoneinfo import ZoneInfo

from daengs_walk.diary.board.models import (
    BaseBoard,
    BoardScene,
    BoundaryCore,
    CheckpointCore,
    ObservationCore,
    PreparedBaseBoard,
    core_anchor,
)
from daengs_walk.diary.contracts.input import DiaryInput
from daengs_walk.diary.contracts.narrative import OBSERVATION_TEXT
from daengs_walk.diary.contracts.output import WritingReceipt
from daengs_walk.diary.selection.board import prepare_base_board


def _base_text(core):
    if isinstance(core, BoundaryCore):
        return (
            ("산책의 시작", "산책을 시작했다.")
            if core.boundary == "start"
            else (
                "산책의 마무리",
                "산책을 마쳤다.",
            )
        )
    if isinstance(core, CheckpointCore):
        return "산책길에서", "산책길의 이 지점을 지났다."
    if isinstance(core, ObservationCore):
        kind = core.observation.kind
        title = {
            "observed_dwell": "잠시 머문 구간",
            "observed_slow": "천천히 이어진 구간",
            "observed_fast": "빠르게 이어진 구간",
        }[kind]
        return title, OBSERVATION_TEXT[kind]
    content = core.record.content
    if content.kind == "note":
        return "남긴 이야기", content.text  # Includes the user's original whitespace/newlines.
    if content.kind == "photo":
        return "사진으로 남긴 순간", "사진을 남겼다."
    return {
        "sniffing": ("킁킁", "냄새를 맡았다."),
        "excretion": ("배변", "배변을 했다."),
        "barking": ("짖음", "짖었다."),
    }[content.code]


def assemble_base_board(source, prepared, *, route=None, receipt=None, failure_code=None):
    """All callers, including a future timeout publisher, get the same completed structure.

    The current provider/API still uses v1. A future writer can bind a WritingReceipt
    to this plan; only existing, exact-core background citations are accepted here.
    Checkpoint/background acquisition is a later integration, never a creation gate.
    """
    source = DiaryInput.model_validate(source.model_dump(mode="json"))
    prepared = PreparedBaseBoard.model_validate(prepared.model_dump(mode="json"))
    if prepared != prepare_base_board(source, prepared.policy, route=route):
        raise ValueError("base plan differs from the current source/policy/route")
    prose = {}
    if receipt is not None:
        receipt = WritingReceipt.model_validate(receipt.model_dump(mode="json"))
        if receipt.plan_revision != prepared.revision() or failure_code is not None:
            raise ValueError("writing belongs to a different base plan")
        writable = {
            s.id for s in prepared.stamps if any(p.kind != "place_reference" for p in s.background)
        }
        prose = {s.scene_id: s for s in receipt.writing.scenes}
        if not writable or set(prose) != writable or len(prose) != len(receipt.writing.scenes):
            raise ValueError("each writable scene must appear exactly once")
    scenes = []
    for order, stamp in enumerate(prepared.stamps, 1):
        title, body = _base_text(stamp.core)
        written = prose.get(stamp.id)
        if written:
            evidence = {p.id for p in stamp.background if p.kind != "place_reference"}
            if not set(written.evidence_ids) <= evidence:
                raise ValueError("cross-scene or unknown citation")
            if written.text:
                body = written.text + "\n" + body
        scenes.append(
            BoardScene(
                id=stamp.id,
                order=order,
                core_ref=stamp.core_ref,
                core=stamp.core,
                anchor=core_anchor(stamp.core),
                title=title,
                body=body,
            )
        )
    return BaseBoard(
        client_session_id=source.client_session_id,
        input_revision=source.revision(),
        plan_revision=prepared.revision(),
        title=receipt.writing.title
        if receipt
        else source.started_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d 산책"),
        model_status="accepted" if receipt else "unavailable" if failure_code else "not_requested",
        failure_code=failure_code,
        scenes=tuple(scenes),
    )
