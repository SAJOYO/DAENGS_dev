"""Run the real split skeleton on a saved scenario, then save/read its local receipt."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from run_diary_route_scenario import configure, prepare, read


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--scene-selection", type=Path)
    parser.add_argument("--roads", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    configure(args.env)
    from daengs_backend.orchestration.relational_diary import generate_relational_skeleton
    from daengs_backend.services.walk_diary.preparation.board import with_scene_backgrounds
    from daengs_backend.services.walk_diary.storage.relational import read_skeleton, save_skeleton
    from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot

    base = with_scene_backgrounds(
        prepare(read(args.source / "input.json")),
        SceneBackgroundSnapshot.model_validate(read(args.source / "backgrounds.json")),
    )
    ids = (
        [c["card_id"] for c in read(args.scene_selection)["cases"]]
        if args.scene_selection
        else None
    )
    roads = [read(p) for p in sorted(args.roads.glob("*-10.json"))] if args.roads else []
    path = args.output / "receipt.json"
    if path.exists():
        raise FileExistsError("use a fresh output directory")
    start = time.monotonic()
    result = asyncio.run(generate_relational_skeleton(base, scene_ids=ids, road_snapshots=roads))
    elapsed = round(time.monotonic() - start, 2)
    save_skeleton(path, result)
    receipt = read_skeleton(path)
    if receipt != result["receipt"]:
        raise ValueError("stored receipt mismatch")
    lines = [
        "# 분리형 관계 일기 워킹 스켈레톤",
        "",
        f"실행 {elapsed}초 · 저장 후 재조회 동일",
        "",
        "제목: " + receipt["title"]["text"],
        "",
    ]
    for index, card in enumerate(receipt["cards"], 1):
        lines += [f"## 장면 {index}", ""]
        for stage, label in [("space", "공간 관계"), ("action", "현재 행동")]:
            part = card["parts"][stage]
            lines += [f"**{label} · {part['status']}**", "", part["text"] or "생성 문장 없음", ""]
        lines += ["**별도 이동 관측**", ""]
        lines += ["- " + m["text"] for m in card["movement_observations"]]
        lines += [""]
    (args.output / "실행결과.md").write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "wall_seconds": elapsed,
                "body_calls": len(receipt["writing"]["results"]),
                "title_status": receipt["title"]["status"],
                "cards": receipt["cards"],
                "failures": [r for r in receipt["writing"]["results"] if r["status"] == "failed"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
