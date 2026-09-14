"""Immutable, owner-scoped backup with an explicit complete receipt and bounded pages."""

from datetime import UTC, datetime

from daengs_backend.models.walk_motion import WalkMotionBackup, WalkMotionChunk
from daengs_backend.repositories import walk_motion as repo
from daengs_backend.schemas.walk_motion import (
    CHUNK_SIZE,
    MotionBackupStatus,
    MotionChunkResponse,
    MotionManifest,
    MotionObservation,
)
from daengs_backend.services.walk_session.chunk import decode_chunk
from daengs_backend.services.walk_session.errors import WalkNotFoundError
from daengs_backend.services.walk_session.finalize import walk_input_fingerprint
from daengs_backend.services.walk_session.motion_contract import (
    MotionConflict,
    chunk_digest,
    evidence_digest,
    manifest_digest,
    validate_manifest,
    validate_observations,
)


class MotionUnavailable(Exception):
    pass


async def _owned(session, owner, walk_id):
    # No sidecar dependency on the legacy API. Unmigrated deployments decline only this capability.
    if not await repo.available(session):
        raise MotionUnavailable
    walk = await repo.owned_locked(session, owner, walk_id)
    if walk is None:
        raise WalkNotFoundError
    return walk


async def _raw(session, walk, manifest):
    if walk.analysis_state != "derived":
        raise MotionConflict("motion_raw_not_finalized")
    if str(walk.client_session_id) != manifest.client_session_id:
        raise MotionConflict("motion_session_mismatch")
    # Wall time is descriptive. Match the saved end but don't infer active time from it.
    elapsed = walk.ended_at - datetime(1970, 1, 1, tzinfo=UTC)
    ended_at_millis = (
        elapsed.days * 86_400_000 + elapsed.seconds * 1000 + elapsed.microseconds // 1000
    )
    if ended_at_millis != manifest.epochs[-1].ended_at_millis:
        raise MotionConflict("motion_end_mismatch")
    raw = [p for c in await repo.raw_chunks(session, walk.id) for p in decode_chunk(c.payload)]
    if (
        len(raw) != manifest.point_count
        or [p.client_seq for p in raw] != list(range(len(raw)))
        or walk_input_fingerprint(raw) != manifest.raw_input_fingerprint
        or any(p.recording_eligible is None for p in raw)
    ):
        raise MotionConflict("motion_raw_mismatch")
    return raw


async def _status(session, row):
    return MotionBackupStatus(
        manifest=MotionManifest.model_validate(row.manifest),
        manifest_fingerprint=row.manifest_fingerprint,
        received_chunks=await repo.chunk_indices(session, row.walk_id),
        evidence_fingerprint=row.evidence_fingerprint,
        state="complete" if row.evidence_fingerprint else "collecting",
    )


async def begin(session, owner, walk_id, body):
    try:
        walk = await _owned(session, owner, walk_id)
        validate_manifest(body)
        await _raw(session, walk, body)
        fingerprint = manifest_digest(body)
        row = await repo.backup(session, walk_id)
        if row is None:
            row = WalkMotionBackup(
                walk_id=walk_id,
                manifest=body.model_dump(mode="json"),
                manifest_fingerprint=fingerprint,
                evidence_fingerprint=None,
            )
            session.add(row)
        elif row.manifest_fingerprint != fingerprint or row.manifest != body.model_dump(
            mode="json"
        ):
            raise MotionConflict("motion_manifest_conflict")
        result = await _status(session, row)
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise


async def upload_chunk(session, owner, walk_id, index, body):
    try:
        await _owned(session, owner, walk_id)
        row = await repo.backup(session, walk_id)
        if row is None or row.manifest_fingerprint != body.manifest_fingerprint:
            raise MotionConflict("motion_manifest_mismatch")
        manifest = MotionManifest.model_validate(row.manifest)
        start = index * CHUNK_SIZE
        expected = min(CHUNK_SIZE, manifest.point_count - start)
        if (
            expected <= 0
            or len(body.points) != expected
            or [p.client_seq for p in body.points] != list(range(start, start + expected))
        ):
            raise MotionConflict("motion_chunk_range")
        raw = [
            p
            for c in await repo.raw_chunks(session, walk_id, start, start + expected)
            for p in decode_chunk(c.payload)
            if start <= p.client_seq < start + expected
        ]
        if len(raw) != expected:
            raise MotionConflict("motion_raw_mismatch")
        validate_observations(manifest, body.points, raw)
        fingerprint = chunk_digest(body.points)
        old = await repo.chunk(session, walk_id, index)
        if old is not None:
            if old.fingerprint != fingerprint or old.payload != [
                p.model_dump(mode="json") for p in body.points
            ]:
                raise MotionConflict("motion_chunk_conflict")
        elif row.evidence_fingerprint:
            raise MotionConflict("motion_backup_sealed")
        else:
            session.add(
                WalkMotionChunk(
                    walk_id=walk_id,
                    chunk_index=index,
                    payload=[p.model_dump(mode="json") for p in body.points],
                    fingerprint=fingerprint,
                )
            )
        await session.commit()
        return MotionChunkResponse(
            **body.model_dump(), chunk_index=index, chunk_fingerprint=fingerprint
        )
    except Exception:
        await session.rollback()
        raise


async def _validated_contents(session, walk, row):
    manifest = MotionManifest.model_validate(row.manifest)
    validate_manifest(manifest)
    raw = await _raw(session, walk, manifest)
    chunks = await repo.chunks(session, walk.id)
    if [c.chunk_index for c in chunks] != list(range((len(raw) + CHUNK_SIZE - 1) // CHUNK_SIZE)):
        raise MotionConflict("motion_chunks_incomplete")
    all_points = []
    for chunk in chunks:
        points = [MotionObservation.model_validate(p) for p in chunk.payload]
        start = chunk.chunk_index * CHUNK_SIZE
        if [p.client_seq for p in points] != list(
            range(start, min(start + CHUNK_SIZE, len(raw)))
        ) or chunk.fingerprint != chunk_digest(points):
            raise MotionConflict("motion_chunk_corrupt")
        all_points.extend(points)
    validate_observations(manifest, all_points, raw)
    fingerprint = evidence_digest(manifest_digest(manifest), [c.fingerprint for c in chunks])
    if row.manifest_fingerprint != manifest_digest(manifest):
        raise MotionConflict("motion_digest_mismatch")
    return manifest, raw, all_points, fingerprint


async def completed_input(session, owner, walk_id):
    """Detach one coherent, revalidated sealed input under the same Walk lock as backup writes."""
    try:
        walk = await _owned(session, owner, walk_id)
        row = await repo.backup(session, walk_id)
        if row is None:
            raise WalkNotFoundError
        if row.evidence_fingerprint is None:
            raise MotionConflict("motion_backup_incomplete")
        manifest, raw, points, fingerprint = await _validated_contents(session, walk, row)
        if fingerprint != row.evidence_fingerprint:
            raise MotionConflict("motion_digest_mismatch")
        from daengs_backend.services.walk_session.precision import refine_completed

        raw, precision_fp = await refine_completed(
            session, walk_id, raw, fingerprint, manifest.client_session_id
        )
        return manifest, raw, points, fingerprint, precision_fp
    finally:
        # No writes and no lock held while the CPU-only engine runs.
        await session.rollback()


async def complete(session, owner, walk_id, body):
    try:
        walk = await _owned(session, owner, walk_id)
        row = await repo.backup(session, walk_id)
        if row is None or row.manifest_fingerprint != body.manifest_fingerprint:
            raise MotionConflict("motion_manifest_mismatch")
        _, _, _, fingerprint = await _validated_contents(session, walk, row)
        if fingerprint != body.evidence_fingerprint:
            raise MotionConflict("motion_digest_mismatch")
        row.evidence_fingerprint = fingerprint
        result = await _status(session, row)
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise


async def read(session, owner, walk_id, index=None):
    try:
        await _owned(session, owner, walk_id)
        row = await repo.backup(session, walk_id)
        if row is None:
            raise WalkNotFoundError
        if index is None:
            return await _status(session, row)
        if row.evidence_fingerprint is None:
            raise MotionConflict("motion_backup_incomplete")
        chunk = await repo.chunk(session, walk_id, index)
        if chunk is None:
            raise WalkNotFoundError
        return MotionChunkResponse(
            manifest_fingerprint=row.manifest_fingerprint,
            points=chunk.payload,
            chunk_index=index,
            chunk_fingerprint=chunk.fingerprint,
        )
    finally:
        # Release the read lock; responses contain only detached DTO data.
        await session.rollback()
