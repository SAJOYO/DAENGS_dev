"""게이트 ② — **오프라인 재정렬**. 리랭커를 붙이면 무엇이 올라오나 (D2 / #316 / RAG-076).

────────────────────────────────────────────────────────────────────────────
게이트 ①이 정한 통과선
────────────────────────────────────────────────────────────────────────────
`d2_candidate_probe.py` 가 **리랭커의 상한이 문항 2개(`Q1`·`I4`)** 임을 보였다. 33문항 중
31문항은 `must` 가 이미 근거에 실려 있어 리랭커가 안 해도 `grounded` 가 선다.

    통과선  `Q1`·`I4` 중 하나 이상이 top-5 로 올라오고, **이미 서 있는 문항이 안 무너진다**

**뒷줄이 앞줄만큼 중요하다.** 리랭커는 순위를 통째로 다시 매기므로 잘 되던 문항을 떨어뜨릴 수
있고, 그것이 `D5`(#208)가 *"상수로는 안 된다"* 로 만난 모양이다.

────────────────────────────────────────────────────────────────────────────
무엇을 하나 — **파일을 안 고친다**
────────────────────────────────────────────────────────────────────────────
`search._SQL` 을 그대로 불러 후보를 받고(= 서빙과 같은 SQL·같은 필터), 그 후보를 크로스
인코더로 다시 정렬해 **top-5 가 어떻게 달라지는지만** 본다. `search.py` 도 DB 도 안 건드린다.
`D5`·`D5d`·`D5e`·`D14`·`D5b` 가 전부 이 모양이었고, 다섯 중 넷이 여기서 멈췄다.

⚠ **인용 확장까지 다시 돈다** (RAG-040). 확장은 top-20 이 가리키는 조문을 끌어오므로
**순위가 바뀌면 확장 결과도 바뀐다.** top-5 만 비교하면 실제로 생성기에 실리는 근거의 변화를
놓친다 — 그래서 두 축을 다 낸다.

⚠ **질의는 두 가지를 쓴다.** 검색에는 어휘가 넓혀진 질의(RAG-066)를, 리랭커에는 **원문**을
넣는다. 크로스 인코더는 사람이 쓴 문장을 읽는 모델이고, 넓힌 문장은 검색 후보를 넓히려고
만든 것이라 목적이 다르다.

⚠ **전수 스캔 위에서 잰다** — `D16`(HNSW)이 켜지면 다시 재야 한다 (`roadmap.md` §4 의 1번).

⚠ **읽기만 한다.** DB 는 조회만 하고 parquet·코퍼스는 안 건드린다.

사용: cd backend && uv run --no-sync python tools/d2_rerank_probe.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))
sys.path.insert(0, str(_HERE))

from d2_candidate_probe import _filters

from daengs_life.rag.core import config
from daengs_life.rag.stages import embed, goldenset, load, search

# 후보를 그대로 받는다 — `search._SQL` 의 마지막 `LIMIT %(k)s` 에 넣을 값.
# 후보는 dense 100 ∪ lex 100 이라 200 을 넘지 않는다.
POOL_LIMIT = 10_000

RERANKER = "BAAI/bge-reranker-v2-m3"
# 청크는 대부분 짧지만 별표·약관 행은 길다. 512 로 자르면 조 번호가 잘려 나가는 자리가 생긴다.
MAX_LENGTH = 1024
BATCH = 16


def _pool(query: search.Query, conn) -> list[search.Hit]:
    """서빙과 **같은 SQL** 로 후보 전부. 순서는 지금의 RRF 순서다."""
    f, lf, params = _filters(query, conn)
    params |= {"k": POOL_LIMIT, "n": search.CANDIDATE_N,
               "rrf": search.RRF_K, "wlex": search.LEXICAL_WEIGHT}
    with conn.cursor() as cur:
        cur.execute(search._SQL.format(filters=f, lex_filters=lf), params)
        return [
            search.Hit(rank=i + 1, score=float(score), chunk_id=cid, citation=cit or "",
                       citation_url=url, section=section, document_title=title or "",
                       content=content, part=part, dense_rank=drn, lexical_rank=lrn)
            for i, (score, cid, cit, url, section, title, content, part, drn, lrn)
            in enumerate(cur.fetchall())
        ]


def _evidence(hits: list[search.Hit], query: search.Query, pool: list[search.Hit],
              conn) -> set[str]:
    """생성기에 실리는 근거의 논리 주소. **인용 확장까지 돈다** (`search.search()` 와 같게)."""
    delivered = search.expand_citations(
        hits[:search.DEFAULT_K], query, scan=pool[:search.EXPAND_SCAN_N], conn=conn)
    return {goldenset.logical(h.chunk_id) for h in delivered}


def _document(hit: search.Hit, *, titled: bool) -> str:
    """리랭커에 넣을 문서 텍스트.

    ⚠ **`content` 만 넣으면 리랭커가 어느 법인지 못 본다.** 법령 청크의 본문은 `제2조(정의) …`
    처럼 시작하고 법령명이 **본문에 없다** — 그것은 `document_title` 에 있다. 1차(content 만)에서
    무너진 문항이 전부 법령 문항이라 그 가설을 재려고 갈라 뒀다.

    `titled` 는 생성기가 보는 것과 같은 모양이다 — `citation` 이 `법령명 제N조` 를 들고 있다.
    """
    if not titled:
        return hit.content
    head = " ".join(x for x in (hit.document_title, hit.section) if x)
    return f"{head}\n{hit.content}" if head else hit.content


def _rank_of(hits: list[search.Hit], must: set[str]) -> int | None:
    """`must` 주소가 처음 나오는 자리. 없으면 None."""
    for i, h in enumerate(hits, 1):
        if goldenset.logical(h.chunk_id) in must:
            return i
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--titled", action="store_true",
                    help="리랭커에 document_title·section 을 같이 넣는다 (`_document` 참고)")
    args = ap.parse_args()

    gs = goldenset.load()
    items = [i for i in gs.items if i.origin == "hand" and i.expect == "answer"]
    key = config.settings.embedding_model_key
    shape = "제목+본문" if args.titled else "본문만"
    print(f"# 게이트 ② — 오프라인 재정렬   ({RERANKER} · 문서 {shape})")
    print(f"# 검색 {key} · CANDIDATE_N={search.CANDIDATE_N} · 문항 {len(items)}\n")

    conn = load.connect()
    # ── 1단계: 후보를 다 받아 둔다. **임베더를 내린 뒤에 리랭커를 올린다** —
    #    RTX 3050 6GB 에 둘을 같이 올리면 스와핑한다 (`embed.release()` 주석).
    st = embed.load_model(embed.MODELS[key])
    staged: list[tuple[object, search.Query, list[search.Hit], set[str], set[str]]] = []
    try:
        for item in items:
            query = search.encode(item.question, model_key=key, st=st)
            pool = _pool(query, conn)
            staged.append((item, query, pool, set(item.must_flat),
                           _evidence(pool, query, pool, conn)))
    finally:
        del st
        embed.release()

    from sentence_transformers import CrossEncoder
    ce = CrossEncoder(RERANKER, max_length=MAX_LENGTH,
                      device="cuda" if _cuda() else "cpu")

    rows, pairs_total, t0 = [], 0, time.perf_counter()
    try:
        for item, query, pool, must, before in staged:
            scores = ce.predict(
                [(item.question, _document(h, titled=args.titled)) for h in pool],
                batch_size=BATCH, show_progress_bar=False)
            pairs_total += len(pool)
            order = sorted(range(len(pool)), key=lambda i: -float(scores[i]))
            reranked = [
                search.Hit(rank=new + 1, score=pool[i].score, chunk_id=pool[i].chunk_id,
                           citation=pool[i].citation, citation_url=pool[i].citation_url,
                           section=pool[i].section, document_title=pool[i].document_title,
                           content=pool[i].content, part=pool[i].part,
                           dense_rank=pool[i].dense_rank, lexical_rank=pool[i].lexical_rank)
                for new, i in enumerate(order)
            ]
            after = _evidence(reranked, query, reranked, conn)
            rows.append((item.id,
                         _rank_of(pool, must), _rank_of(reranked, must),
                         bool(must & before), bool(must & after)))
    finally:
        conn.close()
        del ce
        embed.release()

    elapsed = time.perf_counter() - t0
    print(f"{'문항':<6} {'지금 순위':>9} {'리랭크':>7}   {'근거(지금)':<10} {'근거(리랭크)':<12} 판정")
    won, lost = [], []
    for qid, r_before, r_after, e_before, e_after in rows:
        if e_after and not e_before:
            won.append(qid)
            verdict = "★ 살았다"
        elif e_before and not e_after:
            lost.append(qid)
            verdict = "🔴 무너졌다"
        elif e_before:
            verdict = "그대로 (서 있음)"
        else:
            verdict = "그대로 (못 함)"
        was, now = f"{r_before or '-'}", f"{r_after or '-'}"
        print(f"{qid:<6} {was:>9} {now:>7}   "
              f"{'있음' if e_before else '없음':<10} {'있음' if e_after else '없음':<12} {verdict}")

    n = len(rows)
    b = sum(1 for r in rows if r[3])
    a = sum(1 for r in rows if r[4])
    print(f"\n## 근거에 `must` 가 있는 문항   {b}/{n}  →  {a}/{n}")
    print(f"  살아난 문항  {' '.join(won) or '없음'}")
    print(f"  무너진 문항  {' '.join(lost) or '없음'}")
    top5_b = sum(1 for r in rows if r[1] and r[1] <= search.DEFAULT_K)
    top5_a = sum(1 for r in rows if r[2] and r[2] <= search.DEFAULT_K)
    print(f"\n## `must` 가 top-5 안인 문항 (확장 제외)   {top5_b}/{n}  →  {top5_a}/{n}")
    print(f"\n## 비용   쌍 {pairs_total} · {elapsed:.1f}초 "
          f"· 질의당 {elapsed / max(n, 1) * 1000:.0f}ms")
    print("  ⚠ 배치 예열이 섞인 수다. 서빙 지연은 카드가 통과한 뒤 `/life/ask` 에서 따로 잰다")
    return 0


def _cuda() -> bool:
    import torch
    return torch.cuda.is_available()


if __name__ == "__main__":
    raise SystemExit(main())
