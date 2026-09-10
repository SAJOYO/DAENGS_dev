# 실행 검토 요약

원본 관측은 변경하지 않았다. 의미 판정은 reviews.jsonl의 개별 사유를 따른다.
API의 not_run과 별도 controlled.xml 검증, direct-answer 변형은 원문 모델 평가와 구분한다.

| ID | 변형 | 반복별 최종 판정 |
| --- | --- | --- |
| PC-E01 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-E02 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-E03 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-E04 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-E05 | production-baseline | 1: fail, 2: fail, 3: fail |
| PC-E06 | production-baseline | 1: fail, 2: fail, 3: fail |
| PC-E07 | production-baseline | 1: fail, 2: fail, 3: fail |
| PC-E08 | production-baseline | 1: fail, 2: fail, 3: fail |
| PC-E09 | production-baseline | 1: fail, 2: fail, 3: fail |
| PC-E10 | production-baseline | 1: fail, 2: fail, 3: fail |
| PC-E11 | production-baseline | 1: fail, 2: fail, 3: fail |
| PC-E12 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-E13 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-E14 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-E15 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-E16 | direct-answer | 1: fail, 2: fail, 3: pass |
| PC-E16 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-N01 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-N02 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-N03 | production-baseline | 1: pass, 2: pass, 3: pass |
| PC-N04 | production-baseline | 1: fail, 2: fail, 3: fail |
| PC-R01 | production-baseline | 1: not_run, 2: not_run, 3: not_run |
| PC-R02 | production-baseline | 1: not_run, 2: not_run, 3: not_run |
| PC-R03 | production-baseline | 1: not_run, 2: not_run, 3: not_run |

모델 호출 87회, 제공자 오류 0회.
모델 호출 지연 중앙값 2304ms (평가 간격 제외).
