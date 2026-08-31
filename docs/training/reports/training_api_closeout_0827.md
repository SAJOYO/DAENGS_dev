# 훈련 RAG 마감 보고서 (2026-08-27)

## 판단

**CONDITIONAL**. 실제 FastAPI 경로와 Gemma 3 4B는 동작하고 안전 게이트의 두 결함은 수정됐지만, 검색 앵커 Hit@4가 47.4%로 그대로이고 answerable gold 계약 문제(`oq0032`)가 남아 있어 무조건 READY로 판정하지 않는다.

## 고정 조건 확인

- `data/eval/queries/training_api_eval_v1.jsonl`은 변경하지 않았다.
- 기존 기준선 `data/scratch/training_api_eval_v1/`와 raw output은 덮어쓰지 않았다.
- 수정 후 결과는 `data/scratch/training_api_eval_v1_postfix2_0827/`에 별도 저장했다.
- 모델·서비스 문서 allow-list·실제 엔드포인트는 각각 `gemma3:4b`, 14개, `POST /chat`로 유지했다.
- 새 모델 설치, 평가셋 확장, 원격 push/배포는 하지 않았다.

## 수정 전후 지표

| 지표 | 수정 전 | 수정 후 | 해석 |
|---|---:|---:|---|
| HTTP 오류 | 0/25 | 0/25 | 회귀 없음 |
| 전체 decision 일치율 | 84.0% (21/25) | **88.0% (22/25)** | +4.0%p |
| Answerable anchor Hit@4 | 47.4% (9/19) | 47.4% (9/19) | 검색 코드는 변경하지 않아 동일 |
| Answerable 생성 비율 | 89.5% (17/19) | 84.2% (16/19) | `oq0032`의 무근거 답변을 UNCERTAIN으로 교정 |
| 평균 지연 | 10.01초 | 39.63초 | 실행 환경 변동. 수정은 검색/생성 호출 수를 늘리지 않음 |
| P95 지연 | 18.29초 | 82.91초 | 같은 날 Ollama에 `qwen3.5:9b`가 별도 로드된 상태였음 |

수정 후의 지연 증가는 코드 변경으로 새 호출이 생긴 결과가 아니다. 후속 운영 승인 전에는 다른 Ollama 모델을 내린 단독 환경에서 warm/cold 지연을 별도로 재측정해야 한다. 이번 실행에서 그 모델을 임의로 중지하지 않았다.

## 라벨별 회귀

| 라벨 | 수정 전 | 수정 후 | 비고 |
|---|---:|---:|---|
| answerable | 17/19 일치 | 16/19 일치 | `oq0032` 1건만 ANSWER→UNCERTAIN |
| partial | 2/2 | 2/2 | 회귀 없음 |
| refuse_boundary | 2/2 | 2/2 | 회귀 없음 |
| missing | 0/2 | **2/2** | `oq0035`, `oq0036` 수정 |

`oq0032`의 gold anchor는 보상 기반 교육 일반론이며 질문의 “몇 초”를 답하지 않는다. 따라서 이 행은 코드 오답이라기보다 동결 평가셋의 answerable 계약을 재검토해야 하는 사례로 기록한다. 평가셋은 지시대로 변경하지 않았다.

## 변경 파일

- `scripts/rag_api.py`: 특정 문장 일치 대신 짧고 무인용인 근거 부족 문장 패턴을 감지한다.
- `data/guardrail/medical_terms_v2.json`: 일반 의학 질의의 `복용량`, `약물` 어휘를 추가했다.
- `tests/test_training_eval_regressions.py`: 동결 missing 행을 로드해 의료 short-circuit와 무근거 UNCERTAIN API 경로를 회귀 고정했다.
- 체크포인트/보고서: `data/scratch/training_api_eval_v1/diagnostic_checkpoint.json`, `reports/training_api_diagnostic_0827.md`, `experiments/local_generation_benchmark/progress.log`.

## 검색 누락 10건

10개 gold anchor는 모두 PGVector에 존재하고 구조 청크와 text/length가 일치한다. 같은 E5 질의를 top-50까지 조회하면 순위 9~47에서 모두 발견되므로, 원인은 gold 부재·청킹/ID 매핑·후보 생성 실패가 아니라 top-4 바깥 랭킹이다. 문항별 순위와 ID는 `reports/training_api_diagnostic_0827.md`에 표로 고정했다.

## 채택·기각한 수정

- **채택**: `복용량`·`약물` 추가. 특정 질문을 겨냥하지 않는 일반 의학 어휘 누락이며 `oq0035`를 `MEDICAL_REFUSAL`로 처리했다.
- **채택**: 인용 없는 근거 부족 paraphrase 감지. `oq0036`을 `UNCERTAIN`으로 처리하고, 인용이 있는 답변은 유지한다.
- **기각/보류**: top_k 증가, score threshold 조정, 질문 ID별 예외, 평가셋 anchor 변경. 검색 숫자만 높이거나 계약을 오염시키므로 수행하지 않았다.
- **보류**: 검색 랭킹/청킹/임베딩 튜닝. 먼저 gold 계약과 14개 문서의 일반 랭킹 문제를 별도 실험으로 다뤄야 한다.

## 검증 및 재개

- 전체 테스트: **372 passed, 8 skipped**
- API health: 정상 (`gemma3:4b`, 14 documents)
- 수정 후 E2E 결과: `data/scratch/training_api_eval_v1_postfix2_0827/summary.json`
- 기존 기준선: `data/scratch/training_api_eval_v1/summary.json`

```powershell
uv run uvicorn scripts.rag_api:app --host 127.0.0.1 --port 8000
uv run python scripts/evaluate_rag_api.py `
  --input data/eval/queries/training_api_eval_v1.jsonl `
  --out-dir data/scratch/training_api_eval_v1_next
```

다음 작업은 (1) 단독 Ollama 환경에서 지연 재측정, (2) `oq0032`를 포함한 gold 의미 적합성 사람 검토, (3) top-4 랭킹 개선 실험이다. 현재 엔드포인트는 안전 게이트 기준으로는 조건부 운영 가능하지만, 검색 품질까지 포함한 최종 READY는 아니다.

## 환경 재측정: Gemma cold/warm (2026-08-27)

요청에 따라 코드·평가셋·설정은 변경하지 않고, 같은 동결 25건을 새 디렉터리에서 두 번 실행했다. 최초 `ollama ps`에는 `qwen3.5:4b`가 있었고 `qwen3.5:9b`는 없었다. `ollama stop qwen3.5:4b`와 `ollama stop qwen3.5:9b`를 실행했으며, Gemma-only 상태를 위해 당시 자동 로드된 `exaone3.5:7.8b`도 정상 `ollama stop`으로 내렸다. 이후 `ollama ps`가 빈 상태임을 확인하고 cold를 시작했다.

| 실행 | 오류 | 평균 | 중앙값 | P95 | 첫 요청 |
|---|---:|---:|---:|---:|---:|
| 기존 기준선 | 0/25 | 10.01초 | 미기록 | 미기록 | 미기록 |
| Gemma-only cold | 1/25 (oq0004, HTTP 503) | 60.59초 | 48.88초 | 139.33초 | 24.49초 |
| Gemma-only warm | 0/25 | 34.28초 | 11.48초 | 125.15초 | 12.66초 |

Cold의 성공 요청만 계산하면 평균 55.53초, 중앙값 47.87초, P95 113.86초다. 원문 결과는 `data/scratch/training_api_latency_cold_gemmaonly_0827/`와 `data/scratch/training_api_latency_warm_0827/`, 집계는 `data/scratch/training_api_latency_0827_metrics.json`에 보존했다. cold 평가 스크립트는 기존 코드의 `response=null` 오류행 집계 버그로 종료됐지만 25개 행과 raw output은 모두 저장되어, 여기의 통계는 저장된 행을 직접 계산했다.

warm 종료 시점의 상태는 `ollama ps`: `gemma3:4b`만 로드(2.8GB, 100% GPU, context 8192), RTX 3050 VRAM 4,324/6,144MiB, GPU 2%, 62°C, 시스템 free physical memory 4,011,740KB였다.

### Qwen 원인에 대한 측정 결론

Qwen(처음에는 4B가 로드됐고 9B는 로드되지 않음)과 ExaOne을 모두 unload한 뒤에도 warm 평균은 34.28초로 기존 10.01초보다 3.42배 높았다. 따라서 **이번 측정만으로는 Qwen이 지연의 단독 원인이라고 결론 내릴 수 없다**. cold→warm에서 중앙값은 48.88→11.48초로 크게 줄었지만 P95는 125초로 여전히 높아, 모델 cold/warm 상태와 간헐적 503·호스트/Ollama 스케줄링이 함께 영향을 준 것으로만 기록한다. 다른 모델을 임의로 다시 로드하거나 코드를 수정하지 않았다.

## 검색 랭킹 오프라인 재측정 (2026-08-27)

동결 25건과 기존 dense 구현을 유지한 채 Gemma 없이 65개 eligible 청크를 비교했다. Dense는 Hit@4 47.4%를 재현했으며 BM25 26.3%, RRF 42.1%, top-20 lexical rerank 31.6%, top-50 lexical rerank 42.1%로 모두 개선하지 못했다. RRF는 평균 순위만 14.16→13.74로 좋아졌지만 기존 top-4 정답 `oq0032`를 밀어냈다. 따라서 프로덕션 retriever에는 어떤 rerank도 반영하지 않았다. 전체 표·유형별 지표·10건 점수 gap은 `reports/retrieval_reranking_0827.md`, 원자료는 `data/scratch/retrieval_reranking_0827_v5/results.json`에 있다. 최종 판정은 **`NO_LEXICAL_RERANK_ADOPTION`**으로 범위를 한정하며, cross-encoder 전체 기각은 아니다. API 상태는 기존과 같이 `CONDITIONAL`이다.
