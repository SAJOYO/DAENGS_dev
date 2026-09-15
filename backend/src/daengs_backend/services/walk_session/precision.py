"""Independent immutable precision extension; all writes use the parent Walk lock."""

from daengs_backend.models.walk_precision import WalkPrecisionBackup, WalkPrecisionChunk
from daengs_backend.repositories import walk_motion as base_repo
from daengs_backend.repositories import walk_precision as repo
from daengs_backend.schemas.walk_motion import CHUNK_SIZE
from daengs_backend.schemas.walk_precision import (
    PrecisionChunkResponse,
    PrecisionManifest,
    PrecisionPoint,
    PrecisionStatus,
)
from daengs_backend.services.walk_session import motion as base
from daengs_backend.services.walk_session.chunk import decode_chunk
from daengs_backend.services.walk_session.errors import WalkNotFoundError
from daengs_backend.services.walk_session.motion_contract import MotionConflict
from daengs_backend.services.walk_session.precision_contract import (
    chunk_digest,
    evidence_digest,
    manifest_digest,
    refine_points,
)


async def _parent(session, owner, walk_id):
    walk = await base._owned(session, owner, walk_id)
    if not await repo.available(session):
        raise base.MotionUnavailable
    backup = await base_repo.backup(session, walk_id)
    if backup is None or backup.evidence_fingerprint is None:
        raise MotionConflict("precision_base_incomplete")
    return walk, backup


def _matches(manifest, walk, backup):
    if (
        manifest.client_session_id != str(walk.client_session_id)
        or manifest.base_evidence_fingerprint != backup.evidence_fingerprint
        or manifest.point_count != backup.manifest["point_count"]
    ):
        raise MotionConflict("precision_base_mismatch")


async def _status(session, row):
    return PrecisionStatus(
        manifest=PrecisionManifest.model_validate(row.manifest),
        manifest_fingerprint=row.manifest_fingerprint,
        received_chunks=await repo.chunk_indices(session, row.walk_id),
        evidence_fingerprint=row.evidence_fingerprint,
        state="complete" if row.evidence_fingerprint else "collecting",
    )


async def begin(session, owner, walk_id, body):
    try:
        walk, parent = await _parent(session, owner, walk_id)
        _matches(body, walk, parent)
        row = await repo.backup(session, walk_id)
        fp = manifest_digest(body)
        if row is None:
            row = WalkPrecisionBackup(
                walk_id=walk_id, manifest=body.model_dump(), manifest_fingerprint=fp
            )
            session.add(row)
        elif row.manifest_fingerprint != fp or row.manifest != body.model_dump():
            raise MotionConflict("precision_manifest_conflict")
        result = await _status(session, row)
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise


async def upload(session, owner, walk_id, index, body):
    try:
        walk, parent = await _parent(session, owner, walk_id)
        row = await repo.backup(session, walk_id)
        if row is None or row.manifest_fingerprint != body.manifest_fingerprint:
            raise MotionConflict("precision_manifest_mismatch")
        manifest = PrecisionManifest.model_validate(row.manifest)
        _matches(manifest, walk, parent)
        start, end = index * CHUNK_SIZE, min((index + 1) * CHUNK_SIZE, manifest.point_count)
        if end <= start or [p.client_seq for p in body.points] != list(range(start, end)):
            raise MotionConflict("precision_chunk_range")
        raw = [
            p
            for c in await base_repo.raw_chunks(session, walk_id, start, end)
            for p in decode_chunk(c.payload)
            if start <= p.client_seq < end
        ]
        refine_points(body.points, raw)
        fingerprint = chunk_digest(body.points)
        old = await repo.chunk(session, walk_id, index)
        payload = [p.model_dump() for p in body.points]
        if old is not None:
            if old.fingerprint != fingerprint or old.payload != payload:
                raise MotionConflict("precision_chunk_conflict")
        elif row.evidence_fingerprint is not None:
            raise MotionConflict("precision_sealed")
        else:
            session.add(
                WalkPrecisionChunk(
                    walk_id=walk_id, chunk_index=index, payload=payload, fingerprint=fingerprint
                )
            )
        await session.commit()
        return PrecisionChunkResponse(
            **body.model_dump(), chunk_index=index, chunk_fingerprint=fingerprint
        )
    except Exception:
        await session.rollback()
        raise


async def _contents(session, row, raw, base_fingerprint):
    manifest = PrecisionManifest.model_validate(row.manifest)
    if manifest.base_evidence_fingerprint != base_fingerprint or manifest.point_count != len(raw):
        raise MotionConflict("precision_base_mismatch")
    chunks = await repo.chunks(session, row.walk_id)
    if [c.chunk_index for c in chunks] != list(range((len(raw) + CHUNK_SIZE - 1) // CHUNK_SIZE)):
        raise MotionConflict("precision_chunks_incomplete")
    points = []
    for c in chunks:
        decoded = [PrecisionPoint.model_validate(p) for p in c.payload]
        start = c.chunk_index * CHUNK_SIZE
        if chunk_digest(decoded) != c.fingerprint or [p.client_seq for p in decoded] != list(
            range(start, min(start + CHUNK_SIZE, len(raw)))
        ):
            raise MotionConflict("precision_chunk_corrupt")
        points.extend(decoded)
    if manifest_digest(manifest) != row.manifest_fingerprint:
        raise MotionConflict("precision_manifest_corrupt")
    return refine_points(points, raw), evidence_digest(
        row.manifest_fingerprint, [c.fingerprint for c in chunks]
    )


async def complete(session, owner, walk_id, body):
    try:
        walk, parent = await _parent(session, owner, walk_id)
        row = await repo.backup(session, walk_id)
        if row is None or row.manifest_fingerprint != body.manifest_fingerprint:
            raise MotionConflict("precision_manifest_mismatch")
        _matches(PrecisionManifest.model_validate(row.manifest), walk, parent)
        _, raw, _, base_fp = await base._validated_contents(session, walk, parent)
        if base_fp != parent.evidence_fingerprint:
            raise MotionConflict("precision_base_mismatch")
        _, fingerprint = await _contents(session, row, raw, base_fp)
        if fingerprint != body.evidence_fingerprint:
            raise MotionConflict("precision_digest_mismatch")
        row.evidence_fingerprint = fingerprint
        result = await _status(session, row)
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise


async def refine_completed(session, walk_id, raw, base_fp, client_id):
    """Called under the base Walk lock, before its snapshot is detached."""
    if not await repo.available(session):
        return raw, None
    row = await repo.backup(session, walk_id)
    if row is None:
        return raw, None
    if row.evidence_fingerprint is None:
        raise MotionConflict("precision_incomplete")
    if row.manifest.get("client_session_id") != client_id:
        raise MotionConflict("precision_base_mismatch")
    precise, fp = await _contents(session, row, raw, base_fp)
    if fp != row.evidence_fingerprint:
        raise MotionConflict("precision_digest_mismatch")
    return precise, fp


async def read(session, owner, walk_id, index=None):
    try:
        walk, parent = await _parent(session, owner, walk_id)
        row = await repo.backup(session, walk_id)
        if row is None:
            raise WalkNotFoundError
        _matches(PrecisionManifest.model_validate(row.manifest), walk, parent)
        if index is None:
            return await _status(session, row)
        if row.evidence_fingerprint is None:
            raise MotionConflict("precision_incomplete")
        chunk = await repo.chunk(session, walk_id, index)
        if chunk is None:
            raise WalkNotFoundError
        return PrecisionChunkResponse(
            manifest_fingerprint=row.manifest_fingerprint,
            points=chunk.payload,
            chunk_index=index,
            chunk_fingerprint=chunk.fingerprint,
        )
    finally:
        await session.rollback()
