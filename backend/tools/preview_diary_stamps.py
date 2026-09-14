"""Offline DiaryInput -> prepared stamps, system-only bundle, and readable trace.

Run from backend with uv run python tools/preview_diary_stamps.py --help.
An input file is not authentication. Keep actual private exports outside the repository.
"""

import argparse
import html
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from daengs_walk.diary.contracts.input import DiaryInput
from daengs_walk.diary.contracts.output import assemble_diary
from daengs_walk.diary.selection.stamps import StampPolicy, prepare_stamps


def cell(value):
    return html.escape(str(value)).replace("|", "&#124;").replace("\n", "<br>")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target", type=int, required=True)
    args = parser.parse_args()
    source = DiaryInput.model_validate_json(args.input.read_text(encoding="utf-8-sig"))
    prepared = prepare_stamps(source, StampPolicy(target_scene_count=args.target))
    bundle = assemble_diary(source, prepared.plan, None)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, value in (("input", source), ("prepared", prepared), ("bundle", bundle)):
        (args.out / f"{name}.json").write_text(
            json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    lines = [
        "# 일기 스탬프 준비 결과",
        "",
        f"입력 출처: `{source.evidence_origin}`. LLM 호출 없음. 제목은 시스템 기본값.",
        "",
        (
            f"사용자 기록 {prepared.counts['user_records']}개 / 보충 {prepared.counts['supplemented']}개 / "
            f"목표 {args.target}개 / 남은 부족분 {prepared.counts['remaining_deficit']}개."
        ),
        "",
        "| 순서 | 한국 시각 | 중심 기록/관측 | 위치 방법 | 배경 조각 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for scene, stamp in zip(bundle.scenes, prepared.plan.scenes, strict=True):
        record = scene.user_record
        label = (
            f"기기 관측: {scene.observation.kind}"
            if record is None
            else record.text
            if record.kind == "note"
            else f"행동 기록: {record.code}"
            if record.kind == "behavior"
            else "사진 기록"
        )
        details = (
            ", ".join(
                f"{p.facts.get('name', p.kind)}: {p.facts.get('distance_m', '?')}m (등록 위치까지)"
                for p in stamp.background
            )
            or "없음"
        )
        at = scene.anchor.event_at.astimezone(ZoneInfo("Asia/Seoul")).isoformat()
        lines.append(
            "| "
            + " | ".join(map(cell, [scene.order, at, label, scene.anchor.method, details]))
            + " |"
        )
    lines += ["", "## 중심 선택", "", "| 재료 참조 | 결정 |", "| --- | --- |"]
    lines += [
        f"| {cell(d.material.identity)} | {cell(d.reason)} |" for d in prepared.core_decisions
    ]
    lines += ["", "## 배경 선택", "", "| 봉투 | 조각 | 결정 |", "| --- | --- | --- |"]
    lines += [
        f"| {cell(d.background_id)} | {cell(d.piece_id or '—')} | {cell(d.reason)} |"
        for d in prepared.background_decisions
    ]
    lines += [
        "",
        "제약: " + (", ".join(prepared.limits) or "추가 상태 없음"),
        "",
        "원문·위치·근거는 input.json, 선택 정책과 전체 사유는 prepared.json에 보존된다.",
    ]
    (args.out / "preview.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(prepared.counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
