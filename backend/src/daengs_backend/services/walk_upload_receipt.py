"""Opt-in uploads acknowledge committed chunks without materializing the whole route."""

from sqlalchemy.exc import IntegrityError

from daengs_backend.models import Walk, WalkPet, WalkPointChunk
from daengs_backend.repositories import pet as pets
from daengs_backend.repositories import walk as walks
from daengs_backend.repositories import walk_upload as repo
from daengs_backend.schemas.walk import WalkPointsAppend, WalkUpload
from daengs_backend.schemas.walk_upload_receipt import WalkChunkReceipt, WalkUploadReceipt
from daengs_backend.services import activity, activity_game
from daengs_backend.services.walk import WalkNotFoundError, WalkStateConflictError
from daengs_backend.services.walk_chunk import decode_chunk, encode_chunk


def _chunk(points):
    payload = encode_chunk(points)
    seq_from, seq_to = min(p.client_seq for p in points), max(p.client_seq for p in points)
    if seq_to - seq_from + 1 != len(points):
        raise WalkStateConflictError(
            "walk_chunk_not_contiguous", "한 좌표 묶음의 순번은 빠짐없이 이어져야 합니다."
        )
    return WalkPointChunk(
        seq_from=seq_from, seq_to=seq_to, point_count=len(points), payload=payload
    )


def _receipt(walk, chunk, status):
    return WalkUploadReceipt(
        walk_id=walk.id,
        client_session_id=walk.client_session_id,
        chunk=WalkChunkReceipt(
            seq_from=chunk.seq_from,
            seq_to=chunk.seq_to,
            point_count=chunk.point_count,
            status=status,
        )
        if chunk is not None
        else None,
    )


def _same_chunk(stored, incoming):
    if (stored.seq_to, stored.point_count) != (incoming.seq_to, incoming.point_count):
        return False
    old, new = decode_chunk(stored.payload), decode_chunk(incoming.payload)
    old = sorted(old, key=lambda p: p.client_seq)
    if [p.client_seq for p in old] != list(range(stored.seq_from, stored.seq_to + 1)):
        return False
    for existing, submitted in zip(old, new, strict=True):
        if existing.model_dump(exclude={"recording_eligible"}) != submitted.model_dump(
            exclude={"recording_eligible"}
        ):
            return False
        # An omitted observation makes no assertion about metadata filled in by repair.
        # Explicit eligibility must already match; only recording-evidence may fill it.
        if (
            submitted.recording_eligible is not None
            and submitted.recording_eligible != existing.recording_eligible
        ):
            return False
    return True


async def _existing_chunk(db, walk_id, incoming):
    stored = await repo.chunk(db, walk_id, incoming.seq_from)
    if stored is not None and not _same_chunk(stored, incoming):
        raise WalkStateConflictError(
            "walk_chunk_conflict", "같은 순번으로 저장된 좌표 묶음의 내용이 다릅니다."
        )
    if await repo.overlaps(db, walk_id, incoming.seq_from, incoming.seq_to):
        raise WalkStateConflictError(
            "walk_chunk_overlap", "이미 저장된 다른 좌표 묶음과 순번이 겹칩니다."
        )
    return stored


async def _replay_upload(db, walk, body, incoming):
    if any(
        getattr(walk, field) != getattr(body, field)
        for field in ("started_at", "ended_at", "weather_code", "is_day", "temperature_c")
    ):
        raise WalkStateConflictError(
            "walk_upload_conflict", "같은 세션으로 저장된 산책의 시각 또는 날씨가 다릅니다."
        )
    stored = await _existing_chunk(db, walk.id, incoming) if incoming is not None else None
    if incoming is not None and stored is None:
        raise WalkStateConflictError(
            "walk_chunk_missing", "기존 산책에 없는 좌표 묶음입니다. 좌표 추가로 전송해 주세요."
        )
    await activity.record_walk(db, walk)
    receipt = _receipt(walk, stored, "replayed")
    await db.commit()
    return receipt, False


async def upload_walk(db, owner, body: WalkUpload) -> tuple[WalkUploadReceipt, bool]:
    try:
        incoming = _chunk(body.points) if body.points else None
        await activity_game.acquire(db)
        existing = await repo.by_client_session(db, owner, body.client_session_id)
        if existing is not None:
            return await _replay_upload(db, existing, body, incoming)
        mine = await pets.accessible_ids(db, owner, body.pet_ids)
        walk = Walk(app_user_id=owner, **body.model_dump(exclude={"points", "pet_ids"}))
        walk.pets = [WalkPet(pet_id=pet_id) for pet_id in sorted(mine)]
        walk.points = [incoming] if incoming is not None else []
        try:
            walks.add(db, walk)
            await db.flush()
            await activity.record_walk(db, walk)
            receipt = _receipt(walk, incoming, "stored")
            await db.commit()
        except IntegrityError as error:
            await db.rollback()
            if not walks.is_client_session_conflict(error):
                raise
            # Reacquire game then walk locks after rollback, just like legacy upload.
            await activity_game.acquire(db)
            existing = await repo.by_client_session(db, owner, body.client_session_id)
            if existing is None:
                raise
            return await _replay_upload(db, existing, body, incoming)
        return receipt, True
    except BaseException:
        await db.rollback()
        raise


async def append_points(db, owner, walk_id, body: WalkPointsAppend) -> WalkUploadReceipt:
    try:
        walk = await repo.owned(db, owner, walk_id)
        if walk is None:
            raise WalkNotFoundError
        if walk.analysis_state != "collecting":
            raise WalkStateConflictError(
                "walk_already_finalized", "이미 봉인된 산책에는 좌표를 더할 수 없습니다."
            )
        incoming = _chunk(body.points)
        stored = await _existing_chunk(db, walk_id, incoming)
        if stored is None:
            incoming.walk_id = walk_id
            repo.add_chunk(db, incoming)
        receipt = _receipt(
            walk, stored if stored is not None else incoming, "replayed" if stored else "stored"
        )
        await db.commit()
        return receipt
    except BaseException:
        await db.rollback()
        raise
