"""검색 순위 스윕 하네스 (D5 · RAG-057).

`stages/search.py` 의 `LEXICAL_WEIGHT` 주석이 지정한 방식 그대로다 — **두 축의 순위를 한 번만
뽑아 두고 파이썬에서 가중 합을 다시 계산한다.** DB 왕복이 문항당 한 번이라 스윕 자체는 몇 초다.

왜 스크립트로 남기는가 — 그 주석이 *"문항이 늘거나 코퍼스 성격이 바뀌면 다시 재야 한다"* 고
못박아 뒀다. 0.3 을 고른 표는 골든셋 12문항 시절이고 지금은 25문항이다. 다음에 또 이 조건이
성립하면(F1 `food` 가 들어올 때가 그렇다) 같은 표를 다시 떠야 하므로, 그때 scratchpad 에서
다시 짜지 않도록 `router_benchmark` 옆에 둔다.

**서빙을 흉내내는 것이 아니라 순위만 잰다.** 인용 확장(RAG-040)과 Gemini 는 여기 없다 —
확장은 top-k 뒤에 덧붙는 것이라 순위 비교를 흐리고, 답변 품질은 랩(`rag generate --questions`)이
잰다. 이 하네스가 답하는 물음은 하나다: **`must` 청크가 top-5 에 오는가.**

    uv run --no-sync python -m tools.search_sweep dump                 # 두 축의 순위를 받아 캐시
    uv run --no-sync python -m tools.search_sweep dump --lex ts_rank_cd
    uv run --no-sync python -m tools.search_sweep sweep                # 가중치 표
    uv run --no-sync python -m tools.search_sweep sweep --grid rrf_k
    uv run --no-sync python -m tools.search_sweep diagnose Q3 S3 B1 DP1

캐시는 `<DAENGS_DATA_DIR>/processed/sweep/axes-<lex>.json` 이다. `dump` 만 DB 와 임베딩
모델이 필요하고 `sweep`·`diagnose` 는 캐시만 읽는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from daengs_life.rag.core import config, transport
from daengs_life.rag.stages import embed, goldenset, load as loader, search as searcher

# 캐시에 받아 둘 후보 수. `search.CANDIDATE_N`(100) 보다 **넉넉히** 받는다 — 순위는 자르기만
# 하면 재현되므로, 100 으로 받아 두면 "후보를 200 으로 늘리면 잡히나"를 다시 물을 수 없다.
DUMP_N = 300

# 렉시컬 축의 랭킹 함수. 키가 캐시 파일 이름이 된다.
#
# `ts_rank` 는 IDF 를 안 봐서 흔한 낱말이 많은 짧은 청크가 쉽게 1위가 된다 (`search.py` 의
# Q5 실측). `ts_rank_cd` 는 cover density — 질의어들이 **얼마나 가까이 뭉쳐 나오는지**를 본다.
# 정규화 플래그는 비트마스크다: 2=길이로 나눔 · 32=`rank/(rank+1)` 로 눌러 줌.
# 정규화 플래그는 **전부 긴 문서를 깎는 방향**이다 (1=1+log(길이) · 2=길이 · 8=고유낱말수 ·
# 16=1+log(고유낱말수) · 32=rank/(rank+1)). 그런데 여기서 고치고 싶은 편향은 반대쪽 —
# 짧은 청크가 쉽게 1위가 되는 것이다. 그래서 이 축은 기대가 아니라 **확인**으로 돈다.
LEX_EXPRS = {
    "ts_rank": "ts_rank(d.content_tsv, q.q)",
    "ts_rank_cd": "ts_rank_cd(d.content_tsv, q.q)",
    "ts_rank_cd|1": "ts_rank_cd(d.content_tsv, q.q, 1)",
    "ts_rank_cd|2": "ts_rank_cd(d.content_tsv, q.q, 2)",
    "ts_rank_cd|16": "ts_rank_cd(d.content_tsv, q.q, 16)",
    "ts_rank_cd|32": "ts_rank_cd(d.content_tsv, q.q, 32)",
    "ts_rank|2": "ts_rank(d.content_tsv, q.q, 2)",
}

# `stages/search.py` 의 두 CTE 를 그대로 옮기되 **섞지 않고** 양쪽 순위를 돌려준다.
# 필터·정렬·동점 처리(`d.id` 2차 키)가 저기와 한 글자도 다르면 이 표는 서빙을 말하지 않는다.
_SQL = """
WITH dense AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(q)s) AS rn
    FROM documents
    WHERE embedding IS NOT NULL
      {filters}
    ORDER BY embedding <=> %(q)s
    LIMIT %(n)s
), lex AS (
    SELECT d.id,
           row_number() OVER (ORDER BY {lexexpr} DESC, d.id) AS rn
    FROM documents d, to_tsquery('simple', NULLIF(%(tsq)s, '')) AS q(q)
    WHERE d.content_tsv @@ q.q
      {lex_filters}
    ORDER BY {lexexpr} DESC, d.id
    LIMIT %(n)s
)
SELECT doc.metadata->>'chunk_id' AS chunk_id,
       1 - (doc.embedding <=> %(q)s) AS score,
       dn.rn AS drn, lx.rn AS lrn
FROM dense dn
FULL OUTER JOIN lex lx ON dn.id = lx.id
JOIN documents doc ON doc.id = COALESCE(dn.id, lx.id)
"""


@dataclass(frozen=True)
class Cand:
    """후보 한 줄 — 두 축에서의 순위. `None` = 그 축의 후보에 없었다."""
    chunk_id: str
    score: float
    dense_rank: int | None
    lexical_rank: int | None


@dataclass(frozen=True)
class Case:
    """문항 하나. `must` 는 **요구별로** 들고 있는다 — 요구 안은 OR 다 (RAG-055)."""
    id: str
    question: str
    must: list[list[str]]
    cands: list[Cand]


def cases_from_goldenset() -> list[tuple[str, str, list[list[str]]]]:
    """스윕에 쓸 문항 — **`must` 가 있는 hand 문항만.**

    랩(`hand_questions()`)은 30문항이지만 그중 다섯(B2·B3 기권 · B4·B5·B6 거절)은 `must` 가
    비어 있어 재현율이 정의되지 않는다. easylaw QA 8문항은 6단계 `evaluate` 몫이라 애초에
    랩에 없다. 그래서 여기 남는 것이 25다.
    """
    gs = goldenset.load()
    return [(i.id, i.question, [list(g) for g in i.must])
            for i in gs.items if i.origin == "hand" and i.expect == "answer"]


def cache_path(lex: str) -> Path:
    """캐시는 `processed/sweep/` 이다 — `answers/`(랩) 옆이고 git 미추적이다.

    커밋하지 않는 이유는 랩 덤프와 같다: 코퍼스가 움직이면 그대로 낡는데, 파일만 보면
    그것을 알 수 없다. 표는 RAG-057 에 남고 캐시는 다시 뜨면 된다.
    """
    if config.PROCESSED_DIR is None:
        raise SystemExit("DAENGS_DATA_DIR 이 없다 — backend/.env 를 확인할 것")
    return config.PROCESSED_DIR / "sweep" / f"axes-{lex.replace('|', '_')}.json"


# ------------------------------------------------------------------ dump (DB + 인코더)

def dump(lexes: list[str], *, model_key: str | None = None, n: int = DUMP_N) -> list[Path]:
    """문항마다 두 축의 후보를 받아 캐시에 쓴다. **여기서만 DB 와 모델을 쓴다.**

    렉시컬 함수를 여러 개 받는 것은 **모델을 한 번만 올리려고**다. dense 축은 함수와 무관하게
    같으므로 벡터는 한 벌이면 되고, 함수마다 달라지는 것은 렉시컬 CTE 뿐이다.
    """
    for lex in lexes:
        if lex not in LEX_EXPRS:
            raise SystemExit(f"모르는 렉시컬 함수: {lex}   가능: {list(LEX_EXPRS)}")
    key = model_key or config.settings.embedding_model_key
    cases = cases_from_goldenset()
    print(f"모델 {key} · 렉시컬 {', '.join(lexes)} · 문항 {len(cases)} · 후보 {n}")

    # 모델을 한 번만 올린다 (`cmd_search` 와 같은 이유 — 문항마다 6.5GB 를 올렸다 내리지 않는다)
    model = embed.MODELS[key]
    st = embed.load_model(model)
    try:
        queries = [(qid, q, must, searcher.make_query(q, embed.encode_query(model, q, st=st)))
                   for qid, q, must in cases]
    finally:
        del st
        embed.release()

    written = []
    with loader.connect() as conn:
        for lex in lexes:
            out = {"lex": lex, "model": key, "n": n, "cases": []}
            sql = _SQL.replace("{lexexpr}", LEX_EXPRS[lex])
            for qid, question, must, query in queries:
                filters, lex_filters = [], []
                params = {"q": query.vector, "n": n, "tsq": query.tsquery}
                # 서빙(`SERVING_SUPPLEMENTARY = True`)과 같게 — 부칙을 빼지 않는다.
                # 교통 배제만 질의에 따라 걸린다 (RAG-052)
                excluded = transport.exclusions(question)
                if excluded:
                    filters.append("AND subcategory <> ALL(%(excluded)s)")
                    lex_filters.append("AND d.subcategory <> ALL(%(excluded)s)")
                    params["excluded"] = list(excluded)
                with conn.cursor() as cur:
                    cur.execute(sql.format(filters="\n      ".join(filters),
                                           lex_filters="\n      ".join(lex_filters)), params)
                    rows = cur.fetchall()
                out["cases"].append({
                    "id": qid, "question": question, "must": must,
                    "excluded": sorted(excluded),
                    "cands": [{"chunk_id": cid, "score": float(sc), "drn": drn, "lrn": lrn}
                              for cid, sc, drn, lrn in rows],
                })
            path = cache_path(lex)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            print(f"  {lex:<14} → {path.name}")
            written.append(path)
    return written


def read_cache(lex: str) -> list[Case]:
    path = cache_path(lex)
    if not path.exists():
        raise SystemExit(f"캐시가 없다: {path}\n   먼저 `dump --lex {lex}` 를 돌릴 것")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Case(id=c["id"], question=c["question"], must=c["must"],
                 cands=[Cand(x["chunk_id"], x["score"], x["drn"], x["lrn"]) for x in c["cands"]])
            for c in raw["cases"]]


# ------------------------------------------------------------------ 융합·채점 (캐시만)

def fuse(cands: list[Cand], *, w: float, rrf_k: int, cand_n: int, k: int) -> list[Cand]:
    """`search._SQL` 의 `fused` 를 파이썬으로 재현한다.

    **`cand_n` 으로 자르는 것이 CTE 의 `LIMIT n` 과 같다** — 순위는 이미 매겨져 있으므로
    자르기만 하면 후보 수를 줄인 것과 결과가 같다. 동점 처리도 SQL 과 같게: `rrf DESC`,
    그다음 코사인 거리 오름차순(= `score` 내림차순).
    """
    scored = []
    for c in cands:
        drn = c.dense_rank if c.dense_rank and c.dense_rank <= cand_n else None
        lrn = c.lexical_rank if c.lexical_rank and c.lexical_rank <= cand_n else None
        if drn is None and lrn is None:
            continue
        rrf = (1.0 / (rrf_k + drn) if drn else 0.0) + (w / (rrf_k + lrn) if lrn else 0.0)
        scored.append((rrf, c.score, c))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [c for _, _, c in scored[:k]]


def met_groups(must: list[list[str]], hits: list[Cand]) -> list[bool]:
    """요구별 충족 여부. 요구 안은 OR 이고, 주소는 **논리 주소**로 대조한다 (RAG-022 ⑥B)."""
    got = {goldenset.logical(h.chunk_id) for h in hits}
    return [any(a in got for a in group) for group in must]


@dataclass(frozen=True)
class Score:
    groups_met: int
    groups_total: int
    items_hit: int          # must 를 하나라도 올린 문항 수 — 12문항 시절 표와 비교하려고 같이 센다
    items_total: int

    def __str__(self) -> str:
        return f"{self.groups_met:3d}/{self.groups_total}  ({self.items_hit}/{self.items_total}문항)"


def score(cases: list[Case], **kw) -> Score:
    gm = gt = ih = 0
    for c in cases:
        met = met_groups(c.must, fuse(c.cands, **kw))
        gm += sum(met)
        gt += len(met)
        ih += 1 if any(met) else 0
    return Score(gm, gt, ih, len(cases))


# ------------------------------------------------------------------ 출력

WEIGHTS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45,
           0.50, 0.60, 0.75, 1.00, 1.50]
RRF_KS = [10, 20, 30, 60, 100, 200]
CAND_NS = [20, 50, 100, 200, 300]


def cmd_sweep(args: argparse.Namespace) -> int:
    lexes = args.lex or ["ts_rank"]
    for lex in lexes:
        cases = read_cache(lex)
        base = dict(rrf_k=args.rrf_k, cand_n=args.cand_n, k=args.k)
        print(f"\n=== {lex} · RRF_K={args.rrf_k} · 후보 {args.cand_n} · top-{args.k}"
              f" · {len(cases)}문항 ===")
        if args.grid == "weight":
            print("  가중치   요구충족")
            for w in WEIGHTS:
                print(f"  {w:5.2f}   {score(cases, w=w, **base)}")
        elif args.grid == "rrf_k":
            print("  RRF_K    요구충족")
            for rk in RRF_KS:
                print(f"  {rk:5d}   {score(cases, w=args.weight, cand_n=args.cand_n, rrf_k=rk, k=args.k)}")
        elif args.grid == "cand_n":
            print("  후보수    요구충족")
            for cn in CAND_NS:
                print(f"  {cn:5d}   {score(cases, w=args.weight, cand_n=cn, rrf_k=args.rrf_k, k=args.k)}")
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    """문항의 `must` 가 **두 축에서 몇 위인지** 찍는다.

    원인을 셋으로 가르는 자리다 — ⓐ 후보 안에 있는데 융합에서 밀린다(가중치로 고칠 수 있다)
    ⓑ 후보 밖이다(`CANDIDATE_N`) ⓒ 두 축 어디에도 없다(청킹·코퍼스 — 재적재라 이 카드 밖).
    """
    for lex in (args.lex or ["ts_rank"]):
        cases = {c.id: c for c in read_cache(lex)}
        print(f"\n=== {lex} ===")
        for qid in (args.ids or sorted(cases)):
            c = cases.get(qid)
            if c is None:
                print(f"[{qid}] 없는 문항")
                continue
            by_logical: dict[str, Cand] = {}
            for cand in c.cands:
                by_logical.setdefault(goldenset.logical(cand.chunk_id), cand)
            top = fuse(c.cands, w=args.weight, rrf_k=args.rrf_k, cand_n=args.cand_n, k=args.k)
            met = met_groups(c.must, top)
            print(f"\n[{qid}] {c.question}")
            print(f"      요구 {sum(met)}/{len(met)} 충족 · top-{args.k}: "
                  + ", ".join(goldenset.logical(h.chunk_id) for h in top))
            for group, ok in zip(c.must, met):
                for address in group:
                    cand = by_logical.get(address)
                    if cand is None:
                        where = "ⓒ 두 축 어디에도 없다"
                    else:
                        d = f"dense {cand.dense_rank}" if cand.dense_rank else "dense —"
                        lx = f"lex {cand.lexical_rank}" if cand.lexical_rank else "lex —"
                        inside = ((cand.dense_rank or 10**9) <= args.cand_n
                                  or (cand.lexical_rank or 10**9) <= args.cand_n)
                        where = f"{d} · {lx}" + ("" if inside else "  ⓑ 후보 밖")
                    print(f"      {'✓' if ok else '✗'} {address:<55} {where}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """**캐시 융합이 실물 `search()` 와 같은 top-k 를 내는가.**

    이 하네스의 값은 전부 여기에 걸려 있다 — 파이썬 재계산이 SQL 과 한 칸이라도 다르면 표가
    서빙을 말하지 않는다. `search()` 는 인용 확장을 뒤에 덧붙이므로(RAG-040) **확장분(`cited_by`)은
    빼고** 비교한다. 확장은 순위가 아니라 순위 뒤에 오는 일이다.
    """
    lex = args.lex or "ts_rank"
    if lex != "ts_rank":
        print(f"⚠ {lex} 는 실물 SQL 이 아직 안 쓰는 함수다 — ts_rank 로만 대조가 성립한다")
    cases = {c.id: c for c in read_cache(lex)}
    key = args.model or config.settings.embedding_model_key
    model = embed.MODELS[key]
    st = embed.load_model(model)
    try:
        queries = {qid: searcher.make_query(q, embed.encode_query(model, q, st=st))
                   for qid, q, _ in cases_from_goldenset()}
    finally:
        del st
        embed.release()

    bad = 0
    with loader.connect() as conn:
        for qid, case in cases.items():
            live = [h.chunk_id for h in searcher.search(queries[qid], k=searcher.DEFAULT_K,
                                                        conn=conn)
                    if h.cited_by is None]
            mine = [c.chunk_id for c in fuse(case.cands, w=searcher.LEXICAL_WEIGHT,
                                             rrf_k=searcher.RRF_K, cand_n=searcher.CANDIDATE_N,
                                             k=searcher.DEFAULT_K)]
            if live != mine:
                bad += 1
                print(f"  [{qid}] 다름\n      실물 {live}\n      캐시 {mine}")
    print(f"\n{len(cases) - bad}/{len(cases)} 문항 일치"
          + ("" if bad else "  — 캐시 융합 = 실물 검색"))
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.search_sweep", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dump", help="두 축의 순위를 받아 캐시에 쓴다 (DB · 모델 필요)")
    d.add_argument("--lex", action="append", choices=list(LEX_EXPRS),
                   help="여러 번 줄 수 있다 — 모델을 한 번만 올린다. 기본은 전부")
    d.add_argument("--model")
    d.add_argument("-n", type=int, default=DUMP_N)
    d.set_defaults(fn=lambda a: (dump(a.lex or list(LEX_EXPRS), model_key=a.model, n=a.n), 0)[1])

    s = sub.add_parser("sweep", help="캐시로 표를 뜬다")
    s.add_argument("--lex", action="append", choices=list(LEX_EXPRS))
    s.add_argument("--grid", default="weight", choices=["weight", "rrf_k", "cand_n"])
    s.add_argument("--weight", type=float, default=searcher.LEXICAL_WEIGHT)
    s.add_argument("--rrf-k", dest="rrf_k", type=int, default=searcher.RRF_K)
    s.add_argument("--cand-n", dest="cand_n", type=int, default=searcher.CANDIDATE_N)
    s.add_argument("-k", type=int, default=searcher.DEFAULT_K)
    s.set_defaults(fn=cmd_sweep)

    g = sub.add_parser("diagnose", help="문항의 must 가 두 축에서 몇 위인지")
    g.add_argument("ids", nargs="*")
    g.add_argument("--lex", action="append", choices=list(LEX_EXPRS))
    g.add_argument("--weight", type=float, default=searcher.LEXICAL_WEIGHT)
    g.add_argument("--rrf-k", dest="rrf_k", type=int, default=searcher.RRF_K)
    g.add_argument("--cand-n", dest="cand_n", type=int, default=searcher.CANDIDATE_N)
    g.add_argument("-k", type=int, default=searcher.DEFAULT_K)
    g.set_defaults(fn=cmd_diagnose)

    v = sub.add_parser("verify", help="캐시 융합 == 실물 search() 인지 대조 (DB · 모델 필요)")
    v.add_argument("--lex", choices=list(LEX_EXPRS))
    v.add_argument("--model")
    v.set_defaults(fn=cmd_verify)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
