# 시설 Judge 검토 목록

자동 판정은 검토 보조이며 독립 사람 리뷰나 출시 승인 점수가 아닙니다.
코드 검사 실패와 기존 리뷰는 유지합니다. 판단 보류·미측정·호출 오류는 통과가 아닙니다.

Judge: gemini-3-flash-preview. 호출 24회 / 오류 24회.
앵커 0/12. 앵커 미통과 시 실제 턴 판정은 실행하지 않습니다.

| 사례 / 변형 / 반복 / 턴 | 코드 | 별도 리뷰 | 의도 | 범위 | 결과 설명 | 최종 |
| --- | --- | --- | --- | --- | --- | --- |
| FJ-D01/production-exploration-v1/1/1 | pass | review_required | not_run | not_run | not_run | review_required |
| FJ-D03/production-exploration-v1/1/1 | pass | review_required | not_run | not_run | not_run | review_required |
| FJ-D07/production-exploration-v1/1/1 | blocked | review_required | not_run | not_run | not_run | blocked |
| FJ-D07/production-exploration-v1/1/2 | not_run | review_required | not_run | not_run | not_run | not_run |
| FJ-H01/production-exploration-v1/1/1 | blocked | review_required | not_run | not_run | not_run | blocked |
| FJ-H02/production-exploration-v1/1/1 | pass | review_required | not_run | not_run | not_run | review_required |

## 근거

    {"key": {"case_id": "FJ-D01", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "intent_alignment", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D01", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "scope_fit", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D01", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "result_faithfulness", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D03", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "intent_alignment", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D03", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "scope_fit", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D03", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "result_faithfulness", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D07", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "intent_alignment", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D07", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "scope_fit", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D07", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "result_faithfulness", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D07", "variant": "production-exploration-v1", "repetition": 1, "turn": 2}, "axis": "intent_alignment", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D07", "variant": "production-exploration-v1", "repetition": 1, "turn": 2}, "axis": "scope_fit", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-D07", "variant": "production-exploration-v1", "repetition": 1, "turn": 2}, "axis": "result_faithfulness", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-H01", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "intent_alignment", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-H01", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "scope_fit", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-H01", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "result_faithfulness", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-H02", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "intent_alignment", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-H02", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "scope_fit", "status": "not_run", "reason": "not judged", "evidence": []}
    {"key": {"case_id": "FJ-H02", "variant": "production-exploration-v1", "repetition": 1, "turn": 1}, "axis": "result_faithfulness", "status": "not_run", "reason": "not judged", "evidence": []}
