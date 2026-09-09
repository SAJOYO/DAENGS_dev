"""산책 repository의 async 응답 로딩 계약."""

import uuid
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from daengs_backend.repositories import walk as walk_repo


class CapturingSession:
    statement: Select[Any] | None = None

    async def scalar(self, statement: Select[Any]) -> None:
        self.statement = statement


async def test_업로드_재시도_조회는_상세_응답_관계를_미리_읽는다() -> None:
    """라우터가 points·pets에 접근해도 async lazy load가 일어나면 안 됩니다."""
    session = CapturingSession()

    await walk_repo.get_by_client_session(
        cast(AsyncSession, session),
        uuid.uuid4(),
        uuid.uuid4(),
    )

    assert session.statement is not None
    relationship_keys = {
        element.key
        for option in session.statement._with_options
        for element in option.path.path
        if hasattr(element, "key")
    }
    assert relationship_keys == {"points", "pets"}
