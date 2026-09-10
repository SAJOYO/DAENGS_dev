# 실행 검토 요약

원본 관측은 변경하지 않았다. 의미 판정은 reviews.jsonl의 개별 사유를 따른다.
API의 not_run과 별도 controlled.xml 검증, direct-answer 변형은 원문 모델 평가와 구분한다.

| ID | 변형 | 반복별 최종 판정 |
| --- | --- | --- |
| EX01 | production-exploration-v1 | 1: pass, 2: pass, 3: pass |
| EX02 | production-exploration-v1 | 1: fail, 2: fail, 3: fail |
| EX03 | production-exploration-v1 | 1: pass, 2: pass, 3: pass |
| EX04 | production-exploration-v1 | 1: pass, 2: pass, 3: pass |
| EX05 | production-exploration-v1 | 1: pass, 2: pass, 3: pass |
| EX06 | production-exploration-v1 | 1: pass, 2: pass, 3: pass |
| EX07 | production-exploration-v1 | 1: fail, 2: fail, 3: fail |
| EX08 | production-exploration-v1 | 1: fail, 2: fail, 3: fail |
| EX09 | production-exploration-v1 | 1: pass, 2: pass, 3: pass |
| EX10 | production-exploration-v1 | 1: pass, 2: pass, 3: pass |

모델 호출 54회, 제공자 오류 0회.
모델 호출 지연 중앙값 2599.0ms (평가 간격 제외).
