"""검문소④ 채점 — 지금 지표를 대신할 것, 그리고 소급 재채점 (RAG-029).

────────────────────────────────────────────────────────────────────────────
지금 지표가 어디서 새는가 — 저장된 6랩 실측 (2026-08-28)
────────────────────────────────────────────────────────────────────────────
`generate.cited_articles`/`ungrounded_articles` 는 답변에서 `제N조` 를 정규식으로 뽑아
ⓐ 몇 문항이 하나라도 인용했나 ⓑ 그중 컨텍스트에 없는 것이 있나, 둘을 센다. 두 방향으로
동시에 틀린다:

  분자가 부풀어 오른다   lap2 Q3 — *"자료에 포함되어 있지 않습니다"* 라고 물러서면서
                        무관한 조항(가축전염병 시행령)을 나열해도 '인용'으로 센다
  분자가 빠진다          lap6 S4·S5 — 보조금24는 조 번호가 없다. 금액까지 정확히 답해도
                        `cited=[]` 라 0으로 센다
  문서를 안 본다          lap4 S3 — `제6조` 를 들었는데 정답은 다른 지자체(동래구)의 제8조다.
                        조 번호만 보므로 대전 서구 조례의 제6조로도 통과한다

**새 지표 — "근거로 정답을 인용했나".** 답변이 `[N]` 으로 실제로 지목한 근거 중 골든셋
`must` 를 가리키는 것이 있는가. 문서까지 대조하므로 위 세 문제를 한 번에 잡는다 — Q3 은
나열한 근거의 tier 가 전부 `-` 라 안 걸리고, S4·S5 는 조 번호가 없어도 `[1]` 이 가리키는
근거가 must 면 걸리고, S3 는 대전 서구 조례의 tier 가 `-` 라 안 걸린다.

**뺀 축 둘 — 축을 늘리지 않는다.**

  거부 분리     "물러섰는데 근거인용=True" 인 사례가 6랩 전부에서 0건이었다.
              근거인용이 이미 그 구분을 한다 — 물러선 답변은 must 를 지목하지 않는다
  근거번호 사용률  6랩 전부 `[N]` 사용률이 12/12·7/7 이다. 프롬프트가 요구하는 형식이라
                모델이 항상 지킨다 — 변별력이 0이라 지표 후보에서 뺐다

**기존 두 수(`cited_articles`/`ungrounded_articles`)는 지우지 않는다.** `lap1`~`lap6` 이
그 수로 기록돼 있어서, 지우면 여섯 랩과의 비교가 통째로 끊긴다. 이 모듈은 옆에 더한다.

**이 지표는 골든셋 라벨이 있어야 잰다.** 자유 질의(`--questions` 없이)에는 못 쓴다 — must
라벨이 없어서다. 그리고 **라벨 자체의 문제를 그대로 물려받는다.** Q1 의 top-5 에 정답이
없으면(RAG-022 ④가 이미 지목한 것) 이 지표도 0이다. 그것이 의도다 — 지표를 먼저 고쳐
라벨 문제가 숫자로 드러나게 하고, 라벨은 별도 카드(RAG-022 재검토)로 간다.
"""
from __future__ import annotations

import re
from typing import Any

from .goldenset import logical

# 답변이 지목한 근거. 프롬프트가 `[1]` 처럼 쓰라고 요구한다 (`generate.PROMPT`).
REF_RE = re.compile(r"\[(\d+)\]")


def referenced_indices(text: str) -> list[int]:
    """답변이 `[N]` 으로 지목한 근거 번호. 중복 제거, 오름차순."""
    return sorted({int(n) for n in REF_RE.findall(text)})


def referenced_hits(text: str, hits: list[Any]) -> list[Any]:
    """지목된 근거만 골라낸다. **1-indexed, 범위 밖은 버린다** — 모델이 `[9]` 를
    지어내도(hits 가 5개뿐이면) 죽지 않는다.

    `hits` 는 `search.Hit` 객체 리스트여도, 저장된 랩의 `hits` dict 리스트여도 된다 —
    여기서는 인덱싱만 하고 속성에 접근하지 않는다.
    """
    return [hits[n - 1] for n in referenced_indices(text) if 1 <= n <= len(hits)]


def grounds_the_answer(text: str, hits: list[Any], must: set[str]) -> bool:
    """**라이브용.** `hits` 는 `search.Hit` 객체 리스트다 (`.chunk_id` 속성 접근).

    답변이 지목한 근거 중 골든셋 `must` 를 가리키는 것이 있으면 True.
    """
    return any(logical(h.chunk_id) in must for h in referenced_hits(text, hits))


def grounded_from_dump(row: dict[str, Any]) -> bool:
    """**소급용.** 저장된 랩의 문항 하나(dict). `hits[].tier` 가 적재 시점에 이미
    must/nice/- 로 매겨져 있으므로(`generate.dump_rows` 가 `search.tier_of` 로 계산),
    골든셋을 다시 읽지 않고도 잰다 — `lap1`~`lap6` 처럼 이 모듈이 생기기 전에 저장된
    랩도 그대로 재채점된다.

    `hits` 나 `tier` 가 없는 형식이면(더 옛 덤프) 조용히 False 다 — 없는 것을 있다고
    지어내지 않는다.
    """
    hits = row.get("hits") or []
    return any(h.get("tier") == "must" for h in referenced_hits(row.get("text", ""), hits))


def score_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    """저장된 랩 하나(문항 목록) → 지표 요약. `python -m rag score-laps` 가 랩마다 이걸 부른다."""
    n = len(rows)
    return {
        "n": n,
        "cited": sum(1 for r in rows if r.get("cited")),          # 현행 지표 (참고용으로 남긴다)
        "grounded": sum(1 for r in rows if grounded_from_dump(r)),  # 새 지표
    }
