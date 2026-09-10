# 실행 검토 요약

원본 관측은 변경하지 않았다. 의미 판정은 reviews.jsonl의 개별 사유를 따른다.
API의 not_run과 별도 controlled.xml 검증, direct-answer 변형은 원문 모델 평가와 구분한다.

| ID | 변형 | 반복별 최종 판정 |
| --- | --- | --- |
| PC-E05 | production-policy-v1 | 1: pass |
| PC-E06 | production-policy-v1 | 1: pass |
| PC-E07 | production-policy-v1 | 1: pass |
| PC-E08 | production-policy-v1 | 1: pass |
| PC-E09 | production-policy-v1 | 1: pass |
| PC-E11 | production-policy-v1 | 1: pass |
| PC-E16 | direct-answer | 1: pass |
| PC-E16 | production-policy-v1 | 1: pass |

모델 호출 8회, 제공자 오류 0회.
모델 호출 지연 중앙값 1654.0ms (평가 간격 제외).
