"""산책 기록의 규칙. 트랜잭션 경계도 여기입니다.

규칙이 거의 없는 것이 이 서비스의 특징입니다. **끝난 기록은 다시 바뀌지 않기**
때문에 병합도 충돌도 없습니다 — 없으면 넣고 있으면 그대로 돌려줍니다.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Walk, WalkAnalysis, WalkPet, WalkPointChunk
from daengs_backend.orchestration.adapters.life import (
    WalkWeatherLookup,
    WalkWeatherObservation,
)
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.schemas.walk import (
    WalkFinalizeRequest,
    WalkPointsAppend,
    WalkUpload,
)
from daengs_backend.services import activity, activity_game
from daengs_backend.services.walk_artifacts.api import build_analysis_models
from daengs_backend.services.walk_artifacts.capsule import build_capsule_model
from daengs_backend.services.walk_chunk import encode_chunk
from daengs_backend.services.walk_finalize import PreparedWalkEvidence, prepare_finalized_walk
from daengs_walk.capsule import build_walk_capsule, select_context_anchor
from daengs_walk.cellophane import build_cellophane
from daengs_walk.contracts import WalkEvidencePoint
from daengs_walk.evidence import analyze_walk


class WalkNotFoundError(Exception):
    """내 산책이 아니거나 없습니다.

    **남의 것일 때도 이 예외입니다** — 403 으로 나누면 "그 id 는 존재한다"를
    알려 주는 셈입니다 (`services/pet.py` 와 같은 판단).
    """


class WalkStateConflictError(RuntimeError):
    """현재 입력 봉인 상태와 요청이 충돌한다."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


async def list_walks(session: AsyncSession, app_user_id: uuid.UUID) -> list[Walk]:
    return await walk_repo.list_for_owner(session, app_user_id)


async def get_walk(session: AsyncSession, app_user_id: uuid.UUID, walk_id: uuid.UUID) -> Walk:
    walk = await walk_repo.get_owned(session, app_user_id, walk_id)
    if walk is None:
        raise WalkNotFoundError
    return walk


async def upload_walk(
    session: AsyncSession, app_user_id: uuid.UUID, body: WalkUpload
) -> tuple[Walk, bool]:
    """올리기. **이미 있으면 있던 것을 그대로 돌려줍니다.**

    앱은 네트워크가 끊기면 다음에 다시 올립니다(지하철에 들어가면 그렇습니다).
    그때 같은 산책이 두 건이 되면 안 되므로 기기가 준 `client_session_id` 로 먼저
    찾아봅니다. 동시에 처음 올려 UNIQUE 충돌이 나면 rollback 뒤 먼저 저장된
    산책을 다시 읽어 같은 상세 응답을 돌려줍니다. 다른 제약 오류는 그대로 전달합니다.

    **덮어쓰지 않습니다.** 끝난 기록은 바뀌지 않으므로 다시 온 것은 재시도일 뿐이고,
    좌표를 다시 넣으면 이미 저장한 원본을 흔들 위험만 있습니다.

    강아지는 **내가 돌보는 아이만** 붙입니다 (대표 ∪ 돌보미 — docs/co-care.md §2).
    남의 pet_id 를 실어 보내도 그 강아지에 산책이 붙으면 안 됩니다. 닿지 못하는 id 는
    조용히 뺍니다 — 산책 자체는 사용자의 것이라 거절할 이유가 없습니다.

    **여기가 대표 기준이면 공동 돌봄의 하루 요약이 통째로 죽습니다.** 아빠가 맥스를
    태그한 산책이 아예 안 만들어지므로, `walk.count_for_pet_between` 에서 소유자 조건을
    뺀 것도 셀 것이 없습니다. 그래서 **쓰기 중 여기 하나만** 구성원 기준입니다 —
    올라가는 것은 아빠 **자신의** 산책이고 강아지는 그 위의 태그일 뿐입니다.

    **아무도 안 붙어도 저장합니다.** 강아지를 등록하기 전에 걸었거나 고르지 않고
    나선 경우인데, 그래도 사람이 걸은 것은 걸은 것입니다.

    :returns: (산책, 이번에 새로 만들었는가)
    """
    await activity_game.acquire(session)
    existing = await walk_repo.get_by_client_session(session, app_user_id, body.client_session_id)
    if existing is not None:
        return await _return_existing_upload(session, existing)

    mine = await pet_repo.accessible_ids(session, app_user_id, body.pet_ids)

    walk = Walk(
        app_user_id=app_user_id,
        client_session_id=body.client_session_id,
        started_at=body.started_at,
        ended_at=body.ended_at,
        weather_code=body.weather_code,
        is_day=body.is_day,
        temperature_c=body.temperature_c,
    )
    # **pet_id 순으로 담습니다.** 관계가 그 순서로 다시 읽히기 때문입니다 —
    # 방금 올린 응답과 나중에 받아 온 응답의 순서가 다르면 앱이 "바뀌었다" 로 읽습니다.
    walk.pets = [WalkPet(pet_id=pet_id) for pet_id in sorted(mine)]
    # 좌표는 **묶음 하나**로 담는다. 앱이 2,000점씩 끊어 보내므로 요청 하나가
    # 곧 묶음 하나다 (`WalkSync.POINTS_PER_REQUEST`).
    walk.points = [_chunk(body.points)] if body.points else []
    try:
        walk_repo.add(session, walk)
        if activity.settings.activity_game_enabled:
            await session.flush()
            await activity.record_walk(session, walk)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if not walk_repo.is_client_session_conflict(error):
            raise
        # rollback은 게임 잠금과 그 안의 시즌/만료 반영도 되돌립니다.
        await activity_game.acquire(session)
        existing = await walk_repo.get_by_client_session(
            session, app_user_id, body.client_session_id
        )
        if existing is None:
            # 충돌 후 삭제됐을 수도 있습니다. 없는 행을 성공으로 응답하지 않습니다.
            raise
        return await _return_existing_upload(session, existing)
    return walk, True


async def _return_existing_upload(session: AsyncSession, walk: Walk) -> tuple[Walk, bool]:
    await activity.record_walk(session, walk)
    if activity.settings.activity_game_enabled:
        await session.commit()
    return walk, False


async def append_points(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    walk_id: uuid.UUID,
    body: WalkPointsAppend,
) -> Walk:
    """좌표를 이어 붙입니다. **긴 산책을 나눠 올릴 때** 씁니다.

    두 시간 산책이면 좌표가 5천 점 가까이 되고 촘촘히 잡히면 만 점도 넘어, 한 번에
    보내면 nginx 바디 한도(기본 1MB)에 걸립니다. 걸리면 그 산책은 영영 안 올라갑니다.

    **이미 있는 순번은 조용히 넘깁니다.** 앱이 같은 묶음을 다시 보내는 것은 재시도지
    오류가 아닙니다 — DB 의 PK 가 막아 주기는 하지만 그건 500 으로 터지는 방식입니다.
    """
    try:
        walk = await walk_repo.get_owned_for_update(session, app_user_id, walk_id)
        if walk is None:
            raise WalkNotFoundError
        if walk.analysis_state != "collecting":
            raise WalkStateConflictError(
                "walk_already_finalized",
                "이미 봉인된 산책에는 좌표를 더할 수 없습니다.",
            )

        # **묶음의 첫 순번으로 재시도를 판정한다.** 앱이 같은 묶음을 다시 보내는 것은
        # 재시도지 오류가 아니다. 예전에는 좌표 순번을 전부 읽어 하나씩 걸렀는데,
        # 묶음 단위면 몇 개만 읽으면 된다.
        starts = await walk_repo.existing_chunk_starts(session, walk_id)
        chunk = _chunk(body.points)
        if chunk.seq_from in starts:
            await session.commit()  # 변경 없이 행 잠금만 풀어 재시도를 완료한다.
            return walk

        walk.points.append(chunk)
        await session.commit()
        return walk
    except Exception:
        await session.rollback()
        raise


@dataclass(frozen=True)
class _FinalizeSnapshot:
    walk_id: uuid.UUID
    started_at: datetime
    ended_at: datetime
    weather_code: int | None
    is_day: bool | None
    temperature_c: Decimal | None
    pet_ids: tuple[uuid.UUID, ...]
    prepared: PreparedWalkEvidence

    @classmethod
    def capture(cls, walk: Walk, prepared: PreparedWalkEvidence) -> "_FinalizeSnapshot":
        return cls(
            walk_id=walk.id,
            started_at=walk.started_at,
            ended_at=walk.ended_at,
            weather_code=walk.weather_code,
            is_day=walk.is_day,
            temperature_c=walk.temperature_c,
            pet_ids=tuple(sorted(walk.pet_ids)),
            prepared=prepared,
        )


async def finalize_walk(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    walk_id: uuid.UUID,
    manifest: WalkFinalizeRequest,
    weather_lookup: WalkWeatherLookup | None = None,
) -> tuple[WalkAnalysis, bool]:
    """불변 입력의 계산·날씨 조회 후 최신 입력을 잠그고 원자적으로 봉인한다.

    외부 조회 중에는 트랜잭션을 유지하지 않습니다. 재조회에서 입력 변경을 거절하고,
    다른 finalize가 먼저 완료했으면 그 분석을 그대로 돌려줍니다.
    """
    try:
        walk = await walk_repo.get_owned_for_finalize(session, app_user_id, walk_id)
        if walk is None:
            raise WalkNotFoundError
        prepared = prepare_finalized_walk(walk.points, manifest)
        if walk.analysis_state not in {"collecting", "derived"}:
            raise WalkStateConflictError(
                "walk_state_invalid",
                f"알 수 없는 산책 봉인 상태입니다: {walk.analysis_state!r}",
            )
        snapshot = _FinalizeSnapshot.capture(walk, prepared)
        already_derived = walk.analysis_state == "derived"
        await session.commit()  # 읽기 트랜잭션 종료. 아래 계산·외부 호출에는 ORM을 넘기지 않는다.

        analysis = None
        weather = None
        if not already_derived:
            evidence = analyze_walk(
                snapshot.walk_id,
                snapshot.started_at,
                snapshot.ended_at,
                snapshot.prepared.points,
            )
            analysis = build_analysis_models(
                snapshot.prepared, evidence, build_cellophane(evidence)
            )
            anchor = select_context_anchor(
                evidence.accepted_points,
                started_at=snapshot.started_at,
                ended_at=snapshot.ended_at,
            )
            weather = await _lookup_context_weather(weather_lookup, anchor)

        # 게임 공통 잠금 → Walk 행 잠금 순서를 유지한다. 시즌/만료 반영도 이 트랜잭션이다.
        await activity_game.acquire(session)
        walk = await walk_repo.get_owned_for_update(session, app_user_id, walk_id)
        if walk is None:
            raise WalkNotFoundError

        prepared = prepare_finalized_walk(walk.points, manifest)
        if walk.analysis_state == "derived":
            return await _reuse_finalized_walk(session, walk, prepared)

        if walk.analysis_state != "collecting":
            raise WalkStateConflictError(
                "walk_state_invalid",
                f"알 수 없는 산책 봉인 상태입니다: {walk.analysis_state!r}",
            )

        if analysis is None or _FinalizeSnapshot.capture(walk, prepared) != snapshot:
            raise WalkStateConflictError(
                "walk_input_changed",
                "산책 기록이 변경되었습니다. 최신 기록으로 다시 시도해 주세요.",
            )
        _attach_capsule(
            walk,
            analysis,
            sealed_at=datetime.now(UTC),
            provider="android_walk_upload_v1",
            weather=weather,
        )
        walk_repo.add_analysis(session, analysis)
        walk.analysis_state = "derived"
        await session.flush()
        await activity.record_walk(session, walk, analysis)
        await session.commit()
        return analysis, True
    except Exception:
        await session.rollback()
        raise


async def _reuse_finalized_walk(
    session: AsyncSession, walk: Walk, prepared: PreparedWalkEvidence
) -> tuple[WalkAnalysis, bool]:
    """Walk 잠금 안에서 같은 분석을 재사용하고 누락된 legacy Capsule만 복구한다."""
    existing = await walk_repo.get_analysis_for_input(
        session, walk_id=walk.id, input_fingerprint=prepared.input_fingerprint
    )
    if existing is None:
        raise WalkStateConflictError(
            "finalized_analysis_not_found", "봉인 상태와 저장된 분석 결과가 맞지 않습니다."
        )
    if existing.capsule is None:
        _attach_capsule(
            walk,
            existing,
            sealed_at=existing.derived_at,
            provider="legacy_walk_metadata_v1",
            context_version=1,
        )
        # 기존 Analysis의 backref만으로는 새 Capsule이 session에 등록되지 않습니다.
        walk_repo.add_analysis(session, existing)
        await session.flush()
    await activity.record_walk(session, walk, existing)
    await session.commit()
    return existing, False


def _attach_capsule(
    walk: Walk,
    analysis: WalkAnalysis,
    *,
    sealed_at: datetime,
    provider: str,
    weather: WalkWeatherObservation | None = None,
    context_version: int = 2,
) -> None:
    """앱 원본을 보존하고, 있으면 Life 관측으로 환경 Snapshot을 보강한다."""

    app_temperature = float(walk.temperature_c) if walk.temperature_c is not None else None
    has_app_context = any(
        value is not None for value in (walk.weather_code, walk.is_day, app_temperature)
    )
    has_observed_context = (
        weather is not None
        and weather.status
        in {
            "captured",
            "partial",
        }
        and any(
            value is not None
            for value in (
                weather.temperature_c,
                weather.humidity_pct,
                weather.precipitation_kind,
                weather.precipitation_mm,
            )
        )
    )
    context_provider = provider
    if has_observed_context:
        observed_provider = weather.provider or "life_weather_at"
        context_provider = (
            f"{observed_provider}+{provider}" if has_app_context else observed_provider
        )
    elif weather is not None and not has_app_context:
        context_provider = weather.provider or "life_weather_at"

    capsule = build_walk_capsule(
        walk_id=walk.id,
        facts_record_version=analysis.facts_record_version,
        calculation_version=analysis.calculation_version,
        receipt_version=analysis.receipt_version,
        observation_version=analysis.observation_version,
        walked_at=walk.started_at,
        sealed_at=sealed_at,
        weather_code=walk.weather_code,
        is_day=walk.is_day,
        temperature_c=(
            weather.temperature_c
            if has_observed_context and weather is not None and weather.temperature_c is not None
            else app_temperature
        ),
        precipitation_kind=(weather.precipitation_kind if has_observed_context else None),
        precipitation_mm=(weather.precipitation_mm if has_observed_context else None),
        humidity_pct=(weather.humidity_pct if has_observed_context else None),
        source_observed_at=(weather.observed_at if has_observed_context else None),
        failure_reason=(
            weather.failure_reason[:256]
            if weather is not None
            and not has_app_context
            and not has_observed_context
            and weather.failure_reason is not None
            else None
        ),
        provider=context_provider,
        context_version=context_version,
    )
    analysis.capsule = build_capsule_model(analysis, capsule)


async def _lookup_context_weather(
    lookup: WalkWeatherLookup | None,
    anchor: WalkEvidencePoint | None,
) -> WalkWeatherObservation | None:
    """외부 관측의 어떤 실패도 산책 분석 트랜잭션 밖으로 새지 않게 한다."""

    if lookup is None or anchor is None:
        return None
    try:
        return await lookup(anchor.lat, anchor.lng, anchor.at)
    except Exception as exc:  # noqa: BLE001 - 주입된 adapter도 같은 저하 계약을 지킨다
        return WalkWeatherObservation(
            status="failed",
            failure_reason=f"Life 과거 날씨 조회 실패: {type(exc).__name__}",
        )


def _chunk(points: list) -> WalkPointChunk:
    """좌표 묶음 한 줄.

    `seq_from` · `seq_to` · `point_count` 를 밖에 꺼내 두는 이유는 **payload 를 풀지
    않고** 재시도를 판정하고 개수를 세기 위해서다.
    """
    seqs = [p.client_seq for p in points]
    return WalkPointChunk(
        seq_from=min(seqs),
        seq_to=max(seqs),
        point_count=len(points),
        payload=encode_chunk(points),
    )
