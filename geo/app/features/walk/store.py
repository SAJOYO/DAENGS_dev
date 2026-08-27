"""산책 세션 저장. 원칙 하나: **fix 는 파생 사실이 확정될 때까지만 있다.**

finish가 SEALED → DERIVED → PURGED를 같은 트랜잭션에서 통과한다. 공간 정지 이벤트를
포함한 파생 사실이 먼저고 삭제가 마지막이다. 궤적 영구 보관은 프라이버시 정책이
서기 전까지 하지 않는다 (008_walk_collection_hardening.sql).
"""

import json
import math
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.walk.curve import CURVE_VERSION, CurveBucket
from app.features.walk.encounter import FacilityCandidate
from app.features.walk.facts import FixQuality
from app.features.walk.models import (
    FacilityEncounter,
    MotionEventOccurrence,
    WalkFacts,
    WalkFix,
    WalkSession,
)


class WalkSessionNotFoundError(Exception):
    pass


class WalkSessionNotOpenError(Exception):
    pass


class EvidenceOriginConflictError(Exception):
    pass


class FixSequenceConflictError(Exception):
    pass


@dataclass(frozen=True)
class AppendResult:
    stored: int
    duplicates: int
    fix_count: int


async def upsert_session(db: AsyncSession, s: WalkSession) -> WalkSession:
    """멱등 시작. 같은 id 가 다시 오면 기존 세션을 그대로 돌려준다 (재전송·재시작)."""
    await db.execute(text("""
        INSERT INTO walk_session (id, dog_id, started_at)
        VALUES (:id, :dog_id, :started_at)
        ON CONFLICT (id) DO NOTHING
    """), {"id": s.id, "dog_id": s.dog_id, "started_at": s.started_at})
    return await get_session(db, s.id)  # type: ignore[return-value]  # 방금 넣었다


async def get_session(db: AsyncSession, session_id: str) -> WalkSession | None:
    row = (await db.execute(text("""
        SELECT id, dog_id, started_at, ended_at, fix_count, state, evidence_origin
        FROM walk_session WHERE id = :id
    """), {"id": session_id})).one_or_none()
    if row is None:
        return None
    return WalkSession(id=row.id, dog_id=row.dog_id, started_at=row.started_at,
                       ended_at=row.ended_at, fix_count=row.fix_count, state=row.state,
                       evidence_origin=row.evidence_origin)


async def lock_session(db: AsyncSession, session_id: str) -> WalkSession | None:
    """업로드와 finish가 공유하는 직렬화 지점."""
    row = (await db.execute(text("""
        SELECT id, dog_id, started_at, ended_at, fix_count, state, evidence_origin
        FROM walk_session WHERE id = :id FOR UPDATE
    """), {"id": session_id})).one_or_none()
    if row is None:
        return None
    return WalkSession(id=row.id, dog_id=row.dog_id, started_at=row.started_at,
                       ended_at=row.ended_at, fix_count=row.fix_count, state=row.state,
                       evidence_origin=row.evidence_origin)


async def append_fixes(
    db: AsyncSession, session_id: str, fixes: list[WalkFix]
) -> AppendResult:
    """client_seq로 재전송을 제거하고, seq에는 서버 수신 순서를 따로 보존한다."""
    session = await lock_session(db, session_id)
    if session is None:
        raise WalkSessionNotFoundError(session_id)
    if session.state != "open":
        raise WalkSessionNotOpenError(session_id)

    origins = {f.is_mock for f in fixes}
    if len(origins) != 1:
        raise EvidenceOriginConflictError("one batch cannot mix device and mock fixes")
    incoming_origin = "mock" if True in origins else "device"
    if session.evidence_origin not in ("unknown", incoming_origin):
        raise EvidenceOriginConflictError("a session cannot mix device and mock fixes")

    seqs = [f.client_seq for f in fixes]
    if len(seqs) != len(set(seqs)):
        raise FixSequenceConflictError("client_seq must be unique within a batch")
    rows = await db.execute(text("""
        SELECT client_seq, chain_index, at, lat, lng, accuracy_m, is_mock
        FROM walk_fix
        WHERE session_id = :id AND client_seq = ANY(CAST(:seqs AS integer[]))
    """), {"id": session_id, "seqs": seqs})
    existing = {r.client_seq: r for r in rows}
    for f in fixes:
        row = existing.get(f.client_seq)
        if row is None:
            continue
        same_accuracy = (
            row.accuracy_m is None and f.accuracy_m is None
            or row.accuracy_m is not None and f.accuracy_m is not None
            and math.isclose(row.accuracy_m, f.accuracy_m, abs_tol=1e-5)
        )
        if not (
            row.at == f.at
            and row.chain_index == f.chain_index
            and math.isclose(row.lat, f.lat, abs_tol=1e-12)
            and math.isclose(row.lng, f.lng, abs_tol=1e-12)
            and same_accuracy
            and row.is_mock == f.is_mock
        ):
            raise FixSequenceConflictError(
                f"client_seq {f.client_seq} was already stored with different data"
            )

    new_fixes = [f for f in fixes if f.client_seq not in existing]
    if new_fixes:
        await db.execute(text("""
            INSERT INTO walk_fix
                (session_id, seq, client_seq, chain_index, at, lat, lng, accuracy_m, is_mock)
            VALUES (:sid, :seq, :client_seq, :chain_index, :at, :lat, :lng, :accuracy_m, :is_mock)
        """), [
            {"sid": session_id, "seq": session.fix_count + i,
             "client_seq": f.client_seq, "chain_index": f.chain_index,
             "at": f.at, "lat": f.lat, "lng": f.lng,
             "accuracy_m": f.accuracy_m, "is_mock": f.is_mock}
            for i, f in enumerate(new_fixes)
        ])
    total = session.fix_count + len(new_fixes)
    await db.execute(text("""
        UPDATE walk_session SET
            fix_count = :n,
            mock_fix_count = mock_fix_count + :mocks,
            evidence_origin = :origin,
            updated_at = now()
        WHERE id = :id
    """), {"id": session_id, "n": total, "origin": incoming_origin,
           "mocks": sum(1 for f in new_fixes if f.is_mock)})
    return AppendResult(stored=len(new_fixes), duplicates=len(fixes) - len(new_fixes),
                        fix_count=total)


async def load_fixes_ordered(db: AsyncSession, session_id: str) -> list[WalkFix]:
    """계산 입력. 측정 시각 우선, 동시각이면 수신 순서 — 재생하면 같은 열이 나온다."""
    rows = await db.execute(text("""
        SELECT client_seq, chain_index, at, lat, lng, accuracy_m, is_mock
        FROM walk_fix WHERE session_id = :id ORDER BY client_seq, seq
    """), {"id": session_id})
    return [WalkFix(client_seq=r.client_seq, chain_index=r.chain_index,
                    at=r.at, lat=r.lat, lng=r.lng,
                    accuracy_m=r.accuracy_m, is_mock=r.is_mock) for r in rows]


async def facility_candidates(db: AsyncSession, session_id: str) -> list[FacilityCandidate]:
    """궤적 20m 버퍼 안의 시설 전부. 존재 필터 없음 — 폐업도 관측 대상이다.

    fix 가 살아 있는 DERIVED 이전에만 부를 수 있다. 교차 원천 중복(같은 가게가
    kcisa·kto 양쪽)만 링크로 접는다 — 그건 판정이 아니라 동일성이다. 후보는 넉넉히
    뽑고 정밀 기하(체류·횡거리)는 encounter 계산이 수용 세그먼트로 다시 잰다.
    """
    rows = await db.execute(text("""
        WITH chains AS (
            SELECT chain_index,
                   ST_MakeLine(ST_SetSRID(ST_MakePoint(lng, lat), 4326) ORDER BY client_seq, seq)
                       AS line,
                   count(*) AS n
            FROM walk_fix WHERE session_id = :id
            GROUP BY chain_index
            HAVING count(*) >= 2
        ), trail AS (
            SELECT ST_Collect(line) AS line, coalesce(sum(n), 0) AS n FROM chains
        )
        SELECT f.source, f.source_ref, f.kind,
               ST_Y(f.location::geometry) AS lat, ST_X(f.location::geometry) AS lng,
               f.last_written AS as_of, p.active AS place_active
        FROM facility f, trail
        LEFT JOIN LATERAL (
            SELECT pl.active
            FROM facility_link l JOIN place pl ON pl.id = l.source_ref::bigint
            WHERE l.source = 'mois:place' AND l.facility_id = f.id
            LIMIT 1
        ) p ON true
        WHERE trail.n >= 2
          AND f.source_ref IS NOT NULL
          AND ST_DWithin(f.location, trail.line::geography, 20)
          AND NOT EXISTS (
              SELECT 1
              FROM facility_link l
              JOIN facility winner ON winner.id = l.facility_id
              WHERE l.source = 'facility' AND l.source_ref = f.id::text
                -- 서로 다른 canonical kind는 별도 관측 후보다. Place 다중 분류 계약 전에는
                -- 한쪽을 임의의 winner로 접지 않는다.
                AND winner.kind = f.kind
          )
    """), {"id": session_id})
    return [FacilityCandidate(facility_source=r.source, facility_ref=r.source_ref,
                              kind=r.kind, lat=r.lat, lng=r.lng,
                              place_active=r.place_active, as_of=r.as_of)
            for r in rows]


async def finalize(
    db: AsyncSession,
    facts: WalkFacts,
    quality: FixQuality,
    events: list[MotionEventOccurrence],
    encounters: list[FacilityEncounter] = (),
    curve: list[CurveBucket] | None = None,
) -> None:
    """SEALED → DERIVED → PURGED. 파생 사실을 쓴 뒤에만 원좌표를 지운다."""
    await db.execute(text("""
        UPDATE walk_session SET state = 'sealed', ended_at = :ended_at, updated_at = now()
        WHERE id = :id
    """), {"id": facts.session_id, "ended_at": facts.ended_at})
    await db.execute(text("""
        INSERT INTO walk_facts (session_id, record_version, calculation_version, dog_id,
                                evidence_origin, started_at, ended_at,
                                duration_s, distance_m, moving_distance_m, moving_s,
                                stop_count, stop_s, avg_speed_mps, fix_count, quality,
                                curve, curve_version)
        VALUES (:session_id, :record_version, :calculation_version, :dog_id,
                :evidence_origin, :started_at, :ended_at,
                :duration_s, :distance_m, :moving_distance_m, :moving_s,
                :stop_count, :stop_s, :avg_speed_mps, :fix_count, CAST(:quality AS jsonb),
                CAST(:curve AS jsonb), :curve_version)
    """), {
        **facts.model_dump(),
        "quality": json.dumps(quality.to_dict()),
        # 곡선과 버전은 한 몸이다 (CHECK walk_facts_curve_paired). 곡선이 없으면 둘 다 NULL —
        # 못 만든 것을 0 으로 채우면 "평탄하게 걸었다" 는 거짓이 된다.
        "curve": json.dumps([b.to_dict() for b in curve]) if curve else None,
        "curve_version": CURVE_VERSION if curve else None,
    })
    if events:
        await db.execute(text("""
            INSERT INTO walk_motion_event
                (session_id, event_index, type, started_at, ended_at, duration_s, location,
                 route_offset_m, accuracy_p50_m, fix_count)
            VALUES (:session_id, :event_index, :type, :started_at, :ended_at, :duration_s,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                    :route_offset_m, :accuracy_p50_m, :fix_count)
        """), [e.model_dump() for e in events])
    if encounters:
        await db.execute(text("""
            INSERT INTO walk_encounter
                (session_id, event_index, occurrence_version, occurrence_index,
                 entered_at, exited_at, entry_observed, exit_observed,
                 entered_offset_m, exited_offset_m,
                 facility_source, facility_ref, kind, lat, lng,
                 place_active, as_of, min_lateral_m, offset_m,
                 dwell_s_10m, dwell_s_15m, dwell_s_20m, pass_count,
                 stop_overlap_10m, stop_overlap_15m, stop_overlap_20m, stop_s_10m,
                 accuracy_p50_m)
            VALUES (:session_id, :event_index, :occurrence_version, :occurrence_index,
                    :entered_at, :exited_at, :entry_observed, :exit_observed,
                    :entered_offset_m, :exited_offset_m,
                    :facility_source, :facility_ref, :kind,
                    :lat, :lng, :place_active, :as_of, :min_lateral_m, :offset_m,
                    :dwell_s_10m, :dwell_s_15m, :dwell_s_20m, :pass_count,
                    :stop_overlap_10m, :stop_overlap_15m, :stop_overlap_20m, :stop_s_10m,
                    :accuracy_p50_m)
        """), [e.model_dump() for e in encounters])
    await db.execute(text("""
        UPDATE walk_session SET state = 'derived', updated_at = now() WHERE id = :id
    """), {"id": facts.session_id})
    await db.execute(text("DELETE FROM walk_fix WHERE session_id = :id"),
                     {"id": facts.session_id})
    await db.execute(text("""
        UPDATE walk_session SET state = 'purged', updated_at = now() WHERE id = :id
    """), {"id": facts.session_id})


async def get_facts(db: AsyncSession, session_id: str) -> tuple[WalkFacts, dict] | None:
    row = (await db.execute(text("""
        SELECT session_id, record_version, calculation_version, dog_id, evidence_origin,
               started_at, ended_at, duration_s, distance_m,
               moving_distance_m, moving_s, stop_count, stop_s, avg_speed_mps,
               fix_count, quality
        FROM walk_facts WHERE session_id = :id
    """), {"id": session_id})).one_or_none()
    if row is None:
        return None
    data = dict(row._mapping)
    quality = data.pop("quality")
    return WalkFacts(**data), quality


async def get_events(db: AsyncSession, session_id: str) -> list[MotionEventOccurrence]:
    rows = await db.execute(text("""
        SELECT session_id, event_index, type, started_at, ended_at, duration_s,
               ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng,
               route_offset_m, accuracy_p50_m, fix_count
        FROM walk_motion_event WHERE session_id = :id ORDER BY event_index
    """), {"id": session_id})
    return [MotionEventOccurrence(**dict(r._mapping)) for r in rows]


async def get_encounters(db: AsyncSession, session_id: str) -> list[FacilityEncounter]:
    rows = await db.execute(text("""
        SELECT session_id, event_index, occurrence_version, occurrence_index,
               entered_at, exited_at, entry_observed, exit_observed,
               entered_offset_m, exited_offset_m,
               facility_source, facility_ref, kind, lat, lng,
               place_active, as_of, min_lateral_m, offset_m,
               dwell_s_10m, dwell_s_15m, dwell_s_20m, pass_count,
               stop_overlap_10m, stop_overlap_15m, stop_overlap_20m, stop_s_10m,
               accuracy_p50_m
        FROM walk_encounter WHERE session_id = :id ORDER BY event_index
    """), {"id": session_id})
    return [FacilityEncounter(**dict(r._mapping)) for r in rows]
