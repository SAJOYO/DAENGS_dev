# 실행 검토 요약

원본 관측은 변경하지 않았다. 의미 판정은 reviews.jsonl의 개별 사유를 따른다.
API의 not_run과 별도 controlled.xml 검증, direct-answer 변형은 원문 모델 평가와 구분한다.

| ID | 변형 | 반복별 최종 판정 |
| --- | --- | --- |
| EX01 | production-exploration-v1 | 1: blocked |
| EX02 | production-exploration-v1 | 1: blocked |
| EX05 | production-exploration-v1 | 1: pass |
| EX07 | production-exploration-v1 | 1: blocked |
| EX08 | production-exploration-v1 | 1: blocked |
| EX09 | production-exploration-v1 | 1: blocked |
| EX10 | production-exploration-v1 | 1: blocked |

모델 호출 7회, 제공자 오류 6회.
모델 호출 지연 중앙값 744ms (평가 간격 제외).
