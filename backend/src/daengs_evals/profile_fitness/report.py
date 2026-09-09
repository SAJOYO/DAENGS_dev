"""리포트 — 판정 파일에서 **결정론으로**. 모델을 부르지 않는다.

    uv run python -m daengs_evals.profile_fitness.report --label pf_v1
    uv run python -m daengs_evals.profile_fitness.report --label pf_v1 --figures

핵심은 부등식 하나다: **N < S ≪ P**.

    N  noise      같은 프로필 두 번        → changed 비율. 잡음 바닥
    S  invariant  다른 프로필 · 법령류 질문  → changed 비율. 특이도
    P  ablation   프로필 없음 vs 있음        → changed 비율. 민감도

이게 성립하면 지표가 잡음이 아니라 개인화를 재고 있는 것이고, P 에서 안 움직이면 지표가 고장났거나
**차별점이 실제로 작동하지 않는 것**이다 — 둘 다 그대로 적는다.

숫자마다 오차막대를 붙인다 (Wilson · 문항 단위 부트스트랩). **종합 점수는 내지 않는다.** 항목이 서로
다른 것을 재고 있어서 합치는 순간 무엇이 나빠졌는지 말할 수 없게 된다.

**사람 라벨 전의 숫자는 "잠정" 이다.** `calibration/gate.py` 가 κ 미달 항목의 숫자를 지우는데, 라벨이
아직 없으면 전부 지워져 아무것도 못 본다. 그래서 라벨 파일이 없을 때는 지우지 않고 **잠정** 이라고
크게 적는다 — 발표에 쓰는 숫자는 확정 표가 있는 것뿐이다. 이 선택은 계획서에서 갈린 지점이라
Notion 3번 페이지에 적었다.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from daengs_evals.answer_quality.provenance import utc_now
from daengs_evals.calibration.agreement import cohen_kappa, simple_agreement, weighted_kappa
from daengs_evals.calibration.gate import (
    DEFAULT_THRESHOLDS,
    Thresholds,
    require_calibration,
    withhold,
)
from daengs_evals.calibration.stats import bootstrap_paired_difference, wilson_interval
from daengs_evals.profile_fitness.anchors import OBSERVATIONS
from daengs_evals.profile_fitness.judge import judgments_path
from daengs_evals.profile_fitness.profiles import ASSETS_DIR
from daengs_evals.profile_fitness.rubric import FAILURES, ITEM_MAX, ITEMS

HUMAN_LABELS_DIR = ASSETS_DIR.parent / "calibration" / "human_labels"


def human_labels_path(label: str) -> Path:
    return HUMAN_LABELS_DIR / f"profile_fitness__{label}.jsonl"


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------


def load_judgments(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    meta: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    ledgers: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        kind = row.get("kind")
        if kind == "meta":
            meta = row
        elif kind == "judgment":
            rows.append(row)
        elif kind == "ledger":
            ledgers.append(row)
    if meta is None:
        raise ValueError(f"{path}: meta 행이 없다")
    return meta, rows, ledgers


def load_human_labels(path: Path) -> dict[str, dict[str, Any]]:
    """pair_id → {changed, profile, fabricated, stereotype, responsiveness?, note}. 없으면 빈 dict."""
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("kind") == "meta":
                continue
            out[row["pair_id"]] = row
    return out


# ---------------------------------------------------------------------------
# 집계 — 순수 함수
# ---------------------------------------------------------------------------


def _rate(rows: Iterable[Mapping[str, Any]], key: str = "changed") -> dict[str, Any]:
    rs = [
        r for r in rows if not r.get("position_dependent") and not r["observation"].get("abstained")
    ]
    k = sum(int(r["observation"][key]) for r in rs)
    return {**wilson_interval(k, len(rs)).as_dict(), "k": k}


def condition_rates(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by = defaultdict(list)
    for r in rows:
        by[r["condition"]].append(r)
    reactive_contrast = [r for r in by["contrast"] if r["question_kind"] == "reactive"]
    invariant_contrast = [r for r in by["contrast"] if r["question_kind"] == "invariant"]
    probe_contrast = [r for r in by["contrast"] if r["question_kind"] == "probe"]
    return {
        "N_noise": _rate(by["noise"]),
        "S_invariant": _rate(invariant_contrast),
        "P_ablation": _rate(by["ablation"]),
        "reactive_contrast": _rate(reactive_contrast),
        "probe_fabricated": _rate(probe_contrast, "fabricated"),
    }


def _defined(*values: float) -> bool:
    """비율이 하나라도 NaN(표본 0) 이면 부등식을 판정하지 않는다."""
    return not any(math.isnan(v) for v in values)


def inequality(rates: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n, s, p = rates["N_noise"]["point"], rates["S_invariant"]["point"], rates["P_ablation"]["point"]
    # P − N 을 문항 단위로 짝지어 부트스트랩 — 같은 reactive 문항에 noise 와 ablation 이 다 있을 때
    noise = {
        r["question_id"]: int(r["observation"]["changed"])
        for r in rows
        if r["condition"] == "noise"
        and r["question_kind"] == "reactive"
        and not r.get("position_dependent")
    }
    abl = {
        r["question_id"]: int(r["observation"]["changed"])
        for r in rows
        if r["condition"] == "ablation" and not r.get("position_dependent")
    }
    paired = [(noise[q], abl[q]) for q in noise if q in abl]
    ci = bootstrap_paired_difference(paired) if paired else None
    return {
        "N": n,
        "S": s,
        "P": p,
        "holds_N_lt_S": (n < s) if _defined(n, s) else None,
        "holds_S_lt_P": (s < p) if _defined(s, p) else None,
        "holds": (n < s < p) if _defined(n, s, p) else None,
        "P_minus_N": ci.as_dict() if ci else None,
        "P_minus_N_ci_excludes_zero": (ci.low > 0) if ci else None,
        "paired_questions": len(paired),
    }


def item_means(rows: Sequence[Mapping[str, Any]], *, group: str | None = None) -> dict[str, Any]:
    """항목별 평균과 n. `group` 을 주면 그 필드(tier · question_kind)로 나눈다."""
    buckets: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        items = (r.get("score") or {}).get("items") or {}
        key = str(r.get(group)) if group else "all"
        for item, value in items.items():
            buckets[key][item].append(int(value))
    out: dict[str, Any] = {}
    for key, per_item in buckets.items():
        out[key] = {
            item: {
                "mean": round(sum(v) / len(v), 3),
                "max": ITEM_MAX[item],
                "n": len(v),
                "distribution": dict(Counter(v)),
            }
            for item, v in per_item.items()
        }
    return out


def failure_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if (r.get("score") or {}).get("skipped") is None]
    counts = Counter(f for r in scored for f in (r["score"].get("failures") or []))
    return {"scored_pairs": len(scored), **{f: counts.get(f, 0) for f in FAILURES}}


def drop_fake_adapter_pairs(
    meta: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """가짜 어댑터의 자리표시 답을 비교한 쌍을 판정에서 뺀다 — 범위 밖으로 센다.

    `pairs.build_pairs` 가 이제 그런 쌍을 아예 안 만들지만, 그 전에 판정된 파일(pf_v1_1 의 2쌍)이
    있다. 리포트가 셀을 다시 읽어 걸러내므로 옛 판정 파일도 바르게 읽힌다.
    """
    from daengs_evals.profile_fitness.collect import cells_path, load_cells

    label = meta.get("cells_label")
    if not label or not cells_path(label).exists():
        return list(rows), []
    cmeta, cells = load_cells(cells_path(label))
    if cmeta.get("adapters") != "fallback-only":
        return list(rows), []
    idx = {(c["question_id"], c["arm"], int(c["run"])): c for c in cells}

    def real(cell: Mapping[str, Any]) -> bool:
        return any(
            r.get("status") == "OK" and r.get("capability") == "general"
            for r in cell.get("results") or []
        )

    kept, dropped = [], []
    for r in rows:
        q, _cond, a, b = r["pair_id"].split("|")
        ca = idx.get((q, *a.rsplit("#", 1)[:1], int(a.rsplit("#", 1)[1])))
        cb = idx.get((q, *b.rsplit("#", 1)[:1], int(b.rsplit("#", 1)[1])))
        if ca is None or cb is None or (real(ca) and real(cb)):
            kept.append(r)
        else:
            dropped.append({"pair_id": r["pair_id"], "reason": "out_of_scope"})
    return kept, dropped


def unmeasured(meta: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    skipped = Counter(s["reason"] for s in meta.get("skipped_pairs") or [])
    pos = sum(1 for r in rows if r.get("position_dependent"))
    pos_checked = sum(1 for r in rows if r.get("position_dependent") is not None)
    abst = sum(1 for r in rows if r["observation"].get("abstained"))
    total = len(rows) + sum(skipped.values())
    return {
        "total_pairs": total,
        "judged": len(rows),
        "skipped_before_judging": dict(skipped),
        "position_dependent": pos,
        "position_checked": pos_checked,
        "position_flip_rate": round(pos / pos_checked, 3) if pos_checked else None,
        "abstained": abst,
        "unmeasured_rate": round((sum(skipped.values()) + pos + abst) / total, 3)
        if total
        else None,
    }


def paraphrase_stability(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """같은 뜻 다른 문장. 원 질문과 패러프레이즈의 `changed` 가 같은 조건에서 일치하는가."""
    index = {(r["question_id"], r["condition"]): int(r["observation"]["changed"]) for r in rows}
    agree, total = 0, 0
    for r in rows:
        origin = r.get("paraphrase_of")
        if not origin:
            continue
        key = (origin, r["condition"])
        if key in index:
            total += 1
            agree += int(index[key] == int(r["observation"]["changed"]))
    return {"pairs": total, "agreement": round(agree / total, 3) if total else None}


def variant_agreement(
    a_rows: Sequence[Mapping[str, Any]], b_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    b_by = {r["pair_id"]: r for r in b_rows}
    shared = [(r, b_by[r["pair_id"]]) for r in a_rows if r["pair_id"] in b_by]
    out: dict[str, Any] = {"shared_pairs": len(shared), "items": {}}
    for item in OBSERVATIONS:
        if item == "abstained":
            continue
        a = [int(x["observation"][item]) for x, _ in shared]
        b = [int(y["observation"][item]) for _, y in shared]
        k = cohen_kappa(a, b)
        out["items"][item] = {
            "agreement": simple_agreement(a, b),
            "kappa": k.value,
            "undefined": k.undefined,
        }
    return out


def human_agreement(
    rows: Sequence[Mapping[str, Any]], labels: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """사람 라벨 vs 판정기 — 관찰 칸별 κ, responsiveness 는 가중 κ."""
    by_id = {r["pair_id"]: r for r in rows}
    shared = [(labels[p], by_id[p]) for p in labels if p in by_id]
    out: dict[str, Any] = {"n": len(shared), "items": {}}
    for item in ("changed", "profile", "fabricated", "stereotype"):
        h = [int(x[item]) if x.get(item) is not None else None for x, _ in shared]
        j = [int(y["observation"][item]) for _, y in shared]
        out["items"][item] = cohen_kappa(h, j)
    h_resp = [x.get("responsiveness") for x, _ in shared]
    j_resp = [((y.get("score") or {}).get("items") or {}).get("responsiveness") for _, y in shared]
    if any(v is not None for v in h_resp):
        out["items"]["responsiveness"] = weighted_kappa(h_resp, j_resp, levels=[0, 1, 2])
    return out


# ---------------------------------------------------------------------------
# 요약 · 렌더
# ---------------------------------------------------------------------------


def summarize(
    meta: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    ledgers: Sequence[Mapping[str, Any]],
    *,
    b_rows: Sequence[Mapping[str, Any]] | None = None,
    labels: Mapping[str, Mapping[str, Any]] | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    rows, dropped = drop_fake_adapter_pairs(meta, rows)
    if dropped:
        meta = {**meta, "skipped_pairs": [*(meta.get("skipped_pairs") or []), *dropped]}
    rates = condition_rates(rows)
    ledger_in = sum(int(x.get("input_tokens") or 0) for x in ledgers)
    ledger_out = sum(int(x.get("output_tokens") or 0) for x in ledgers)
    summary: dict[str, Any] = {
        "label": meta.get("cells_label"),
        "judge": {
            "model": meta.get("judge_model"),
            "variant": meta.get("variant"),
            "prompt_version": meta.get("prompt_version"),
            "hygiene": meta.get("hygiene"),
        },
        "generation": meta.get("generation"),
        "anchor_record": meta.get("anchor_record"),
        "rates": rates,
        "inequality": inequality(rates, rows),
        "items": {k: v["mean"] for k, v in item_means(rows).get("all", {}).items()},
        "items_detail": item_means(rows).get("all", {}),
        "items_by_tier": item_means(rows, group="tier"),
        "items_by_kind": item_means(rows, group="question_kind"),
        "failures": failure_counts(rows),
        "unmeasured": {},
        "coverage": unmeasured(meta, rows),
        "paraphrase": paraphrase_stability(rows),
        "cost": {
            "input_tokens": ledger_in,
            "output_tokens": ledger_out,
            "pairs": len(rows),
            "tokens_per_1000_pairs": round((ledger_in + ledger_out) / len(rows) * 1000)
            if rows
            else None,
        },
        "calibration": None,
        "provisional": True,
        "generated_at": utc_now(),
    }
    if b_rows is not None:
        summary["variant_agreement"] = variant_agreement(rows, b_rows)
    agreements: dict[str, Any]
    if labels:
        # 게이트는 **무작위 블록**으로 — 자기모순 블록은 판정기가 어려워한 것만 모아서 κ 가 과소추정된다
        random_labels = {k: v for k, v in labels.items() if v.get("block") in (None, "random")}
        ha = human_agreement(rows, random_labels or labels)
        ha_all = human_agreement(rows, labels)
        summary["calibration_all_blocks"] = {
            "n": ha_all["n"],
            "items": {k: {"kappa": v.value, "n": v.n} for k, v in ha_all["items"].items()},
            "note": "합산 — 모집단 추정이 아니다",
        }
        summary["calibration"] = {
            "block": "random",
            "n": ha["n"],
            "items": {
                k: {"kappa": v.value, "n": v.n, "undefined": v.undefined, "marginals": v.marginals}
                for k, v in ha["items"].items()
            },
        }
        agreements = {
            "no_fabrication": ha["items"].get("fabricated"),
            "responsiveness": ha["items"].get("responsiveness"),
            "invariance": ha["items"].get("changed"),
        }
        gate = require_calibration(agreements, thresholds)
        summary = withhold(summary, gate)
        summary["provisional"] = bool(gate.withheld)
    else:
        summary["gate"] = {item: {"passed": False, "reason": "not_calibrated"} for item in ITEMS}
    return summary


def _pct(d: Mapping[str, Any] | None) -> str:
    if not d or d.get("point") != d.get("point"):
        return "—"
    return f"{d['point'] * 100:.0f}% [{d['low'] * 100:.0f}, {d['high'] * 100:.0f}] (n={d['n']})"


def render_markdown(s: Mapping[str, Any]) -> str:
    ineq = s["inequality"]
    lines = [
        f"# 개체 적합성 — `{s['label']}`",
        "",
        (
            f"판정 {s['judge']['model']} · {s['judge']['prompt_version']} · "
            f"생성 {s['generation']['general']['model']} "
            f"({s['generation']['general']['prompt_version']}) · {s['generated_at']}"
        ),
        "",
    ]
    if s.get("provisional", True):
        lines += [
            "> ⚠ **잠정.** 사람 라벨 κ 게이트를 아직 못 지났다. 발표에 쓰는 숫자는 확정 표가 있는 것뿐이다.",
            "",
        ]
    lines += [
        "## 부등식 N < S ≪ P — 이 지표가 작동하는가",
        "",
        "| 조건 | 무엇 | changed 비율 [95% CI] |",
        "| --- | --- | --- |",
        f"| **N** 잡음 | 같은 프로필 두 번 | {_pct(s['rates']['N_noise'])} |",
        f"| **S** 특이도 | 다른 프로필 · 법령류 | {_pct(s['rates']['S_invariant'])} |",
        f"| **P** 절제 | 프로필 없음 vs 있음 | {_pct(s['rates']['P_ablation'])} |",
        f"| 본 비교 | 다른 프로필 · reactive | {_pct(s['rates']['reactive_contrast'])} |",
        "",
        f"- N < S: **{ineq['holds_N_lt_S']}** · S < P: **{ineq['holds_S_lt_P']}** · 전체: **{ineq['holds']}**",
        f"- P − N (문항 단위 부트스트랩, {ineq['paired_questions']}문항): "
        + (
            f"{ineq['P_minus_N']['point']:+.2f} [{ineq['P_minus_N']['low']:+.2f}, {ineq['P_minus_N']['high']:+.2f}] — "
            f"0 을 {'안 걸침' if ineq['P_minus_N_ci_excludes_zero'] else '걸침'}"
            if ineq["P_minus_N"]
            else "—"
        ),
        f"- probe 에서 날조: {_pct(s['rates']['probe_fabricated'])}",
        "",
        "## 항목 (종합 점수는 없다)",
        "",
        "| 항목 | 평균 / 만점 | n | 분포 | 게이트 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in ITEMS:
        d = s["items_detail"].get(item)
        g = (s.get("gate") or {}).get(item) or {}
        if not d:
            lines.append(f"| {item} | — | 0 | — | {g.get('reason', '')} |")
            continue
        val = s["items"].get(item)
        shown = f"{val:.2f}" if val is not None else "**보류**"
        lines.append(
            f"| {item} | {shown} / {d['max']} | {d['n']} | {d['distribution']} | {'통과' if g.get('passed') else g.get('reason', '')} |"
        )
    lines += ["", "### 계층별", ""]
    for group_name, groups in (("tier", s["items_by_tier"]), ("kind", s["items_by_kind"])):
        for key, per in groups.items():
            cells = ", ".join(f"{k} {v['mean']:.2f} (n={v['n']})" for k, v in per.items())
            lines.append(f"- {group_name}={key}: {cells}")
    f = s["failures"]
    lines += [
        "",
        f"## 실패 종류 (채점된 쌍 {f['scored_pairs']}개)",
        "",
        "| 실패 | 건수 |",
        "| --- | --- |",
        *[f"| {name} | {f[name]} |" for name in FAILURES],
        "",
        "## 못 잰 것",
        "",
        (
            f"- 쌍 {s['coverage']['total_pairs']} 중 판정 {s['coverage']['judged']} · "
            f"판정 전 제외 {s['coverage']['skipped_before_judging']} · "
            f"위치 뒤집힘 {s['coverage']['position_dependent']}/{s['coverage']['position_checked']} (양방향 본 쌍 중) · 기권 {s['coverage']['abstained']} → "
            f"미측정 비율 **{s['coverage']['unmeasured_rate']}**"
        ),
        f"- 패러프레이즈 일치: {s['paraphrase']['agreement']} ({s['paraphrase']['pairs']}쌍)",
        f"- 비용: 입력 {s['cost']['input_tokens']:,} / 출력 {s['cost']['output_tokens']:,} 토큰 · 1000쌍당 {s['cost']['tokens_per_1000_pairs']:,}"
        if s["cost"]["tokens_per_1000_pairs"]
        else "- 비용: —",
        "",
        "## 측정 대상이 아닌 것",
        "",
        "- 훈련 능력 — `TrainingPayload = {question}`, 프로필이 구조적으로 안 간다 (계약 테스트로 못 박음)",
        "- Life 경로 — 실서버(pgvector · Redis) 필요. v1 은 `general` 만",
        "- 안전 상호작용(unsafe_escalation) — 축 B 의 `safe` 판정으로 따로 잰다",
        "",
    ]
    if s.get("variant_agreement"):
        va = s["variant_agreement"]
        lines += ["## 프롬프트 변형 A/B 일치", "", f"공유 쌍 {va['shared_pairs']}", ""]
        for item, d in va["items"].items():
            k = f"{d['kappa']:.2f}" if d["kappa"] is not None else f"({d['undefined']})"
            lines.append(
                f"- {item}: 일치 {d['agreement']:.2f} · κ {k}"
                if d["agreement"] is not None
                else f"- {item}: —"
            )
        lines.append("")
    if s.get("calibration"):
        c = s["calibration"]
        lines += ["## 사람 라벨 κ (무작위 블록 — 게이트가 보는 값)", "", f"라벨 {c['n']}건", ""]
        for item, d in c["items"].items():
            k = f"{d['kappa']:.2f}" if d["kappa"] is not None else f"못 잼 ({d['undefined']})"
            lines.append(
                f"- {item}: κ {k} (n={d['n']}) 사람 주변 {d.get('marginals', {}).get('human') if d.get('marginals') else '—'}"
            )
        lines.append("")
    if s.get("unmeasured"):
        lines += ["## 보류된 항목", ""] + [f"- {k}: {v}" for k, v in s["unmeasured"].items()] + [""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 그림 — 의존성 없이 SVG
# ---------------------------------------------------------------------------


def render_svg(s: Mapping[str, Any]) -> str:
    bars = [
        ("N 잡음", s["rates"]["N_noise"]),
        ("S 법령", s["rates"]["S_invariant"]),
        ("P 절제", s["rates"]["P_ablation"]),
        ("본 비교", s["rates"]["reactive_contrast"]),
    ]
    w, h, pad, bw = 520, 300, 50, 80
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="sans-serif" font-size="12">',
        f'<text x="{pad}" y="24" font-size="15" font-weight="bold">changed 비율 — N &lt; S ≪ P 인가</text>',
    ]
    for i, y in enumerate((0, 0.5, 1.0)):
        yy = h - pad - y * (h - 2 * pad)
        parts.append(f'<line x1="{pad}" y1="{yy:.0f}" x2="{w - 10}" y2="{yy:.0f}" stroke="#ddd"/>')
        parts.append(f'<text x="{pad - 30}" y="{yy + 4:.0f}">{int(y * 100)}%</text>')
    for i, (name, d) in enumerate(bars):
        if d["point"] != d["point"]:
            continue
        x = pad + 20 + i * (bw + 30)
        top = h - pad - d["point"] * (h - 2 * pad)
        lo = h - pad - d["low"] * (h - 2 * pad)
        hi = h - pad - d["high"] * (h - 2 * pad)
        parts.append(
            f'<rect x="{x}" y="{top:.0f}" width="{bw}" height="{h - pad - top:.0f}" fill="#4a7ebb"/>'
        )
        cx = x + bw / 2
        parts.append(
            f'<line x1="{cx}" y1="{hi:.0f}" x2="{cx}" y2="{lo:.0f}" stroke="#222" stroke-width="2"/>'
        )
        parts.append(f'<text x="{cx}" y="{h - pad + 18}" text-anchor="middle">{name}</text>')
        parts.append(
            f'<text x="{cx}" y="{top - 6:.0f}" text-anchor="middle">{d["point"] * 100:.0f}% (n={d["n"]})</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개체 적합성 리포트")
    parser.add_argument("--label", required=True)
    parser.add_argument("--variant", default="A")
    parser.add_argument("--figures", action="store_true")
    parser.add_argument("--note", default="")
    parser.add_argument(
        "--judgments",
        default=None,
        help="판정 파일 경로. 기본은 지금 프롬프트 버전의 것 — 옛 버전을 다시 그릴 때 준다",
    )
    parser.add_argument("--suffix", default="", help="산출물 이름 뒤에 붙일 표시 (예: v1a)")
    args = parser.parse_args(argv)

    src = Path(args.judgments) if args.judgments else judgments_path(args.label, args.variant)
    meta, rows, ledgers = load_judgments(src)
    other = "B" if args.variant == "A" else "A"
    b_path = judgments_path(args.label, other)
    b_rows = load_judgments(b_path)[1] if b_path.exists() else None
    labels = load_human_labels(human_labels_path(args.label))
    summary = summarize(meta, rows, ledgers, b_rows=b_rows, labels=labels)
    if args.note:
        summary["note"] = args.note

    tag = f"{args.label}_{args.suffix}" if args.suffix else args.label
    md = ASSETS_DIR / f"report_pf_{tag}.md"
    js = ASSETS_DIR / f"summary_pf_{tag}.json"
    md.write_text(
        render_markdown(summary) + (f"\n> {args.note}\n" if args.note else ""), encoding="utf-8"
    )
    js.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(render_markdown(summary))
    if args.figures:
        svg = ASSETS_DIR / f"figure_pf_{tag}_conditions.svg"
        svg.write_text(render_svg(summary), encoding="utf-8")
        print(f"→ {svg.name}")
    print(f"→ {md.name} · {js.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
