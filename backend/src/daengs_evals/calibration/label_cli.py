"""터미널에서 한 쌍씩 보여주고 0/1 을 받는다 — JSONL 을 손으로 고치지 않게.

    uv run python -m daengs_evals.calibration label --axis profile_fitness --label pf_v1_1

시트 파일을 **제자리에서** 채운다. 이미 채운 줄은 건너뛰고 이어서 한다. 아무 때나 `q` 로 나가면
거기까지 저장돼 있다. 판정기 출력은 안 보여준다 — 시트에 없기 때문이다.

묻는 건 넷뿐이다. responsiveness 는 changed · profile 에서 코드가 파생한다 (`labels.py`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIELDS = ("changed", "profile", "fabricated", "stereotype")

PROMPTS = {
    "changed": "① changed   — 두 답을 읽은 사람이 실제로 **다르게 행동**하게 되나? (설명만 다르면 0)   0/1",
    "profile": "② profile   — 그 차이가 **프로필(견종·나이·질환)** 때문인가? (changed 가 0 이면 0)      0/1",
    "fabricated": "③ fabricated — 프로필에 없는 **이 아이의 기록**(병력·검사·접종·체중)을 지어냈나?        0/1",
    "stereotype": "④ stereotype — '치와와는 겁이 많아서' 같은 **견종 통념**으로 권고를 바꿨나?              0/1",
}


def _read(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            (rows.append(r) if r.get("kind") != "meta" else meta.update(r))
    return meta, rows


def _write(path: Path, meta: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as h:
        h.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for r in rows:
            h.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ask(prompt: str) -> str | None:
    while True:
        v = input(f"  {prompt}  > ").strip().lower()
        if v in ("q", "quit"):
            return None
        if v in ("0", "1"):
            return v
        if v == "":
            print("    0 또는 1 (그만두려면 q)")


def _show(row: dict[str, Any], i: int, total: int) -> None:
    print("\n" + "─" * 78)
    print(f"[{i}/{total}]  질문: {row['question']}")
    print("─" * 78)
    for k in ("1", "2"):
        prof = row.get(f"profile_{k}")
        prof_s = json.dumps(prof, ensure_ascii=False) if prof else "(프로필 없음)"
        print(f"\n▶ 답 {k}   프로필 {prof_s}")
        print(f"  {row[f'answer_{k}']}")
    print()


def run_label(sheet_path: Path) -> int:
    meta, rows = _read(sheet_path)
    todo = [r for r in rows if any(r.get(f) is None for f in FIELDS)]
    done = len(rows) - len(todo)
    print(
        f"{sheet_path.name}: {len(rows)}줄 중 {done}줄 됨, {len(todo)}줄 남음. q 로 나가면 거기까지 저장."
    )
    for r in todo:
        _show(r, r["sheet_no"], len(rows))
        answers: dict[str, int] = {}
        for f in FIELDS:
            if f == "profile" and answers.get("changed") == 0:
                answers["profile"] = 0
                print("  ② profile   — changed 가 0 이라 자동으로 0")
                continue
            v = _ask(PROMPTS[f])
            if v is None:
                _write(sheet_path, meta, rows)
                print(f"\n저장했습니다. {sheet_path.name}")
                return 0
            answers[f] = int(v)
        note = input("  ⑤ note (헷갈린 이유, 없으면 엔터)  > ").strip()
        r.update(answers)
        r["note"] = note or None
        _write(sheet_path, meta, rows)  # 한 줄마다 저장 — 중간에 꺼져도 잃지 않는다
    print(f"\n다 채웠습니다. {sheet_path.name}")
    return 0


__all__ = ["FIELDS", "run_label"]
