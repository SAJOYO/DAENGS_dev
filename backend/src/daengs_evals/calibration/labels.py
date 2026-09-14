"""채워진 시트 → 라벨. 뒤집기를 되돌리고, 반복분으로 본인 일관성을 낸다.

시트 포맷은 `training_quality/judgments_lap1__human.jsonl`(header + judgment) 의 모양을 따르되
쌍 단위다. 합성 프로필로 만든 답변의 라벨은 `evals/calibration/human_labels/` 에 커밋한다 —
재현에 필요하다. **실사용 대화**로 만든 라벨은 `human_labels/real/` 에 두고 그 디렉터리는
`.gitignore` 다: 라벨 시트에 답변 원문이 들어가므로 저장소에 남으면 D-037 위반이다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from daengs_evals.answer_quality.provenance import utc_now
from daengs_evals.calibration.agreement import Agreement, cohen_kappa, weighted_kappa

BINARY_FIELDS: tuple[str, ...] = ("changed", "profile", "fabricated", "stereotype")


def _read(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("kind") != "meta":
                rows.append(row)
    return rows


def _coerce(row: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in BINARY_FIELDS:
        v = row.get(f)
        out[f] = None if v in (None, "") else int(v)
    v = row.get("responsiveness")
    if v in (None, ""):
        # 사람이 안 적었으면 루브릭대로 파생 — rubric.score_pair 와 같은 진리표
        c, pr = out["changed"], out["profile"]
        out["responsiveness"] = None if c is None else (0 if c == 0 else (2 if pr == 1 else 1))
    else:
        out["responsiveness"] = int(v)
    out["note"] = row.get("note") or ""
    return out


def read_labels(
    sheet_path: Path, key_path: Path
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, dict[str, Any], dict[str, Any]]]]:
    """(pair_id → 라벨, 반복 쌍 목록). 뒤집힌 시트는 `profile`/`changed` 가 대칭이라 그대로 두고,
    비대칭인 것(어느 답이 날조했나 등)은 이 스키마에 없다 — 관찰이 쌍 단위라서다."""
    sheet = {r["sheet_no"]: r for r in _read(sheet_path)}
    keys = _read(key_path)
    labels: dict[str, dict[str, Any]] = {}
    repeats: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for k in keys:
        row = sheet.get(k["sheet_no"])
        if row is None:
            continue
        label = _coerce(row)
        if all(label[f] is None for f in BINARY_FIELDS):
            continue  # 아직 안 매김
        label["block"] = k["block"]
        label["labeled_at"] = row.get("labeled_at") or utc_now()
        if k["block"] == "repeat":
            if k["pair_id"] in labels:
                repeats.append((k["pair_id"], labels[k["pair_id"]], label))
            continue
        labels[k["pair_id"]] = {"pair_id": k["pair_id"], **label}
    return labels, repeats


def intra_rater(
    repeats: Sequence[tuple[str, Mapping[str, Any], Mapping[str, Any]]],
) -> dict[str, Agreement]:
    """같은 사람이 나중에 다시 붙인 라벨과의 κ. 이게 판정기 κ 의 천장이다."""
    out: dict[str, Agreement] = {}
    for f in BINARY_FIELDS:
        a = [first[f] for _, first, _ in repeats]
        b = [second[f] for _, _, second in repeats]
        out[f] = cohen_kappa(a, b, min_n=2)
    a = [first.get("responsiveness") for _, first, _ in repeats]
    b = [second.get("responsiveness") for _, _, second in repeats]
    if any(v is not None for v in a):
        out["responsiveness"] = weighted_kappa(a, b, levels=[0, 1, 2], min_n=2)
    return out


def write_labels(labels: Mapping[str, Mapping[str, Any]], path: Path, *, labeler: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as h:
        h.write(
            json.dumps(
                {"kind": "meta", "labeler": labeler, "count": len(labels), "written_at": utc_now()},
                ensure_ascii=False,
            )
            + "\n"
        )
        for pair_id in sorted(labels):
            h.write(json.dumps(labels[pair_id], ensure_ascii=False) + "\n")


__all__ = ["BINARY_FIELDS", "intra_rater", "read_labels", "write_labels"]
