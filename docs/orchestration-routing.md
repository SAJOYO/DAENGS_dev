# 오케스트레이터 라우팅 정책

`/assistant/query` 로 들어온 요청이 어느 능력으로 가는지를 정하는 정책 문서입니다.
구조는 [orchestration-architecture.md](orchestration-architecture.md), 계약 모양은
[orchestration-contracts.md](orchestration-contracts.md) 가 원본입니다. 표기는 같습니다
(CONFIRMED / OPEN / PENDING).

## 1. 결정적 라우팅의 경계 (CONFIRMED)

라우팅 경로는 둘입니다: 결정적(deterministic) 경로와 의미(semantic) LLM 경로.
`RoutePlan.router` 가 어느 쪽이 판단했는지를 남깁니다 (contracts §2).

**결정적 라우팅은 기계가 읽는 명시적 신호에만 허용됩니다:**

- `requested_capability` — 클라이언트가 능력을 명시한 경우.
  **라우팅 신호일 뿐, 절대 인가가 아닙니다** (D-036) — 인가는 §5 의 매트릭스가 따로 봅니다.
  PR #196부터 `place`도 이 경로에서만 실행합니다. 위치가 없으면 Place 전용 좌표 CLARIFY를
  만들며, 이 추가는 아래 의미 라우터 schema/prompt를 넓히지 않습니다.
- 구조화된 UI/액션 메타데이터 — 어느 화면·버튼에서 온 요청인지
- 명시적 source/action 식별자
- 의미가 모호하지 않은, 이미 구조화된 컨텍스트

**자연어 키워드 하드 라우팅은 결정적 경로가 아닙니다.** "훈련"·"짖음"·"배변" 같은 단어가
보인다고 Training 으로 직행하는 규칙을 만들지 않습니다. "짖음 때문에 산책을 못 가요"는
Training 인지 Walk 인지 둘 다인지 단어로는 갈리지 않습니다 — 자유 자연어의 의미 판단은
**전부 의미 라우팅 경로(§2)로** 갑니다. 키워드 규칙은 처음엔 잘 맞다가 오답을 조용히
쌓고, 규칙 목록이 늘수록 서로 부딪히는데 그 충돌을 심판할 기준이 없습니다.

## 2. 의미 라우팅 — LLM 폴백

결정적 신호가 없으면(대부분의 자연어 입력) 다음 경로를 탑니다 (D-041, Card 2A PASS —
production 구현은 Card 2B 의 `backend/src/daengs_backend/orchestration/` `semantic.py` ·
`planner.py` · `service.py`):

```text
자연어 원문 + 허용된 라우팅 메타데이터
→ Gemini 의미 능력 선택
→ 결정론적 RoutePlan 조립
→ 기존 Card 1 실행 그래프
```

LLM은 의미만 판단합니다. EXECUTE에서는 `training`·`life`·`walk`, HANDOFF에서는
`skin`·`gait` 중 사용자가 요청한 대상을 복수 선택할 수 있습니다. 자연어 키워드가 아니라
질의의 의미와 부정을 읽고, Skin/Gait를 EXECUTE로 선택하지 않습니다.

최종 `RoutePlan`(`requests[] + handoffs[] + clarify` — contracts 문서 §2)은 결정론적
오케스트레이션 코드가 만듭니다. 이 코드는 Training/Life payload에 사용자 원문을 그대로
넣고, Walk 좌표는 허용된 구조화 컨텍스트에서만 복사합니다. Walk를 선택했는데 좌표가
빠졌으면 누락 키로 CLARIFY를 만들고 배타성을 적용하며, Skin/Gait reason은 각각
`image_upload_required`·`video_upload_required`로 고정합니다. `RoutePlan.router=llm`은 이
의미 선택 경로를 거쳤다는 관측값이지 최종 객체를 LLM이 직접 작성했다는 뜻이 아닙니다.

따라서 의미 모델은 Training/Life payload 문구, 좌표, CLARIFY 객체, handoff reason 또는
도메인 답변을 생성하지 않습니다.

**구조화 출력 실패 정책 (CONFIRMED — O-14):**

1. LLM의 의미 선택 구조화 출력을 스키마 검증합니다.
2. 실패하면 **1회 한정** 재시도합니다 — 정교한 재시도 프레임워크를 만들지 않습니다.
3. 재시도도 실패하면: **아무 능력도 실행하지 않고**, 시스템/라우팅 실패로서 최상위
   응답 **FAILED** 를 냅니다.
4. 잘못된 모델 원출력은 사용자에게 노출하지 않습니다.

**라우터 실패를 CLARIFY 로 위장하지 않습니다.** CLARIFY 는 "사용자가 정보를 덜 준 것",
스키마 실패는 "시스템이 유효한 RoutePlan 을 못 만든 것"입니다 — 둘을 섞으면 시스템
장애가 사용자 탓 질문으로 표시되고, CLARIFY 정밀도 지표(§4)도 오염됩니다.

라우터는 **분류기이지 답변자가 아닙니다.** 라우터가 도메인 답을 직접 생성하는 순간
"도메인 안전·거절은 능력이 소유한다"(architecture §논리 오케스트레이션)가 깨집니다.

### 순수 인사말 — `social_intent` (CONFIRMED — `semantic-router-ko-v4`, PR #163; v5·v6 에서도 그대로)

"고마워"·"안녕하세요"·"잘가"처럼 **요청이 통째로 인사말뿐**이면 예전에는 빈 결정 →
빈 RoutePlan → `실행하거나 안내할 수 있는 기능이 없습니다.`(FAILED)가 났습니다. AI 비서가
인사에 기능 실패로 답하는 것을 막기 위해 프롬프트를 `semantic-router-ko-v3` → **v4** 로
올리고 `SemanticRoutingDecision` 에 `social_intent: greeting | thanks | goodbye | null`
(기본 null) 하나를 더했습니다. 위 "분류기이지 답변자가 아니다" 원칙은 그대로입니다:

- **Gemini 는 분류만 합니다.** 사용자에게 보이는 문장은 `orchestration/social.py` 의
  고정 한국어 템플릿 세 개가 냅니다. 추가 LLM 호출·대화 기억·checkpointer·새 능력은 없습니다.
- **능력 의도가 항상 우선합니다.** "고마워, 근데 오늘 산책 나가도 돼?" 는 Walk(좌표 없으면
  CLARIFY), "안녕, 강아지가 자꾸 손을 물어요" 는 Training, "고마워, 걷는 영상도 봐줘" 는 Gait
  HANDOFF 로 — social_intent 는 null 입니다. 스키마 불변식이 이를 강제합니다:
  `social_intent != null` 이면 `execute`·`handoffs` 가 모두 비어 있어야 하고, 섞인 출력은
  스키마 무효라 기존 O-14 1회 재시도를 탑니다.
- **LangGraph 에 들어가지 않습니다.** `AssistantOrchestrationService` 가 RoutePlan 을 만들기
  전에 `AssistantResponse(status=ANSWERED, results=[], handoffs=[], clarify=null)` 로
  바로 돌려줍니다. 가짜 `assistant` 능력을 만들지 않았고 `requested_capability` 결정적
  경로는 이전과 똑같이 Gemini 를 건너뜁니다.
- **범위는 greeting / thanks / goodbye 세 가지뿐입니다.** 날씨·잡담·일반 지식처럼 지원
  범위 밖인 요청은 social_intent 가 null 이고, 기존 빈 RoutePlan 동작(FAILED + 위 문구)이
  그대로 남습니다. 일반 미지원 UX 는 이 카드가 풀지 않았습니다.
- 회귀 근거: 같은 80건 v3 gold·동결 gate·`gemini-3.1-flash-lite` 로 v4 프롬프트를 1회
  재실행해 PASS (`backend/evals/orchestration_router/summary_v5.json`, 80건 모두
  social_intent null) — `docs/orchestration-router-benchmark.md` §v5.

### 일반 돌봄(사육) 정보 질문 — 분류 공백, 실행 대상 없음 (CONFIRMED — PR #172, 2026-09-03)

실제 사용자 질문 **"푸들 산책은 몇 회가 좋아?"** 가 드러낸 공백입니다. 견종·연령·체격별
산책 횟수, 하루 급여 횟수, 수면 시간, 정상 음수량 같은 **일반 돌봄(husbandry) 정보**는
v1 의 세 EXECUTE 능력 어디에도 의미상 속하지 않습니다:

| 능력 | 왜 아닌가 |
| --- | --- |
| Walk | **지금 이 위치의 환경** 적합성 판정(더위·대기·강수 세 축, `daengs_life.realtime.rules`)이고 개 쪽 입력(견종·나이·체중)이 아예 없습니다. "산책"이라는 단어가 보인다고 Walk 로 보내면 좌표 CLARIFY 라는 **엉뚱한 되물음**이 나갑니다 |
| Training | 행동을 바꾸거나 기술을 가르치는 계획입니다. 서빙 14문서는 전부 행동 FAQ 이고(`serving_corpus_v1.json`, docs/training/reports/training_knowledge_coverage_0903.md), 산책 문서 2건도 줄 당김·달려듦 근거입니다. 보내면 직접성 규칙(`grounded-answer-ko-v3`)이 UNCERTAIN 으로 물러서는 것이 최선입니다 |
| Life | 근거의 **종류**로 경계를 긋는 도메인입니다 — 법령·조례·고시·약관·기관 공식 안내만 (docs/life/roadmap.md §2). `care`·`emergency` 카테고리는 RAG-008 ③ 이 명시적으로 뺐고 `db/init/01_schema.sql` 의 CHECK 가 막습니다. 게다가 `/ask` 는 hit 0건일 때만 404 라 **무관한 조례 5건 위에 OK 답변**이 나갑니다 — 가장 위험한 오라우팅입니다 |

**근거 소스 감사 결론 (2026-09-03, PR #172):** 저장소 어디에도 이 질문들을 인용 가능하게
답할 소스·테이블·규칙·서비스가 **없습니다.** 반려견 프로필(`models/pet.py`)은 breed(자유 문자열) ·
weight_kg · birth_date 를 저장할 뿐 권고 로직이 없고, Training 의 급여 문서 3건은 미서빙·미검수
(DNS)이며, Life 의 `nias-pet-care-basics`("사육에 관한 기본사항") 1건은 수집됐지만 수집기 자체가
범위 밖으로 선언했고 답변 lap 14회 어디에도 등장한 적이 없습니다. 벤치마크 gold 80건에는
"지원 범위 밖" 범주가 없어 이 질문군의 라우팅은 측정된 적이 없습니다.

**결정 — 옵션 C: 가짜 `care` 능력을 만들지 않습니다.** 라우팅은 이미 신뢰할 수 있는 능력을
고르는 것이지 도메인 권위를 만들어내는 것이 아닙니다. 지금 확정된 경계:

- **지원하는 것(변경 없음):** Walk = 지금 환경 적합성 · Training = 행동/기술 변화 · Life =
  제도·절차 근거 · Skin/Gait = HANDOFF · 순수 인사말 = `social_intent`.
- **지원하지 않는 것:** 견종·연령·체격별 **일반 돌봄 정보** — 산책 횟수·시간, 급여 횟수·양,
  수면, 음수량, 그 밖의 사육 상식. 올바른 의미 결정은 **빈 선택**(`execute=[]`, `handoffs=[]`,
  `social_intent=null`)이고, 결정론적 계층은 그것을 그대로 FAILED + "실행하거나 안내할 수 있는
  기능이 없습니다." 로 냅니다 (위 social_intent 절의 "일반 미지원 UX" 와 같은 경로). Gemini
  사전학습 지식으로 답하지 않고, Training/Life/Walk 로 억지 배정하지도 않습니다.
- **기계 강제:** `backend/tests/test_orchestration_care_boundary.py` — 수용 사례 표
  `CARE_BOUNDARY_CASES`(미지원 6 · Walk 2 · Training 1 · Life 3 · 인사 1)로 결정론적 계층
  (planner·engine·aggregate)이 빈 결정을 Walk 로 승격하거나 키워드로 하드 라우팅하지 않음,
  계약에 `care` 류 능력이 없음, 라우터가 그런 이름을 내면 기존 O-14 경로(1회 재시도 → FAILED)를
  탐, 그리고 아래 v5 프롬프트 계약 문구를 고정합니다.

**측정된 v4 결함 (2026-09-03, 유료 호출 2건).** production 모듈 그대로(`semantic-router-ko-v4`,
`gemini-3.1-flash-lite`, temperature 0, 질문당 1회)로 보내자 **두 질문 모두 `execute=["life"]`**
가 나왔습니다 — 스키마 유효, 재시도 없음. v4 의 Life 정의에 있던 "official guidance" 가 사육
상식까지 흡수한 것입니다. 이것이 세 오라우팅 중 가장 위험한 경로인 이유는 위 Life 행 그대로입니다:
`/ask` 는 hit 0건일 때만 404 라 무관한 조례·약관 5건 위에 **status OK 답변**이 사용자에게 나갑니다.

**`semantic-router-ko-v5` (PR #172, 사람 결정 — 같은 카드에서 수정).** 모델(`gemini-3.1-flash-lite`)·
스키마(`ExecuteName` 셋 · `HandoffName` 둘 · `social_intent`)·planner·graph·aggregate 는 그대로이고
프롬프트만 두 곳이 바뀌었습니다:

1. Life 정의를 **공식적(formal) 제도·법률·행정·정책·계약 근거**로 좁혔습니다 — 등록, 기관,
   공식 절차, 자격, 정부·지원 프로그램, 요금, 기한, 법령·규제 요건, 보험 약관, 운송·여행 규정.
   "official guidance" 는 **그런 공식 제도·정책 주제일 때만** Life 입니다.
2. 일반 돌봄 권고(산책·운동 횟수·시간, 급여 횟수·양, 수면, 음수량, 일반 관리 상식, 견종·연령·
   체격별 관리)는 v1 어느 목적지도 지원하지 않는다고 선언했습니다 — 기관·공식 출처·권장을
   언급해도 Life 가 아니고, 산책이라서 Walk 도 아니며, 행동 변화·기술 교육이 아니면 Training 도
   아닙니다. 해당하는 목적지가 없으면 두 목록을 비우고 `social_intent=null`.

**v5 는 돌봄 질문에 답하지 않습니다.** 근거 없는 도메인으로 보내는 것을 막을 뿐이고, 사용자에게는
기존 미지원 문구(FAILED)가 그대로 나갑니다. 그 문구 개선은 별도 UX 카드입니다.

**v5 검증 (2026-09-03):**

- 5건 라이브 프로브(production 모듈, 질문당 1회, 유료 5건): 위 두 질문 + "3개월 강아지는 얼마나
  자야 해?" + "성견은 하루에 밥을 몇 번 줘?" → 전부 빈 결정, "오늘 미세먼지 심한데 산책 나가도
  돼?" → Walk, "반려견 등록은 어디서 해?" → Life. **5/5.**
- 동결 80건 회귀 1회(`runner_v6.py`, 유료 80건, `benchmark_v1.yaml` gate 그대로): **FAIL** —
  `exact_mixed_execute_handoff_match` 0.80 < 0.90. 나머지 14개 gate 는 통과(exact 0.95, 스키마 100%,
  social_intent non-null 0/80). non-exact 4건 중 `boundary_05`·`mixed_09` 는 v4·v5 run 에서도 흔들린
  기존 경계 사례이고, **새로 틀린 것은 `mixed_10`("저녁 산책 시간 추천이랑 …")·`clarify_08`("오늘
  산책 시간하고 …") 둘** — 둘 다 Walk 를 놓쳤습니다. gold 는 "오늘/저녁 산책 시간" 을 **오늘의 환경
  창(window) = Walk** 로 보는데, v5 의 "walk or exercise frequency or duration … is not Walk merely
  because it concerns walking" 문장이 그 "시간" 을 돌봄 상식으로 읽게 만든 것입니다.
  상세: `docs/orchestration-router-benchmark.md` §v6.

**`semantic-router-ko-v6` — v5 의 한 문장 보정 (사람 결정, 같은 카드).** v5 는 일상 돌봄을 Life 에서
올바르게 막았지만, "walk or exercise frequency or duration … not Walk merely because it concerns walking"
문구가 **오늘/저녁 산책 시간 창** 질문의 Walk 까지 눌렀습니다. v6 는 v5 의 Life 제한을 그대로 두고 그
문장만 바꿉니다: **일상적·규범적 운동 권고**(하루 몇 번·몇 분, 견종·연령·체격별 산책량 — 현재 조건과
무관)는 미지원(빈 결정), **지금·오늘·이번 저녁에 걸을지/언제 걸을지**(오늘의 산책 시간 창 고르기 포함)는
날씨·대기질을 명시하지 않아도 Walk. Walk 를 반복 운동 일정으로 넓히지는 않습니다. 모델·스키마·planner·
graph·aggregate·다른 경계 문구는 그대로이고 한국어 키워드 규칙은 없습니다.

**v6 검증 (2026-09-03):**

- 7건 타깃 프로브(production 모듈, 질문당 1회, 유료 7건): "푸들 산책은 몇 회가 좋아?" · "성견은 하루에
  몇 분 정도 산책해야 해?" → 빈 결정, 동결 원문 그대로의 `mixed_10` → Life+Walk+Gait, `clarify_08` →
  Life+Walk(좌표 없음은 planner 의 CLARIFY), `walk_03` → Walk, `clarify_03` → Walk, "반려견 등록은 어디서
  해?" → Life. **7/7.**
- 동결 80건 회귀 1회(`runner_v7.py`, run **v7**, 유료 80건, gate 그대로): **PASS — 15/15 gate.** exact
  97.5%, 실행 precision/recall 97.4%/100%, 다중 재현율 100%, mixed execute+handoff 0.90(경계값, gate ≥0.90),
  CLARIFY 100%/100%, Skin/Gait 100%, social_intent non-null 0/80. non-exact 2건은 모두 기존 흔들림
  사례 — `boundary_05`(Walk 추가, v5·v6 run 과 동일) · `mixed_09`(Training 추가, v4 run 과 동일). v6 가 잃었던
  `mixed_10`·`clarify_08` 은 회복. 상세: `docs/orchestration-router-benchmark.md` §v7.

**현재 상태 (CONFIRMED — v6 수용).** production 프롬프트는 `semantic-router-ko-v6` 입니다. 일반 돌봄
질문은 어느 능력으로도 가지 않고 기존 미지원 문구(FAILED)로 떨어지며, **그 질문에 답하는 능력은 여전히
없습니다**(옵션 C). 미지원 사용자 문구 개선은 별도 UX 카드입니다.

**미래 계약 경계 (PENDING — 사람 결정, 이 카드가 구현하지 않음).** 실행 능력이 생기려면
순서가 고정입니다: ① 인용 가능한 근거 소스 ② 그 소스를 소유하는 도메인 서비스 ③ 그 뒤에야
라우터 목적지. 라우터 목적지가 먼저 생기면 뒤에 아무것도 없는 문이 됩니다.

1. **근거 소스** — 검수된 돌봄 가이드 코퍼스(출처·권리 확인, `trust_level` 부여). 후보는
   Life 가 이미 수집 가능하다고 적어 둔 nias-pet 사육·건강관리 묶음(decisions-rag.md RAG-031 유보)
   과 Training 의 미서빙 급여 문서 3건이지만, **둘 다 검수 전이라 지금은 근거가 아닙니다.**
   견종·연령·체격별 수치는 그 소스가 실제로 그 축을 가질 때만 지원합니다 — 프로필 필드가
   있다는 것이 근거가 아닙니다.
2. **도메인 서비스** — 그 코퍼스를 소유하고 근거 없음을 기계 신호(ABSTAINED)로 내는 서비스.
   Life 의 `care` 제외(RAG-008 ③)를 뒤집을지, 별도 패키지로 둘지는 사람이 정합니다 — Life 에
   얹으려면 관련도 게이트(RAG-029 의 약한 근거 거부)가 먼저 있어야 무관 조례 위의 OK 답변이
   재현되지 않습니다. 의료 경계(영양·용량·질병)는 여전히 REFUSED 입니다.
3. **라우터 목적지** — 그때 `ExecuteName` 에 정확한 도메인 이름(`general`·`care` 같은 포괄명
   금지)을 하나 더하고, 프롬프트 버전을 올리며, gold 에 "지원 범위 밖" 범주를 추가한 동결
   80건 회귀를 1회 돌립니다. payload 는 기존 Training/Life 와 같은 `question` 원문 + (승인되면)
   `active_dog_id` 로 프로필을 서버가 조회하는 모양이 되어야 하고, 라우터가 견종·나이를
   생성해 넣는 일은 없습니다.

## 3. CLARIFY 와 HANDOFF 의 뜻

세 행위는 RoutePlan 의 스칼라 mode 가 아니라 **목록 구조**로 표현됩니다
(`requests[]` · `handoffs[]` · `clarify` — contracts 문서 §2, D-034):

| 행위 | 뜻 | 예 |
| --- | --- | --- |
| EXECUTE (`requests[]`) | 능력 실행 계획이 섰다 | "우리 개 입질 교육 방법 알려줘" → Training |
| CLARIFY (`clarify`) | **실행 없이** 사용자에게 부족한 정보를 되묻는다 | "오늘 산책 어때?" 인데 위치 컨텍스트가 없음 |
| HANDOFF (`handoffs[]`) | 대화로 처리하지 않고 전용 플로우로 안내한다 | "피부에 뭐가 났는데 봐줘" → Skin 업로드 UI 로 |

- **EXECUTE 와 HANDOFF 는 한 요청에서 공존합니다.** "오늘 산책 괜찮은지도 알려주고
  피부에 난 것도 봐줘" → Walk 실행 + Skin 핸드오프. 최상위 status 는 실행 결과에서만
  계산하고 핸드오프는 항상 별도 표면화합니다 (contracts §5).
- **CLARIFY 는 배타적입니다** (O-8). `clarify != None` 이면 능력도 핸드오프도 실행하지
  않습니다 — stateless 재요청(아래)이 이미 실행된 능력을 재실행해 비용을 이중으로 무는
  것을 막습니다.
- CLARIFY 는 2026-08-22 멘토링에서 합의된 원칙의 계승입니다 — **도구가 직접 되묻지 않고
  오케스트레이터가 후속 질문을 담당한다.**
- HANDOFF 는 실패가 아닙니다. multipart 이미지·영상 워크플로(Skin·Gait)는 전용 API 에
  남는다는 확정 경계(architecture §논리 오케스트레이션)의 라우팅 쪽 표현입니다.
  대상 플로우 식별자를 각 `handoffs[].target` 에 담아 프론트가 이동시킬 수 있게 합니다.
  Skin 핸드오프의 인수인계 계약은 **PR #79** 가 원본입니다 — "우리 개 피부가 이상해"
  류 텍스트 진입 → 업로드 플로우 유도, 결과 문구(`headline`·`body`·`disclaimer` 등)
  무수정 통과, 대화 이력 활용은 저장소 결정(#78) 뒤 (architecture §능력 현실 표).
  Life 쪽 핸드오프 식별자 후보(`medical`·`emergency`·`training`·`place`)는
  docs/life/roadmap.md §2·§6 의 제안이고 확정은 사람 몫입니다.
- **stateless CLARIFY (CONFIRMED — O-8)**: v1 에 checkpointer 는 없습니다. CLARIFY 는
  현재 그래프 실행을 종료하고, 클라이언트가 **원 질의 + 새로 채운 구조화 컨텍스트**로
  새 `/assistant/query` 요청을 보냅니다. 서버는 그것을 새 요청으로 취급하며 continuation
  token 은 v1 에 필요 없습니다. 인가·소유권을 함의하는 컨텍스트는 권위 있는 원천이
  생기는 시점부터 서버에서 재검증합니다.

## 4. 라우터 모델 — `gemini-3.1-flash-lite` 수용 완료 (CONFIRMED — D-041, 2026-09-01 정정)

v1 의미 라우터 모델은 팀 결정으로 **`gemini-3.1-flash-lite`** 를 사용합니다. Card 2A
최초 구현은 사람의 모델 선정 기억 착오로 `gemini-3.5-flash-lite`를 썼고 그 상태로
PASS했지만, 원래 의도된 팀 결정은 3.1이었습니다. 착오를 프로덕션에 반영하기 전에
`gemini-3.1-flash-lite`를 **동일한** 80개 골드 RoutePlan·`semantic-router-ko-v3` 프롬프트·
동결 gate에 대해 재실행해 **PASS**를 확인했습니다(PR #130,
`backend/evals/orchestration_router/summary_v4.json`) — 자세한 수치는 D-041 참고.
이 선택은 Card 2A의 모델 비교 결과가 아닙니다. Card 2A는 모델을 고르는 실험이 아니라,
결과를 보기 전에 골드 RoutePlan·프롬프트·수용 게이트를 함께 동결하고 이 모델이 생산
수용 기준을 충족하는지 **PASS/FAIL**로 판정한 acceptance benchmark입니다. 수용된 경계는
LLM 의미 선택 + 결정론적 RoutePlan 조립입니다.

측정 항목:

| 항목 | 무엇을 재나 |
| --- | --- |
| 라우팅 정확도 | 단일 능력 질의를 맞는 능력으로 보내는가 |
| 다중 능력 재현율 | 두 능력이 필요한 질의에서 둘 다 잡는가 |
| 스키마 유효성 | 의미 선택 출력과 최종 조립 RoutePlan 이 각 계약을 통과하는 비율 |
| 안전 결정적 오라우팅 | 의료·안전 질의를 엉뚱한 데로 보내는 사고율 |
| CLARIFY 정밀도 | 되물을 때만 되묻는가 (과잉 CLARIFY 는 UX 비용) |
| warm p50/p95 지연 | 라우터는 모든 대화 요청의 앞단에 선다 |
| 자원/비용 특성 | 서버 상주 RAM · API 비용 |

게이트는 `docs/orchestration-router-benchmark.md`와
`backend/evals/orchestration_router/benchmark_v1.yaml`이 원본입니다. 지연은 관측하지만
더 빠른 다른 모델을 고르는 규칙은 없습니다. 실패하면 모델을 조용히 바꾸지 않고 실패
유형을 분석한 뒤 사람이 프롬프트·스키마/컨텍스트·아키텍처 중 다음 조치를 정합니다.

### Production 구현 상태 (CURRENT — 2026-09-01, Card 2B·Card 3)

이 정책 자체는 바뀌지 않았습니다 — 아래는 **구현이 이 정책을 실제로 지키는지**의
상태 기록입니다.

- **production 의미 라우터가 merge 됐습니다** (PR #113) — `semantic.py`(Gemini 의미
  선택 + O-14 1회 재시도) · `planner.py`(결정론적 RoutePlan 조립) · `service.py`.
- **인증된 `POST /assistant/query` 진입점이 merge 됐습니다** (PR #115,
  orchestration-contracts.md §8) — 이 정책 §1~§5 가 설명하는 경로 전체(결정적 신호 →
  의미 라우팅 → 결정론적 조립 → Card 1 실행 → 집계)가 이제 실제 HTTP 요청으로
  도달 가능합니다.
- **최종 경로에 포커스 E2E 검증이 있습니다** — 실제 `AssistantOrchestrationService` ·
  planner · Card 1 그래프 · 집계를 그대로 쓰고 Gemini 전송과 능력 어댑터만 대체한
  통합 테스트입니다(`backend/tests/test_assistant_orchestration_e2e.py`). Card 2A 의
  80건 골드 벤치마크(`docs/orchestration-router-benchmark.md`)와는 다른 것이고, **이
  closeout 이 그 벤치마크를 다시 돌리거나 동결을 해제하지 않습니다.**
- **라이브 Gemini 스모크 3건** — 실제 `gemini-3.5-flash-lite` 로 production 경로가
  실제로 붙어 있는지 확인한 소규모 스모크이지, 새 벤치마크가 아닙니다. Provider 원문
  프롬프트/응답은 기록하지 않고 결과만 남깁니다.

  | 의도 | 기대 라우팅/plan 결과 |
  | --- | --- |
  | Training 의도 | `execute=[training]` → RoutePlan.requests 에 training |
  | Gait handoff 의도 | `handoffs=[gait]` → RoutePlan.handoffs 에 gait |
  | Walk(좌표 없음) 의도 | `execute=[walk]` 선택 → 좌표 없음 → RoutePlan.clarify |

  3/3 기대한 라우팅/plan 결과와 일치했습니다. 이 스모크는 벤치마크 지표(§4 위 표)를
  갱신하지 않습니다.

- **프론트를 `/assistant/query` 에 연결하는 것과, 배포된 서버 인프라에서 실제
  Training/Life/Walk 로 스모크하는 것은 별도 후속 작업입니다.** 둘 다 이 라우팅
  계약을 바꾸지 않습니다 — 이미 승인된 정책 위에 남은 통합/배포 작업입니다.

## 5. 능력 가용성 · v1 범위 · 인가 매트릭스

의미 라우터가 EXECUTE 로 고를 수 있는 대상은 현재 **Training · Life · Walk**뿐입니다.
실행 registry에는 Place가 추가됐지만 PR #196에서는 `requested_capability=place`라는 명시적
신호로만 들어갑니다. Place 자연어 목적지 선택은 별도 gold set과 기존 80건 회귀를 통과할
후속 PR의 범위입니다.

### v1 인가 매트릭스 (CONFIRMED — D-036)

인증(`/assistant/query` 진입)과 능력별 인가는 별개 관심사입니다. 진입은 인증 필수 —
익명 운영 접근 없음, 인증된 앱 회원과 관리자를 받습니다 (O-2). 능력별 인가는 백엔드/
오케스트레이션 경계 안의 **중앙 집중 매트릭스 한 곳**이 정합니다:

| 능력 | 앱 회원 | 관리자 |
| --- | --- | --- |
| Training | **YES** | YES |
| Life | YES | YES |
| Walk | YES | YES |
| Place (명시 신호만) | YES | YES |
| Skin EXECUTE | NO | NO |
| Gait EXECUTE | NO | NO |

- **앱 회원의 assistant 경유 Training 실행은 의도된 제품 접근 확대입니다** — 우발적
  우회가 아닙니다 (2026-08-30 사람 승인). 기존 직접 엔드포인트 `/training/chat` 은
  현행 관리자 지향 인가 정책을 그대로 유지할 수 있고, **다른 직접 엔드포인트의 인가를
  조용히 넓히지 않습니다.**
- `requested_capability` 는 라우팅 신호일 뿐 인가가 아닙니다 (contracts §6 불변식 12).
- 현행 `admin_or_app_user` 의존성이 principal 을 사용하는 인가에 부적합하면, 인증된
  principal 을 반환하는 전용 의존성을 구현에서 새로 둘 수 있습니다 (O-2).
- rate limit 과 구체 한도는 구현/운영 후속이지 아키텍처 차단 요소가 아닙니다.

### 나머지 규칙

- **Skin · Gait 로는 EXECUTE 하지 않습니다.** 대화에서 그 도메인이 감지되면 HANDOFF 입니다.
  Skin 은 #100/D-040 이후 main backend 의 `/screen/*` 로 기술적으로 호출 가능하지만,
  multipart 업로드와 통제 문구 보존이 필요한 전용 플로우라 Card 1 역할은 그대로
  HANDOFF 입니다. Gait 는 여전히 `gait` profile 뒤의 별도 프로세스이고 #98도 미머지입니다.
  Journey는 #99로 소스가 backend 프로젝트에 합쳐졌을 뿐 Card 1 EXECUTE 대상이 아닙니다.
  Place는 PR #196의 명시 신호 표적 경로만 예외이며 전역 의미 라우터 대상은 아닙니다.
  **기술 가용성은 오케스트레이션 범위 승인이 아닙니다.**
- Gait 가 미래에 들어오면 동기 EXECUTE 가 아니라 CapabilityResult 의 PENDING + job
  메타데이터 경로(contracts §4)입니다 — 추론이 분 단위입니다.
- 능력의 의존성이 일시적으로 죽어 있을 때(예: Training 의 전용 PGVector 컨테이너나
  Gemini 호출 실패 — #94 이후 Training 은 backend 프로세스 안이므로 "프로세스 다운"이
  아니라 의존성 실패입니다) 그 실패는 REFUSED 도 ABSTAINED 도 아니라 ERROR 입니다
  (contracts §6 불변식 1). **실패한 능력을 무관한 능력으로 조용히 폴백하지 않고**,
  성공한 독립 결과는 보존합니다 (O-10).
- 독립 능력의 동시 실행은 허용하되, 능력 선언형 정책 DSL 은 만들지 않습니다 — v1 은
  오케스트레이터 안의 단순 정적 상수로 충분합니다 (O-10). `Send` 는 구현이 실제로 득을
  볼 때만 씁니다. 타임아웃 수치·monolith 동시성 한계는 운영 실측까지 FOLLOW-UP
  (#94 가 운영 관찰 항목으로 넘김 — architecture §서버 재구축 상태).

## 6. 사람 결정 이력 — 전부 해결됨 (2026-08-30)

초안이 OPEN 으로 남겼던 O-1~O-6 은 2026-08-30 어드버서리얼 아키텍처 리뷰(읽기 전용,
`origin/dev` 코드 대조)를 거쳐 리뷰가 추가한 O-7~O-14 와 함께 **사람이 일괄 승인**했고,
decisions.md 의 D- 번호로 옮겨졌습니다:

| 항목 | 결론 | 기록 |
| --- | --- | --- |
| O-1 Life 호출 경계 | 전송 무관 어댑터, v1 은 기존 서비스 심 in-process (HTTP 자기 호출 금지) | D-035 |
| O-2 `/assistant/query` 인증 | 인증 필수 · 익명 없음 · 앱 회원+관리자 | D-036 |
| O-3 Life 기권/안전 | 기존 기계 신호만 어댑터 매핑, 재설계 없음 — 산문 물러섬 미탐지는 수용된 v1 한계 | D-035 |
| O-4 반려견 컨텍스트 | `active_dog_id` 는 context 예약 키만 — 타입 필드 아님, 소유권 증명 아님 | contracts §1 (D-030 갱신) |
| O-5 집계 규칙 | 결정적 진리표 확정, 기계 status 와 안전 공지 분리 | D-033 · contracts §5 |
| O-6 능력 인가 | 위 매트릭스 — 앱 회원 Training 은 의도된 확대 | D-036 |
| O-7 혼합 EXECUTE+HANDOFF | RoutePlan 을 목록 구조로, 스칼라 mode 폐기 | D-034 |
| O-8 stateless CLARIFY | CLARIFY 배타 · checkpointer 없음 · continuation token 불요 | D-034 |
| O-9 응답 조립 | 결정적 조립, Life 출력 축소, 2차 합성 LLM 없음 | D-035 · contracts §6-5 |
| O-10 병렬 실행 | 동시 실행 허용, 정책 DSL 기각, 정적 상수 | §5 (D-030 갱신) |
| O-11 접점 확장 | 어댑터가 backend↔life 의 유일한 신규 승인 접점, 테스트 갱신 후 재강제 | D-035 |
| O-12 ABSTAINED ≠ REFUSED | 상태 모델 6종 + 최상위 UNCERTAIN | D-033 |
| O-13 관측 프라이버시 | 질문 원문 비로깅 불변식 | D-037 |
| O-14 라우터 실패 | CLARIFY 위장 금지, 1회 재시도 후 FAILED | §2 (D-034) |

**Card 1 아키텍처를 막는 미결 사람 결정: 없음.** 남은 것은 전부 서버 실측·벤치마크·
후속 카드의 몫입니다 (architecture §서버 재구축 상태, §4 벤치마크).
