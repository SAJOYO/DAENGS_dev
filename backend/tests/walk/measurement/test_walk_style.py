"""5단계 정책의 Android golden 계약과 실제 /app/walks 라우터 경계."""

import json
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers.walk import router
from daengs_backend.services.walk_style import walk_style_policy
from tests.walk.support.paths import TESTS

TEST_FIXTURES = TESTS / "fixtures"


def test_policy_matches_android_bundled_contract():
    expected = json.loads((TEST_FIXTURES / "walk-style-v1.json").read_text(encoding="utf-8"))
    assert walk_style_policy().model_dump() == expected


def test_policy_url_is_not_parsed_as_walk_uuid_and_needs_no_walk_lookup():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=uuid.uuid4()
    )
    with TestClient(app) as client:
        response = client.get("/app/walks/style-policy")
    assert response.status_code == 200
    assert response.json() == walk_style_policy().model_dump()


def test_policy_requires_app_authentication():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/app/walks/style-policy").status_code == 401
