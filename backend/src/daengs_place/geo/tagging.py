"""사업장명 → 태그. docs/explorations/hospital-search/name-tagging.md

한국 수의 진료에는 **과목(전문의) 제도가 없다.** 그래서 과목 태그는 자격이 아니라 간판
문구였고, 실측에서도 28,284곳 중 109곳(0.39%)뿐이었다 — 결정 #64 로 어휘에서 뺐다.
남는 것은 영업 형태(24h·night·emergency)와 규모/유형(center·secondary), 그리고 종 배제
(cat_only)다. 공공데이터(이름)만 쓰므로 저장 자유.
"""

import re

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("24h",       re.compile(r"24\s*시|24시간|24H", re.IGNORECASE)),
    ("emergency", re.compile(r"응급")),
    ("night",     re.compile(r"야간|심야")),
    ("center",    re.compile(r"의료센터|메디컬센터|메디컬|센터")),
    ("secondary", re.compile(r"2차|이차")),
    ("cat_only",  re.compile(r"고양이\s*(전문|병원|클리닉)|캣\s*(전문|병원|클리닉)")),
]

def tags_for(name: str) -> list[str]:
    return [tag for tag, rx in RULES if rx.search(name)]


def dog_ok(tags: list[str]) -> bool:
    return "cat_only" not in tags
