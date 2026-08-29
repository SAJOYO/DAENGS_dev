"""Alembic 실행 환경.

DB URL 은 앱과 **같은 곳**(`app.core.config.settings`)에서 온다. 마이그레이션이 앱과
다른 DB 를 보는 사고는 설정이 두 벌일 때 생긴다.

드라이버만 바꿔 낀다. 앱은 asyncpg 로 돌지만 마이그레이션은 동기 psycopg 로 돌린다 —
DDL 은 배포 시 한 번 순차 실행되는 것이라 async 로 얻을 게 없고, 동기 쪽이 원본 SQL 을
그대로 흘려보내기 쉽다.
"""

import sys
from logging.config import fileConfig

from sqlalchemy import create_engine

from alembic import context
from app.core.config import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate 를 **끈다**. ORM 에는 Place 하나뿐이고 facility·walk·anchor 를 비롯한
# 대부분의 테이블이 없어서, metadata 를 넘기면 `revision --autogenerate` 가 그것들을 전부
# "삭제 대상"으로 판단한다. 모든 스키마를 ORM 으로 표현할 계획이 서기 전까지는 리비전을
# 손으로 쓴다. 되살리려면 전체 테이블 metadata + PostGIS 시스템 객체 제외 필터가 함께 필요하다.
target_metadata = None


def migration_url() -> str:
    return settings.database_url.replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    # 원본 SQL 의 주석이 한국어다. Windows 기본 콘솔(cp949)로 내보내면 em dash 하나에
    # UnicodeEncodeError 로 죽는다 — alembic.ini 를 ASCII 로 둔 것과 같은 이유다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    context.configure(
        url=migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(migration_url(), poolclass=None)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
