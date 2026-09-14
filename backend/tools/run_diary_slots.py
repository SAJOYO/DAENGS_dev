"""Run the DEV slot preview without DB. See docs/walk/diary-part-slots.md."""

import argparse
import asyncio
import html
import json
import os
import uuid
from functools import partial
from pathlib import Path

from daengs_backend.services.walk_diary.legacy.slots import generate_slot_prose, write_slot_preview
from daengs_evals.diary_slots_demo import demo_input
from daengs_walk import analyze_walk
from daengs_walk.contracts import WalkEvidencePoint
from daengs_walk.diary_board import BaseBoardPolicy, VerifiedBoardRoute
from daengs_walk.diary_input import DiaryInput
from daengs_walk.diary_slots import SlotPolicy, prepare_slot_preview
from daengs_walk.diary_stamps import StampPolicy


def read_key(path):
    if path:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip().removeprefix("export ")
            if line.lower().startswith("gemini:"):
                return line.split(":", 1)[1].strip().strip("\"'")
            name, sep, value = line.partition("=")
            if sep and name.strip() in {"GEMINI_API_KEY", "GOOGLE_API_KEY"}:
                return value.strip().strip("\"'")
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")


def render(preview):
    stamps = {s.scene_id: s for s in preview.stamps}
    cards = []
    for scene in preview.scenes:
        stamp = stamps[scene.id]
        parts = " · ".join(
            f"{part} {sum(e.part == part for e in stamp.evidence)}"
            for part in ("space", "environment", "motion")
        )
        diagnostics = "".join(
            f"<tr><td>{html.escape(d.part)}</td><td>{html.escape(d.reason)}</td>"
            f"<td>{html.escape(d.eligibility)} / {html.escape(d.admission)}</td>"
            f"<td><pre>{html.escape(json.dumps(d.details, ensure_ascii=False, indent=2))}</pre></td></tr>"
            for d in stamp.decisions
        )
        cards.append(
            f"<article><small>{scene.order:02} / {html.escape(parts)}</small>"
            f"<small> · 위치 설명 {int(stamp.location_reference is not None)}</small>"
            f"<h2>{html.escape(scene.title)}</h2><p>{html.escape(scene.body)}</p>"
            f"<details><summary>판정값과 기준값</summary><div class=scroll><table>"
            f"<tr><th>파트</th><th>사유</th><th>판정 / 적재</th><th>값과 기준</th></tr>"
            f"{diagnostics}</table></div></details>"
            f"<details><summary>원자료 출처와 전체 스탬프</summary><pre>"
            f"{html.escape(stamp.model_dump_json(indent=2))}</pre></details></article>"
        )
    return (
        "<!doctype html><html lang=ko><meta charset=utf-8><title>산책 파트 슬롯 미리보기</title>"
        "<style>body{font:17px/1.7 system-ui;max-width:860px;margin:45px auto;background:#f4f5ef;"
        "color:#20352c;padding:0 20px}article{background:white;padding:26px;border-radius:18px;"
        "margin:18px 0}small{color:#557764}p{white-space:pre-wrap}pre{font-size:12px;"
        "overflow:auto}summary{cursor:pointer}h1{line-height:1.3}table{border-collapse:collapse;"
        "font-size:13px}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left;"
        "vertical-align:top}.scroll{overflow:auto}td pre{max-width:350px}</style>"
        f"<h1>산책 파트 슬롯 미리보기</h1><p>Gemini: {preview.model_status}"
        f" · {preview.failure_code or '오류 없음'}<br>입력 출처는 함께 저장한 input.json 참고.</p>"
        + "".join(cards)
        + "</html>"
    )


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, help="source: DiaryInput, points: canonical input array"
    )
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--target-scenes", type=int, default=3)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--env-file", type=Path, help="Only GEMINI_API_KEY is read; never copied")
    parser.add_argument("--output", type=Path, default=Path("evals/diary-slots/local"))
    args = parser.parse_args()
    if args.input:
        raw = json.loads(args.input.read_text(encoding="utf-8-sig"))
        source = DiaryInput.model_validate(raw["source"])
        points = tuple(WalkEvidencePoint.model_validate(p) for p in raw.get("points", []))
        route = (
            VerifiedBoardRoute(
                source.route,
                analyze_walk(
                    uuid.UUID(source.walk_id),
                    source.started_at,
                    source.ended_at,
                    points,
                ),
            )
            if points
            else None
        )
    else:
        source, route, points = demo_input()
    policy = (
        SlotPolicy.model_validate_json(args.policy.read_text(encoding="utf-8-sig"))
        if (args.policy)
        else SlotPolicy()
    )
    preview = prepare_slot_preview(
        source,
        policy,
        BaseBoardPolicy(
            intermediate=StampPolicy(
                target_scene_count=args.target_scenes,
            )
        ),
        route=route,
    )
    if args.generate:
        key = read_key(args.env_file)
        if not key:
            parser.error("GEMINI_API_KEY is missing; use --env-file or the environment")
        preview = await write_slot_preview(preview, partial(generate_slot_prose, api_key=key))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "input.json").write_text(
        json.dumps(
            {
                "source": source.model_dump(mode="json"),
                "points": [p.model_dump(mode="json") for p in points],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (args.output / "policy.json").write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    (args.output / "preview.json").write_text(preview.model_dump_json(indent=2), encoding="utf-8")
    (args.output / "preview.html").write_text(render(preview), encoding="utf-8")
    print(
        json.dumps(
            {
                "model_status": preview.model_status,
                "failure_code": preview.failure_code,
                "scenes": len(preview.scenes),
                "output": str(args.output.resolve()),
            }
        )
    )
    if args.generate and preview.model_status == "unavailable":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
