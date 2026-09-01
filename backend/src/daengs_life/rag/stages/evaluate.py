"""6단계 3파전 — 골든셋으로 임베딩 3종을 채점하고 **승자 하나를 고른다** (RAG-024).

계획서에 모순이 하나 있었다. 검문소②는 "15건이라 통계적 판정은 불가"라고 말하는데 7단계는
1종을 요구한다. RAG-024 가 그 모순을 푼 방식은 **되돌리는 비용**에서 출발한다 — parquet 3벌이
이미 디스크에 있어 재적재가 몇 분이므로 **6단계는 최선을 증명할 필요가 없다.** 명백히 나쁜
것만 버리고 나머지는 결과를 보기 전에 정한 규칙으로 집으면 된다.

그러면 진짜 위험은 "틀린 모델을 고르는 것"이 아니라 **15문항의 점수차를 근거로 읽는 것**이
된다. 그래서 이 파일의 판정 함수(`judge`)는 **점수를 비교하지 않는다** — `Hit@5` 의 문항 수만
보고, 그마저 2문항 이상 벌어졌을 때만 탈락시킨다. 나머지는 사전 순위가 정한다.

  ① 사전 등록 + 기준선 우선 — 탈락 후 남은 것들의 점수차는 **읽지 않는다**. 둘 이상 남으면 `bge-m3`
  ② 판정 k = 5 — 변별력과 운영 k(검문소③ top-5)가 같은 답을 가리켰다. 표에는 1·3·5·10 전부
  ③ 탈락선 = `Hit@5`(문항 균등), 2문항 차. `Recall`·`MRR` 은 **산출·기록하되 승자 규칙에 안 들어간다**
  ④ 덤프는 `data/processed/eval/` 미추적, 판정 요약은 RAG-024 에 붙인다

**`Hit` 이 KPI 와 어긋난다는 것은 알고 쓴다.** Q4(로트와일러)는 법 제2조와 시행규칙 제2조가
둘 다 있어야 인용이 성립하는데 `Hit` 은 하나만 걸려도 만점이다. 그 대가는 RAG-024 ③에 적혀 있고,
인용의 온전함은 8단계 검문소③이 잡는다. 여기서 `Recall` 을 승자 규칙에 슬쩍 넣으면
"Hit 은 동률인데 Recall 이 높으니 이쪽"이 되어 ①의 사전 등록이 뒷문으로 무너진다.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core import io
from . import embed, goldenset

VERSION = 1

KS: tuple[int, ...] = (1, 3, 5, 10)   # 표에 싣는 k 전부 (RAG-024 ②)
JUDGE_K = 5                           # 그중 **탈락선이 읽는** 단 하나
CUT_GAP = 2                           # 최고보다 이만큼 낮으면 탈락. 15분의 1 = 6.7%p 눈금이라 1은 우연이다
DUMP_TOP = 10                         # 덤프에 남기는 깊이. 검문소②가 눈으로 보는 것

# **사전 순위** (RAG-024 ①). 기준선이 맨 앞이고, 이 순서는 `embed.MODELS` 등록 순서 그대로다.
# ① 은 "둘 이상 남으면 bge-m3" 라고만 적혀 있어 **기준선이 탈락한 경우를 말하지 않는다.**
# 그 자리를 순위로 일반화했다 — 결과를 보고 정하는 자리가 생기면 사전 등록이 무너지므로,
# 일어나지 않을 것 같은 경우에도 규칙이 먼저 있어야 한다.
PREFERENCE: tuple[str, ...] = tuple(embed.MODELS)
BASELINE = PREFERENCE[0]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 지표 (RAG-024 ③)
def item_metrics(must_ranks: list[int], ks: tuple[int, ...] = KS) -> dict[str, Any]:
    """한 문항의 지표. 입력은 **필수 라벨 각각이 랭킹에서 몇 위인가**(1-based) 뿐이다.

    검색과 분리해 둔 이유 — 지표 정의는 RAG-024 ③ 의 결정이고, 벡터나 데이터 없이 손계산으로
    검증할 수 있어야 한다. `tests/test_evaluate.py` 가 여기만 붙잡는다.

    - `hit@k`   필수가 **하나라도** top-k 에 있으면 1. 이진값이라 문항 균등 말고 다른 평균이 없다
    - `recall@k` 필수 중 top-k 에 든 비율. 기록만 한다
    - `rr`      **첫** 필수의 역순위(표준 MRR). 다중 정답을 얼마나 건졌나는 recall 이 이미 본다
    """
    if not must_ranks:                    # 골든셋 검증이 먼저 막지만, 0으로 나누지는 않는다
        return {"hit": {k: 0.0 for k in ks}, "recall": {k: 0.0 for k in ks}, "rr": 0.0}
    return {
        "hit": {k: 1.0 if any(r <= k for r in must_ranks) else 0.0 for k in ks},
        "recall": {k: sum(1 for r in must_ranks if r <= k) / len(must_ranks) for k in ks},
        "rr": 1.0 / min(must_ranks),
    }


def macro(per_item: list[dict[str, Any]], field: str, k: int) -> float:
    """문항 균등 평균 (RAG-024 ③).

    43쌍을 통으로 세는 micro 를 쓰지 않는 이유는 easylaw 가 31/43 = 72% 를 지배하기 때문이다.
    그 라벨이 많은 건 그 문항이 어려워서가 아니라 **법제처 목록을 통째로 차용**했기 때문이라
    (RAG-022 ②), micro 는 라벨링 출처의 차이를 그대로 채점 가중치로 바꾼다.
    """
    return sum(m[field][k] for m in per_item) / len(per_item) if per_item else 0.0


# ---------------------------------------------------------------- 판정 (RAG-024 ①③)
class Verdict(_Base):
    hits: dict[str, int]              # 모델 → Hit@5 를 맞힌 문항 수 (15점 만점)
    survivors: list[str]
    eliminated: list[str]
    winner: str
    rule: str                         # ① 의 어느 줄이 적용됐나 — 요약에 반드시 들어간다 (RAG-024 ④)


def judge(hits: dict[str, int]) -> Verdict:
    """`Hit@5` 의 **문항 수**만 보고 승자를 정한다. 점수는 보지 않는다 (RAG-024 ①③).

    입력이 `dict[모델, 문항 수]` 뿐인 것이 이 함수의 요지다 — Recall 이나 MRR 을 인자로 받지
    않으므로 "동률인데 Recall 이 높으니" 가 코드에 들어올 자리가 없다.
    """
    ordered = [k for k in PREFERENCE if k in hits]
    best = max(hits[k] for k in ordered)
    survivors = [k for k in ordered if best - hits[k] < CUT_GAP]
    eliminated = [k for k in ordered if k not in survivors]

    if len(survivors) == 1:
        rule = f"탈락으로 갈렸다 — 나머지가 Hit@{JUDGE_K} 에서 {CUT_GAP}문항 이상 낮다"
    elif survivors[0] == BASELINE:
        rule = (f"동률(최대 {CUT_GAP - 1}문항 차)이라 점수차를 읽지 않고 기준선을 쓴다 — RAG-024 ①")
    else:
        rule = (f"동률이라 사전 순위로 집었다. **기준선 {BASELINE} 이 탈락한 경우**이고 "
                f"RAG-024 ① 이 명시하지 않은 자리다 — ADR 에 이 실행을 근거로 한 줄 추가할 것")
    return Verdict(hits=hits, survivors=survivors, eliminated=eliminated,
                   winner=survivors[0], rule=rule)


# ---------------------------------------------------------------- 산출물 (RAG-024 ④)
class EvalItem(_Base):
    type: str = "eval"
    item_id: str
    origin: str
    question: str
    must: list[dict[str, Any]]        # {address, chunk_id, rank} — 필수가 몇 위였나
    top: list[dict[str, Any]]         # {rank, chunk_id, score, tier} — 검문소②가 눈으로 보는 것
    hit: dict[int, float]
    recall: dict[int, float]
    rr: float


class EvalSet(_Base):
    type: str = "evalset"
    model_key: str
    embedding_model: str
    chunks_sha256: str
    corpus_collected_on: str
    corpus_chunk_count: int
    goldenset_items: int
    goldenset_must: int
    ks: list[int]
    judge_k: int
    evaluator_version: int
    evaluated_at: str


# ---------------------------------------------------------------- 검색
def load_matrix(key: str):
    """parquet → (chunk_id 목록, (n, 1024) float32 행렬).

    벡터는 이미 L2 정규화되어 있으므로(4단계) **내적이 곧 코사인**이다. 여기서 다시 정규화하지
    않는 것은 실수 방지가 아니라 계약이다 — `test_embed.test_normalized_and_aligned` 가 지킨다.
    """
    import numpy as np
    import pyarrow.parquet as pq

    path = embed.parquet_path(key)
    if not path.is_file():
        return None, None
    table = pq.read_table(path)
    ids = table["chunk_id"].to_pylist()
    matrix = np.stack(table["embedding"].to_pylist()).astype("float32")
    return ids, matrix


def rank_question(model: embed.Model, question: str, matrix, st=None):
    """질의 하나 → (내림차순 행 인덱스, 유사도, 1-based 순위 배열).

    **질의는 반드시 `encode_query` 로 넣는다.** Qwen3 만 질의에 공식 지시문을 붙이는 비대칭
    모델이라(4단계 실측), 문서 경로로 넣으면 그 모델을 자기 설계와 다르게 쓰면서 점수를 매기게 된다.
    """
    import numpy as np

    q = embed.encode_query(model, question, st=st)
    sims = matrix @ q
    order = np.argsort(-sims, kind="stable")      # 동점은 행 순서로 — 실행마다 바뀌면 비교가 안 된다
    ranks = np.empty(len(sims), dtype=np.int32)
    ranks[order] = np.arange(1, len(sims) + 1)
    return order, sims, ranks


def score_model(key: str, gs: goldenset.GoldenSet, index: dict[str, str], *,
                top: int = DUMP_TOP, progress=None) -> tuple[list[EvalItem], dict[str, Any]] | None:
    """모델 하나를 15문항으로 채점한다. **모델은 이 함수 안에서 올라갔다 내려간다.**

    RTX 3050 6GB 에서 3종을 한 프로세스에 연속으로 올리면 VRAM 이 누적돼 마지막 모델이 10배
    넘게 느려진다(4단계 실측). 6단계가 인코딩하는 것은 질의 15개뿐이라 압박이 훨씬 작지만,
    `release()` 를 부르는 자리를 단계마다 다르게 두지 않는다.
    """
    ids, matrix = load_matrix(key)
    if ids is None:
        return None
    id_to_row = {cid: i for i, cid in enumerate(ids)}
    model = embed.MODELS[key]

    st = embed.load_model(model)
    try:
        items: list[EvalItem] = []
        for item in gs.items:
            if progress:
                progress(item.id)
            order, sims, ranks = rank_question(model, item.question, matrix, st=st)

            must_rows = {}
            for address in item.must:
                row = id_to_row.get(index[address])
                must_rows[address] = row
            must_ranks = [int(ranks[r]) for r in must_rows.values() if r is not None]
            nice_rows = {index[a] for a in item.nice if a in index}

            m = item_metrics(must_ranks)
            items.append(EvalItem(
                item_id=item.id, origin=item.origin, question=item.question,
                must=[{"address": a, "chunk_id": index[a],
                       "rank": int(ranks[r]) if r is not None else None}
                      for a, r in must_rows.items()],
                top=[{"rank": i + 1, "chunk_id": ids[int(row)], "score": round(float(sims[row]), 5),
                      "tier": ("must" if ids[int(row)] in {index[a] for a in item.must}
                               else "nice" if ids[int(row)] in nice_rows else "-")}
                     for i, row in enumerate(order[:top])],
                hit=m["hit"], recall=m["recall"], rr=m["rr"],
            ))
    finally:
        del st
        embed.release()

    per_item = [{"hit": i.hit, "recall": i.recall} for i in items]
    summary = {
        "hit": {k: macro(per_item, "hit", k) for k in KS},
        "recall": {k: macro(per_item, "recall", k) for k in KS},
        "mrr": sum(i.rr for i in items) / len(items) if items else 0.0,
        "hit_count": sum(int(i.hit[JUDGE_K]) for i in items),
        "zero_hit": [i.item_id for i in items if not i.hit[JUDGE_K]],
    }
    return items, summary


def write_dump(key: str, items: list[EvalItem], gs: goldenset.GoldenSet,
               fingerprint: str) -> Any:
    meta = embed.read_meta(key) or {}
    header = EvalSet(
        model_key=key,
        embedding_model=meta.get("embedding_model", embed.MODELS[key].repo),
        chunks_sha256=fingerprint,
        corpus_collected_on=str(gs.corpus.collected_on),
        corpus_chunk_count=gs.corpus.chunk_count,
        goldenset_items=len(gs.items),
        goldenset_must=gs.must_total,
        ks=list(KS), judge_k=JUDGE_K,
        evaluator_version=VERSION, evaluated_at=io.now_kst(),
    )
    return io.write_eval(header, items)


# ---------------------------------------------------------------- 요약 (RAG-024 ④ 필수 4항)
def markdown(summaries: dict[str, dict[str, Any]], verdict: Verdict,
             gs: goldenset.GoldenSet, fingerprint: str, chunk_count: int) -> str:
    """**그대로 RAG-024 에 붙일 markdown.** 덤프가 미추적이라 이것이 뒤에 남는 전부다.

    네 가지가 반드시 들어간다 (RAG-024 ④): 점수표 · `Hit@5=0` 문항 id · ① 의 어느 줄이
    적용됐나 · 코퍼스 스냅샷.
    """
    n = len(gs.items)
    out = [f"### 판정 결과 ({io.now_kst()[:10]})", ""]
    out.append(f"코퍼스 {chunk_count}청크 · 라벨 기준 {gs.corpus.collected_on} · "
               f"`chunks_sha256` `{fingerprint[:16]}` · 골든셋 {n}문항 / 필수 {gs.must_total}")
    out.append("")
    head = " | ".join(f"Hit@{k}" for k in KS)
    out.append(f"| 모델 | {head} | Recall@{JUDGE_K} | MRR | Hit@{JUDGE_K} 문항 |")
    out.append("|---|" + "---|" * (len(KS) + 3))
    for key in PREFERENCE:
        s = summaries.get(key)
        if not s:
            continue
        cells = " | ".join(f"{s['hit'][k]:.3f}" for k in KS)
        mark = "**" if key == verdict.winner else ""
        out.append(f"| {mark}{key}{mark} | {cells} | {s['recall'][JUDGE_K]:.3f} | "
                   f"{s['mrr']:.3f} | {s['hit_count']}/{n} |")
    out.append("")
    out.append(f"**`Hit@{JUDGE_K}` = 0 인 문항** — \"명백히 나쁨\"의 실체다")
    out.append("")
    for key in PREFERENCE:
        s = summaries.get(key)
        if not s:
            continue
        out.append(f"- `{key}` — {', '.join(s['zero_hit']) if s['zero_hit'] else '없음'}")
    out.append("")
    out.append(f"**판정** — 승자 **`{verdict.winner}`**. {verdict.rule}")
    if verdict.eliminated:
        out.append(f"탈락: {', '.join(verdict.eliminated)} "
                   f"(`Hit@{JUDGE_K}` 기준 {CUT_GAP}문항 이상 낮다)")
    out.append("")
    out.append(f"> `Recall`·`MRR`·`Hit@1·@3` 은 기록만 한다 — 승자 규칙에 들어가지 않는다 (RAG-024 ③).")
    out.append(f"> 인용의 온전함(`must` 를 다 건졌나)은 8단계 검문소③이 눈으로 잡는다.")
    return "\n".join(out)
