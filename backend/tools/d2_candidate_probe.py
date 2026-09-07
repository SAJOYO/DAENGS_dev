"""게이트 ① — **리랭커가 볼 수 있는 자리에 답이 있는가** (D2 / #316 / RAG-076).

────────────────────────────────────────────────────────────────────────────
왜 이 스크립트가 먼저인가
────────────────────────────────────────────────────────────────────────────
리랭커는 **후보 안에서만 재정렬한다.** `RAG-072` ③ 이 렉시컬에 대해 적은
*"dense 후보를 재정렬할 뿐 후보를 못 들여온다"* 가 리랭커에도 그대로 걸린다.

그런데 `search.CANDIDATE_N = 100` 인데 `I4` 의 정답 면책 조는 **dense 89·153위**다
(`RAG-072` ⑤). 89위는 후보 안, **153위는 밖**이다 — 리랭커를 붙여도 153위는 못 본다.

그래서 도입을 재기 전에 **후보 안에 답이 얼마나 들어와 있는지**부터 센다. 답이 후보 밖이면
이 카드의 답은 리랭커가 아니라 `CANDIDATE_N` 이다.

────────────────────────────────────────────────────────────────────────────
무엇을 재나 — 요구 하나를 네 칸 중 하나에 넣는다
────────────────────────────────────────────────────────────────────────────
    A  이미 top-5 안        리랭커가 할 일이 없다
    A+ 인용 확장으로 들어옴  리랭커가 아니어도 이미 근거에 실린다 (RAG-040)
    B  후보 안 · 근거 밖    ★ **리랭커의 사정거리** — 이 수가 게이트 ①의 답이다
    C  후보 밖 (>N)         리랭커가 못 본다. N 확대가 답이다
    D  코퍼스에 없다        분모에서 뺀다 (RAG-022 ③ 과 같은 이유)

⚠ **`A` 와 `A+` 를 가르는 이유가 있다.** `search()` 가 돌려주는 것은 top-5 가 아니라
**top-5 + 인용 확장**이다 — 확장 히트는 `cited_by` 를 들고 온다(`search.Hit`). 둘을 뭉뚱그리면
리랭커가 안 해도 되는 일을 그 몫으로 세게 된다. 다만 **둘 다 리랭커의 사정거리 밖**이다:
확장은 top-k 가 가리키는 조문을 한 번 더 가져오는 것이라 순위를 바꿔도 이미 실린다.

**요구(`must` 의 항목 하나) 단위로 센다** — `RAG-055` 가 정한 채점 단위다. 한 요구 안은
OR 이므로 대안 중 **가장 좋은 순위**를 그 요구의 순위로 본다.

────────────────────────────────────────────────────────────────────────────
서빙과 같은 경로로 잰다
────────────────────────────────────────────────────────────────────────────
`search.encode()` 를 그대로 부른다 — 어휘 확장(RAG-066)이 그 안에 있다. 필터도 `search()` 가
거는 것과 같게 건다(교통 배제 · 지역 `org` · 부칙 포함). ⚠ **거기서 갈리면 잰 것이 서빙이
아니게 된다.** 아래 `_filters()` 는 `search.search()` 의 그 부분을 그대로 옮긴 것이고,
그 함수가 바뀌면 여기도 같이 바뀌어야 한다.

⚠ **전수 스캔 위에서 잰다.** 지금 dense 는 인덱스가 없어 정확한 최근접이고, 그것이 리랭커의
바닥선이다 (`roadmap.md` §4 의 1번). `D16`(HNSW)이 켜지면 이 수는 다시 재야 한다.

⚠ **읽기만 한다.** DB 는 조회만 하고 parquet·코퍼스는 안 건드린다.

사용: cd backend && uv run --no-sync python tools/d2_candidate_probe.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daengs_life.rag.core import config, region, transport
from daengs_life.rag.stages import embed, goldenset, load, search

# 후보 수를 넓히면 몇 개가 더 들어오는지 — "리랭커냐 N 확대냐"의 저울이다.
# 100 이 현재 값(`search.CANDIDATE_N`).
N_SWEEP = (100, 200, 300, 500, 1000)

# 전 코퍼스에 대한 dense·렉시컬 순위. `search._SQL` 과 달리 **LIMIT 이 없다** —
# 후보 밖의 순위(153위 같은 것)를 알아야 하는 것이 이 스크립트의 존재 이유다.
_RANK_SQL = """
WITH dense AS (
    SELECT id, metadata->>'chunk_id' AS cid,
           row_number() OVER (ORDER BY embedding <=> %(q)s) AS rn
    FROM documents
    WHERE embedding IS NOT NULL
      {filters}
), lex AS (
    SELECT d.id, d.metadata->>'chunk_id' AS cid,
           row_number() OVER (ORDER BY ts_rank(d.content_tsv, q.q) DESC, d.id) AS rn
    FROM documents d, to_tsquery('simple', NULLIF(%(tsq)s, '')) AS q(q)
    WHERE d.content_tsv @@ q.q
      {lex_filters}
)
SELECT COALESCE(dn.cid, lx.cid) AS cid, dn.rn AS drn, lx.rn AS lrn
FROM dense dn FULL OUTER JOIN lex lx ON dn.id = lx.id
"""


def _filters(query: search.Query, conn) -> tuple[str, str, dict[str, Any]]:
    """`search.search()` 가 거는 필터를 그대로. ⚠ 저쪽이 바뀌면 여기도 바뀐다."""
    filters: list[str] = []
    lex_filters: list[str] = []
    params: dict[str, Any] = {"q": query.vector, "tsq": query.tsquery}
    # include_supplementary=True (서빙 기본값) 이라 부칙 필터는 안 건다.
    if excluded := transport.exclusions(query.text):
        filters.append("AND subcategory <> ALL(%(excluded)s)")
        lex_filters.append("AND d.subcategory <> ALL(%(excluded)s)")
        params["excluded"] = list(excluded)
    if kept := region.orgs(query.text, search.known_orgs(conn)):
        filters.append("AND (metadata->>'org' IS NULL OR metadata->>'org' = ANY(%(orgs)s))")
        lex_filters.append(
            "AND (d.metadata->>'org' IS NULL OR d.metadata->>'org' = ANY(%(orgs)s))")
        params["orgs"] = list(kept)
    return "\n      ".join(filters), "\n      ".join(lex_filters), params


def _merge(a: int | None, b: int | None) -> int | None:
    """둘 중 더 앞선 순위. 둘 다 없으면 None."""
    both = [x for x in (a, b) if x is not None]
    return min(both) if both else None


def _ranks(query: search.Query, conn) -> dict[str, tuple[int | None, int | None]]:
    """논리 주소 → (dense 순위, 렉시컬 순위). 같은 주소가 여럿이면 더 좋은 쪽을 남긴다."""
    out: dict[str, tuple[int | None, int | None]] = {}
    f, lf, params = _filters(query, conn)
    with conn.cursor() as cur:
        cur.execute(_RANK_SQL.format(filters=f, lex_filters=lf), params)
        for cid, drn, lrn in cur.fetchall():
            if not cid:
                continue
            key = goldenset.logical(cid)
            prev_d, prev_l = out.get(key, (None, None))
            out[key] = (_merge(prev_d, drn), _merge(prev_l, lrn))
    return out


def _best(group: list[str], ranks: dict[str, tuple[int | None, int | None]]
          ) -> tuple[int | None, int | None, str | None]:
    """요구 하나(OR) 의 최선 순위. 대안 중 **가장 앞선 것**이 그 요구의 자리다."""
    best_d = best_l = None
    who: str | None = None
    for address in group:
        d, lex = ranks.get(address, (None, None))
        pool = _merge(d, lex)
        if pool is None:
            continue
        current = _merge(best_d, best_l)
        if current is None or pool < current:
            best_d, best_l, who = d, lex, address
    return best_d, best_l, who


def _verdict(item, ranks: dict[str, tuple[int | None, int | None]],
             evidence: set[str]) -> str:
    """문항 하나의 판정. **`grounded` 는 `any` 다** (`score.grounded_from_dump`).

    `must` 주소가 **하나만** 근거에 실려도 성립하므로, 요구 단위 집계는 리랭커의 몫을 부풀린다.
    문항 단위로는 셋 중 하나다:

        이미      must 주소가 하나라도 근거에 있다 — 리랭커가 안 해도 grounded 가 선다
        리랭커     하나도 없는데, 가장 좋은 것이 **후보 안**이다  ★ 이 카드가 살릴 수 있는 문항
        N확대      하나도 없고 가장 좋은 것도 후보 밖이다
    """
    if any(a in evidence for a in item.must_flat):
        return "이미"
    best = min((p for a in item.must_flat
                if (p := _merge(*ranks.get(a, (None, None)))) is not None), default=None)
    if best is None:
        return "없음"
    return "리랭커" if best <= search.CANDIDATE_N else "N확대"


def main() -> int:
    gs = goldenset.load()
    items = [i for i in gs.items if i.origin == "hand" and i.expect == "answer"]
    key = config.settings.embedding_model_key
    model = embed.MODELS[key]
    print(f"# 게이트 ① — 후보 안에 답이 있는가   (모델 {key} · CANDIDATE_N={search.CANDIDATE_N})")
    print(f"# 채점 문항 {len(items)} (hand · expect=answer)\n")

    st = embed.load_model(model)
    conn = load.connect()
    buckets: dict[str, list[str]] = {"A": [], "A+": [], "B": [], "C": [], "D": []}
    sweep = dict.fromkeys(N_SWEEP, 0)
    rows: list[tuple[str, str, str, str, str]] = []
    per_item: list[tuple[str, str]] = []
    try:
        for item in items:
            query = search.encode(item.question, model_key=key, st=st)
            ranks = _ranks(query, conn)
            hits = search.search(query, k=search.DEFAULT_K, conn=conn)
            # `cited_by` 가 있으면 인용 확장으로 딸려 온 히트다 (RAG-040 · `search.Hit`).
            top5 = {goldenset.logical(h.chunk_id) for h in hits if h.cited_by is None}
            expanded = {goldenset.logical(h.chunk_id) for h in hits if h.cited_by} - top5
            for gi, group in enumerate(item.must):
                d, lex, who = _best(group, ranks)
                pool = _merge(d, lex)
                label = f"{item.id}#{gi + 1}"
                if any(a in top5 for a in group):
                    bucket = "A"
                elif any(a in expanded for a in group):
                    bucket = "A+"
                elif pool is None:
                    bucket = "D"
                elif pool <= search.CANDIDATE_N:
                    bucket = "B"
                else:
                    bucket = "C"
                buckets[bucket].append(label)
                for n in N_SWEEP:
                    if pool is not None and pool <= n:
                        sweep[n] += 1
                rows.append((label, bucket,
                             "-" if d is None else str(d),
                             "-" if lex is None else str(lex),
                             (who or group[0])[-46:]))
            per_item.append((item.id, _verdict(item, ranks, top5 | expanded)))
    finally:
        conn.close()
        del st
        embed.release()

    print(f"{'요구':<10} {'칸':<3} {'dense':>6} {'lex':>6}  주소")
    for label, bucket, d, lex, who in rows:
        print(f"{label:<10} {bucket:<3} {d:>6} {lex:>6}  {who}")

    total = len(rows)
    scored = total - len(buckets["D"])
    print(f"\n## 요구 {total}개 (분모 {scored} — D 제외)\n")
    print(f"  A  이미 top-5 안        {len(buckets['A']):>3}   리랭커가 할 일 없음")
    print(f"  A+ 인용 확장으로 들어옴  {len(buckets['A+']):>3}   확장이 이미 싣는다 (RAG-040)")
    print(f"  B  후보 안 · 근거 밖    {len(buckets['B']):>3}   ★ 리랭커의 사정거리")
    print(f"  C  후보 밖 (>{search.CANDIDATE_N})       {len(buckets['C']):>3}"
          "   리랭커가 못 봄 — N 확대가 답")
    print(f"  D  코퍼스에 없음        {len(buckets['D']):>3}   분모에서 뺌")
    print(f"\n  A+: {' '.join(buckets['A+']) or '없음'}")
    print(f"  B: {' '.join(buckets['B']) or '없음'}")
    print(f"  C: {' '.join(buckets['C']) or '없음'}")
    print(f"  D: {' '.join(buckets['D']) or '없음'}")
    print(f"\n## 문항 {len(per_item)}개 — `grounded` 는 `any` 라 문항 단위로 다시 센다\n")
    for verdict, note in (("이미", "must 가 이미 근거에 있다 — 리랭커가 안 해도 선다"),
                          ("리랭커", "★ 이 카드가 살릴 수 있는 문항"),
                          ("N확대", "가장 좋은 must 도 후보 밖이다"),
                          ("없음", "코퍼스에 없다")):
        got = [i for i, v in per_item if v == verdict]
        if verdict == "없음" and not got:
            continue
        print(f"  {verdict:<5} {len(got):>3}   {note}")
        if verdict != "이미":
            print(f"        {' '.join(got) or '없음'}")

    print("\n## N 을 넓히면 후보 안에 들어오는 요구 수")
    for n in N_SWEEP:
        mark = "  ← 지금" if n == search.CANDIDATE_N else ""
        print(f"  N={n:<5} {sweep[n]:>3} / {scored}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
