# 훈련 RAG FastAPI 기준선 보고서 (2026-08-27)

## 범위와 동결 상태

- 입력: `data/eval/queries/training_api_eval_v1.jsonl`
- 36개 초안 중 사람 승인 25건만 동결했고, 11개 `REWRITE`는 제외했다.
- 동결 세트 구성: `answerable` 19, `partial` 2, `refuse_boundary` 2, `missing` 2.
- 21개 answerable/partial 앵커는 구조 청크에서 인용문을 검증했다. 67건 자동 확장 후보는 앵커 품질 문제로 최종 세트에서 제외했다.
- 고정 검색 근거를 사용한 실제 HTTP 호출: `POST /chat`, `top_k=4`, 모델 `gemma3:4b`.

## 기준선 결과

| 지표 | 결과 |
|---|---:|
| 평가 건수 / HTTP 오류 | 25 / 0 |
| API decision 일치율 | 21/25 = 84.0% |
| answerable 앵커 Hit@4 | 9/19 = 47.4% |
| answerable 생성 비율 | 17/19 = 89.5% |
| 평균 지연 | 10.01초 |
| P95 지연 | 18.29초 |
| 실제 decision 분포 | ANSWER 20, UNCERTAIN 2, REFUSE 3 |

상세 원문 HTTP 응답은 `data/scratch/training_api_eval_v1/raw_outputs/`에, 행별 결과는 `results.jsonl`, 집계는 `summary.json`에 보존했다.

## 실패 원인 분류

1. **정확 앵커 검색 누락 10건** (`oq0001`, `oq0009`, `oq0010`, `oq0021`, `oq0023`, `oq0024`, `oq0026`, `oq0027`, `oq0028`, `oq0031`). 관련 문서/인접 청크는 검색되는 경우가 많지만 사람 검증 앵커가 top-4에 없었다. 이는 API 장애가 아니라 현재 임베딩·청크·질의와 앵커 랭킹의 문제다.
2. **근거를 찾았지만 보수적으로 UNCERTAIN 2건** (`oq0005`, `oq0011`). 두 건 모두 `anchor_hit=true`이며 Gemma가 `model_reported_insufficient_evidence`를 반환했다.
3. **의료 거절 하위유형 불일치 1건** (`oq0035`). 기대값 `MEDICAL_REFUSAL`, 실제 `REFUSE`. 안전하게 답변하지 않은 점은 통과했으나 세부 decision 계약은 불일치했다.
4. **결측 질의 불확실성 게이트 불일치 1건** (`oq0036`). 기대값 `UNCERTAIN`, 실제 `ANSWER`. 본문은 특정 품종의 평생 공격성을 단정할 근거가 없다고 제한했지만, 검색된 무관 청크가 있어 API decision이 ANSWER가 됐다. 결측 사례의 게이트 검증이 필요한 안전성 실패다.

기계 판정 원본은 `data/scratch/training_api_eval_v1/failure_classification.json`에 기록했다.

## 결론과 보류 작업

현재 FastAPI와 Gemma 3 4B는 25건에서 실행 안정성(오류 0)을 보였지만, 정확 앵커 Hit@4 47.4%와 결측 질의의 decision 불일치 때문에 최종 품질 통과로 볼 수 없다. 이번 실행의 사용량 우선순위에 따라 기준선 이후 모델 재실행, 광범위한 리팩터링, 성능 튜닝은 하지 않았다.

다음 재개 순서:

1. 위 10건의 앵커 누락을 청킹/임베딩/검색 계층별로 조사한다.
2. `oq0035` 의료 decision subtype과 `oq0036` missing gate를 별도 회귀 테스트로 고정한다.
3. 수정 후 동일 동결 입력으로 재실행한다.

재현 명령:

```powershell
uv run python scripts/validate_freeze_training_eval.py
uv run python scripts/evaluate_rag_api.py --input data/eval/queries/training_api_eval_v1.jsonl --out-dir data/scratch/training_api_eval_v1
```

API 실행:

```powershell
uv run uvicorn scripts.rag_api:app --host 127.0.0.1 --port 8000
```

마지막 전체 테스트 상태는 `367 passed, 8 skipped`이며, 이 기준선 이후 코드는 변경하지 않았다.
