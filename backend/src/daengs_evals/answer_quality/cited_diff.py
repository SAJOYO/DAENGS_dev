"""인용 조항 비교 — 판정이 **어느 조항을 꺼내는지** 바꾸는가 (#318).

## 왜 판정기가 아니라 이것인가

#314 의 `branch_selected`("갈래를 골랐나")는 판정기가 매겼고, #319 가 재 보니 변형 A·B 의
일치율이 0.714 · 0.571 로 문턱(0.8) 아래였습니다. 불일치가 **한 방향**이라(B 가 A 보다 낮게
준 적 0건) 판정기의 흔들림이 아니라 루브릭 문구가 두 문턱을 인코딩한 것이었고, 이분화로도
안 살아났습니다. 그 항목은 지표에서 빠졌습니다.

여기는 **판정기를 안 거칩니다.** Life 응답의 `results[].data.citations` 는 생성이 실제로 인용한
조항이고(`generate.cited_articles`), 집합 비교는 결정적입니다. 일치율도 앵커도 필요 없습니다.

## 무엇을 답하나

"경과일이 갈래를 가르는가" 는 곧 "다른 조항이 나오는가" 입니다. 같게 나오면 **그것이 답**이고
실패가 아닙니다 — 판정은 검색 질의에 안 들어가므로(#283, B4 와 같은 이유) top-k 가 같고,
프롬프트 블록은 *무엇을 인용할지가 아니라 어떻게 말할지* 를 바꿉니다. 같다는 결과는
`SCREENING_BLOCK` 을 다시 볼 근거가 됩니다.

## 무엇을 안 세는가

**양쪽 다 인용이 없는 문항은 분모에서 뺍니다.** 인용이 없는 `ABSTAINED` · `FAILED` · `REFUSED` 는
답을 안 한 것이지 "같은 조항을 인용한" 것이 아닙니다. 그것까지 "동일" 로 세면 답을 안 할수록
일치율이 올라갑니다 — #314 실측에서 14건 중 5건이 이 자리였습니다.

⚠️ **기준은 상태가 아니라 `data.citations` 의 유무입니다** (#328 · RAG-077). 경계 답변이 조항을
짚고 `[N]` 을 단 채로 `REFUSED` 가 되는 자리가 있고(#318 의 거절 15건 중 14건), RAG-077 뒤로는
그 거절도 `data.citations` 를 싣습니다 — 생성이 실제로 한 인용이므로 비교에 **들어옵니다.**
#318 리포트의 수(양쪽 인용 15~17)는 RAG-077 이전 수집이라 그 문항들이 빠져 있고, 같은 질문을
다시 수집하면 분모가 늘어납니다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: 인용 비교에서 빼는 계층. `multi_intent` 는 한 문장에 요청이 둘이라 `place` 까지 라우팅되고,
#: 그쪽이 실패하면 Life 결과까지 안 남습니다 — #314 에서 2/2, #318 의 v2 질문 넷도 전부
#: place 를 부릅니다(공원 · 유치원 · 훈련사 · 문 연 병원).
#:
#: **문체를 지우지 않고 여기서 빼는 것이 의도입니다.** 지우면 "복합 발화에서 판정이 라우팅을
#: 흔드나" 를 잃습니다 — 그것은 이 지표가 아니라 상태 분포가 답할 질문이고, 답변 파일에 남습니다.
EXCLUDED_STYLES = ("multi_intent",)


def _citations(row: Mapping[str, Any]) -> set[str]:
    """한 응답이 인용한 조항 이름. Life 결과에서만 읽습니다.

    다른 능력의 결과에는 `citations` 가 없거나 뜻이 다릅니다 — Place 의 출처는 조항이 아닙니다.
    """
    labels: set[str] = set()
    for result in row.get("results") or []:
        if result.get("capability") != "life":
            continue
        for citation in (result.get("data") or {}).get("citations") or []:
            label = citation.get("label")
            if isinstance(label, str) and label.strip():
                labels.add(label)
    return labels


def _by_question(
    rows: Sequence[Mapping[str, Any]], *, exclude_styles: Sequence[str]
) -> dict[str, tuple[str, set[str]]]:
    excluded = set(exclude_styles)
    out: dict[str, tuple[str, set[str]]] = {}
    for row in rows:
        stratum = str(row.get("stratum", ""))
        style = stratum.split("__")[-1]
        if style in excluded:
            continue
        out[str(row.get("question_id", ""))] = (str(row.get("status", "")), _citations(row))
    return out


def compare(
    a_rows: Sequence[Mapping[str, Any]],
    b_rows: Sequence[Mapping[str, Any]],
    *,
    exclude_styles: Sequence[str] = EXCLUDED_STYLES,
) -> dict[str, Any]:
    """두 수집의 인용 집합을 문항마다 맞댑니다.

    `comparable` 은 **한쪽이라도 인용이 있는** 문항 수입니다. 양쪽 다 없는 것은 답을 안 한
    자리라 세지 않습니다 (모듈 docstring).
    """
    a, b = (
        _by_question(a_rows, exclude_styles=exclude_styles),
        _by_question(b_rows, exclude_styles=exclude_styles),
    )
    shared = [q for q in a if q in b]
    details: list[dict[str, Any]] = []
    same = different = 0
    for question_id in shared:
        (status_a, cited_a), (status_b, cited_b) = a[question_id], b[question_id]
        if not cited_a and not cited_b:
            continue
        identical = cited_a == cited_b
        same += identical
        different += not identical
        details.append(
            {
                "question_id": question_id,
                "identical": identical,
                "a_status": status_a,
                "b_status": status_b,
                "a_count": len(cited_a),
                "b_count": len(cited_b),
                "shared": len(cited_a & cited_b),
                "a_only": sorted(cited_a - cited_b),
                "b_only": sorted(cited_b - cited_a),
            }
        )
    comparable = same + different
    return {
        "questions": len(shared),
        "excluded_styles": list(exclude_styles),
        "comparable": comparable,
        "identical": same,
        "different": different,
        # 분모가 0 이면 비율을 0 으로 내리지 않습니다 — "다 달랐다" 로 읽힙니다.
        "identical_rate": round(same / comparable, 3) if comparable else None,
        "details": details,
    }


__all__ = ["EXCLUDED_STYLES", "compare"]
