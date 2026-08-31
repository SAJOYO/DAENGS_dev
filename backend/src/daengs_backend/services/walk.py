"""산책 기록의 규칙. 트랜잭션 경계도 여기입니다.

규칙이 거의 없는 것이 이 서비스의 특징입니다. **끝난 기록은 다시 바뀌지 않기**
때문에 병합도 충돌도 없습니다 — 없으면 넣고 있으면 그대로 돌려줍니다.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import Walk, WalkPoint
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.schemas.walk import WalkUpload


class WalkNotFoundError(Exception):
    """내 산책이 아니거나 없습니다.

    **남의 것일 때도 이 예외입니다** — 403 으로 나누면 "그 id 는 존재한다"를
    알려 주는 셈입니다 (`services/pet.py` 와 같은 판단).
    """


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

    강아지는 **내 강아지일 때만** 붙입니다. 남의 pet_id 를 실어 보내도 그 강아지에
    산책이 붙으면 안 됩니다. 내 것이 아니면 조용히 `None` 으로 둡니다 — 산책 자체는
    사용자의 것이라 거절할 이유가 없습니다.

    :returns: (산책, 이번에 새로 만들었는가)
    """
    existing = await walk_repo.get_by_client_session(
        session, app_user_id, body.client_session_id
    )
    if existing is not None:
        return existing, False

    pet_id = body.pet_id
    if pet_id is not None:
        owned = await pet_repo.get_owned(session, app_user_id, pet_id)
        if owned is None:
            pet_id = None

    walk = Walk(
        app_user_id=app_user_id,
        pet_id=pet_id,
        client_session_id=body.client_session_id,
        started_at=body.started_at,
        ended_at=body.ended_at,
        weather_code=body.weather_code,
        is_day=body.is_day,
        temperature_c=body.temperature_c,
    )
    walk.points = [
        WalkPoint(
            client_seq=point.client_seq,
            chain_index=point.chain_index,
            at=point.at,
            lat=point.lat,
            lng=point.lng,
            accuracy_m=point.accuracy_m,
            is_mock=point.is_mock,
        )
        for point in sorted(body.points, key=lambda p: p.client_seq)
    ]
    walk_repo.add(session, walk)
    await session.commit()
    return walk, True
