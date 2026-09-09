"""라벨링 대상 선별 — 두 블록, 서로소, 결정론.

    자기모순 블록   판정기가 흔들린 쌍: 위치 뒤집힘 · 기권 · A/B 불일치 · (드문) 날조 · 통념 표시
    무작위 블록     조건 × 종류로 층화한 무작위 표본

**두 블록의 κ 를 따로 낸다.** 자기모순만 라벨하면 분모가 판정기가 어려워한 것들로 편향돼 κ 가
과소추정된다. 무작위 블록이 모집단 추정이고, 자기모순 블록은 약한 자리의 지도다. 합산 κ 에는
"모집단 추정이 아니다" 주석이 붙는다 (`gate.py`).

**반복 10건.** 두 블록의 합에서 뽑아 나중에 같은 사람이 다시 라벨한다 — 본인 일관성(intra-rater).
사람도 자기랑 안 맞으면 판정기를 탓할 수 없다.

**블라인드.** 내보내는 시트에 판정기 출력은 없다. 답변 순서도 쌍마다 무작위로 뒤집고, 뒤집은 기록은
시트가 아니라 **키 파일**에 둔다. 라벨러가 키 파일을 열 이유는 없다.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daengs_evals.answer_quality.provenance import utc_now

DEFAULT_SEED = 20260909


@dataclass(frozen=True)
class Selection:
    contradiction: list[str]
    random: list[str]
    repeats: list[str]
    reasons: dict[str, list[str]] = field(default_factory=dict)

    @property
    def all_ids(self) -> list[str]:
        return self.contradiction + self.random


def contradiction_reasons(
    row: Mapping[str, Any], b_row: Mapping[str, Any] | None = None
) -> list[str]:
    reasons: list[str] = []
    obs = row.get("observation") or {}
    if row.get("position_dependent"):
        reasons.append("position_dependent")
    if obs.get("abstained"):
        reasons.append("abstained")
    if obs.get("fabricated"):
        reasons.append("fabricated")  # 드물고 위험 — 재현율을 봐야 한다
    if obs.get("stereotype"):
        reasons.append("stereotype")
    if b_row is not None:
        b_obs = b_row.get("observation") or {}
        if any(
            int(obs.get(k, 0)) != int(b_obs.get(k, 0)) for k in ("changed", "profile", "fabricated")
        ):
            reasons.append("variant_disagreement")
    return reasons


def select_for_labeling(
    rows: Sequence[Mapping[str, Any]],
    *,
    b_rows: Sequence[Mapping[str, Any]] | None = None,
    n_contradiction: int = 15,
    n_random: int = 25,
    n_repeats: int = 10,
    seed: int = DEFAULT_SEED,
    exclude_control: bool = True,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> Selection:
    """판정 행 → 선별. `exclude_control` 이면 noise 쌍은 빼고 뽑는다 (점수가 없어 라벨할 항목이 적다)."""
    rng = random.Random(seed)
    b_by = {r["pair_id"]: r for r in (b_rows or [])}
    pool = [
        r
        for r in rows
        if not (exclude_control and r.get("condition") == "noise") and r["pair_id"] not in exclude
    ]

    reasons: dict[str, list[str]] = {}
    for r in pool:
        rs = contradiction_reasons(r, b_by.get(r["pair_id"]))
        if rs:
            reasons[r["pair_id"]] = rs
    contradiction_ids = sorted(reasons)
    rng.shuffle(contradiction_ids)
    contradiction = contradiction_ids[:n_contradiction]
    taken = set(contradiction)

    strata: dict[str, list[str]] = defaultdict(list)
    for r in pool:
        if r["pair_id"] not in taken:
            strata[f"{r.get('condition')}|{r.get('question_kind')}"].append(r["pair_id"])
    for ids in strata.values():
        ids.sort()
        rng.shuffle(ids)
    random_block: list[str] = []
    keys = sorted(strata)
    while len(random_block) < n_random and any(strata[k] for k in keys):
        for k in keys:
            if strata[k] and len(random_block) < n_random:
                random_block.append(strata[k].pop())

    union = contradiction + random_block
    repeats = rng.sample(union, min(n_repeats, len(union)))
    return Selection(contradiction, random_block, repeats, reasons)


# ---------------------------------------------------------------------------
# 시트 내보내기 — 블라인드
# ---------------------------------------------------------------------------

LABEL_FIELDS: tuple[str, ...] = (
    "changed",
    "profile",
    "fabricated",
    "stereotype",
    "responsiveness",
    "note",
)


def export_sheet(
    selection: Selection,
    rows_by_id: Mapping[str, Mapping[str, Any]],
    cells_by_key: Mapping[tuple[str, str, int], Mapping[str, Any]],
    profiles_by_id: Mapping[str, Any],
    *,
    sheet_path: Path,
    key_path: Path,
    seed: int = DEFAULT_SEED,
) -> tuple[int, int]:
    """시트(라벨러용)와 키(뒤집기 · 블록 기록)를 따로 쓴다. 시트에는 판정기 출력이 없다."""
    rng = random.Random(seed + 1)
    order = selection.all_ids + selection.repeats
    sheet_rows: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []
    for i, pair_id in enumerate(order, start=1):
        r = rows_by_id[pair_id]
        q, _cond, a_key, b_key = pair_id.split("|")
        arm_a, run_a = a_key.rsplit("#", 1)
        arm_b, run_b = b_key.rsplit("#", 1)
        cell_a = cells_by_key[(q, arm_a, int(run_a))]
        cell_b = cells_by_key[(q, arm_b, int(run_b))]
        prof_a = profiles_by_id[arm_a].dog if profiles_by_id[arm_a].dog else None
        prof_b = profiles_by_id[arm_b].dog if profiles_by_id[arm_b].dog else None
        swapped = rng.random() < 0.5
        first, second = (
            ((prof_b, cell_b), (prof_a, cell_a))
            if swapped
            else ((prof_a, cell_a), (prof_b, cell_b))
        )
        is_repeat = i > len(selection.all_ids)
        sheet_rows.append(
            {
                "sheet_no": i,
                "question": cell_a.get("_question") or r.get("query") or "",
                "profile_1": first[0],
                "profile_2": second[0],
                "answer_1": first[1]["message"],
                "answer_2": second[1]["message"],
                **{f: None for f in LABEL_FIELDS},
            }
        )
        key_rows.append(
            {
                "sheet_no": i,
                "pair_id": pair_id,
                "swapped": swapped,
                "block": "repeat"
                if is_repeat
                else ("contradiction" if pair_id in selection.contradiction else "random"),
                "reasons": selection.reasons.get(pair_id, []),
            }
        )
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    with sheet_path.open("w", encoding="utf-8") as h:
        h.write(
            json.dumps(
                {
                    "kind": "meta",
                    "created_at": utc_now(),
                    "fields": LABEL_FIELDS,
                    "guide": "changed/profile/fabricated/stereotype 는 0 또는 1, responsiveness 는 0~2 (reactive 만), "
                    "note 는 자유. 판정기 출력은 이 시트에 없다 — 보지 말고 매긴다.",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        for row in sheet_rows:
            h.write(json.dumps(row, ensure_ascii=False) + "\n")
    with key_path.open("w", encoding="utf-8") as h:
        h.write(
            json.dumps({"kind": "meta", "created_at": utc_now(), "seed": seed}, ensure_ascii=False)
            + "\n"
        )
        for row in key_rows:
            h.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(selection.all_ids), len(selection.repeats)


def summarize_selection(selection: Selection) -> dict[str, Any]:
    from collections import Counter

    return {
        "contradiction": len(selection.contradiction),
        "random": len(selection.random),
        "repeats": len(selection.repeats),
        "reasons": dict(Counter(r for rs in selection.reasons.values() for r in rs)),
    }


__all__ = [
    "LABEL_FIELDS",
    "Selection",
    "contradiction_reasons",
    "export_sheet",
    "select_for_labeling",
    "summarize_selection",
]
