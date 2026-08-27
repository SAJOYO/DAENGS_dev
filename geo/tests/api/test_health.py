from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import get_session
from app.main import app


class FakeDatabase:
    def __init__(self, failure: Exception | None = None):
        self.failure = failure
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        if self.failure is not None:
            raise self.failure


def override_database(database: FakeDatabase):
    async def dependency():
        yield database

    return dependency


def test_liveness_does_not_require_a_database():
    async def broken_dependency():
        raise AssertionError("liveness touched the database")
        yield  # pragma: no cover — makes this an async dependency generator

    app.dependency_overrides[get_session] = broken_dependency
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_readiness_checks_the_database():
    database = FakeDatabase()
    app.dependency_overrides[get_session] = override_database(database)
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "database": "ready"}
    assert database.statements == ["SELECT 1"]


def test_readiness_returns_503_without_leaking_database_details():
    database = FakeDatabase(SQLAlchemyError("postgres password=secret"))
    app.dependency_overrides[get_session] = override_database(database)
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    assert "secret" not in response.text


def test_readiness_timeout_is_unavailable_too():
    database = FakeDatabase(TimeoutError())
    app.dependency_overrides[get_session] = override_database(database)
    try:
        with TestClient(app) as client:
            response = client.get("/health/ready")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 503
