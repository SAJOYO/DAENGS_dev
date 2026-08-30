"""지자체 지원사업을 거르는 반려동물 키워드 — `subsidy` 소스들이 **같은 목록**을 쓴다 (RAG-053 ④).

처음엔 `benefit24_services.KEYWORDS` 였다. `seoul-notice-api` 가 같은 필터가 필요해지면서
복사하지 않고 여기로 뺐다 — 복사해 두면 한쪽만 고치는 날 "보조금24에는 잡히는데 고시공고에는
안 잡힌다"가 되고, 그 어긋남을 아무도 알려주지 않는다.

**전부 복합어다.** 단독 `반려` 는 返戾(신청을 되돌려보냄)와 동음이의어라, 행정 문서에서
"신청이 반려된 경우" 문장을 끌고 온다 (RAG-034 실측: C형간염 확진검사비·산모신생아 건강관리 …).
`반려식물` 도 같은 이유로 빠진다.
"""
from __future__ import annotations

KEYWORDS: tuple[str, ...] = (
    "반려동물", "반려견", "반려묘", "동물등록", "중성화", "내장형", "유기동물", "광견병",
)


def matches(text: str) -> list[str]:
    """`text` 에 걸린 키워드 목록. 비어 있으면 안 걸린 것이다.

    무엇이 걸렸는지를 돌려주는 이유는 키워드를 손볼 때 무엇이 사라지는지 보이게 하려는 것이다 —
    `benefit24_services` 가 `matched_by` 로 남기는 것과 같은 판단.
    """
    return [k for k in KEYWORDS if k in text]


__all__ = ["KEYWORDS", "matches"]
