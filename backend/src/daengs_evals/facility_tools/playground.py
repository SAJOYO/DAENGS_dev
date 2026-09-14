"""Run the actual command/tool modules against synthetic places on loopback only."""

import argparse
import asyncio
import json
import re
import secrets
from pathlib import Path
from time import monotonic, perf_counter
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from daengs_backend.services.facility_tools.catalog import SYSTEM_INSTRUCTION, tools_for
from daengs_backend.services.facility_tools.dialogue import FacilityDialogue, ToolTurn
from daengs_evals.facility_tools.workspace import MemoryWorkspace
from daengs_evals.place_conversation.fixtures import FixtureSearcher, initial_filters
from daengs_place.place.commands.contract import FacilityState
from daengs_place.place.commands.executor import FacilityCommands
from daengs_place.place.commands.view import ui_view
from daengs_place.place.providers.conversation_gemini import GeminiConversation

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parents[2] / "evals/place_conversation"


async def new_workspace():
    fixtures = json.loads((DATA / "fixtures.policy.v1.json").read_text(encoding="utf-8"))
    setup = json.loads((DATA / "cases.v1.jsonl").read_text(encoding="utf-8").splitlines()[0])[
        "setup"
    ]
    searcher = FixtureSearcher(setup, fixtures)
    workspace = MemoryWorkspace(
        FacilityState(filters=initial_filters(setup)), FacilityCommands(searcher)
    )
    await workspace.execute("bootstrap", "search_places", {}, 0)
    return workspace


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)


class Chat(Input):
    query: str = Field(min_length=1, max_length=1000)


class Manual(Input):
    name: str = Field(max_length=100)
    arguments: dict


def create_app(
    key, model="gemini-3.1-flash-lite", *, port=8769, provider_factory=GeminiConversation
):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sessions = {}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @app.middleware("http")
    async def loopback(request, call_next):
        if request.headers.get("host") not in hosts:
            return JSONResponse({"error": "host_rejected"}, status_code=403)
        if request.method == "POST":
            if request.headers.get("origin") not in {"http://" + h for h in hosts}:
                return JSONResponse({"error": "origin_rejected"}, status_code=403)
            if "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"error": "json_required"}, status_code=415)
            if len(await request.body()) > 16384:
                return JSONResponse({"error": "request_too_large"}, status_code=413)
        sid = request.cookies.get("facility_tools")
        now = monotonic()
        for expired in [
            k for k, v in sessions.items() if now - v["touched"] > 1800 and not v["running"]
        ]:
            sessions.pop(expired)
        if sid not in sessions:
            if len(sessions) >= 32:
                return JSONResponse({"error": "session_limit"}, status_code=429)
            sid = secrets.token_urlsafe(24)
            sessions[sid] = {
                "workspace": await new_workspace(),
                "turns": {},
                "recent": [],
                "last": None,
                "touched": now,
                "running": False,
                "lock": asyncio.Lock(),
            }
        session = sessions[sid]
        session["touched"] = now
        request.state.session = session
        response = await call_next(request)
        response.set_cookie("facility_tools", sid, httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    def view(session):
        state = session["workspace"].state
        return {
            "ui": ui_view(state),
            "model": model,
            "ready": bool(key),
            "recent": session["recent"],
            "system_prompt": SYSTEM_INSTRUCTION,
            "tools": tools_for(state),
            "last": session["last"],
            "boundary": "실제 Gemini 함수 호출 · 합성 장소 6개 · 로컬 메모리 세션",
        }

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "web/index.html")

    @app.get("/app.js")
    async def javascript():
        return FileResponse(ROOT / "web/app.js", media_type="text/javascript")

    @app.get("/style.css")
    async def css():
        return FileResponse(ROOT / "web/style.css", media_type="text/css")

    @app.get("/api/state")
    async def state(request: Request):
        return view(request.state.session)

    @app.post("/api/command")
    async def manual(command: Manual, request: Request):
        session = request.state.session
        try:
            result = await session["workspace"].execute(
                "manual:" + str(command.request_id),
                command.name,
                command.arguments,
                command.expected_revision,
            )
        except (ValidationError, ValueError):
            return JSONResponse({"error": "invalid_arguments"}, status_code=422)
        return {**view(session), "command": result.model_dump(mode="json", exclude={"state"})}

    @app.post("/api/chat")
    async def chat(message: Chat, request: Request):
        if not key:
            return JSONResponse({"error": "model_key_missing"}, status_code=503)
        session = request.state.session
        async with session["lock"]:
            if session["running"]:
                return JSONResponse({"error": "turn_running"}, status_code=409)
            request_id = str(message.request_id)
            turn = session["turns"].get(request_id)
            if turn and turn.query != message.query:
                return JSONResponse({"error": "request_id_reused"}, status_code=409)
            if turn is None:
                if len(session["turns"]) >= 60:
                    return JSONResponse({"error": "turn_limit"}, status_code=429)
                turn = ToolTurn(request_id, message.query, message.expected_revision)
                session["turns"][request_id] = turn
            elif turn.status == "ready":
                return {**view(session), "turn": turn_view(turn)}
            session["running"] = True
        started = perf_counter()
        try:
            provider = provider_factory(key, model)
            async with asyncio.timeout(90):
                await FacilityDialogue(provider).run(
                    turn, session["workspace"], recent=session["recent"]
                )
        except TimeoutError:
            turn.status = "timeout"
        finally:
            session["running"] = False
        summary = turn_view(turn)
        summary["latency_ms"] = round((perf_counter() - started) * 1000)
        session["last"] = summary
        if turn.status == "ready":
            session["recent"] = [
                *session["recent"],
                {"query": message.query, "answer": turn.response},
            ][-6:]
        return {**view(session), "turn": summary}

    @app.get("/api/trace/{request_id}")
    async def trace(request_id: UUID, request: Request):
        turn = request.state.session["turns"].get(str(request_id))
        if turn is None:
            return JSONResponse({"error": "turn_missing"}, status_code=404)
        return {"calls": turn.provider_calls, "executions": turn.executions}

    return app


def turn_view(turn):
    return {
        "request_id": turn.request_id,
        "status": turn.status,
        "answer": turn.response,
        "revision": turn.revision,
        "executions": turn.executions,
        "model_calls": len(turn.provider_calls),
    }


def read_key(path):
    match = re.search(
        r"(?im)^\s*(?:GEMINI_API_KEY\s*=|gemini\s*:)\s*(\S+)", path.read_text(encoding="utf-8-sig")
    )
    if not match:
        raise ValueError("Gemini key entry is missing")
    return match.group(1).strip("\"'")


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()
    app = create_app(read_key(args.key_file), args.model, port=args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
