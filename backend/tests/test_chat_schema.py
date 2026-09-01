"""대화 기록의 스키마와 공개 계약.

`test_chat_sessions.py` 는 가짜 리포지토리로 **규칙**을 보고, 여기서는 그 규칙이
기대는 **실제 스키마**를 봅니다 — FK 의 삭제 동작이 뒤바뀌면 가짜 테스트는 그대로
통과하면서 운영에서 사용자의 보관함이 사라집니다.

Swagger/OpenAPI 도 여기서 봅니다. 라우트가 문서에 안 뜨면 앱 쪽에서 계약을 못 읽습니다.
"""

from pathlib import Path

from daengs_backend.models import ChatMessage, ChatSession, ChatSummary

_INIT_SQL = (
    Path(__file__).resolve().parents[2] / "db" / "init" / "07_chats.sql"
)
_MIGRATION_SQL = (
    Path(__file__).resolve().parents[2] / "db" / "migrations" / "2026-09-01_chats.sql"
)


def _ondelete(model: type, column: str) -> str | None:
    fk = next(iter(model.__table__.c[column].foreign_keys))
    return fk.ondelete


# --- FK 의 삭제 동작 ----------------------------------------------------------


def test_메시지는_세션과_함께_지워진다() -> None:
    """세션만 지우고 원문이 남으면 "지웠다"가 거짓말이 됩니다."""
    assert _ondelete(ChatMessage, "session_id") == "CASCADE"


def test_저장된_요약은_세션이_지워져도_남는다() -> None:
    """**이 카드의 핵심 불변식입니다.**

    여기가 CASCADE 로 바뀌면 5개 유지가 도는 순간 사용자가 저장해 둔 요약이
    조용히 사라집니다. 가짜 리포지토리는 그것을 못 잡습니다.
    """
    assert _ondelete(ChatSummary, "session_id") == "SET NULL"
    assert ChatSummary.__table__.c["session_id"].nullable is True


def test_대화는_계정과_강아지에_묶여_있다() -> None:
    """탈퇴가 개인정보를 파기하는데 대화가 남으면 파기가 반쪽입니다."""
    assert _ondelete(ChatSession, "app_user_id") == "CASCADE"
    assert _ondelete(ChatSession, "pet_id") == "CASCADE"
    # 요약의 주인은 세션이 아니라 계정입니다 — 세션이 사라져도 조회돼야 합니다.
    assert _ondelete(ChatSummary, "app_user_id") == "CASCADE"


# --- SQL 과 모델이 갈라지지 않았는가 ------------------------------------------


def test_init_과_migration_이_같은_삭제_동작을_말한다() -> None:
    """Alembic 이 없어서 **둘을 손으로 맞춥니다** (CLAUDE.md). 갈라지면 여기서 걸립니다."""
    for sql_file in (_INIT_SQL, _MIGRATION_SQL):
        sql = sql_file.read_text(encoding="utf-8")
        assert "REFERENCES chat_sessions(id) ON DELETE CASCADE" in sql, sql_file.name
        assert "REFERENCES chat_sessions(id) ON DELETE SET NULL" in sql, sql_file.name


def test_마이그레이션은_여러_번_돌려도_안전하다() -> None:
    """버전 테이블이 없어 무엇이 적용됐는지 DB 가 기억하지 않습니다."""
    sql = _MIGRATION_SQL.read_text(encoding="utf-8")
    assert sql.count("CREATE TABLE IF NOT EXISTS") == 3
    assert "CREATE INDEX IF NOT EXISTS" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in sql
    # ADD CONSTRAINT 에는 IF NOT EXISTS 가 없어서 존재를 직접 봐야 합니다.
    assert "pg_constraint" in sql
    assert "BEGIN;" in sql and "COMMIT;" in sql


def test_멱등_인덱스가_양쪽에_다_있다() -> None:
    """두 번 눌렀을 때를 마지막으로 막는 것이 이 인덱스입니다."""
    for sql_file in (_INIT_SQL, _MIGRATION_SQL):
        sql = sql_file.read_text(encoding="utf-8")
        assert "chat_messages_idempotency_idx" in sql, sql_file.name
        assert "chat_summaries_idempotency_idx" in sql, sql_file.name


# --- OpenAPI ------------------------------------------------------------------


def _openapi() -> dict:
    """라우터만 얹어서 문서를 만듭니다 — `main.py` 를 띄우면 설정·DB 가 딸려 옵니다."""
    from fastapi import FastAPI

    from daengs_backend.routers import chat as chat_router

    app = FastAPI()
    app.include_router(chat_router.router)
    return app.openapi()


def test_대화_기록_라우트가_문서에_뜬다() -> None:
    paths = _openapi()["paths"]
    assert set(paths) == {
        "/app/chats",
        "/app/chats/summaries",
        "/app/chats/{session_id}",
        "/app/chats/{session_id}/summary",
    }
    assert set(paths["/app/chats"]) == {"get", "post"}
    assert set(paths["/app/chats/{session_id}"]) == {"get", "delete"}


def test_응답_스키마가_문서에_박혀_있다() -> None:
    """`response_model` 없이 나가면 앱이 필드를 추측하게 됩니다."""
    schemas = _openapi()["components"]["schemas"]
    for name in (
        "ChatSessionResponse",
        "ChatSessionListResponse",
        "ChatSessionDetailResponse",
        "ChatSummaryResponse",
        "ChatSummaryListResponse",
        "ChatSessionCreate",
        "ChatSummaryCreate",
    ):
        assert name in schemas, name


def test_배지_값이_문서에_열거된다() -> None:
    """앱이 배지 라벨을 매핑하려면 **가능한 값이 문서에 있어야** 합니다.

    Pydantic 은 `Literal` 을 별도 컴포넌트로 빼지 않고 필드 안에 펼칩니다.
    배지가 뜨는 세 곳(카드 · 메시지 · 저장된 요약) 모두에 값이 박혀 있어야
    앱이 어느 응답을 받든 같은 매핑을 쓸 수 있습니다.
    """
    schemas = _openapi()["components"]["schemas"]
    for name in ("ChatSessionResponse", "ChatMessageResponse", "ChatSummaryResponse"):
        field = schemas[name]["properties"]["agent_categories"]
        assert field["items"]["enum"] == ["training", "life", "walk"], name


def test_강아지_스코프가_문서에_드러난다() -> None:
    """목록은 `pet_id` 없이 부를 수 없습니다 — 스코프가 계약의 일부입니다."""
    params = _openapi()["paths"]["/app/chats"]["get"]["parameters"]
    pet_param = next(p for p in params if p["name"] == "pet_id")
    assert pet_param["required"] is True
    assert pet_param["in"] == "query"


def test_에러_응답이_문서화되어_있다() -> None:
    paths = _openapi()["paths"]
    assert "404" in paths["/app/chats"]["get"]["responses"]
    assert "401" in paths["/app/chats"]["get"]["responses"]
    summary_responses = paths["/app/chats/{session_id}/summary"]["post"]["responses"]
    # 빈 대화(409)와 공급자 실패(502)가 앱에서 다르게 보여야 합니다.
    assert "409" in summary_responses
    assert "502" in summary_responses


def test_요약_생성은_멱등_키를_요구한다() -> None:
    """두 번 눌렀을 때를 앱이 알아서 막게 두지 않습니다."""
    schemas = _openapi()["components"]["schemas"]
    assert schemas["ChatSummaryCreate"]["required"] == ["client_request_id"]
