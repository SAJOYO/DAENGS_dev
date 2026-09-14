"""Compute off-lock, then atomically publish an immutable summary and all route pages."""

import asyncio
import hashlib

from daengs_backend.models.walk_measurement import WalkMeasurement, WalkMeasurementChunk
from daengs_backend.repositories import walk_measurement as repo
from daengs_backend.repositories import walk_motion as motion_repo
from daengs_backend.repositories import walk_precision as precision_repo
from daengs_backend.schemas.walk_measurement import (
    CHUNK_SIZE,
    MAX_POINTS,
    VERSION,
    ChunkRef,
    MeasurementSummary,
    RoutePage,
    RoutePoint,
)
from daengs_backend.services.walk_measurement_projection import project as project_measurement
from daengs_backend.services.walk_session import motion as walk_motion
from daengs_backend.services.walk_session.errors import WalkNotFoundError
from daengs_backend.services.walk_session.motion import MotionUnavailable
from daengs_backend.services.walk_session.motion_contract import MotionConflict
from daengs_walk.trajectory_view import digest


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def input_key(base, precision):
    # Bump transport version for any projection/engine change, never reuse sealed IDs.
    return digest({"version": VERSION, "base": base, "precision": precision})


def project(inputs, owner, walk_id):
    result = project_measurement(*inputs, owner=owner)
    ledger = result.measurement.ledger
    locations = {p.ref: p for p in result.locations}
    times = {p.ref: p for p in result.wall_times}
    events = {p.ref: p for p in ledger.journal.events}
    distances = {
        (i.source_range.start, i.source_range.end): i.walking_distance_m for i in ledger.intervals
    }
    points = []
    for sections in (result.walking_sections, result.observed_runs):
        for section_index, section in enumerate(sections):
            for i, ref in enumerate(section.point_refs):
                points.append(
                    RoutePoint(
                        kind=section.kind,
                        section_id=section.id,
                        section_index=section_index,
                        point_index=i,
                        ref=ref,
                        lat=locations[ref].lat,
                        lng=locations[ref].lng,
                        wall_time_millis=times[ref].original_wall_time_millis,
                        elapsed_ns=events[ref].elapsed_ns,
                        walking_distance_m=distances[section.point_refs[i - 1], ref] if i else 0,
                    )
                )
    pages = [
        RoutePage(
            measurement_id=result.measurement.measurement_id,
            chunk_index=i // CHUNK_SIZE,
            points=tuple(points[i : i + CHUNK_SIZE]),
        ).model_dump_json()
        for i in range(0, len(points), CHUNK_SIZE)
    ]
    refs = {getattr(result.boundaries, name) for name in type(result.boundaries).model_fields}
    summary = MeasurementSummary(
        walk_id=walk_id,
        measurement=result.measurement.ref(),
        base_evidence_fingerprint=inputs[3],
        precision_fingerprint=inputs[4],
        metrics=result.metrics,
        motion_recording_duration_ns=result.motion_recording_duration_ns,
        boundaries=result.boundaries,
        boundary_wall_times=tuple(t for t in result.wall_times if t.ref in refs),
        walking_section_count=len(result.walking_sections),
        observed_run_count=len(result.observed_runs),
        route_point_count=len(points),
        required_route_chunks=tuple(
            ChunkRef(
                index=i,
                sha256=sha(p),
                byte_size=len(p.encode("utf-8")),
                point_count=min(CHUNK_SIZE, len(points) - i * CHUNK_SIZE),
            )
            for i, p in enumerate(pages)
        ),
    )
    return summary, pages


async def owned(session, owner, walk_id):
    if await motion_repo.owned_locked(session, owner, walk_id) is None:
        raise WalkNotFoundError
    if not await repo.available(session):
        raise MotionUnavailable


def checked(row):
    if sha(row.payload) != row.fingerprint:
        raise MotionConflict("measurement_storage_corrupt")
    return row.payload.encode("utf-8")


async def prepare(session, owner, walk_id):
    try:
        await owned(session, owner, walk_id)
    finally:
        await session.rollback()
    inputs = await walk_motion.completed_input(session, owner, walk_id)
    if inputs[0].point_count > MAX_POINTS:
        raise MotionConflict("measurement_point_limit")
    if inputs[4] is None:
        raise MotionConflict("measurement_precision_required")
    key = input_key(inputs[3], inputs[4])
    try:
        await owned(session, owner, walk_id)
        existing = await repo.by_input(session, walk_id, key)
        if existing is not None:
            return checked(existing)
    finally:
        await session.rollback()
    try:
        summary, pages = await asyncio.to_thread(project, inputs, owner, walk_id)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise MotionConflict("measurement_calculation_invalid_input") from None
    payload = summary.model_dump_json()
    try:
        await owned(session, owner, walk_id)
        base = await motion_repo.backup(session, walk_id)
        precision = await precision_repo.backup(session, walk_id)
        if (
            base is None
            or precision is None
            or (base.evidence_fingerprint, precision.evidence_fingerprint) != inputs[3:]
        ):
            raise MotionConflict("measurement_input_changed")
        existing = await repo.by_input(session, walk_id, key)
        if existing is not None:
            if checked(existing) != payload.encode("utf-8"):
                raise MotionConflict("measurement_immutable_conflict")
        else:
            session.add(
                WalkMeasurement(
                    walk_id=walk_id,
                    measurement_id=summary.measurement.measurement_id,
                    input_key=key,
                    payload=payload,
                    fingerprint=sha(payload),
                )
            )
            await session.flush()
            session.add_all(
                [
                    WalkMeasurementChunk(
                        walk_id=walk_id,
                        measurement_id=summary.measurement.measurement_id,
                        chunk_index=i,
                        payload=p,
                        fingerprint=sha(p),
                    )
                    for i, p in enumerate(pages)
                ]
            )
        await session.commit()
        return payload.encode("utf-8")
    except BaseException:
        await session.rollback()
        raise


async def read(session, owner, walk_id, measurement_id, index=None):
    try:
        await owned(session, owner, walk_id)
        row = await repo.read(session, walk_id, measurement_id, index)
        if row is None:
            raise WalkNotFoundError
        return checked(row)
    finally:
        await session.rollback()
