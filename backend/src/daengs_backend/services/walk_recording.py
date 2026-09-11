"""Recording eligibility survives storage and retries; original GPS stays immutable."""

import hashlib
import json

from daengs_backend.repositories import walk as walks
from daengs_backend.repositories import walk_entry_v2 as entries
from daengs_backend.schemas.walk import WalkRecordingReceipt, WalkRecordingRepair
from daengs_backend.services.walk_chunk import RECORDING_POLICY, decode_chunk, encode_chunk
from daengs_backend.services.walk_finalize import walk_input_fingerprint


class RecordingConflict(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def recording_receipt(points) -> WalkRecordingReceipt:
    points = sorted(points, key=lambda p: p.client_seq)
    value = {
        "v": 1,
        "policy": RECORDING_POLICY,
        "points": [[p.client_seq, p.recording_eligible] for p in points],
    }
    fingerprint = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    return WalkRecordingReceipt(
        raw_input_fingerprint=walk_input_fingerprint(points),
        evidence_fingerprint=fingerprint,
        point_count=len(points),
        known_point_count=sum(p.recording_eligible is not None for p in points),
    )


def merge_recording_points(stored, incoming):
    """No partial write on conflict. Return replacements after validating every point."""
    by_seq = {p.client_seq: p for p in stored}
    replacements = {}
    for point in incoming:
        old = by_seq.get(point.client_seq)
        if old is None or walk_input_fingerprint([old]) != walk_input_fingerprint([point]):
            raise RecordingConflict(
                "recording_raw_mismatch", "보완할 GPS가 저장된 원본과 다릅니다."
            )
        if (
            old.recording_eligible is not None
            and old.recording_eligible != point.recording_eligible
        ):
            raise RecordingConflict(
                "recording_metadata_conflict", "이미 저장된 GPS 구분을 바꿀 수 없습니다."
            )
        if old.recording_eligible is None:
            replacements[point.client_seq] = old.model_copy(
                update={"recording_eligible": point.recording_eligible}
            )
    return replacements


async def repair_recording(session, owner, walk_id, body: WalkRecordingRepair):
    # Same walk lock as finalize and v2 pin mutations. Metadata cannot race an accepted pin.
    from daengs_backend.services.walk import WalkNotFoundError

    try:
        walk = await walks.get_owned_for_update(session, owner, walk_id)
        if walk is None:
            raise WalkNotFoundError
        chunks = list(walk.points)
        decoded = [(chunk, decode_chunk(chunk.payload)) for chunk in chunks]
        points = [point for _, rows in decoded for point in rows]
        receipt = recording_receipt(points)
        if receipt.raw_input_fingerprint != body.raw_input_fingerprint:
            raise RecordingConflict("recording_raw_mismatch", "산책 원본의 지문이 다릅니다.")
        replacements = merge_recording_points(points, body.points)
        if replacements and await entries.contains_v2(session, [walk_id]):
            raise RecordingConflict(
                "recording_metadata_locked",
                "이미 저장된 행동 핀의 GPS 근거는 자동으로 제외할 수 없습니다.",
            )
        for chunk, rows in decoded:
            if any(p.client_seq in replacements for p in rows):
                chunk.payload = encode_chunk([replacements.get(p.client_seq, p) for p in rows])
        result = recording_receipt([replacements.get(p.client_seq, p) for p in points])
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise
