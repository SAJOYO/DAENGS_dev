"""Loopback-only review app. Live and fixture routes never fall back to each other.

Run from backend: uv run python ../tools/facility-review/serve.py
Optional local config beside this file: connection.local.json (never committed).
"""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None)


def config():
    path = ROOT / "connection.local.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.headers.get("host") not in {"127.0.0.1:8766", "localhost:8766", "testserver"}:
        return JSONResponse({"detail": "Local review only"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:8766", "http://localhost:8766"}:
        return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
    if (
        request.method == "POST"
        and request.headers.get("content-type", "").split(";")[0] != "application/json"
    ):
        return JSONResponse({"detail": "JSON required"}, status_code=415)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/config")
def get_config():
    value = config()
    return {
        "live_ready": bool(value.get("backend_url") and value.get("access_token")),
        "profile_source": "app-pets",
        "fixture_label": "저장 표본 + 고정 해석 · 실제 AI/DB 호출 없음",
    }


async def body(request):
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > 64 * 1024:
            raise HTTPException(413, "Request too large")
    try:
        return json.loads(data)
    except ValueError as exc:
        raise HTTPException(400, "Invalid JSON") from exc


def connection(require_token: bool = True):
    value = config()
    url = value.get("backend_url", "").rstrip("/")
    parsed = urlsplit(url)
    if (
        not parsed.netloc
        or parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(503, "개발 API 주소가 설정되지 않았어요.")
    if require_token and not value.get("access_token"):
        raise HTTPException(503, "개발 계정 연결이 설정되지 않았어요.")
    headers = {"Authorization": "Bearer " + value["access_token"]} if require_token else {}
    return url, headers


@app.get("/api/live/profiles")
async def live_profiles():
    # Standalone host loads the same account PetListResponse MainActivity supplies.
    # This is read-only: facility search never creates or edits a dog profile.
    url, headers = connection()
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(url + "/app/pets", headers=headers)
        if response.status_code != 200:
            raise HTTPException(
                response.status_code if response.status_code in {401, 403} else 502,
                "계정의 반려견 목록을 불러오지 못했어요.",
            )
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("pets"), list):
            raise TypeError("Invalid pet list")
        # Token changes conservatively clear selection; raw credentials stay server-side.
        data["owner_context"] = hashlib.sha256(
            (url + headers["Authorization"]).encode()
        ).hexdigest()
        return data
    except (TypeError, ValueError, httpx.RequestError):
        raise HTTPException(503, "계정의 반려견 목록을 불러오지 못했어요.") from None


@app.get("/api/fixture/profiles")
def fixture_profiles():
    return json.loads((ROOT / "profiles.fixture.json").read_text(encoding="utf-8"))


@app.post("/api/live/{mode}")
async def live(mode: str, request: Request):
    if mode not in {"ai", "normal", "actions"}:
        raise HTTPException(404)
    url, headers = connection(require_token=mode != "normal")
    path = {
        "ai": "/app/places/discovery",
        "actions": "/app/places/discovery/actions",
        "normal": "/v2/places/search",
    }[mode]
    try:
        async with httpx.AsyncClient(timeout=55, follow_redirects=False) as client:
            response = await client.post(url + path, json=await body(request), headers=headers)
        try:
            data = response.json()
        except ValueError:
            raise HTTPException(502, "개발 서버에서 JSON 검색 응답을 받지 못했어요.") from None
        return JSONResponse(data, status_code=response.status_code)
    except httpx.TimeoutException:
        raise HTTPException(504, "개발 서버 응답 시간이 초과됐어요.") from None
    except httpx.RequestError:
        raise HTTPException(503, "개발 서버에 연결할 수 없어요.") from None


@app.post("/api/fixture/{mode}")
async def fixture(mode: str, request: Request):
    from fixtures import run_fixture

    owner = request.cookies.get("facility_review_owner") or str(uuid4())
    result = await run_fixture(mode, await body(request), owner=owner)
    response = JSONResponse(result.model_dump(mode="json"))
    response.set_cookie(
        "facility_review_owner", owner, httponly=True, samesite="strict", max_age=86400
    )
    return response


app.mount(
    "/vendor", StaticFiles(directory=ROOT / "node_modules" / "leaflet" / "dist"), name="leaflet"
)
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="review")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False)
