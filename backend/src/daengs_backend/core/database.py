"""DB 연결. 엔진과 세션은 앱 전체에서 하나만 씁니다."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
