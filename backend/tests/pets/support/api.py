"""Shared setup for profile and photo tests; each test owns its identity and state."""

import uuid

import pytest
from fakes import FakeAdmin, FakeAppUser, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import pet as pet_router


def make_store(owner: uuid.UUID, monkeypatch: pytest.MonkeyPatch) -> Store:
    store = install(Store(FakeAdmin()), monkeypatch)
    store.add_app_user(FakeAppUser(kakao_id=1, id=owner))
    return store


def make_client(owner: uuid.UUID) -> TestClient:
    app = FastAPI()
    app.include_router(pet_router.router)
    app.dependency_overrides[next(iter(CurrentAppUser.__metadata__)).dependency] = lambda: (
        AppPrincipal(app_user_id=owner)
    )
    return TestClient(app, raise_server_exceptions=False)
