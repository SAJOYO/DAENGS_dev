"""별칭 후보가 실제로 문항을 살리는지 잰다 (D11 / RAG-066).

────────────────────────────────────────────────────────────────────────────
왜 이 스크립트가 있나 — **사전을 짐작으로 늘리지 않기 위해서다**
────────────────────────────────────────────────────────────────────────────
별칭 사전은 무한히 자라는 종류의 파일이고, **오탐이 조용하다** — 엉뚱한 낱말이 붙은
질의는 예외가 나지 않고 그냥 답이 나빠질 뿐이다. 그래서 규칙을 하나 둔다:

    **정찰이 "이 별칭이 이 문항을 살린다"를 보인 쌍만 사전에 넣는다.**

이 스크립트가 그 증거를 만든다. 쌍을 더하고 싶으면 `CASES` 에 한 줄 넣고 돌린다.

────────────────────────────────────────────────────────────────────────────
무엇을 재나 — 세 변형을 **같은 코퍼스에서** 비교한다
────────────────────────────────────────────────────────────────────────────
    base   지금 그대로
    lex    **벡터는 그대로 두고** tsquery 에만 별칭 토큰을 더한다
    both   별칭을 붙인 문장을 다시 인코딩한다 (벡터도 바뀐다)

`lex` 와 `both` 를 가르는 것이 이 카드의 설계를 정했다. 2026-09-06 실측:

    HS1  아파트 반려견 동의   base 없음 · lex 없음 · both 1위
    FW3  동물 화장장         base 없음 · lex 7위  · both 1위
    DP1  비행기 태우기       base 6위  · lex 3위  · both 2위

**렉시컬만 넓히는 것으로는 `HS1` 이 안 온다.** dense 가 후보를 정하기 때문이다. 그래서 확장은
질의 텍스트에 걸고 **다시 인코딩**해야 하며, 그것이 `transport`·`region`(SQL 필터라 `search()`
안에 사는 것들)과 층이 다른 이유다 — 벡터는 `search()` 에 들어오기 전에 이미 만들어져 있다.

⚠ **읽기만 한다.** DB 는 조회만 하고 parquet·코퍼스는 안 건드린다.

사용: uv run python tools/d11_vocabulary_probe.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daengs_life.rag.core import config, tokenize          # noqa: E402
from daengs_life.rag.stages import embed, search           # noqa: E402
from daengs_life.rag.stages.search import Query            # noqa: E402

# (문항 id, 질문, 목표 chunk_id, 시험할 별칭)
#
# **질문은 골든셋 원문 그대로 쓴다.** 통과하도록 다듬으면 이 정찰이 재려던 것이 사라진다 —
# `roadmap.md` §0 의 목표가 *"사람이 실제로 물을 질문"* 이라, 사람이 "가축"이라고 치지 않는
# 다는 것이 곧 이 문항이다 (FW3 · RAG-059 와 같은 판단).
#
# 별칭이 빈 목록인 줄은 **대조군**이다 — 신호가 없으면 세 변형이 한 글자도 안 달라야 한다.
CASES: list[tuple[str, str, str, list[str]]] = [
    ("HS1", "아파트에서 반려견을 키우려면 관리사무소 동의를 받아야 하나요?",
     "law-drf-api-apartment-mgmt-decree__20260906#제19조②",
     ["공동주택", "가축", "관리주체"]),
    ("FW3", "우리 동네에 동물 화장장이 들어온다는데 그게 가능한가요?",
     "law-drf-api-animal-protection-act__20260827#제72조",
     ["동물장묘시설", "장묘업"]),
    ("DP1", "우리 애 비행기에 태울 수 있나요?",
     "airlines-pet-pages-eastar__20260829#t-1-r13",
     ["반려동물", "항공", "기내", "운송"]),
    # ---- 소유권 이전 (RAG-073 ⑤ⓐ). 사람은 "입양"·"명의 변경", 법은 "소유권을 이전받은".
    #
    # ⚠ 목표 청크의 본문은 `소유권을이전받은` 으로 **띄어쓰기가 붙어 있다**(파싱 산물).
    # 그래서 `lex` 는 이 별칭으로 목표를 직접 못 집는다 — `both` 와 갈리는 자리를 여기서 본다.
    ("OT1", "강아지를 입양받고 명의 변경을 안 하면 어떻게 되나요?",
     "law-drf-api-animal-protection-decree__20260827#별표 4-2-바",
     ["소유권", "이전"]),
    # ---- 대조군: 별칭이 없으므로 세 변형이 같아야 한다
    ("FD1", "강아지한테 초콜릿 줘도 되나요?",
     "nias-pet-dog-food__20260906#h2-2", []),
    ("S3", "부산 동래구는 내장형 동물등록 비용을 지원해 주나요?",
     "ordinance-search-2182010__20260829#제8조", []),
]
K = 8


def rank_of(hits, needle: str) -> str:
    for h in hits:
        if h.chunk_id == needle:
            return f"{h.rank:>2}위  score={h.score:.4f}  dense={h.dense_rank} lex={h.lexical_rank}"
    return "  없음"


def main() -> int:
    key = config.settings.embedding_model_key
    model = embed.MODELS[key]
    print(f"모델 {key}  ·  목표는 top-{K} 안에 들어오는가\n")
    st = embed.load_model(model)
    try:
        for cid, question, target, aliases in CASES:
            expanded = question + (" " + " ".join(aliases) if aliases else "")
            v_base = embed.encode_query(model, question, st=st)
            v_exp = embed.encode_query(model, expanded, st=st) if aliases else v_base

            # ⚠ 세 변형 모두 `text=question` 이다 — **원문을 넘긴다.**
            # `transport.exclusions()` 와 `region.orgs()` 가 `query.text` 를 읽으므로,
            # 넓힌 문장을 거기 넣으면 교통·지역 신호가 오작동한다 (RAG-052 · RAG-063).
            variants = {
                "base": Query(vector=v_base, tsquery=tokenize.tsquery(question), text=question),
                "lex ": Query(vector=v_base, tsquery=tokenize.tsquery(expanded), text=question),
                "both": Query(vector=v_exp, tsquery=tokenize.tsquery(expanded), text=question),
            }

            print(f"=== {cid}  {question}")
            print(f"    목표 {target}")
            print(f"    별칭 {' · '.join(aliases) if aliases else '(대조군 — 없음)'}")
            for name, q in variants.items():
                hits = search.search(q, k=K)
                print(f"    {name}  {rank_of(hits, target)}")
                if name == "base":
                    top = " / ".join(h.citation or h.chunk_id.split("#")[0] for h in hits[:3])
                    print(f"          top3: {top}")
            print()
    finally:
        del st
        embed.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
