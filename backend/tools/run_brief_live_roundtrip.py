"""One explicit live Gemini run through HTTP/lifecycle/real JSONB using synthetic sources.

Only identity authentication and source acquisition are fixtures. Writing, reservation,
completion, database repository and GET projection are the production implementations.
No TMAP, external spatial lookup, prompt override, response substitution or retries.
"""

import argparse
import asyncio
import html
import json
import sys
import uuid
from dataclasses import replace
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    from run_diary_route_scenario import configure

    configure(args.env)  # Credential allowlist only; does not call its route/scenario code.
    asyncio.run(run(args))


async def run(args):
    import httpx
    from fastapi import FastAPI
    from pytest import MonkeyPatch
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from daengs_backend.config import settings
    from daengs_backend.core.database import get_session
    from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
    from daengs_backend.routers import walk_storyboard as router
    from daengs_backend.services.walk_diary.lifecycle import relational
    from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary
    from daengs_backend.services.walk_diary.preparation.relational_base import (
        assemble_relational_base,
    )
    from daengs_backend.services.walk_diary.runtime import write_relational_board
    from daengs_walk.diary.contracts.input import DiaryInput
    from daengs_walk.diary.selection.board import observed_anchor
    from tests.walk.diary.test_diary_activity import prepared as activity
    from tests.walk.diary.test_diary_space_integration import public_collector
    from tests.walk.support.route_patterns import route

    args.output.mkdir(parents=True, exist_ok=False)

    def dump(name, value):
        (args.output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    assembled = activity()[0].input
    raw = assembled.source.model_dump(mode="json")
    owner = uuid.uuid5(uuid.NAMESPACE_URL, "daengs-step5-owner")
    session_id = uuid.uuid5(uuid.NAMESPACE_URL, "daengs-step5-session")
    raw.update(owner_id=str(owner), client_session_id=str(session_id))
    dogs = {
        key: str(uuid.uuid5(uuid.NAMESPACE_URL, "daengs-step5-dog:" + key))
        for key in raw["pet_ids"]
    }
    if not dogs:
        dogs = {"fixture-dog": str(uuid.uuid5(uuid.NAMESPACE_URL, "daengs-step5-dog"))}
    raw["pet_ids"] = list(dogs.values())
    points = route("pace-return", [(0, 0, 0), (120, 0, 120), (220, 0, 20), (240, 0, 16)]).points
    for number, record in enumerate(raw["records"]):
        record["ref"]["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"daengs-step5-record:{number}"))
        if record["content"]["kind"] == "behavior":
            record["content"]["pet_id"] = next(iter(dogs.values()))
            # Choose an actual fix on the same route, away from the ending card.
            record["anchor"] = (
                observed_anchor(points[36])
                .model_copy(update={"time_basis": "recorded_at"})
                .model_dump(mode="json")
            )
    source = DiaryInput.model_validate(raw)
    assembled = replace(
        assembled, source=source, pet_names=tuple((key, "보리") for key in dogs.values())
    )
    base = assemble_relational_base(assembled, 3)
    scenes = sorted(base.board.scenes, key=lambda s: (s.anchor.event_at, s.id))
    action_scene = next(
        s
        for s in scenes
        if getattr(s.core, "record", None) and s.core.record.content.kind == "behavior"
    )
    selected = sorted(
        {scenes[0].id, action_scene.id, scenes[-1].id},
        key=lambda key: next(i for i, s in enumerate(scenes) if s.id == key),
    )
    if len(selected) != 3:
        raise ValueError("scenario must contain exactly three distinct scenes")
    captured = {}
    with MonkeyPatch.context() as patches:
        collect = public_collector.__wrapped__(patches)

        async def prepare(base, *, scene_ids=None):
            backgrounds = await collect(base.board)
            names = ("성내천로", "성내천로12길", "오금로")
            roads = []
            for scene_id, name in zip(selected, names, strict=True):
                scene = next(s for s in base.board.scenes if s.id == scene_id)
                roads.append(
                    {
                        "point": scene.anchor.point.model_dump(mode="json"),
                        "addr_type": 10,
                        "response": {"errCd": 0, "result": [{"road_nm": name}]},
                    }
                )
            result = prepare_relational_diary(
                replace(base, scene_backgrounds=backgrounds),
                scene_ids=scene_ids,
                road_snapshots=roads,
                writing_briefs=True,
            )
            captured["prepared_before_writing"] = result
            return result

        inspection = await prepare(base, scene_ids=selected)
        dump("input.json", {"source": raw, "prepared": inspection})
        summary = {
            "live": args.live,
            "scenario": "synthetic GPS, behavior, identity and public-response fixtures; no TMAP",
            "source_boundary": "InputAssembly fixture instead of raw source database acquisition",
            "http": "production router in ASGI, fixture authentication, real lifecycle and PostgreSQL",
            "model": "gemini-3.1-flash-lite",
            "minimum_interval_s": 10,
            "automatic_retries": 0,
            "scenes": [
                {
                    "id": f["scene_id"],
                    "at": f["anchor"]["event_at"],
                    "position": f["narrative_context"]["current"]["position"],
                    "action": f["action_brief"]["required_event"] if f["action_brief"] else None,
                }
                for f in inspection["snapshot"]["frames"]
            ],
            "planned_tasks": [
                p[k]["stage"]
                for p in inspection["snapshot"]["plans"]
                for k in ("space_task", "action_task")
                if p[k]
            ],
        }
        dump("inspection.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if not args.live:
            return
        if not settings.gemini_api_key.get_secret_value():
            raise ValueError("Gemini credential not configured")
        engine = create_async_engine(
            "postgresql+asyncpg://postgres:brief-test-only@127.0.0.1:55439/walk_pin_test",
            poolclass=NullPool,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as db:
                await db.execute(text("CREATE TABLE IF NOT EXISTS walks (id UUID PRIMARY KEY)"))
                await db.execute(
                    text("INSERT INTO walks(id) VALUES (:id)"), {"id": uuid.UUID(source.walk_id)}
                )
                connection = (await db.get_raw_connection()).driver_connection
                await connection.execute(
                    (Path.cwd().parent / "db/migrations/2026-09-05_walk_storyboards.sql").read_text(
                        encoding="utf-8"
                    )
                )

            async def read_source(db, principal, walk_id):
                if str(principal) != source.owner_id or str(walk_id) != source.walk_id:
                    raise LookupError("fixture identity mismatch")
                # Preserve the source-lock boundary while providing a fixed synthetic source.
                await db.execute(
                    text("SELECT id FROM walks WHERE id=:id FOR UPDATE"), {"id": walk_id}
                )
                return assembled

            async def sessions():
                async with factory() as db:
                    yield db

            async def writer(value, current_base, *, execution_policy):
                result = await write_relational_board(
                    value,
                    current_base,
                    scene_ids=selected,
                    prepare=prepare,
                    execution_policy=execution_policy,
                )
                captured["result"] = {"prepared": result.prepared, "receipt": result.receipt}
                dump("result.json", captured["result"])
                return result

            patches.setattr(relational, "read_input", read_source)
            patches.setattr(settings, "walk_diary_enabled", True)
            app = FastAPI()
            app.include_router(router.router)
            app.dependency_overrides[get_session] = sessions
            app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: (
                AppPrincipal(app_user_id=owner)
            )
            app.dependency_overrides[router.get_diary_writer] = lambda: writer
            path = f"/app/walks/{source.walk_id}/storyboard"
            body = {
                "bundle_format": "walk-relational-diary-v1",
                "target_scene_count": 3,
                "expected_entries": {r.ref.id: int(r.ref.version) for r in source.records},
                "expected_photo_manifest": None,
            }
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://brief-local", timeout=300
            ) as client:
                post = await client.post(path, json=body)
                get = await client.get(
                    path, params={"bundle_format": body["bundle_format"], "target_scene_count": 3}
                )
            async with factory() as db:
                stored = await db.scalar(
                    text("SELECT bundle FROM walk_storyboards WHERE walk_id=:id"),
                    {"id": uuid.UUID(source.walk_id)},
                )
            report = {
                "inspection": summary,
                "http_status": [post.status_code, get.status_code],
                "post": post.json(),
                "get": get.json(),
                "post_equals_get": post.json() == get.json(),
                "stored": stored,
                "generation": captured.get("result"),
            }
            dump("report.json", report)
            render(args.output / "DAENGS_step5.html", report)
            print(
                json.dumps(
                    {
                        "http_status": report["http_status"],
                        "status": post.json().get("status"),
                        "post_equals_get": report["post_equals_get"],
                        "execution": captured.get("result", {}).get("receipt", {}).get("execution"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        finally:
            await engine.dispose()


def render(path, report):
    esc = html.escape
    cards = report["get"].get("bundle", {}).get("cards", []) if report["get"].get("bundle") else []
    title = (report["get"].get("bundle") or {}).get("title") or "제목 미채택"
    receipt = (report.get("generation") or {}).get("receipt", {})
    execution = receipt.get("execution", {})
    blocks = []
    for n, card in enumerate(cards, 1):
        blocks.append(
            f"<article><h2>장면 {n} · {esc(card['anchor']['event_at'])}</h2>"
            f"<p><b>공간</b> {esc(card['space']['text'] or '(미채택)')}</p>"
            f"<p><b>행동</b> {esc(card['action']['text'] or '(없음/미채택)')}</p></article>"
        )
    path.write_text(
        '<!doctype html><meta charset="utf-8"><title>DAENGS 5단계 실제 호출</title>'
        "<style>body{max-width:1050px;margin:40px auto;padding:20px;font:17px/1.7 system-ui;background:#f5f5f1;color:#232820}article{background:white;padding:24px;margin:20px 0;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}</style>"
        "<h1>5단계 · 실제 Gemini → 저장 → GET</h1><p>합성 GPS·공간·행동 자료 / 실제 모델과 HTTP 서비스·PostgreSQL. 원본 획득·인증은 fixture. 공공데이터 실조회·실기기 산책 아님.</p>"
        f"<p>POST/GET: {esc(str(report['http_status']))} · 결과 동일: {report['post_equals_get']}</p>"
        f"<p>실제 호출: {execution.get('model_call_attempts', 0)}회 · 의미 검수: {execution.get('semantic_review_enabled')} · 자동 재시도: {execution.get('automatic_retries')}</p>"
        f"<h2>모델이 쓴 제목: {esc(title)}</h2>"
        + (
            "<article><h2>실행 후 관찰</h2>"
            + "".join(f"<p>{esc(note)}</p>" for note in report.get("observations", []))
            + "</article>"
            if report.get("observations")
            else ""
        )
        + "".join(blocks)
        + "<details><summary>전체 입력·실제 출력·실패·저장 결과</summary><pre>"
        + esc(json.dumps(report, ensure_ascii=False, indent=2))
        + "</pre></details>",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
