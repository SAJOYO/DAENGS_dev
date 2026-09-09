"""이름 검색 HTTP/plan/SQL 경계. 실제 DB나 LLM을 호출하지 않는다."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from daengs_place.core.db import get_session
from daengs_place.main import app
from daengs_place.place.planning.guard import PlanValidationError, guard_plan_transition
from daengs_place.place.search import (
    PlaceSearchRequest,
    compile_place_search_request,
    search_place_groups,
)


def request(**kwargs):
    return PlaceSearchRequest(lat=37.556, lng=126.923, kinds=["cafe"], **kwargs)


@pytest.mark.parametrize("query", ["홍대", "%_\\", "O'Reilly", "Cafe", "🐕" * 120])
def test_query_is_a_trimmed_literal_in_the_executable_plan(query):
    assert compile_place_search_request(request(name_query=f"  {query}  ")).name_query == query


@pytest.mark.parametrize("query", ["", "  ", "\t\n"])
def test_blank_query_preserves_existing_search(query):
    assert compile_place_search_request(request(name_query=query)).name_query == ""


@pytest.mark.parametrize("query", ["가" * 121, "🐕" * 121, None, 123, ["홍대"]])
def test_invalid_query_is_rejected(query):
    with pytest.raises(ValidationError):
        request(name_query=query)


@pytest.mark.parametrize("next_query", ["", "다른 이름"])
def test_plan_editor_cannot_relax_or_replace_explicit_name(next_query):
    plan = compile_place_search_request(request(name_query="홍대"))
    with pytest.raises(PlanValidationError, match="name query"):
        guard_plan_transition(plan, plan.model_copy(update={"name_query": next_query}))
    assert guard_plan_transition(plan, plan) == plan


def empty_db():
    return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=list)))


@pytest.mark.parametrize("kind", ["cafe", "pet_shop", "shopping", "hospital", "pharmacy"])
async def test_name_reaches_each_source_sql_before_limit_and_response_echo(kind):
    db = empty_db()
    query = "%_ O'Reilly"
    response = await search_place_groups(db, PlaceSearchRequest(
        lat=37.556, lng=126.923, kinds=[kind], name_query=query, limit_per_kind=1,
    ))
    assert response.name_query == query
    assert response.groups[0].results == []
    stmt = db.execute.call_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert sql.index("strpos(") < sql.rindex("LIMIT")
    # 이름·종류·공간·원천 권위 필터를 SQL에 함께 둔다. 와일드카드 연산자를 쓰지 않는다.
    assert "ST_DWithin" in sql and "kind" in sql and "source" in sql
    assert "LIKE" not in sql.upper()
    assert query not in sql
    params = (db.execute.call_args.args[1] if len(db.execute.call_args.args) > 1
              else stmt.compile().params)
    assert query in params.values()
    if kind not in {"hospital", "pharmacy"}:
        assert params["require_canonical_identity"] is True
        assert params["limit"] == 2
        # 보강 원천 이름이 아니라 노출되는 canonical 이름을 검사한다.
        assert "lower(f.name)" in sql
    else:
        assert "lower(place.name)" in sql
        assert "public:mois:animal_" in str(params)


@pytest.mark.parametrize("query", [None, "", "  ", "  홍대  "])
def test_http_echoes_only_an_applied_nonblank_name(query):
    db = empty_db()

    async def no_db():
        yield db

    app.dependency_overrides[get_session] = no_db
    try:
        body = {"lat": 37.556, "lng": 126.923, "kinds": ["cafe"]}
        if query is not None:
            body["name_query"] = query
        with TestClient(app) as client:
            response = client.post("/v2/places/search", json=body)
        assert response.status_code == 200
        if query and query.strip():
            assert response.json()["name_query"] == query.strip()
        else:
            assert "name_query" not in response.json()
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_http_rejects_overlong_query_without_sql():
    db = empty_db()

    async def no_db():
        yield db

    app.dependency_overrides[get_session] = no_db
    try:
        with TestClient(app) as client:
            response = client.post("/v2/places/search", json={
                "lat": 37.556, "lng": 126.923, "kinds": ["cafe"], "name_query": "가" * 121,
            })
        assert response.status_code == 422
        db.execute.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_session, None)
