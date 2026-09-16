"""Offline replay of saved public inputs through activity writing and storage.

No credential file, acquisition, model call, database or publication. Generated parts
are explicitly marked deterministic placeholders, never presented as Gemini prose.
"""

import argparse
import asyncio
import html
import json
from pathlib import Path

from run_diary_route_scenario import configure, dump, prepare, read


async def placeholder(stage, payload, schema):
    if stage == "title":
        return {
            "titles": [{"id": c["id"], "text": f"연결 확인 · {c['id']}"} for c in payload["cards"]]
        }
    if stage == "action":
        if "recorded_action" in payload:
            refs = [payload["movement_context"]["id"]] if "movement_context" in payload else []
            if payload.get("recorded_action"):
                refs.append(payload["recorded_action"]["id"])
            return {"text": "[오프라인 고정 응답] 활동 작성 연결 확인.", "evidence_ids": refs}
        return {"text": "[오프라인 고정 응답] 행동 작성 연결 확인."}
    refs = [m["id"] for m in payload["materials"]]
    return {
        "text": "[오프라인 고정 응답] 공간 작성 연결 확인." if refs else "",
        "evidence_ids": refs[:1],
    }


async def replay(source, output):
    from daengs_backend.services.walk_diary.preparation.board import with_scene_backgrounds
    from daengs_backend.services.walk_diary.preparation.diary import PreparedWalkDiary
    from daengs_backend.services.walk_diary.runtime import write_cards
    from daengs_backend.services.walk_diary.storage.board import load_board, store_board
    from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
    from daengs_walk.diary.contracts.input import digest

    raw = read(source / "input.json")
    base = prepare(raw)
    snapshot = SceneBackgroundSnapshot.model_validate(read(source / "backgrounds.json"))
    enriched = with_scene_backgrounds(base, snapshot)
    result = await write_cards(base.input.source, enriched, generate=placeholder)
    stored = store_board(
        PreparedWalkDiary(enriched.input, enriched.plan.intermediate, enriched),
        result.bundle,
        digest(["offline-activity", result.input_revision, result.writer_version]),
        writing=result,
    )
    assert load_board(stored).bundle == result.bundle
    assert all(j.failure_code is None for j in result.jobs)
    jobs = [j for j in result.jobs if j.stage == "action"]
    summary = {
        "mode": "offline-placeholder",
        "external_calls": 0,
        "source": source.name,
        "input_sha256": digest(raw),
        "backgrounds_sha256": digest(snapshot),
        "writer_version": result.writer_version,
        "cards": len(result.bundle.scenes),
        "activity_jobs": len(jobs),
        "combined_action_jobs": sum(
            bool(j.request.get("action") and j.request.get("movement")) for j in jobs
        ),
        "movement_only_jobs": sum(not j.request.get("action") for j in jobs),
        "title_jobs": sum(j.stage == "title" for j in result.jobs),
        "storage_roundtrip": True,
    }
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "summary.json", summary)
    dump(output / "result.json", result)
    dump(output / "stored.json", stored)
    dump(output / "slots.json", enriched.slots)
    cards = []
    for scene in result.bundle.scenes:
        job = next((j for j in jobs if j.request["card_id"] == scene.id), None)
        payload = json.dumps(job.llm_request if job else {}, ensure_ascii=False, indent=2)
        cards.append(
            "<article><h2>"
            + html.escape(f"{scene.order}. {scene.anchor.event_at.isoformat()}")
            + "</h2><p>"
            + html.escape(scene.body).replace("\n", "<br>")
            + "</p><details><summary>활동 작성기에 전달한 입력</summary><pre>"
            + html.escape(payload)
            + "</pre></details></article>"
        )
    page = """<!doctype html><meta charset="utf-8"><title>산책 활동 연결 확인</title>
<style>body{max-width:1000px;margin:40px auto;background:#f7f6f2;color:#263a32;font:16px/1.7 system-ui;padding:0 24px}
article{background:white;border:1px solid #d9e1db;border-radius:16px;padding:20px;margin:20px 0}
pre{white-space:pre-wrap;font-size:13px}h1{font-size:30px}.notice{background:#e6efe8;padding:20px;border-radius:12px}</style>
<h1>산책 활동 연결 확인</h1><div class="notice">외부 호출 0회. 이전에 수집한 실제 공공자료와 가상 GPS를 재사용했다.
본문·제목은 연결 검사용 고정 응답이다. Gemini 출력이나 문장 품질 평가가 아니다.</div>"""
    (output / "preview.html").write_text(page + "".join(cards), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("choose a new output directory; existing results are not overwritten")
    configure(None)
    asyncio.run(replay(args.source.resolve(), args.output.resolve()))


if __name__ == "__main__":
    main()
