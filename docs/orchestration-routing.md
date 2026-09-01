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
- 구조화된 UI/액션 메타데이터 — 어느 화면·버튼에서 온 요청인지
- 명시적 source/action 식별자
- 의미가 모호하지 않은, 이미 구조화된 컨텍스트

**자연어 키워드 하드 라우팅은 결정적 경로가 아닙니다.** "훈련"·"짖음"·"배변" 같은 단어가
보인다고 Training 으로 직행하는 규칙을 만들지 않습니다. "짖음 때문에 산책을 못 가요"는
Training 인지 Walk 인지 둘 다인지 단어로는 갈리지 않습니다 — 자유 자연어의 의미 판단은
**전부 의미 라우팅 경로(§2)로** 갑니다. 키워드 규칙은 처음엔 잘 맞다가 오답을 조용히
쌓고, 규칙 목록이 늘수록 서로 부딪히는데 그 충돌을 심판할 기준이 없습니다.

## 2. 의미 라우팅 — LLM 폴백

결정적 신호가 없으면(대부분의 자연어 입력) 다음 경로를 탑니다 (D-041, Card 2A PASS):

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

## 4. 라우터 모델 — `gemini-3.5-flash-lite` 수용 완료 (CONFIRMED — D-041)

v1 의미 라우터 모델은 팀 결정으로 **`gemini-3.5-flash-lite`** 를 사용합니다. 이 선택은
Card 2A의 모델 비교 결과가 아닙니다. Card 2A는 모델을 고르는 실험이 아니라, 결과를 보기
전에 골드 RoutePlan·프롬프트·수용 게이트를 함께 동결하고 이 모델이 생산 수용 기준을
충족하는지 **PASS/FAIL**로 판정한 acceptance benchmark이며 최종 결과는 **PASS**입니다.
수용된 경계는 LLM 의미 선택 + 결정론적 RoutePlan 조립입니다.

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

## 5. 능력 가용성 · v1 범위 · 인가 매트릭스

라우터가 EXECUTE 로 보낼 수 있는 대상은 v1 에서 **Training · Life · Walk** 뿐입니다
(architecture §v1 범위 — 능력별 현실은 그 문서 §능력 현실 표).

### v1 인가 매트릭스 (CONFIRMED — D-036)

인증(`/assistant/query` 진입)과 능력별 인가는 별개 관심사입니다. 진입은 인증 필수 —
익명 운영 접근 없음, 인증된 앱 회원과 관리자를 받습니다 (O-2). 능력별 인가는 백엔드/
오케스트레이션 경계 안의 **중앙 집중 매트릭스 한 곳**이 정합니다:

| 능력 | 앱 회원 | 관리자 |
| --- | --- | --- |
| Training | **YES** | YES |
| Life | YES | YES |
| Walk | YES | YES |
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
  Place·Journey 역시 #99로 소스가 backend 프로젝트에 합쳐졌을 뿐 Card 1 EXECUTE 대상이
  아닙니다. **기술 가용성은 오케스트레이션 범위 승인이 아닙니다.**
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
