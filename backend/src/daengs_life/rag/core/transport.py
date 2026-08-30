"""질의의 교통수단 신호 → 검색에서 **배제할** subcategory (RAG-052).

**질의 쪽만 본다.** 문서 쪽에는 `subcategory` 에 `transport-rail`·`transport-air` 가 이미 있고
(RAG-046 ④ — IR 에 속성을 새로 만들지 않는다), 이 모듈은 그 값 사전을 **질의의 낱말**과 잇는
자리다.

────────────────────────────────────────────────────────────────────────────
왜 "남기기"가 아니라 "배제"인가
────────────────────────────────────────────────────────────────────────────
`#75` 가 재 보니 오염은 22문항 중 2개(T3 기차 무게 · T4 기내 무게)였고 KPI 에 닿는 것은 T3
하나였다 — "기차에" 라고 물었는데 항공 청크 둘이 4·5위로 들어와 **조항 번호를 실은
`easylaw-pet-2-2-2#h2-1`·`#h2-3` 을 밀어냈다.** 그런데 그 easylaw 청크는 `subcategory` 가
`pet-life-guide` 다(`category=policy`). "기차 → `transport-rail` 만 남긴다" 로 하면 **고치려던
청크가 통째로 빠진다.** 그래서 규칙은 반대다 — **적힌 수단이 아닌 교통 subcategory 만 뺀다.**
교통이 아닌 소스(법령·해설·조례)는 손대지 않는다.

규칙 셋 (`exclusions`):
    1. 신호가 **하나**일 때만 건다. "기차나 비행기" 처럼 둘이면 둘 다 필요하므로 아무것도 안 뺀다
    2. 신호가 없으면 아무것도 안 뺀다 — "반려동물 데리고 여행" 은 전부를 봐야 한다 (`#75` 메모 ③)
    3. 뺄 수 있는 것은 `SUBCATEGORY` 에 있는 교통 subcategory 뿐이다. 지하철·버스는 아직 그 소스가
       없어 값이 없다 — 그래서 "지하철" 만 적힌 질의는 철도·항공을 **둘 다** 뺀다 (SRT 안내가
       지하철 질의의 1위에 오던 것이 lap10 T1 이다). 소스가 들어오면 이 표에 한 줄이 늘 뿐이다

낱말은 부분 일치다 — `항공사`·`광역철도`·`KTX` 소문자도 잡힌다. 오탐 가능성이 있는 관용구
(`전철을 밟다`)는 반려동물 질의에서 본 적이 없어 막지 않는다. 막을 일이 생기면 여기 적을 것.
"""
from __future__ import annotations

# 수단 → 질의에서 찾을 낱말. 영문은 대소문자를 가리지 않는다
MODES: dict[str, tuple[str, ...]] = {
    "rail":   ("기차", "열차", "ktx", "srt", "철도"),
    "air":    ("비행기", "항공", "기내"),
    "subway": ("지하철", "전철"),
    "bus":    ("버스",),
}

# 수단 → 문서 쪽 `subcategory`. **여기 없는 수단은 뺄 대상이 없다는 뜻이지 신호가 없다는 뜻이 아니다**
SUBCATEGORY: dict[str, str] = {
    "rail": "transport-rail",
    "air":  "transport-air",
}


def modes(text: str) -> frozenset[str]:
    """질의에 적힌 교통수단. 없으면 빈 집합."""
    low = (text or "").lower()
    return frozenset(m for m, words in MODES.items() if any(w in low for w in words))


def exclusions(text: str) -> tuple[str, ...]:
    """검색에서 뺄 `subcategory`. 신호가 정확히 하나일 때만 값이 있다 (위 규칙 셋)."""
    found = modes(text)
    if len(found) != 1:
        return ()
    (mode,) = found
    return tuple(sub for m, sub in SUBCATEGORY.items() if m != mode)
