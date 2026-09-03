"""산책 기록의 규칙. 트랜잭션 경계도 여기입니다.

규칙이 거의 없는 것이 이 서비스의 특징입니다. **끝난 기록은 다시 바뀌지 않기**
때문에 병합도 충돌도 없습니다 — 없으면 넣고 있으면 그대로 돌려줍니다.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Walk, WalkAnalysis, WalkPet, WalkPointChunk
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.schemas.walk import (
    WalkFinalizeRequest,
    WalkPointsAppend,
    WalkUpload,
)
from daengs_backend.services.walk_analysis import build_analysis_models
from daengs_backend.services.walk_capsule import build_capsule_model
from daengs_backend.services.walk_chunk import encode_chunk
from daengs_backend.services.walk_finalize import prepare_finalized_walk
from daengs_walk import analyze_walk, build_cellophane, build_walk_capsule


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


async def get_walk(
    session: AsyncSession, app_user_id: uuid.UUID, walk_id: uuid.UUID
) -> Walk:
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
    찾아봅니다. DB 에도 UNIQUE 가 걸려 있어 경쟁이 나도 두 건은 안 생깁니다.

    **덮어쓰지 않습니다.** 끝난 기록은 바뀌지 않으므로 다시 온 것은 재시도일 뿐이고,
    좌표를 다시 넣으면 이미 저장한 원본을 흔들 위험만 있습니다.

    강아지는 **내 강아지만** 붙입니다. 남의 pet_id 를 실어 보내도 그 강아지에
    산책이 붙으면 안 됩니다. 내 것이 아닌 id 는 조용히 뺍니다 — 산책 자체는
    사용자의 것이라 거절할 이유가 없습니다.

    **아무도 안 붙어도 저장합니다.** 강아지를 등록하기 전에 걸었거나 고르지 않고
    나선 경우인데, 그래도 사람이 걸은 것은 걸은 것입니다.

    :returns: (산책, 이번에 새로 만들었는가)
    """
    existing = await walk_repo.get_by_client_session(
        session, app_user_id, body.client_session_id
    )
    if existing is not None:
        return existing, False

    mine = await pet_repo.owned_ids(session, app_user_id, body.pet_ids)

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
    walk_repo.add(session, walk)
    await session.commit()
    return walk, True


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


async def finalize_walk(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    walk_id: uuid.UUID,
    manifest: WalkFinalizeRequest,
) -> tuple[WalkAnalysis, bool]:
    """완전한 좌표열을 계산하고 분석·sheet·봉인 상태를 한 번에 commit한다.

    같은 finalize를 다시 부르면 이미 저장된 같은 identity를 돌려준다.
    append와 같은 Walk 행을 잠그므로 두 요청이 동시에 입력을 바꾸지 못한다.
    """
    try:
        walk = await walk_repo.get_owned_for_update(session, app_user_id, walk_id)
        if walk is None:
            raise WalkNotFoundError

        prepared = prepare_finalized_walk(walk.points, manifest)
        if walk.analysis_state == "derived":
            existing = await walk_repo.get_analysis_for_input(
                session,
                walk_id=walk.id,
                input_fingerprint=prepared.input_fingerprint,
            )
            if existing is None:
                raise WalkStateConflictError(
                    "finalized_analysis_not_found",
                    "봉인 상태와 저장된 분석 결과가 맞지 않습니다.",
                )
            if existing.capsule is None:
                # Capsule migration을 먼저 적용하고 코드를 배포하는 사이에도 이전
                # 프로세스가 finalize할 수 있다. 그 짧은 창에 생긴 Analysis는 같은
                # Walk 행 잠금 안에서 당시 메타데이터로 한 번만 복구한다.
                _attach_capsule(
                    walk,
                    existing,
                    sealed_at=existing.derived_at,
                    provider="legacy_walk_metadata_v1",
                )
                await session.flush()
            await session.commit()  # 필요하면 legacy seal을 복구하고 멱등 응답한다.
            return existing, False

        if walk.analysis_state != "collecting":
            raise WalkStateConflictError(
                "walk_state_invalid",
                f"알 수 없는 산책 봉인 상태입니다: {walk.analysis_state!r}",
            )

        evidence = analyze_walk(
            walk.id,
            walk.started_at,
            walk.ended_at,
            prepared.points,
        )
        analysis = build_analysis_models(
            prepared,
            evidence,
            build_cellophane(evidence),
        )
        _attach_capsule(
            walk,
            analysis,
            sealed_at=datetime.now(UTC),
            provider="android_walk_upload_v1",
        )
        walk_repo.add_analysis(session, analysis)
        walk.analysis_state = "derived"
        await session.flush()
        await session.commit()
        return analysis, True
    except Exception:
        await session.rollback()
        raise


def _attach_capsule(
    walk: Walk,
    analysis: WalkAnalysis,
    *,
    sealed_at: datetime,
    provider: str,
) -> None:
    """이미 저장된 Walk 원자만으로 Analysis에 Capsule seal을 붙인다."""

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
            float(walk.temperature_c) if walk.temperature_c is not None else None
        ),
        provider=provider,
    )
    analysis.capsule = build_capsule_model(analysis, capsule)


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
