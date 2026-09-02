"""DB 연결. 엔진과 세션은 **웹 프로세스**에서 하나만 씁니다.

⚠️ 워커(Celery)는 아래 `engine` 을 쓰면 안 됩니다 — `worker_session()` 을 쓰세요.
   이유는 그 함수의 docstring 에 있습니다 (실제로 한 번 밟은 함정입니다).
"""

import contextlib
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.config import settings

# 엔진은 모듈 로드 시 한 번만 만듭니다. 커넥션 풀을 들고 있어서
# 요청마다 만들면 연결이 계속 새로 열립니다.
engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    # 풀에서 꺼낸 커넥션을 쓰기 전에 살아 있는지 확인합니다.
    # 개발 모드로 돌리는 구성(D-006)이라 pgvector 를 재시작하는 일이 잦은데,
    # 이게 없으면 죽은 커넥션을 꺼내 첫 요청이 한 번 실패합니다.
    pool_pre_ping=True,
)

# expire_on_commit=False : commit 뒤에도 객체 속성을 그대로 읽을 수 있게 합니다.
# 기본값(True)이면 commit 순간 속성이 만료돼, 다시 읽을 때 lazy load 가 돕니다.
# 비동기에서는 그 lazy load 가 MissingGreenlet 으로 터집니다.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 의존성. 요청 하나당 세션 하나.

    commit 은 여기서 하지 않습니다. 트랜잭션 경계는 services/ 가 잡습니다.
    with 블록을 빠져나갈 때 세션이 닫히고, 커밋하지 않은 변경은 롤백됩니다.
    """
    async with SessionLocal() as session:
        yield session


@contextlib.asynccontextmanager
async def worker_session() -> AsyncGenerator[AsyncSession, None]:
    """**Celery 태스크 전용** 세션 — 태스크마다 엔진을 새로 만들고 끝나면 버립니다.

    ⚠️ **위 `SessionLocal` 을 워커에서 쓰면 두 번째 태스크부터 죽습니다.**
       풀에 남은 커넥션은 **그것을 만든 이벤트 루프**에 묶이는데, 태스크는
       `asyncio.run()` 으로 매번 새 루프를 열고 그 루프는 끝나면 닫힙니다.
       그래서 다음 태스크가 죽은 루프의 커넥션을 풀에서 꺼내며 터집니다:

           RuntimeError: got Future attached to a different loop

       2026-09-02 서버 실측입니다 — 첫 분석은 성공했는데 이어진 `gait.cleanup` 이
       이걸로 실패했습니다. **첫 태스크는 항상 성공해서 더 늦게 발견됩니다.**
       `pool_pre_ping` 이 켜져 있어 스택이 ping 에서 끝나는 것도 헷갈리는 지점입니다.

    NullPool 이라 커넥션을 재사용하지 않습니다. 태스크가 분 단위(영상 분석)라
    연결 비용은 무의미하고, **루프 사이에 아무것도 공유하지 않는 것**이 중요합니다.
    """
    engine = create_async_engine(
        settings.database_url, echo=settings.db_echo, poolclass=NullPool
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        # 루프가 닫히기 전에 커넥션을 정리합니다. 안 하면 "Event loop is closed"
        # 경고가 남고, 서버 쪽 연결이 잠깐 새 채로 남습니다.
        await engine.dispose()
