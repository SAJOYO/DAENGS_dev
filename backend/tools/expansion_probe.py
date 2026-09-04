"""인용 확장 프로브 (D5c · RAG-058).

**랩을 뜨기 전에 "확장이 실제로 무엇을 더 데려오는가"만 센다.** Gemini 를 안 부르므로 몇 초다.
이득이 0이면 랩을 뜰 이유가 없다 — #208 이 상수가 안 바뀌어 랩을 접은 것과 같은 판단이다.

`--baseline` 은 **바꾸기 전 규칙**으로 같은 것을 잰다: 조 단위로만 읽고 맨몸 `법` 을 안 푼다.
한 번의 검색 결과 위에서 두 규칙을 나란히 돌리므로 **DB·모델을 한 번만** 쓴다.

    uv run --no-sync python -m tools.expansion_probe
    uv run --no-sync python -m tools.expansion_probe --ids Q3 B1 DP1
"""
from __future__ import annotations

import argparse
import re
import sys

from daengs_life.rag.core import config
from daengs_life.rag.stages import embed, goldenset, load as loader, search as searcher

# 바꾸기 전 규칙 그대로 — 조까지만 읽고, 맨몸 `법` 은 법령명으로 그냥 내보낸다.
_OLD_ARTICLE_RE = re.compile(r"제\d+조(?:의\d+)?")


def old_refs(text: str) -> list[tuple[str, str]]:
    out = []
    for m in _OLD_ARTICLE_RE.finditer(text):
        window = text[max(0, m.start() - searcher._LOOKBACK):m.start()]
        tail = searcher._LAW_TAIL_RE.search(window)
        if not tail:
            continue
        law = " ".join(tail.group(1).split())
        if searcher._LAW_OK_RE.search(law):
            out.append((law, m.group()))
    return out


def old_cited_refs(hits) -> list[tuple[str, str]]:
    have = {(h.document_title, h.section) for h in hits}
    seen: dict[tuple[str, str], None] = {}
    for h in hits:
        for ref in old_refs(f"{h.citation}\n{h.content}"):
            if ref not in have:
                seen.setdefault(ref, None)
    return list(seen)


def resolve(conn, refs, *, fallback: bool) -> dict[tuple[str, str], list[str]]:
    """참조 → 실제로 잡히는 `chunk_id` 들. 폴백 여부로 옛 규칙/새 규칙을 가른다."""
    out: dict[tuple[str, str], list[str]] = {}
    with conn.cursor() as cur:
        for law, sec in refs:
            wanted = [sec] if not fallback else sorted({sec, searcher.article_only(sec)})
            cur.execute(
                "SELECT metadata->>'chunk_id', section FROM documents "
                "WHERE document_title = %s AND section = ANY(%s)",
                (law, wanted))
            rows = cur.fetchall()
            # 정확 일치가 있으면 그것만 (`DISTINCT ON` 이 SQL 에서 하는 것과 같은 우선순위)
            exact = [cid for cid, s in rows if s == sec]
            out[(law, sec)] = exact or [cid for cid, _ in rows]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.expansion_probe")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--model")
    args = ap.parse_args(argv)

    gs = goldenset.load()
    items = [i for i in gs.items if i.origin == "hand"]
    if args.ids:
        items = [i for i in items if i.id in set(args.ids)]

    key = args.model or config.settings.embedding_model_key
    model = embed.MODELS[key]
    st = embed.load_model(model)
    try:
        qs = {i.id: searcher.make_query(i.question, embed.encode_query(model, i.question, st=st))
              for i in items}
    finally:
        del st
        embed.release()

    tot_old = tot_new = tot_must = 0
    changed: list[str] = []
    with loader.connect() as conn:
        for i in items:
            # 서빙과 같은 깊이로 훑되, 확장분은 빼고 원래 히트만 본다
            scanned = [h for h in searcher.search(qs[i.id], k=searcher.EXPAND_SCAN_N, conn=conn)
                       if h.cited_by is None]
            top5 = scanned[:searcher.DEFAULT_K]
            in_top5 = {goldenset.logical(h.chunk_id) for h in top5}
            must = set(i.must_flat)

            old_r = old_cited_refs(scanned)[:searcher.MAX_EXPANDED]
            new_r = searcher.cited_refs(scanned)[:searcher.MAX_EXPANDED]
            old_hit = resolve(conn, old_r, fallback=False)
            new_hit = resolve(conn, new_r, fallback=True)

            def pulled(table):
                out = []
                for cids in table.values():
                    for cid in cids:
                        lg = goldenset.logical(cid)
                        if lg not in in_top5 and lg not in out:
                            out.append(lg)
                return out[:searcher.MAX_EXPANDED]

            po, pn = pulled(old_hit), pulled(new_hit)
            gained_must = [c for c in pn if c in must and c not in po]
            tot_old += len(po)
            tot_new += len(pn)
            tot_must += len(gained_must)
            if po != pn:
                changed.append(i.id)
                print(f"\n[{i.id}] {i.question}")
                print(f"   참조  옛 {len(old_r)} → 새 {len(new_r)}")
                for ref in new_r:
                    mark = "새로" if ref not in old_r else "  〃"
                    got = new_hit.get(ref) or []
                    print(f"      {mark} {ref[0]} {ref[1]} -> "
                          + (", ".join(goldenset.logical(g) for g in got) or "청크 없음"))
                print(f"   확장  옛 {po}\n         새 {pn}"
                      + (f"\n   ★ must 획득: {gained_must}" if gained_must else ""))

    print(f"\n{'='*70}\n문항 {len(items)} · 바뀐 문항 {len(changed)}: {changed}")
    print(f"확장 청크 합계  옛 {tot_old} → 새 {tot_new}   ·   새로 들어온 must {tot_must}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
