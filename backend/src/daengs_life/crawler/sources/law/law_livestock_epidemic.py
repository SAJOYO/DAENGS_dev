"""가축전염병 예방법 / 시행령 / 시행규칙 웹 원문 (law.go.kr).

광견병 예방접종의 **법적 근거**다. 접종 도메인은 "법정 의무(광견병)"와 "권장 스케줄(종합백신 등)"을
반드시 구분해 답해야 하는데(docs/data-sources.md §3), 그 구분의 `law` 쪽 축이 이 법령이다.
시행령·시행규칙까지 받는 이유 — 제2종 가축전염병 지정과 예방접종 명령의 구체 절차가 하위법령에 있다.

정식 법령명은 "가축전염병 예방법"(띄어쓰기 있음). 시드 yaml 과 URL 은 붙여쓴 형태를 쓰지만
law.go.kr 이 둘 다 받아주고, extract() 는 #conTop 에서 정식 명칭을 읽는다.
"""
from __future__ import annotations

from ._lawgokr import LawGoKrSource


class LawLivestockEpidemic(LawGoKrSource):
    id = "law-livestock-epidemic"
    subcategory = "livestock-epidemic-act"

    LAWS = [
        ("가축전염병예방법", "act"),
        ("가축전염병예방법 시행령", "decree"),
        ("가축전염병예방법 시행규칙", "rule"),
    ]
