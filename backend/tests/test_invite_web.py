"""routers/invite_web.py — 공동 돌봄 초대 링크의 웹 폴백.

인증도 DB 도 없는 정적 라우터라 다른 라우터 테스트처럼 fake DB 를 세울 필요가
없다 — 이 라우터만 얹은 얇은 앱으로 충분하다.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.config import settings
from daengs_backend.routers import invite_web

app = FastAPI()
app.include_router(invite_web.router)
client = TestClient(app)


def test_초대_페이지는_html을_돌려준다():
    resp = client.get("/invite")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "앱 설치하기" in resp.text
    assert "이미 설치했나요? 앱에서 초대 열기" in resp.text
    assert "초대 링크 복사" in resp.text
    assert "복사가 안 되나요? 링크 직접 보기" in resp.text


def test_초대_페이지는_캐시하지_않는다():
    resp = client.get("/invite")

    assert resp.headers["cache-control"] == "no-store"


def test_초대_페이지는_토큰을_모른다():
    """토큰은 프래그먼트에만 있고 서버로 안 온다 — 쿼리로 흉내 내 봐도 응답에 안 보여야 한다."""
    resp = client.get("/invite?token=should-not-appear-server-side")

    assert "should-not-appear-server-side" not in resp.text


def test_지문이_없으면_빈_배열을_준다(monkeypatch):
    """가짜 지문을 채워 넣지 않는다 — 검증이 그냥 실패하는 쪽을 고른다."""
    monkeypatch.setattr(settings, "play_signing_sha256_fingerprints", [])

    resp = client.get("/.well-known/assetlinks.json")

    assert resp.status_code == 200
    assert resp.json() == []


def test_지문이_있으면_App_Links_문서를_만든다(monkeypatch):
    monkeypatch.setattr(
        settings,
        "play_signing_sha256_fingerprints",
        ["AA:BB:CC:DD"],
    )

    resp = client.get("/.well-known/assetlinks.json")

    body = resp.json()
    assert len(body) == 1
    target = body[0]["target"]
    assert target["namespace"] == "android_app"
    assert target["package_name"] == "com.daengs.app"
    assert target["sha256_cert_fingerprints"] == ["AA:BB:CC:DD"]
    assert body[0]["relation"] == ["delegate_permission/common.handle_all_urls"]
