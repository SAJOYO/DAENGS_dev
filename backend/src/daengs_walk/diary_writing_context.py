"""Small writing context from fixed cores; raw GPS and private pin metadata stay out."""

from zoneinfo import ZoneInfo

from daengs_walk.diary_board import BoundaryCore, CheckpointCore, ObservationCore, RecordCore

DISPLAY_ZONE = ZoneInfo("Asia/Seoul")


def writing_scene_context(scene):
    core = scene.core
    result = {
        "kind": core.kind,
        "event_at": scene.anchor.event_at.astimezone(DISPLAY_ZONE).isoformat(),
        "time_basis": scene.anchor.time_basis,
    }
    if isinstance(core, RecordCore):
        content = core.record.content.model_dump(mode="json")
        if content["kind"] == "photo":
            content = {"kind": "photo", "image_content": "not_supplied"}
        content.pop("pet_id", None)
        result["record"] = content
    elif isinstance(core, ObservationCore):
        observation = core.observation
        result["observation"] = {
            "kind": observation.kind,
            "subject": observation.subject,
            "action_meaning": observation.action_meaning,
            "support_from": observation.started_at.astimezone(DISPLAY_ZONE).isoformat(),
            "support_until": observation.ended_at.astimezone(DISPLAY_ZONE).isoformat(),
            "time_meaning": "기록 기기의 관측 구간. 강아지 행동 지속시간은 미확정.",
        }
    elif isinstance(core, CheckpointCore):
        result["checkpoint"] = {
            "continuity_block": core.block,
            "observed_route_m": core.route_m,
            "meaning": "관측 경로 위의 보충 지점. 사용자 행동이나 장소 방문을 뜻하지 않음.",
        }
    elif isinstance(core, BoundaryCore):
        result["boundary"] = core.boundary
    return result


def writing_sequence(board):
    return [
        {
            "scene_id": s.id,
            "kind": s.core.kind,
            "event_at": s.anchor.event_at.astimezone(DISPLAY_ZONE).isoformat(),
        }
        for s in board.scenes
    ]
