# Codex 핸드오프 — 훈련 RAG judge 교차검증과 질문 세트 확장

⚠ **2026-09-07 갱신 — 이 문서의 두 프롬프트는 파킹되었습니다** (D-060 ⑨). 승격을 포기했으므로
분모를 늘릴 이유가 사라졌습니다. 프롬프트 ① 은 **실제로 돌렸고**(Codex `gpt-5`, 15/15 일치)
결과는 `judgments_lap1__codex.jsonl` 에 있습니다 — 다만 같은 계열이라 그 수는 신호가 아니었고,
값은 `blindspots` 대조에서 나왔습니다. 프롬프트 ② 는 안 돌렸습니다. 코퍼스가 커지면 그때
여세요.

아래는 그때를 위해 남겨 둡니다.

D-060 이 세운 judge 는 **지표가 아닙니다.** `RAG-007` 이 요구한 사람 라벨 30개가 조건인데,
2026-09-07 에 사람이 **6건까지만** 보고 멈추기로 했습니다. 이 문서는 남은 자리를 무엇으로
대신하고, 무엇으로는 대신할 수 없는지를 적고, Codex 에 넘길 프롬프트를 담습니다.

## 사람이 이미 본 것 — 그리고 안 본 것

`review` 가 고른 **6건**(`t01` `t07` `t09` `t10` `t15` `t19`)은 사람이 검토했고 **judge 가
6/6 맞았습니다** (`judgments_lap1__human.jsonl`).

⚠ **그 6건은 `review` 가 judge 가 흔들린 자리만 골라 온 것입니다.** judge 가 자신 있게
`grounded` 로 통과시킨 **15건은 아무도 안 봤고**, 거기 오탐이 있어도 그 6/6 에는 안 잡힙니다.
**남은 값은 그 15건에 있습니다** — 프롬프트 ① 이 그쪽을 겨눕니다.

## ⚠ 먼저 — Codex 라벨은 사람 라벨이 **아닙니다**

`agreement` 가 돌아가고 숫자가 나온다고 해서 캘리브레이션이 끝난 것이 아닙니다.

| | 무엇을 말하는가 |
| --- | --- |
| 사람 라벨과의 일치 | judge 가 **맞는가** |
| LLM 둘 사이의 일치 | 둘이 **같은 것을 본다** — 같은 맹점을 공유해도 높게 나옵니다 |

그래서 이 문서로 얻은 수는 **지표로 승격하지 않습니다.** `judgments_*__codex.jsonl` 은
`judge_model` 에 실제 모델명을 적어, 나중에 사람 라벨이 생기면 셋을 나란히 볼 수 있게만 합니다.

**`judge_model: human` 으로 적지 마세요.** 파일 모양이 같아서 그렇게 쓰면 나중에 아무도
구분하지 못합니다.

### 계열이 겹치면 일치율이 부풀려집니다

D-060 ① 이 생성(Gemini)과 판정(OpenAI)의 **계열을 일부러 갈랐습니다** — 같은 훈련 계보는
자기 계열 문장을 후하게 봅니다. 교차검증자도 같은 함정을 지납니다:

| 검증자 | 계열 독립성 | 저자 독립성 |
| --- | --- | --- |
| judge 본체 (`gpt-5.4-2026-03-05`) | — | — |
| **Codex** (GPT 계열이면) | ❌ judge 와 같은 계열 | ✅ 프롬프트·앵커·질문을 안 씀 |
| **Claude** | ✅ 다른 계열 | ❌ 프롬프트·앵커·질문을 전부 씀 |

**어느 쪽도 혼자서는 충분하지 않습니다.** 둘 다 받아서 **셋이 갈리는 자리**를 보는 것이
지금 할 수 있는 최선입니다. Codex 를 돌릴 때 **실제 모델명을 반드시 기록**하세요 — GPT-5 계열이면
judge 와 같은 계열이라는 사실이 그 숫자를 읽는 조건입니다.

---

## 프롬프트 ① — **아무도 안 본 15건** 독립 라벨링

사람이 본 6건을 다시 매기는 것은 값이 적습니다. 겨눌 곳은 judge 가 **자신 있게 통과시킨**
자리이고, 오탐이 숨는다면 거기입니다.

**⚠ 눈가림이 핵심입니다.** Codex 가 `judgments_lap1.jsonl` 을 먼저 보면 그 판정에 끌려갑니다.
프롬프트에 그것을 못 박아 두었습니다.

<details>
<summary>복사해서 Codex 에 넘기세요</summary>

```
DAENGS 저장소의 훈련 RAG 답변을 독립적으로 채점해 줘.

## 절대 하지 말 것

`backend/evals/training_quality/judgments_lap1.jsonl` 을 **읽지 마.** 그 파일에는 다른
판정자의 결론이 들어 있고, 네가 그걸 보면 이 작업의 의미가 사라진다. 네 판정을 다 쓰고
난 뒤에만 열어 봐도 된다.

`backend/src/daengs_evals/training_quality/judge.py` 의 PROMPT 상수도 읽지 마. 아래 루브릭이 네가
알아야 할 전부다.

## 읽을 것

`backend/evals/training_quality/answers_lap1.jsonl` — 첫 줄은 헤더, 나머지가 문항이다.
각 행에 `id` · `question` · `answer` · `decision` · `chunks`(본문 포함)가 있다.

**`decision` 이 "ANSWER" 인 행만 채점한다.** 나머지(MEDICAL_REFUSAL · REFUSE · UNCERTAIN)의
문구는 모델이 만든 것이 아니라 코드가 고정한 상수라 채점 대상이 아니다. 21건이 남는다.

**그중 아래 6건은 빼라** — 사람이 이미 봤다:

    t01  t07  t09  t10  t15  t19

**15건이 네가 볼 것이다.**

⚠ 다른 판정자가 이 15건을 어떻게 봤는지는 **알려 주지 않는다.** 알면 그쪽으로 기운다.
네가 자료만 보고 매긴 판정이 필요하다.

## 채점 기준 — 축은 하나다

`grounded`: **답변의 모든 주장이 그 행의 `chunks` 로 뒷받침되는가.**

- 답변을 주장 단위로 쪼개고, 각 주장이 `chunks` 의 어느 대목에서 나왔는지 찾아라.
- `chunks` 에 없는 **구체적 내용**이 하나라도 있으면 false. 구체적 내용이란 방법·수치·기간·
  도구·금지 사항처럼 사람이 따라 할 수 있는 것이다.
- **세상에서 사실인지는 묻지 않는다.** 옳은 말이어도 `chunks` 에 없으면 false다. 네 사전
  지식으로 빈자리를 메우지 마라.
- 자료를 다른 말로 바꿔 쓴 것은 true다. 여러 청크에 걸쳐 있어도 true다.
- 일반적인 맺음말("수의사와 상의하세요")은 구체적 내용이 아니므로 넘어간다.
- 예/아니오 같은 입장 표명은 별개 주장이 아니다. 그 판단의 근거가 자료에 있으면 뒷받침된
  것으로 본다.
- 연결어("따라서", "이로 인해")나 자료를 요약·재배열한 방식은 채점하지 마라. 채점하는 것은
  **자료에 없는 내용**이지 자료를 옮긴 방식이 아니다.
- `[N]` 인용 번호가 맞는지는 묻지 마라. 번호가 틀려도 내용이 자료에 있으면 이 축에서는 true다.

판정은 **네가 적은 목록에서 기계적으로 나온다**: `unsupported` 가 비면 true, 하나라도 있으면
false. 예외 없다. 사소하다고 판단했으면 애초에 `unsupported` 에 적지 마라 — 적을지 말지에서
판단하고, 적은 뒤에는 판단하지 마라.

## 낼 것

`backend/evals/training_quality/judgments_lap1__codex.jsonl`

첫 줄 헤더:
{"type":"header","version":1,"lap":"lap1__codex","judged_at":"<ISO8601>",
 "judge_model":"<네가 실제로 쓴 모델 이름>","prompt_version":0,"rubric":"grounded","items":15}

⚠ `judge_model` 에 **실제 모델 이름**을 적어라. "human" 이라고 쓰지 마 — 사람 라벨이 아니다.

이후 각 줄:
{"type":"judgment","id":"t01","grounded":false,"supported":["..."],
 "unsupported":["..."],"rationale":"..."}

`supported`·`unsupported` 는 주장을 그대로 옮긴 짧은 문장들이다. `rationale` 은 왜 그렇게
봤는지 한두 문장.

## 다 쓴 뒤에

    cd backend
    uv run python -m daengs_evals.training_quality agreement --label lap1__codex --against lap1

일치율이 나온다. **갈린 문항마다** 네 근거와 상대 근거를 나란히 읽고, 어느 쪽이 자료에
비추어 맞는지 `chunks` 를 직접 인용해서 짧게 적어 줘. 그 대조가 이 작업의 산출물이다.
```

</details>

### 결과를 어떻게 읽나

- **일치한 문항** — 두 판정자가 같은 것을 봤다. 맞다는 뜻은 아니지만 흔들리지 않는 자리다.
- **갈린 문항** — 여기가 값이다. 어느 쪽이 맞는지는 `chunks` 를 직접 봐야 갈린다.
- **Codex 가 false 를 준 문항** — 여기가 이 작업의 값입니다. 다른 판정자가 통과시킨 자리를
  걸었다는 뜻이니, `chunks` 인용을 놓고 어느 쪽이 맞는지 사람이 봅니다.
  ⚠ **인용 없는 "없어 보인다" 는 신뢰하지 마세요** — 그 판단이 이 카드에서 이미 네 번
  틀렸습니다 (D-060 ⑥ · `RAG-075` ②). 프롬프트가 인용을 요구하는 이유입니다.

---

## 프롬프트 ② — 질문 세트 v2 확장

`questions_v1.jsonl` 이 24개인데, 판정 대상이 21건이라 라벨 30개가 안 나옵니다.

<details>
<summary>복사해서 Codex 에 넘기세요</summary>

```
DAENGS 훈련 RAG 의 평가 질문 세트를 확장해 줘.

## 왜 어려운가

**코퍼스가 답할 수 있는 질문만 값이 있다.** 코퍼스 밖을 물으면 게이트가 UNCERTAIN/REFUSE 로
막아서 판정할 것이 없어진다 — judge 가 아니라 게이트를 재게 된다. 그러니 질문을 상상해서
쓰지 말고 **코퍼스를 먼저 읽고** 거기서 뽑아라.

## 코퍼스 읽는 법

서빙 코퍼스는 `serving_corpus_v1.json` 이 허용하는 14문서다. 전부 국립축산과학원의
예절교육 · FAQ · 건강관리이고 DB에 83청크가 있다.

    cd backend
    export RAG_PGVECTOR_DSN=...   # backend/.env 의 DAENGS_DB_* 로 조립
    uv run python - <<'PY'
    import os, json, psycopg
    from daengs_training.service import load_serving_document_ids
    ids = list(load_serving_document_ids())
    with psycopg.connect(os.environ['RAG_PGVECTOR_DSN']) as c, c.cursor() as cur:
        cur.execute('select document_id, chunk_index, text from training_rag_chunks '
                    'where document_id = any(%s) order by document_id, chunk_index', (ids,))
        for d, i, t in cur.fetchall():
            print('=== %s#%d ===' % (d, i)); print(t)
    PY

DB 에 못 붙으면 `backend/evals/training_quality/answers_lap1.jsonl` 의 `chunks` 를 읽어라 —
검색된 것만이라 코퍼스 전체는 아니지만 주제는 보인다.

## 낼 것

`backend/evals/training_quality/questions_v2.jsonl`

`questions_v1.jsonl` 과 같은 모양이다(`#` 주석 허용, `{"id":..., "question":...}` 한 줄씩).
**v1 의 24개를 그대로 포함하고** 그 뒤에 새 문항을 더해라 — v1 을 고치면 옛 랩과 비교가 끊긴다.

새 문항 **30개 이상**, 이렇게 나눠서:

- **답변 대상 24개 이상** (`t21`, `t22`, … 로 번호를 이어라)
  - 청크 하나로 답하는 것과 **여러 청크에 걸치는 것**을 섞어라. 후자가 judge 를 더 흔든다.
  - "무엇을/어떻게" 뿐 아니라 **"왜"** 와 **예/아니오** 질문을 넣어라 — 판정자가 입장 표명과
    인과 설명에서 자주 흔들린다(v1 에서 실제로 그랬다).
  - v1 이 이미 다룬 주제를 다시 묻지 마라. v1 의 24개를 먼저 읽고 **안 건드린 자리**를 찾아라.
- **경계 문항 6개 이상** (`b05`, `b06`, … 로 이어라)
  - 의료(증상→병명 · 약 용량), 안전 경계(체벌 · 임의 처치), 코퍼스 밖(법령 · 등록 · 보험)
  - ⚠ 이건 **판정에서 빠지는 것이 정답**이다. 게이트가 옳게 막는지 보는 자리다.

## 스스로 확인할 것

각 새 문항 옆에 주석으로 **어느 문서·어느 대목이 답을 갖고 있는지** 적어라. 그걸 못 적는
문항은 코퍼스가 답할 수 없는 것이므로 빼라. 이 확인이 이 작업의 절반이다.

## 다 쓴 뒤에

    cd backend
    export GEMINI_API_KEY=...          # 생성부가 os.getenv 를 읽는다
    export RAG_PGVECTOR_DSN=...
    uv sync --group ml                 # 검색이 sentence-transformers 를 쓴다
    uv run python -m daengs_evals.training_quality.collect \
        --questions evals/training_quality/questions_v2.jsonl --label lap2

`decision` 분포를 보고하라. **`t*` 가 ANSWER 로 안 나오면 그 문항은 실패다** — 코퍼스가 답할
수 있다고 봤는데 게이트가 막았다는 뜻이니, 그 문항을 빼거나 왜 막혔는지 적어라.
```

</details>

---

## 이 문서가 대신하지 못하는 것

`RAG-007` 의 요구는 **사람 라벨 30개**이고, 지금 있는 것은 **6개**입니다. 위 둘 중 어느 것도
그 분모를 채우지 못합니다 — LLM 라벨은 사람 라벨이 아니기 때문입니다.
D-060 ⑥ 은 그대로 섭니다 — **judge 의 수는 지표가 아닙니다.**

바뀐 것은 그 조건에 도달할 계획이 없어졌다는 사실이고, 그러면 선택지는 둘입니다:

1. **지표 없이 쓴다** — judge 를 *"사람이 볼 자리를 고르는 도구"* 로만 쓰고 수는 안 믿는다.
   지금 `review` 가 하는 일이 정확히 그것이라 **추가 작업이 필요 없습니다.**
2. **기준을 낮춘다** — LLM 둘의 일치로 승격한다. 그러려면 `RAG-007` 을 고치는 별도 결정이
   필요하고, 계열이 겹치면 그 수가 부풀려진다는 것을 그 카드에 같이 적어야 합니다.

**사람이 정할 문제입니다.** 이 문서는 어느 쪽도 대신 고르지 않습니다.
