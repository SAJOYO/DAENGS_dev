# db.py = Postgres(pgvector) 데이터베이스에 "연결(connection)"을 만들어주는 곳.
# 이 함수를 호출할 때마다 새 연결을 하나 맺어서 돌려줍니다 (repository.py에서 사용).

import psycopg  # PostgreSQL용 파이썬 드라이버 (DB와 실제로 통신하는 라이브러리)
from pgvector.psycopg import register_vector  # 벡터(embedding) 타입을 psycopg가 이해하게 해주는 헬퍼

from app.config import get_settings


def get_connection() -> psycopg.Connection:
    settings = get_settings()
    # psycopg.connect(주소) : DB에 실제로 접속. autocommit=True는 각 SQL 문장을 실행하자마자
    # 바로 확정(commit)한다는 뜻 — 별도로 BEGIN/COMMIT을 신경 쓰지 않아도 됨 (이 MVP 규모에 적합).
    conn = psycopg.connect(settings.database_url, autocommit=True)
    # register_vector(conn) : 이 연결에서 pgvector의 "vector" 컬럼 타입을
    # 파이썬 list[float] <-> DB vector 타입으로 자동 변환할 수 있게 등록.
    # (참고: 리스트를 그대로 넘기면 자동 변환이 안 붙는 경우가 있어서, repository.py에서는
    #  pgvector.Vector(...) 로 명시적으로 감싸서 넘김.)
    register_vector(conn)
    return conn
