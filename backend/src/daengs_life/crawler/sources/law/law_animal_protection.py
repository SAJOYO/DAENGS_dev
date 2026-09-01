"""동물보호법 / 시행령 / 시행규칙 웹 원문 (law.go.kr).

등록 의무(제15조), 안전조치·맹견 관리, 맹견사육허가제, 책임보험, 과태료 — 이 레포 6개 도메인 중
동물등록과 목줄·입마개·맹견의 **법적 근거 원문**이다. easylaw-pet 이 해설(official)이라면 이쪽이 법령(law)이고,
easylaw 의 cites 가 가리키는 대상이 대부분 여기다.

시행규칙에 맹견 5종(도사견, 아메리칸 핏불테리어, 아메리칸 스태퍼드셔 테리어, 스태퍼드셔 불 테리어,
로트와일러)이 있어 셋을 함께 받아야 맹견 질문이 완결된다.

LAW_OC 발급 후에는 DRF API(`law-drf-api`)로 조문 단위 XML 재수집 예정 — 그때 이 소스는 대체되지 않고
남는다. 웹 원문은 사람이 보는 URL 이라 답변에 실을 출처 링크로 그대로 쓸 수 있다 (KPI).
"""
from __future__ import annotations

from ._lawgokr import LawGoKrSource


class LawAnimalProtection(LawGoKrSource):
    id = "law-animal-protection"
    subcategory = "animal-protection-act"

    LAWS = [
        ("동물보호법", "act"),
        ("동물보호법 시행령", "decree"),
        ("동물보호법 시행규칙", "rule"),
    ]
