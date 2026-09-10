# 실행 검토 요약

원본 관측은 변경하지 않았다. 의미 판정은 reviews.jsonl의 개별 사유를 따른다.
API의 not_run과 별도 controlled.xml 검증, direct-answer 변형은 원문 모델 평가와 구분한다.

| ID | 변형 | 반복별 최종 판정 |
| --- | --- | --- |
| PC-X01 | context-input | 1: pass, 2: pass, 3: pass |
| PC-X01 | current-input | 1: pass, 2: pass, 3: pass |
| PC-X02 | context-input | 1: pass, 2: pass, 3: pass |
| PC-X02 | current-input | 1: pass, 2: pass, 3: pass |
| PC-X03 | context-input | 1: pass, 2: pass, 3: pass |
| PC-X03 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X04 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X04 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X05 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X05 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X06 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X06 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X07 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X07 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X08 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X08 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X09 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X09 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X10 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X10 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X11 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X11 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X12 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X12 | current-input | 1: fail, 2: fail, 3: fail |
| PC-X13 | context-input | 1: fail, 2: fail, 3: fail |
| PC-X13 | current-input | 1: pass, 2: pass, 3: pass |
| PC-X14 | context-input | 1: pass, 2: pass, 3: pass |
| PC-X14 | current-input | 1: fail, 2: fail, 3: fail |

모델 호출 84회, 제공자 오류 0회.
모델 호출 지연 중앙값 2692.0ms (평가 간격 제외).
