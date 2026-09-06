# 답변 품질 리포트 v1 (#277)

> 라우팅 골드(`evals/orchestration_router/`)와 **다른 잣대**다. 그쪽은 정책대로 골랐는지를, 여기는
> 답변률(FAILED · CLARIFY 가 아닌 비율)과 판정기가 매긴 답변 품질, 폴백(#279) 전/후의 차이를 잰다.
> 사람이 모으지도 채점하지도 않았다 — 질문은 모델이 계층별로 생성해 동결했고, 판정기의 신뢰도는
> 앵커(코드) · 일치율(프롬프트 변형 둘) · 쌍대 위치 교환으로 잰다.

- 생성 시각: 2026-09-06T21:59:46+00:00
- 소스 SHA: `051d78baf91b3407d7a03756344e6af74872f3a5` · `dev` 머지 베이스 `aec7ce50bfbb9d128a785f02e7d973d86a5a6403`
- 패키지: google-genai 2.20.0, langgraph 1.2.11, pydantic 2.13.4
- 답변 라우터 모델: `gemini-3.1-flash-lite` · 판정 모델: 없음
- 앵커를 통과한 판정 모델: `gemini-3.1-flash-lite`, `gemini-3.1-pro-preview`

## 앵커 — 판정기 자동 검증

코드로 만든 답변 7건을 판정기가 기대대로 매기는지. **전부 통과해야 그 모델의 점수를 싣는다.**

| 판정 모델 | 프롬프트 | 통과 | 실패 앵커 |
| --- | --- | --- | --- |
| gemini-3.1-flash-lite | answer-quality-judge-ko-v2a | 7/7 | 없음 |
| gemini-3.1-pro-preview | answer-quality-judge-ko-v2a | 7/7 | 없음 |
| gemini-3.1-pro-preview | answer-quality-judge-ko-v2b | 7/7 | 없음 |

## 질문 세트

`questions_v1.jsonl` · 154건 · 생성기 `answer-quality-questions-ko-v1` · 모델 `gemini-3.1-flash-lite` · temperature 0.9 · seed 277
· 생성 토큰 39617


생성 질문은 실사용 분포가 아니다. 계층(주제 × 문체)을 고르게 두어 "어느 계층이 약한가" 를 보는
용도이고, 묶음(specialized · fallback · clarify)은 리포트가 묶어 보이는 힌트일 뿐 점수에 쓰지 않는다.

| 계층 | 건수 | 묶음 |
| --- | --- | --- |
| training__polite | 2 | specialized |
| training__casual | 2 | specialized |
| training__abbrev_typo | 2 | specialized |
| training__noisy | 2 | specialized |
| training__smalltalk_mixed | 2 | specialized |
| training__multi_intent | 2 | specialized |
| training__no_location | 2 | specialized |
| life_institutional__polite | 2 | specialized |
| life_institutional__casual | 2 | specialized |
| life_institutional__abbrev_typo | 2 | specialized |
| life_institutional__noisy | 2 | specialized |
| life_institutional__smalltalk_mixed | 2 | specialized |
| life_institutional__multi_intent | 2 | specialized |
| life_institutional__no_location | 2 | specialized |
| walk_now__polite | 2 | specialized |
| walk_now__casual | 2 | specialized |
| walk_now__abbrev_typo | 2 | specialized |
| walk_now__noisy | 2 | specialized |
| walk_now__smalltalk_mixed | 2 | specialized |
| walk_now__multi_intent | 2 | specialized |
| walk_now__no_location | 2 | clarify |
| place__polite | 2 | specialized |
| place__casual | 2 | specialized |
| place__abbrev_typo | 2 | specialized |
| place__noisy | 2 | specialized |
| place__smalltalk_mixed | 2 | specialized |
| place__multi_intent | 2 | specialized |
| place__no_location | 2 | clarify |
| skin_gait__polite | 2 | specialized |
| skin_gait__casual | 2 | specialized |
| skin_gait__abbrev_typo | 2 | specialized |
| skin_gait__noisy | 2 | specialized |
| skin_gait__smalltalk_mixed | 2 | specialized |
| skin_gait__multi_intent | 2 | specialized |
| skin_gait__no_location | 2 | specialized |
| general_care__polite | 3 | fallback |
| general_care__casual | 3 | fallback |
| general_care__abbrev_typo | 3 | fallback |
| general_care__noisy | 3 | fallback |
| general_care__smalltalk_mixed | 3 | fallback |
| general_care__multi_intent | 3 | fallback |
| general_care__no_location | 3 | fallback |
| medical_boundary__polite | 3 | fallback |
| medical_boundary__casual | 3 | fallback |
| medical_boundary__abbrev_typo | 3 | fallback |
| medical_boundary__noisy | 3 | fallback |
| medical_boundary__smalltalk_mixed | 3 | fallback |
| medical_boundary__multi_intent | 3 | fallback |
| medical_boundary__no_location | 3 | fallback |
| emergency__polite | 3 | fallback |
| emergency__casual | 3 | fallback |
| emergency__abbrev_typo | 3 | fallback |
| emergency__noisy | 3 | fallback |
| emergency__smalltalk_mixed | 3 | fallback |
| emergency__multi_intent | 3 | fallback |
| emergency__no_location | 3 | fallback |
| off_domain__polite | 3 | fallback |
| off_domain__casual | 3 | fallback |
| off_domain__abbrev_typo | 3 | fallback |
| off_domain__noisy | 3 | fallback |
| off_domain__smalltalk_mixed | 3 | fallback |
| off_domain__multi_intent | 3 | fallback |
| off_domain__no_location | 3 | fallback |

## 답변률 · 루브릭 · 일치율 (라벨별)

**미측정.** 수집된 답변 파일이 없다.

## 폴백 전/후 쌍대 비교

같은 질문의 전/후 답을 위치를 바꿔 두 번 비교했다. 두 번이 같은 답을 가리킬 때만 승/패/무로
세고, 순서에 따라 답이 바뀐 것은 "위치 의존" 으로 따로 센다 — 그 비율이 순서 편향의 크기다.
판정기와 답변 모델이 같은 계열이면 자기 답을 후하게 볼 수 있어 **절대 점수보다 이 전/후 차이가
주 지표다** — 같은 편향이 양쪽에 걸려 상쇄된다.

**미측정.** 쌍대 비교 파일이 없다.

## 미측정

수집되지 않은 계층 (라벨별):

- 수집된 라벨이 없다 — 전 계층 미측정

판정 파일이 없는 라벨: 없음 · 일치율 파일이 없는 라벨: 없음

- 수집된 답변 파일(`answers_<label>.jsonl`)이 없다 — 답변률 · 루브릭 전부 미측정.
- 쌍대 비교 파일(`pairwise_*`)이 없다 — 전/후 승률 미측정.
- 쌍대 비교는 이번에 돌리지 않았다 (예산). 폴백 '전'(#279 이전)의 폴백 계층 답은 전부 같은 FAILED 문구('실행하거나 안내할 수 있는 기능이 없습니다.')라 쌍대는 자명하게 '후'가 이기고 예산만 쓴다 — 전/후 차이는 답변률과 절대 채점으로 보고, 쌍대는 '후' 판본끼리(프롬프트 · 접지 개선 전후) 비교할 때 쓴다.
- 실제 어댑터(Training · Life · Walk · Place)는 돌리지 않았다 — 2026-09-07 개발 PC 에서 서버 DB · Redis 가 닿지 않는다. fake 어댑터 연기 시험(10건)은 배선 확인일 뿐이라 싣지 않았다.
- 폴백 '후'(`answers_on`)는 #279 의 general 어댑터가 이 브랜치에 없어 수집하지 않았다. 명령은 PR #277 본문의 컨텍스트 메모.
- 예산 규칙: 절대 채점은 gemini-3.1-flash-lite 변형 A 1회, 일치율은 30건 부분표본에 gemini-3.1-pro-preview 변형 A·B, 단계마다 400,000 토큰에서 멈춘다.

## 지지되는 결론과 지지되지 않는 결론

**지지되는 것** — 위 표의 숫자는 같은 동결 질문 · 같은 라우터 · 적힌 어댑터 · 적힌 판정 모델로 한
번 잰 값이다. 답변률의 정의는 상태에서 기계적으로 나오고, 루브릭은 앵커를 통과한 판정 모델의 것만
실었으며, 일치율 임계 아래 항목은 표에 "제외" 로 표시했다.

**지지되지 않는 것** — ⑴ 통계적 유의성: 계층당 2~3건, 반복 1회라 검정하지 않는다. ⑵ 실사용
분포에서의 답변률: 계층을 고르게 만든 세트다. ⑶ 가짜 어댑터로 잰 라벨의 도메인 답 품질.
⑷ 판정기의 절대 정확도: 앵커는 명백한 것만 잡는다.
