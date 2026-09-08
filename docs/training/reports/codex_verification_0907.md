# Codex verification (2026-09-07) — GPT-5 (Codex)

## Verdict summary

| Task | Item | Verdict | Evidence |
|---|---|---|---|
| 1-A | 83 re-embedded | CORRECT | `backend/src/daengs_training/cli/pgvector_ingest.py:24-35` |
| 1-A | conflict target/update columns | CORRECT | `backend/src/daengs_training/cli/pgvector_ingest.py:41` |
| 1-A | distinct models = 1 | CORRECT | `db/init/05_training_rag.sql:11,17-21`; `backend/src/daengs_training/cli/pgvector_ingest.py:41` |
| 1-A | A vectors do not survive | CORRECT | `backend/src/daengs_training/cli/pgvector_ingest.py:41`; `db/init/05_training_rag.sql:10-22` |
| 1-A | JSON counts work but hides overwrite | CORRECT | `backend/src/daengs_training/cli/pgvector_ingest.py:43-44` |
| 1-B | (a) | CORRECT | `db/init/05_training_rag.sql:21`; `db/migrations/verify_2026-09-01_training_rag_into_vectordb.sql:57-59` |
| 1-B | (b) | CORRECT | `db/init/05_training_rag.sql:11,21`; `backend/src/daengs_training/cli/pgvector_ingest.py:41` |
| 1-B | (c) | CORRECT | `backend/src/daengs_training/cli/pgvector_ingest.py:38-41` |
| 1-B | (d) | CORRECT | `backend/src/daengs_training/cli/pgvector_ingest.py:29-31,41` |
| 1-B | (e) | CORRECT | `backend/src/daengs_training/cli/pgvector_ingest.py:31-35,41-44` |
| 1-B | (f) | CORRECT | `db/migrations/verify_2026-09-01_training_rag_into_vectordb.sql:57-59,95-96,154-158` |
| 2 | 24 collected / 21 judged | CORRECT | `backend/evals/training_quality/answers_lap1.jsonl:1-25`; `backend/evals/training_quality/judgments_lap1.jsonl:1-22` |
| 2 | gate split | CORRECT | `backend/evals/training_quality/answers_lap1.jsonl:2-25` |
| 2 | judge 15 / 6 | CORRECT | `backend/evals/training_quality/judgments_lap1.jsonl:2-22` |
| 2 | human 6, agreement 5/6, t19 | CORRECT | `backend/evals/training_quality/judgments_lap1__human.jsonl:1-7` |
| 2 | Codex 15/15 | CORRECT | `backend/evals/training_quality/judgments_lap1__codex.jsonl:1-16`; `backend/evals/training_quality/judgments_lap1.jsonl:3-7,9,12-15,17-19,21-22` |
| 2 | decomposition 106 / 52 | CORRECT | `backend/evals/training_quality/judgments_lap1__codex.jsonl:2-16`; `backend/evals/training_quality/judgments_lap1.jsonl:3-7,9,12-15,17-19,21-22` |
| 2 | coverage 46 / 13 | CORRECT | `backend/evals/training_quality/answers_lap1.jsonl:2-25` |
| 2 | t19 sentence is verbatim | WRONG | `backend/evals/training_quality/judgments_lap1.jsonl:20`; `backend/evals/training_quality/answers_lap1.jsonl:20` |
| 2 | same semantic claim passed in t03 | CORRECT | `backend/evals/training_quality/answers_lap1.jsonl:4`; `backend/evals/training_quality/judgments_lap1.jsonl:4` |
| 3 | three OpenAI settings match | CORRECT | `backend/src/daengs_life/rag/core/config.py:95-116`; `backend/src/daengs_backend/config.py:161-195` |
| 3 | D-060 ① follows #305 | CORRECT | `docs/decisions.md:3382-3392`; `docs/life/decisions-rag.md:8605-8635` |
| 3 | D-060 ② base dependency/container | CORRECT | `backend/pyproject.toml:10-35`; `docker-compose.yml:208-226` |
| 3 | D-060 ⑧ prompt gap claim | CORRECT | `backend/src/daengs_evals/training_quality/judge.py:97-136`; `docs/decisions.md:3543-3549` |
| 3 | RAG-075 summary | CORRECT | `docs/decisions.md:3463-3474`; `docs/life/decisions-rag.md:8732-8750` |
| 3 | D-060 ⑦ current section | WRONG | `docs/decisions.md:3516-3524`; `backend/evals/training_quality/judgments_lap1__human.jsonl:1-7` |
| 3 | D-060 ⑧ current section | WRONG | `docs/decisions.md:3543-3549`; `backend/evals/training_quality/answers_lap1.jsonl:20` |
| 3 | D-060 ③–⑥, ⑨ vs RAG-074/075 | CORRECT | `docs/decisions.md:3411-3490,3556-3575`; `docs/life/decisions-rag.md:8591-8603,8674-8699,8704-8750,8799-8811` |

## Task 1 — ingest behaviour

### 1-A my independent reading

이 절은 1-B의 추가 근거를 열어 보기 전에 `pgvector_ingest.py`와 초기 스키마만 읽고 작성했다.
질문의 전제대로 입력이 기존 83개와 같은 `chunk_id`를 가진 83개 청크이고 B가 A와 다른
`--embedding-label`이며, 실행 전에 B label 행은 없다고 해석했다.

1. **실제로 다시 임베딩되는 청크는 83개다.** dedup 조회는 요청한 B label과 같은
   `embedding_model`의 `chunk_id`만 가져오고, 그 집합에 든 입력만 제외한다
   (`backend/src/daengs_training/cli/pgvector_ingest.py:24-31`). 기존 83행은 A label이므로
   제외되지 않는다. 남은 모든 행의 텍스트가 `model.encode(...)`로 전달된다
   (`backend/src/daengs_training/cli/pgvector_ingest.py:32-35`).
2. **충돌 대상은 `chunk_id`다.** `ON CONFLICT(chunk_id)` 뒤의 갱신 열은 `text`,
   `token_count`, `metadata`, `embedding_model`, `embedding`, `content_sha256`이다
   (`backend/src/daengs_training/cli/pgvector_ingest.py:41`). `document_id`, `chunk_index`,
   `created_at`은 이 충돌 경로에서 갱신하지 않는다. 스키마에서 `chunk_id`는 PRIMARY KEY이고,
   별도로 `(document_id, chunk_index, embedding_model)` UNIQUE가 있다
   (`db/init/05_training_rag.sql:10-21`).
3. **실행 뒤 `count(DISTINCT embedding_model)`은 1이다.** 같은 파일의 같은 `chunk_id`를
   INSERT하므로 기존 A행의 PK와 충돌하고, 지정된 충돌 경로가 그 행의
   `embedding_model`과 `embedding`을 B 및 B 벡터로 갱신한다
   (`backend/src/daengs_training/cli/pgvector_ingest.py:41`; `db/init/05_training_rag.sql:11,17-18`).
4. **모델 A의 벡터는 이 테이블 어디에도 남지 않는다.** 83개 기존 행 각각이 새 B 벡터로
   갱신되며, 이 스키마에는 이전 벡터를 보관하는 별도 열이나 이력 테이블이 없다
   (`backend/src/daengs_training/cli/pgvector_ingest.py:41`; `db/init/05_training_rag.sql:10-22`).
5. 배치 중 JSON은 최종 배치에서 `{"processed":83,"total":83}`을 출력하고, 마지막 JSON은
   `{"chunks":83,"skipped_existing":0,"model":<실제 --model>,"embedding_label":<B>,"status":"upserted"}`를
   보고한다. 배치 JSON의 분자·분모는 dedup 후 `data` 길이이고, 마지막 `chunks`도 그 길이이며
   `skipped_existing`은 B label 조회 결과의 크기다
   (`backend/src/daengs_training/cli/pgvector_ingest.py:29-35,43-44`). 따라서 83개를 인코딩하고
   UPSERT했다는 수치는 실제 실행과 맞지만, **83개 기존 A행을 B로 덮어썼다는 사실이나
   삽입 0건/갱신 83건이라는 구분은 보고하지 않는다**. `status: "upserted"`만으로는 그 차이가
   드러나지 않는다 (`backend/src/daengs_training/cli/pgvector_ingest.py:41-44`).

### 1-B ruling on the claims

| Claim | Verdict | Evidence and ruling |
|---|---|---|
| (a) 모델별 공존을 허용하려는 UNIQUE 설계 | CORRECT | 스키마가 `(document_id, chunk_index, embedding_model)`을 UNIQUE로 둔다 (`db/init/05_training_rag.sql:21`). 검증 SQL도 모델을 바꿔도 옛 벡터와 공존해야 한다고 명시한다 (`db/migrations/verify_2026-09-01_training_rag_into_vectordb.sql:57-59,95-96`). |
| (b) 같은 청크 파일의 B 실행에서는 PK 충돌 경로가 작동해 공존하지 못함 | CORRECT | `chunk_id`가 PK이고 (`db/init/05_training_rag.sql:11`), statement의 arbiter가 `ON CONFLICT(chunk_id)`다 (`backend/src/daengs_training/cli/pgvector_ingest.py:41`). A행과 B 입력의 `(document_id, chunk_index, embedding_model)`은 모델 값이 달라 UNIQUE 위반이 아니므로 두 제약이 “경쟁”하는 것이 아니다. 이 시나리오에서 위반되는 것은 같은 `chunk_id`의 PK뿐이고, 그 충돌 경로가 기존 행을 갱신한다. |
| (c) ingest가 모델명을 `chunk_id`에 넣지 않음 | CORRECT | INSERT의 `chunk_id` 값은 입력 행의 `r["chunk_id"]`를 그대로 쓰고, label은 별도 `embedding_model` 값으로 전달한다 (`backend/src/daengs_training/cli/pgvector_ingest.py:38-41`). |
| (d) model-aware dedup과 model-unaware UPSERT의 비대칭 | CORRECT | dedup은 `WHERE embedding_model=%s`지만 (`backend/src/daengs_training/cli/pgvector_ingest.py:29-31`), UPSERT 대상은 모델 열이 없는 `chunk_id`뿐이다 (`backend/src/daengs_training/cli/pgvector_ingest.py:41`). |
| (e) 새 모델 실행은 전부 재임베딩하고 옛 벡터를 덮어쓰지만 성공처럼 기록됨 | CORRECT | dedup 후 전 행을 encode하고 (`backend/src/daengs_training/cli/pgvector_ingest.py:31-35`), 충돌 시 모델명과 벡터를 갱신하며 (`backend/src/daengs_training/cli/pgvector_ingest.py:41`), 마지막에는 덮어쓰기 구분 없이 `status: "upserted"`를 출력한다 (`backend/src/daengs_training/cli/pgvector_ingest.py:44`). |
| (f) 검증 SQL 주석은 모델 공존이 schema intent라는 증거 | CORRECT | 열 설명은 “옛 벡터와 공존”을 이유로 들고 (`db/migrations/verify_2026-09-01_training_rag_into_vectordb.sql:57-59`), 제약 설명과 확인용 질의 주석도 모델이 다르면 다른 행이며 여러 모델의 공존이 정상이라고 한다 (`db/migrations/verify_2026-09-01_training_rag_into_vectordb.sql:95-96,154-158`). |

**(b)의 범위:** 질문의 “같은 chunk files, 다른 label”에서는 PK 충돌이 확실하다. 그러나
일반적으로 `(document_id, chunk_index, B)`가 이미 다른 `chunk_id`로 존재하는 비정상/변경 입력은
PK가 아니라 UNIQUE를 위반한다. 이 statement는 `chunk_id`만 충돌 대상으로 지정하므로 그 UNIQUE
충돌을 처리하지 못하고 실패한다 (`db/init/05_training_rag.sql:11,21`;
`backend/src/daengs_training/cli/pgvector_ingest.py:29-31,41`). 이는 질문의 동일 파일 83행 경로는
아니지만, “항상 PK가 먼저”라는 일반화는 할 수 없다는 경계다.

## Task 2 — numbers

집계 방법은 각 JSONL의 1행(header)을 제외하고 나머지 행을 세는 것이었다. claim decomposition은
Codex가 본 15개 ID에 대해 각각 `len(supported) + len(unsupported)`를 합산했고, coverage는
answer 행의 `chunks`에서 고유 `chunk_id`와 `document_id`를 셌다.

| Report claim | Verdict | Evidence |
|---|---|---|
| 질문 24개 수집, 21개 판정 | CORRECT | answers header와 실제 24행은 `items:24` 및 2–25행이고 (`backend/evals/training_quality/answers_lap1.jsonl:1-25`), judgments header와 실제 21행은 `items:21` 및 2–22행이다 (`backend/evals/training_quality/judgments_lap1.jsonl:1-22`). 보고서 주장 위치는 `docs/training/reports/judge_lap1_0907.md:9,20`. |
| Gate: ANSWER 21 / MEDICAL_REFUSAL 1 / REFUSE 1 / UNCERTAIN 1 | CORRECT | answers의 decision을 집계하면 ANSWER는 `t01`–`t20`과 `b02`(2–21,23행), 나머지는 `b01` MEDICAL_REFUSAL(22행), `b03` REFUSE(24행), `b04` UNCERTAIN(25행)이다 (`backend/evals/training_quality/answers_lap1.jsonl:2-25`). 보고서 표는 `docs/training/reports/judge_lap1_0907.md:18-23`. |
| Judge: grounded 15 / not grounded 6 | CORRECT | 21 judgment 행의 `grounded` 집계는 true 15, false 6이며 false ID는 `t01,t07,t09,t10,t15,t19`다 (`backend/evals/training_quality/judgments_lap1.jsonl:2-22`). 보고서 수치는 `docs/training/reports/judge_lap1_0907.md:57`. |
| Human 6건, 5/6 일치, disagreement `t19` | CORRECT | human header가 6건과 t19 재검토를 기록하고 (`backend/evals/training_quality/judgments_lap1__human.jsonl:1`), 2–6행의 다섯 ID는 judge와 같은 false, 7행 `t19`만 human true 대 judge false다 (`backend/evals/training_quality/judgments_lap1__human.jsonl:2-7`; `backend/evals/training_quality/judgments_lap1.jsonl:2,8,10-11,16,20`). 보고서 최신 결론은 `docs/training/reports/judge_lap1_0907.md:128`. |
| Codex 15건, judge와 15/15 일치 | CORRECT | Codex header가 15건이고 2–16행이 모두 true이며 (`backend/evals/training_quality/judgments_lap1__codex.jsonl:1-16`), 같은 ID의 judge 행도 모두 true다 (`backend/evals/training_quality/judgments_lap1.jsonl:3-7,9,12-15,17-19,21-22`). 보고서 주장은 `docs/training/reports/judge_lap1_0907.md:145-150`. |
| 같은 15건의 claim decomposition: judge 106 / Codex 52 | CORRECT | Codex가 본 ID는 Codex JSONL 2–16행이며, 이 ID들에 대해 양쪽 `supported`와 `unsupported` 배열 길이를 합산하면 judge 106, Codex 52다 (`backend/evals/training_quality/judgments_lap1__codex.jsonl:2-16`; `backend/evals/training_quality/judgments_lap1.jsonl:3-7,9,12-15,17-19,21-22`). 보고서 수치는 `docs/training/reports/judge_lap1_0907.md:147-151`. |
| Coverage: 46 chunks / 13 documents | CORRECT | answers 24행의 `chunks[*].chunk_id`와 `document_id`를 고유 집계하면 각각 46과 13이다. 판정 대상 ANSWER 21행만 세도 같은 값이다 (`backend/evals/training_quality/answers_lap1.jsonl:2-25`). |

### `t19` / `t03` central factual claim

| Claim | Verdict | Evidence |
|---|---|---|
| `t19`에서 unsupported로 적은 **그 문장 자체가 축자로** retrieved chunk에 있다 | WRONG | judge의 문장은 `놀이는 정해진 시간에 반려인과 함께한다는 인식을 심어주어야 합니다.`다 (`backend/evals/training_quality/judgments_lap1.jsonl:20`). 공통 retrieved chunk의 실제 문장은 아래처럼 `별도로`, `논다`, `라는`이 더 있고 어미·띄어쓰기도 다르다 (`backend/evals/training_quality/answers_lap1.jsonl:20`). 의미는 사실상 같지만 축자는 아니다. 보고서의 축자 주장은 `docs/training/reports/judge_lap1_0907.md:113-121`에 있다. |
| `t03`에서 같은 의미의 claim을 grounded로 통과시킴 | CORRECT | `t03` 답변은 `놀이는 정해진 시간에 반려인과 함께하는 것으로 인식시켜야 합니다`라고 쓰고 같은 chunk를 포함한다 (`backend/evals/training_quality/answers_lap1.jsonl:4`). judge는 `t03`을 `grounded:true`, `unsupported:[]`로 판정했다 (`backend/evals/training_quality/judgments_lap1.jsonl:4`). |

두 answer 행 모두 같은 `chunk_id`
`5672db41a058772b0985cadd1b9a3222b6c6696f51cf7dfea9727966db4fcf3f`를 포함한다
(`backend/evals/training_quality/answers_lap1.jsonl:4,20`). 그 청크의 실제 passage는 양쪽 모두 다음과 같다.

> 장난감을 가지고 노는 것은 사람이 시간을 정해서 놀자 라는 명령을 내려주고 충분히 놀아준다면 놀고 난 장난감은 바로 치워 주시기 바랍니다.  
> “놀이는 별도로 정해진 시간에 반려인과 함께 논다“라는 인식을 심어 줍니다.

따라서 **자료 지지는 확인되지만 “그 문장이 verbatim”은 확인되지 않고 오히려 반증된다.**

## Task 3 — decision-record consistency

### Required checks

| Check | Verdict | Evidence |
|---|---|---|
| 두 설정 모듈의 세 필드가 이름·단위·기본값에서 정확히 일치 | CORRECT | Life는 `openai_api_key=""`, `openai_judge_model="gpt-5.4-2026-03-05"`, `openai_timeout_s=120.0`(초)다 (`backend/src/daengs_life/rag/core/config.py:95-116`). backend도 같은 이름, 빈 문자열 상당의 SecretStr 기본값, 같은 모델, `120.0`초다 (`backend/src/daengs_backend/config.py:161-195`). backend 설정은 `backend/.env`를 가리키고 (`backend/src/daengs_backend/config.py:8-10,31-35`), Life도 공용 env 탐색을 import해 backend `.env`를 포함한다 (`backend/src/daengs_life/rag/core/config.py:13-16,118-122`; `backend/src/daengs_life/crawler/core/config.py:23-27,48-50`). 타입 표현(`str`/`SecretStr`)은 다르지만 요청한 이름·단위·기본 **값**에는 divergence가 없다. |
| D-060 ①의 “#305를 정확히 따름”: 날짜 핀과 seconds timeout | CORRECT | D-060은 날짜 핀과 `OPENAI_TIMEOUT_S` 초 단위를 명시한다 (`docs/decisions.md:3382-3391`). RAG-074는 같은 날짜 핀 모델을 고르고 `temperature=0` 실측을 기록한다 (`docs/life/decisions-rag.md:8622-8635`). #305 구현 설정은 모델 `gpt-5.4-2026-03-05`, timeout `120.0`초이고 (`backend/src/daengs_life/rag/core/config.py:103-116`), 호출도 `responses.parse`와 pydantic schema, `temperature=0`이다 (`backend/src/daengs_life/rag/stages/judge.py:154-169`). |
| D-060 ②: `openai`가 base dependency이며 serving container에 설치되도록 구성 | CORRECT | `openai>=3.8.0`은 `[project].dependencies` 안에 있다 (`backend/pyproject.toml:1,10-35`). backend 서비스는 그 프로젝트에서 `uv sync --frozen --group ml --group screening`을 실행한 뒤 `uv run --no-sync dev`로 뜬다 (`docker-compose.yml:19-24,208-226`). D-060의 설명은 `docs/decisions.md:3402-3409`. |
| D-060 ⑧: prompt는 world truth를 막지만 answer appropriateness는 막지 않음 | CORRECT | prompt는 `세상에서 사실인지는 묻지 않는다`고 명시하고 (`backend/src/daengs_evals/training_quality/judge.py:97-104`), 나머지 규칙과 결론부 어디에도 “이 질문에 적절한 답인지 묻지 않는다”는 금지가 없다 (`backend/src/daengs_evals/training_quality/judge.py:105-136`). D-060이 지적한 바로 그 차이는 `docs/decisions.md:3543-3549`. |
| D-060의 RAG-075 요약: I4 통과는 judge 실패가 아니라 원래 사람 읽기의 오류 | CORRECT | D-060은 사람도 I4를 “답함”으로 라벨해 틀린 것은 사람의 읽기였다고 요약한다 (`docs/decisions.md:3463-3474`). RAG-075도 I4에서 사람과 judge가 모두 답함이고 (`docs/life/decisions-rag.md:8712-8722`), 정정 대상은 judge나 corpus가 아니라 기존 사람의 읽기라고 명시한다 (`docs/life/decisions-rag.md:8732-8750`). |

### D-060 ①–⑨ section audit

여기서 verdict는 각 절의 **현재 기록 전체**가 원본 카드/코드/이번 원자료와 일관되는지를 뜻한다.

| Section | Verdict | Evidence |
|---|---|---|
| ① | CORRECT | provider/model/call/version 규약 (`docs/decisions.md:3382-3400`)은 RAG-074의 계열 분리·날짜 핀 (`docs/life/decisions-rag.md:8605-8635`) 및 실제 호출 (`backend/src/daengs_life/rag/stages/judge.py:154-169`)과 일치한다. |
| ② | CORRECT | base dependency/serving 포함 설명 (`docs/decisions.md:3402-3409`)이 pyproject와 compose 구성 (`backend/pyproject.toml:10-35`; `docker-compose.yml:208-226`)에 맞다. |
| ③ | CORRECT | 두 패키지가 같은 env 이름을 각자 읽고 키를 명시 전달한다는 설명 (`docs/decisions.md:3411-3419`)은 두 설정 (`backend/src/daengs_life/rag/core/config.py:95-116`; `backend/src/daengs_backend/config.py:161-195`)과 두 client (`backend/src/daengs_life/rag/stages/judge.py:137-151`; `backend/src/daengs_evals/training_quality/judge.py:185-201`)에 맞다. |
| ④ | CORRECT | D-060은 training faithfulness와 Life `answers_question`을 분리한다 (`docs/decisions.md:3425-3449`). RAG-074도 `answers_question`이 질문과 답변만 본다고 규정한다 (`docs/life/decisions-rag.md:8591-8603`), 반면 training prompt는 자료 청크 지지만 묻는다 (`backend/src/daengs_evals/training_quality/judge.py:92-114`). |
| ⑤ | CORRECT | D-060은 ANSWER만 채점한다고 한다 (`docs/decisions.md:3455-3459`). RAG-074도 abstain/refuse 경계 문항을 제외한다 (`docs/life/decisions-rag.md:8674-8678`), training 구현도 `decision != "ANSWER"`를 제외한다고 명시한다 (`backend/src/daengs_evals/training_quality/judge.py:268-270`). |
| ⑥ | CORRECT | Life label 4개/미승격과 I4 사람 읽기 정정 요약 (`docs/decisions.md:3461-3478`)은 RAG-075의 4개, 3/4, 미승격 및 I4 정정 (`docs/life/decisions-rag.md:8704-8726,8732-8750`)과 일치한다. 뒤의 t19 초기 판단은 같은 절 안에서 명시적으로 재정정된다 (`docs/decisions.md:3492-3510`). 단, 그 재정정의 “축자” 표현 오류는 Task 2와 ⑧ 판정에 별도로 반영했다. |
| ⑦ | WRONG | 절 초반은 `t19`가 뒤집혀 5/6이라고 쓰지만 (`docs/decisions.md:3496-3510`), ⑦ 본문은 아직 `judge와 일치 6/6`이라고 쓴다 (`docs/decisions.md:3516-3519`)가 바로 다음 문단에서는 다시 5/6이라고 한다 (`docs/decisions.md:3521-3524`). human 원자료는 t19만 불일치인 5/6이다 (`backend/evals/training_quality/judgments_lap1__human.jsonl:1-7`). RAG-075의 Life 4-label 동결은 별도 corpus/judge 이야기이며 (`docs/life/decisions-rag.md:8799-8811`), 그 카드와의 모순이 아니라 D-060 내부의 stale 수치다. |
| ⑧ | WRONG | prompt gap에 대한 설명 자체는 맞지만 (`docs/decisions.md:3543-3549`; `backend/src/daengs_evals/training_quality/judge.py:97-136`), 절의 전제인 t19 문장이 청크에 `축자로` 있다는 주장은 틀리다. 실제 두 문장은 서로 다르다 (`backend/evals/training_quality/judgments_lap1.jsonl:20`; `backend/evals/training_quality/answers_lap1.jsonl:20`). |
| ⑨ | CORRECT | training judge를 승격하지 않고 선별 도구로 닫는 결정 (`docs/decisions.md:3556-3575`)은 RAG-075의 Life judge 미승격 원칙과 어긋나지 않는다 (`docs/life/decisions-rag.md:8704-8710`). Life는 조건부 동결 (`docs/life/decisions-rag.md:8799-8811`), training은 승격 포기라는 서로 다른 범위를 각각 명시한다. |

## Could not verify

- **실제 운영 DB의 현재 schema와 행 상태:** DB 접속은 하지 않았다. 따라서 운영 DB가
  `db/init/05_training_rag.sql:10-22`와 실제로 같은지, 과거 A 벡터가 다른 테이블/백업에 있는지는
  CANNOT VERIFY다. Task 1 판정은 요청대로 해당 schema와 ingest 코드의 정적 동작에 한정한다.
- **실행 전 B-label 데이터가 정말 0행인지:** 질문의 “83 chunks embedded with model A” 외의 DB 상태는
  주어지지 않았다. B행이 별도로 이미 존재한다면 `existing`과 마지막 JSON 수가 달라질 수 있으므로
  CANNOT VERIFY다 (`backend/src/daengs_training/cli/pgvector_ingest.py:29-31,44`). 1-A의 숫자는 명시한
  전제(83개 A행만 있고 같은 83개 파일을 B로 실행)에 대한 답이다.
- **실행 중 transaction/모델 호출 성공:** DB 제약 외의 연결 실패, 모델 로딩 실패, 차원 불일치 등은
  실행하지 않았으므로 CANNOT VERIFY다. commit은 각 batch 뒤에만 일어난다
  (`backend/src/daengs_training/cli/pgvector_ingest.py:32-43`).
- **현재 실행 중인 serving container의 실제 설치 상태:** pyproject와 compose가 `openai`를 설치하도록
  구성된 것은 검증했지만, live container를 열어 패키지를 조회하지 않았으므로 실제 런타임 상태는
  CANNOT VERIFY다 (`backend/pyproject.toml:10-35`; `docker-compose.yml:208-226`).
- **외부 GitHub PR #305/#311의 본문과 diff:** 지시된 저장소 내 대응 기록인 RAG-074/RAG-075는
  검증했지만 외부 PR 페이지는 열지 않았으므로 PR 원문 자체는 CANNOT VERIFY다
  (`docs/life/decisions-rag.md:8578-8589,8704-8710`).
