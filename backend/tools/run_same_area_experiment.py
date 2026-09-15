"""Same spatial meaning, five chronological scenes; real production writer, synthetic sources."""
import argparse
import asyncio
import html
import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(args):
    from pytest import MonkeyPatch
    from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary
    from daengs_backend.services.walk_diary.preparation.relational_base import assemble_relational_base
    from daengs_backend.services.walk_diary.runtime import write_relational_board
    from daengs_walk.diary.contracts.input import DiaryInput
    from daengs_walk.diary.selection.board import observed_anchor
    from tests.walk.diary.test_diary_activity import prepared as activity
    from tests.walk.diary.test_diary_space_integration import public_collector
    from tests.walk.support.route_patterns import route

    waypoints = [(0, 0, 0), (300, 0, 300), (480, 0, 390),
                 (600, 0, 300), (780, 0, 120), (900, 0, 0)]
    assembled = activity(waypoints=waypoints)[0].input
    sample = route("same-area-out-and-back", waypoints)
    raw = assembled.source.model_dump(mode="json")
    dog_id = raw["pet_ids"][0] if raw["pet_ids"] else "same-area-dog"
    raw["pet_ids"] = [dog_id]
    template = deepcopy(raw["records"][0])
    raw["records"] = []
    for seconds in (180, 480, 780):
        record = deepcopy(template)
        record["ref"]["id"] = f"same-area-sniffing-{seconds}"
        point = next(p for p in sample.points if (p.at - sample.started_at).total_seconds() == seconds)
        record["anchor"] = observed_anchor(point).model_copy(
            update={"time_basis": "recorded_at"}).model_dump(mode="json")
        record["content"] = {"kind": "behavior", "code": "sniffing", "pet_id": dog_id}
        raw["records"].append(record)
    source = DiaryInput.model_validate(raw)
    assembled = replace(assembled, source=source, pet_names=((dog_id, "보리"),))
    base = assemble_relational_base(assembled, 5)
    scenes = sorted(base.board.scenes, key=lambda s: (s.anchor.event_at, s.id))
    selected = [s.id for s in scenes if s.anchor.event_at in
                {sample.started_at, sample.ended_at, *(r.anchor.event_at for r in source.records)}]
    if len(selected) != 5:
        raise ValueError(f"expected five distinct scenes, got {len(selected)}")
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "result.json").exists():
        raise ValueError("preserve existing model result; choose another output directory")
    with MonkeyPatch.context() as patches:
        collector = public_collector.__wrapped__(patches)
        backgrounds = await collector(base.board)
        roads = [{"point": s.anchor.point.model_dump(mode="json"), "addr_type": 10,
                  "response": {"errCd": 0, "result": [{"road_nm": "성내천로"}]}}
                 for s in scenes if s.id in selected]

        async def prepare(value, *, scene_ids=None):
            return prepare_relational_diary(replace(value, scene_backgrounds=backgrounds),
                                           scene_ids=scene_ids, road_snapshots=roads,
                                           writing_briefs=True)

        prepared = await prepare(base, scene_ids=selected)
        save(args.output / "input.json", {"raw_source": raw, "prepared": prepared,
             "fixture_scope": "Synthetic GPS, same normalized spatial background and road. Public responses are fixtures, not live observations. No TMAP."})
        info = [{"scene": i + 1, "time": f["anchor"]["event_at"],
                 "position": f["narrative_context"]["current"]["position"],
                 "route": f["narrative_context"].get("route"),
                 "action": f["action_brief"],
                 "movement": p["movement_observations"]}
                for i, (f, p) in enumerate(zip(prepared["snapshot"]["frames"],
                                               prepared["snapshot"]["plans"], strict=True))]
        save(args.output / "inspection.json", info)
        print(json.dumps({"scenes": [{"scene": s["scene"], "time": s["time"],
              "action": bool(s["action"]), "motion_items": len(s["movement"])} for s in info]},
              ensure_ascii=False), flush=True)
        if not args.live:
            return
        result = await write_relational_board(source, base, scene_ids=selected, prepare=prepare)
    report = {"prepared": result.prepared, "receipt": result.receipt}
    save(args.output / "result.json", report)
    summary = []
    for i, (card, plan) in enumerate(zip(result.receipt["cards"],
                                        result.prepared["snapshot"]["plans"], strict=True)):
        summary.append({"scene": i + 1, "time": card["anchor"]["event_at"],
                        "reason": plan["state_transition"], "parts": card["parts"],
                        "movement_observations": card["movement_observations"]})
    save(args.output / "summary.json", {"scenes": summary, "title": result.receipt["title"],
                                       "execution": result.receipt["execution"]})
    print(json.dumps({"scenes": summary, "title": result.receipt["title"],
                      "execution": result.receipt["execution"]}, ensure_ascii=False), flush=True)
    esc = html.escape
    blocks = []
    for row in summary:
        blocks.append(f'<article><h2>장면 {row["scene"]} · {esc(row["time"])}</h2>'
                      f'<p>공간 호출 판단: <b>{esc(row["reason"])}</b></p>'
                      + ''.join(f'<p><b>{label}</b> [{part["status"]}] {esc(part["text"] or "(본문 없음)")}</p>'
                                for key, label in (("space", "공간"), ("action", "행동"))
                                for part in [row["parts"][key]])
                      + '<details><summary>확보된 이동 관측</summary><pre>'
                      + esc(json.dumps(row["movement_observations"], ensure_ascii=False, indent=2))
                      + '</pre></details></article>')
    page = ('<!doctype html><meta charset="utf-8"><title>같은 공간의 시간과 장면 실험</title>'
            '<style>body{max-width:1000px;margin:32px auto;padding:20px;font:17px/1.7 system-ui;background:#f4f4ef}article{padding:20px;background:white;margin:16px 0;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}</style>'
            '<h1>같은 공간 · 15분 · 5장면</h1><p>합성 왕복 GPS / 같은 도로·정규화 공간 / 냄새 맡기 핀 3건. '
            '실제 gemini-3.1-flash-lite 호출. 사용한 작성 경로와 요청은 실행 산출물을 참고.  '
            '공공 API 실조회·운영 DB·앱 검증은 아님. 호출 완료 후 10초 간격, 자동 재시도·별도 의미 검수 없음.</p>'
            + ''.join(blocks) + '<details><summary>전체 입력과 실제 응답</summary><pre>'
            + esc(json.dumps(report, ensure_ascii=False, indent=2)) + '</pre></details>')
    (args.output / "DAENGS_same_area_time.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    sys.path[:0] = [str(args.backend), str(args.backend / "src"), str(args.backend / "tools")]
    from run_diary_route_scenario import configure
    configure(args.env)
    asyncio.run(run(args))
