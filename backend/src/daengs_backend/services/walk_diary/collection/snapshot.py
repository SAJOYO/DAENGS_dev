"""Bind a completed source result to its selected scene cores before publishing progress."""

from daengs_walk.diary.board.models import RecordCore
from daengs_walk.diary.contracts.input import SavedBackground, digest


def source_background(target, scene, kind, value=None, at=None, reason="source_unavailable"):
    unlocated = target.anchor.point is None or target.anchor.position_state == "provisional"
    if unlocated:
        value, at, reason = None, None, "scene_location_unavailable"
    if kind == "sgis":
        pin = scene.core.record.pin_payload if isinstance(scene.core, RecordCore) else None
        payload = (
            {
                "format": "sgis-dong-v1",
                "address_type": "administrative_dong",
                "address": value,
                "query_point": target.anchor.point.model_dump(mode="json"),
                "location_basis": pin["method"] if pin else "original_location",
                **({k: pin.get(k) for k in ("uncertainty_m", "uncertainty_basis")} if pin else {}),
            }
            if value is not None
            else None
        )
    else:
        payload = value.model_dump(mode="json") if value is not None else None
    return SavedBackground(
        id="normalized:"
        + digest([target.scene_id, target.core_ref.model_dump(mode="json"), kind, payload]),
        target=target.core_ref,
        provider="sgis" if kind == "sgis" else "public-normalized-" + kind,
        payload_schema="walk-entry-context-v1" if kind == "sgis" else "space-materials-v1",
        policy_version="walk-entry-context-v1" if kind == "sgis" else "space-normalization-v1",
        query_point=target.anchor.point,
        tags=("space",),
        status="known" if value is not None else "not_requested" if unlocated else "unavailable",
        reason=None if value is not None else reason,
        retrieved_at=at,
        temporal_basis="lookup_snapshot",
        payload=payload,
        payload_sha256=digest(payload) if payload is not None else None,
    )
