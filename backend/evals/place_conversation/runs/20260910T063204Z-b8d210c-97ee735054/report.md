# 실행 검토 요약

원본 관측은 변경하지 않았다. 의미 판정은 reviews.jsonl의 개별 사유를 따른다.
API의 not_run과 별도 controlled.xml 검증, direct-answer 변형은 원문 모델 평가와 구분한다.

| ID | 변형 | 반복별 최종 판정 |
| --- | --- | --- |
| PC-H01 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-H02 | production-baseline | 1: fail, 2: fail, 3: fail |
| PC-H03 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-H04 | production-baseline | 1: fail, 2: fail, 3: fail |

모델 호출 15회, 제공자 오류 0회.
모델 호출 지연 중앙값 1795ms (평가 간격 제외).
