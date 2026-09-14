# 슬롯에서 LLM 입력으로

2026-09-14, #508. 현재 기본 카드 작성은 `diary-prose-input-v3`을 사용한다.
동선·속도 통합 슬롯과 전체 맥락 제목을 연결한 결정은
[활동 서술](../../../docs/walk/diary-activity.md)에 있다.
내부 근거와 실제 LLM 입력을 분리한다. 아래 v1의 크기·Gemini 측정은 과거 실행 기록이다.

## 경계

```text
선정된 슬롯 / 확정 본문
  → 내부 작성 작업 (원래 ID·버전·근거 보존)
  → walk_diary.model_input.normalize (허용 필드만 새 객체로 생성)
  → Gemini (작은 입력 + 작성 지침 + 작은 응답 스키마)
  → 호출별 참조표로 원래 ID·버전 복원
  → 기존 인용 검증 / 캐시 / 발행 검사
```

- 공간: 슬롯의 이름·분류·분포·거리·기온과 관계/해석 범위만 보낸다.
  원 좌표, 위치 샘플, 공급자/격자/원자료 식별자, 해시, 순위, 판정값,
  중복된 장면 구성 ID 목록은 보내지 않는다.
- 활동: 장면 기준 상대 시간으로 나눈 phases 안에 경로 형태와 상대 속도를 함께 보낸다.
  회전은 전환 시점 `at_s`를 구별한다. 선택적 행동핀은 확인된 이름과 기록된 행동,
  기록 시각을 별도로 둔다. 원 GPS·속도 숫자·임계값·지지 배열은 보내지 않는다.
  동선이 없고 행동만 있으면 기존 actor/action 계약을 사용한다.
- 현재 카드 제목: 원문을 포함한 모든 확정 본문·순서·시각·행정동을 context로 한 번씩
  보낸다. cards에는 작성 대상 별칭만 넣는다. 원 관측 ID·출처·내용 버전은 서버에 둔다.
- 마지막 장면 제목 실험: 전체 본문과 순서·시각·출발/종료 역할을 함께 보낸다.
  사용자 메모를 포함하는 기존 실험 정책은 유지한다.
- 공간/이동 재료는 `m1`, 기록 행동은 `a1`, 제목 대상은 `c1`부터 시작한다. 서버가 호출마다 별도 참조표를
  소유한다. 모델에 원래 장면/행동 ID나 64자리 버전을 복사하도록 요구하지 않는다.

`walk_diary/model_materials.py`는 필드 허용 목록을 사용한다. 원자료에 새로운 필드가
추가돼도 자동으로 모델 입력에 들어가지 않는다. 등록 지점 거리·형상 거리·조회 원의
분포·행정 위치를 구분하고, 과거 조회 정보/격자 관측의 의미는 짧은 설명으로 유지한다.
기존 regional weather의 누적 구간이 없는 강수량은 그 한계도 함께 전달한다.

정규화는 재료를 재선정하거나 새로운 사실을 추론하지 않는다. 동선은 활동 작성기에
연결하며 공간 작성기에 보내지 않는다. 전송 직전 통합 슬롯의 세부 근거가 실제 payload와
참조표에 전부 남았는지 확인하고, 손실이 있으면 provider를 호출하지 않는다.
모델은 필요한 재료를 골라 쓰되 응답에서 사용한 구간별 별칭을 반환한다.
행정 위치는 동만 보내고 동이 없으면 전체 주소로 대체하지 않는다.
상권은 조회 영역 전체의 통계라는 role과 관계를 유지한다.

## 실제로 보낸 입력을 확인하는 곳

`WritingJob.request`는 내부 의존성·캐시 계약이며, 새 `WritingJob.llm_request`가
실제 생성 함수에 전달한 JSON contents다. 실행 대기 중 마감된 작업에는 이 필드가 없다.
재사용 작업은 `reused`로 구분한다. 시스템 지침과 응답 스키마는 별도 요청 설정이다.

뷰어는 실제 입력을 먼저 보여주고, 원자료와 서버에서 연결한 채택 결과는 따로 접는다.
제목 배치는 현재 카드만 잘라 보여주지 않고 실제 호출 전체를 보여준다. 마지막 제목
갱신은 별도 영역에 있다. 과거 기록은 당시 `request`를 그대로 표시한다.

정규화 버전은 작성 정책과 작업 전략 해시에 포함된다. 의미/필드 계약을 바꿀 때 버전을
올려야 한다. 과거 저장 영수증은 당시 전략 해시로 읽고, 새 영수증은 `llm_request`가
내부 작업에서 재현되는지도 검사한다. 원래 발행의 source revision 검사도 유지한다.

## 현재 구조의 오프라인 연결 확인

[activity-offline-03/summary.json](activity-offline-03/summary.json):
public-02의 저장 공공자료·가상 GPS → 신규 정책 → 슬롯 → 실제 정규화 요청 →
고정 대역 응답 → 저장·재조회까지 통과했다. 활동 8개 중 이동+행동 1개, 이동만 7개,
전체 8개 본문을 읽는 제목 1개다. Gemini·공공 API·DB 호출은 없다.

```powershell
uv run --no-sync python tools/replay_diary_activity.py --source evals/diary_route_scenario/public-02 --output <새-결과-폴더>
```

현재 선별 테스트와 정책 경계는 [활동 문서](../../../docs/walk/diary-activity.md)에 기록한다.
아래 수치를 v3의 토큰 수·품질·운영 지연으로 사용하지 않는다.

## 과거 v1 — 동일 재료의 입력 크기

`public-02`의 **동일한 내부 요청**을 정규화 전/후로 비교했다. 한국어를 그대로
직렬화한 JSON contents의 UTF-8 바이트 수다. 토큰 수나 전체 HTTP 요청 크기가 아니다.

| 단계 | 호출 수 | 전 | 후 | 감소 |
| --- | ---: | ---: | ---: | ---: |
| 공간 | 8 | 25,333 B | 11,121 B | 56.1% |
| 행동 | 1 | 536 B | 46 B | 91.4% |
| 카드 제목 | 1 | 9,008 B | 2,459 B | 72.7% |

세 번째 장면의 실제 새 입력은 [input-comparison.json](public-03-normalized/input-comparison.json)에 있다.

## 과거 v1 — 실제 Gemini 재실행

`public-03-normalized`는 `public-02`에서 **실제로 수집한 공공자료 스냅샷을 그대로 재사용**했다.
이번 실행에서 공공 API를 다시 조회하지 않았다. input/backgrounds/slots가 이전 실행과
동일함을 비교했다. GPS·행동·메모는 이전과 동일한 가상 산책 입력이다.

- Gemini 공간 8개·행동 1개·카드 제목 배치 1개 모두 응답 검증 통과.
- 사전 슬롯 계산을 포함한 작성 측정 9.109초. 한 번의 실행이며 지연 개선이나 운영
  10초 발행 충족을 입증하지 않는다.
- `public-03-normalized-titles`: 완성된 8장면 전체를 읽는 마지막 제목 갱신도 새 입력으로 완료.
- 반환된 짧은 참조가 내부 근거/장면으로 연결되는 것을 확인했다.
- 이전 결과를 덮거나 문장을 손으로 수정하지 않았다.

입력 정리가 서술 품질의 완성을 뜻하지는 않는다. 실제 출력에는 여전히
“숲이 우거진”, 기록된 냄새 맡기에 없는 “코를 낮게 대고”, 상가 “분포”를 제목에서
“밀집”으로 바꾼 표현이 있다. 이는 입력 전송 경계와 별도로 남은 표현 품질 문제다.

## 과거 v1 — 확인 기록

정규화, 카드 작성, 관측 보존, 작성 진입점, 맥락 연결의 5개 테스트 파일을 실행했다.
69개 통과 후 테스트 응답기에 남은 내부 분류 코드 참조 한 곳을 수정했고, 해당 사례
재실행도 통과했다(총 70개 사례). 실제 SDK 경계에서는 provider만 대체하여 JSON contents와
응답 스키마에 내부 식별자가 없고 기록된 입력과 일치하는지 확인한다.
원래 캐시 재사용·부분 제목 채택·발행 중 원자료 변경 차단·과거 영수증 읽기도 포함한다.

```powershell
uv run --no-sync pytest -q tests/walk/diary/test_diary_llm.py tests/walk/diary/test_diary_card_writing.py tests/walk/diary/test_diary_observation_content.py tests/walk/diary/test_diary_writer_entrypoints.py tests/walk/diary/test_diary_context_entry.py
uv run --no-sync python -X utf8 tools/run_diary_route_scenario.py verify --output evals/diary_route_scenario/public-03-normalized
uv run --no-sync python -X utf8 tools/run_diary_final_titles.py --source-run evals/diary_route_scenario/public-03-normalized --output evals/diary_route_scenario/public-03-normalized-titles --replay
```

DB 스키마·의존성·APP 계약·배포 설정 변경 없음. 현재 카드의 실제 LLM 입력과
프롬프트는 변경된다. 전체 pytest와 운영 DB 검증은 실행하지 않았다.

위 과거 재생은 당시 정규화 커밋 `90fa294f` 기준이다. 새 정책에서 hash가 달라졌다고
과거 결과를 덮어쓰지 않는다. 현재 활동 연결 검사는 별도 replay 도구와 새 출력 폴더를 사용한다.
