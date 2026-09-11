# 산책 파트 슬롯 미리보기

저장된 DEV 입력 → 기존 기본 보드 선정 → 공간·환경·동선 조건과 용량 →
장면별 스탬프 → Gemini 배경 서술 → 원문과 조립하는 워킹 스켈레톤이다.
`POST /app/walks/{walk_id}/diary-slots/preview`에서 실행한다.
기존 `storyboard` 생성·확정·발행·편집 경로는 이 미리보기를 사용하지 않는다.

## 현재 연결 범위

| 단계 | 동작 |
| --- | --- |
| 실제 입력 | 기존 `read_input`의 소유자 확인·저장된 기록/사진/맥락·확정 동선 재생을 재사용 |
| 장면 | 기존 records-first 기본 보드. 사용자 기록을 보존하고 동선 보충·시작/종료 포함 |
| 공간 | 기존 place-search·SGIS·공원·상권·하천 projector. 정확히 같은 코어의 저장 근거만 사용 |
| 환경 | `weather-observation`의 `regional-weather-v1` 입력 어댑터. **실제 날씨 수집기는 미연결** |
| 동선 | canonical 관측을 재생하고 실제 연결 구간의 시간·위치 대응 확인 |
| 서술 | `gemini-3.1-flash-lite` 1회, 최대 15초. 장면·슬롯 선택은 코드, Gemini는 배경 문장만 작성 |
| 출력 | 기본 보드·파트 스탬프·조건/적재 판정·생성 문장·인용 ID·입력/정책 revision |

미리보기 스탬프는 **장면마다 다시 계산하는 불변 스냅샷**이다. 장면 사이의 변경은
각 장면의 후보와 조건을 다시 적용해 반영한다. 실시간 공유 캐시의 TTL/퇴출 상태나
영속 슬롯 테이블, 발행된 스탬프 교체 트랜잭션까지 구현한 것은 아니다.
공간 저장 거리를 다른 장면으로 이월하거나 새 장면의 거리로 해석하지 않는다.
시작/종료·동선 보충 장면은 대응하는 공간·환경 자료가 없으면 그 파트를 비워 둔다.

## 조건과 용량

`SlotPolicy`의 기본값은 실험 시작값이다. 요청마다 변경할 수 있다.

| 항목 | 기본값 | 처리 |
| --- | ---: | --- |
| 공간 용량 / 거리 | 3 / 250m | 위치가 유효한 해당 코어의 근거만. 등록 지점 거리 초과 제외 |
| 환경 용량 | 1 | 관측 유효 시간 `[from, until)`과 자료에 명시된 지역 범위 확인 |
| 동선 용량 / 직전 허용 | 1 / 30초 | 현재 구간 우선, 다음으로 가까운 직전 구간. 단절을 건너지 않음 |
| 위치 샘플 시차 / 동선 대응 거리 | 30초 / 30m | 낡거나 대응 불명인 위치에 현재 맥락을 부여하지 않음 |
| 전체 용량 | 8 | 각 파트 순위 큐를 공간→환경→동선 순으로 한 개씩 순회 |

조건 결과 `pass/fail/unknown`과 적재 결과 `kept/excluded/duplicate/part_capacity/total_capacity`를
분리한다. 조건 불명은 적재하지 않는다. 순위는 용량 충돌을 풀 때만 쓰며, 공간은 가까운 근거,
환경은 적용 가능한 최신 관측 구간, 동선은 현재/가까운 직전 구간을 우선한다.
카테고리별 필수 개수는 없다. 공간 집계의 반경은 등록 지점 거리와 구분해 유지한다.
`space_radius_m`을 키워도 기존 공급자가 저장한 250m 검색 결과 밖의 자료가 새로 생기지는 않는다.

환경 예제는 기온과 풍속만 포함한다. 맑음·현장의 감각을 추론할 근거가 아니다.
자료에 명시된 `area_center/area_radius_m`은 공급자가 보장한 관측 적용 범위를 뜻하며,
수집기 연결 시 해당 보장이 가능한지 확인해야 한다. 전체 스키마는 `RegionalWeather`에 있다.

## 실행

backend에서 Python 3.12와 기존 uv 의존성을 사용한다. DB 없이 합성 입력으로 실행:

```powershell
uv run python tools/run_diary_slots.py --output evals/diary-slots/rules
uv run python tools/run_diary_slots.py --generate --env-file C:/path/to/.env --output evals/diary-slots/gemini
```

키는 `GEMINI_API_KEY` 또는 `GOOGLE_API_KEY`를 사용한다. 로컬 메모의 `gemini: ...` 형식도
읽을 수 있다. 파일에서 해당 키 한 줄만 읽으며 결과에 복사하지 않는다.
`preview.html`에 장면과 근거가 나오며 `input.json`, `policy.json`, `preview.json`도 저장된다.
합성 예제의 기록 3개 모두 세 파트가 들어가고 시작/종료 포함 5개 장면이 나온다.
동선은 합성 GPS를 실제 DEV 측정 커널에 통과시켜 만든다.

저장된 입력이나 조건을 바꿔 다시 실행:

```powershell
uv run python tools/run_diary_slots.py --input evals/diary-slots/rules/input.json --policy evals/diary-slots/rules/policy.json --generate --output evals/diary-slots/variant
```

CLI 입력은 로컬 실험용 `source: DiaryInput`, `points: WalkEvidencePoint[]`이다.
서버의 소유권 증명이 아니다. `points`가 없으면 동선 배경과 지점 보충을 수행하지 않는다.

실제 저장 산책은 앱 회원 인증과 `DAENGS_WALK_DIARY_ENABLED=true` 아래에서:

```http
POST /app/walks/{walk_id}/diary-slots/preview
Content-Type: application/json

{"target_scene_count":3,"generate":true,"policy":{"space_slots":3,"environment_slots":1,"motion_slots":1,"total_slots":8}}
```

원본을 요청으로 받지 않고 저장소에서 읽는다. DB 읽기 잠금은 LLM 호출 전에 해제하며
생성 예약이나 발행 보드를 저장하지 않는다. `generate:false`는 조건 결과만 반환한다.
`context_pending`과 `excluded_backgrounds`로 저장 자료 준비 상태도 반환한다.
공간·환경에 자료가 없으면 파트를 비운다. LLM 실패 시 기본 보드가 그대로 반환된다.
12개 초과 작성 장면, 32KB 초과 입력은 `budget_exceeded`; 모든 원본 장면은 유지된다.

## 확인과 다음 연결

직접 영향 테스트는 `uv run pytest tests/walk/diary/test_diary_slots.py tests/walk/diary/test_diary_slots_api.py -q`.
공간 범위·환경 시간/지역·동선 단절·용량·원문 보존·잘못된 인용·실패 대체·소유자·잠금 경계를 본다.
LLM 출력의 내용 정확성은 자동 검증하지 않는다. 실제 생성에서 느린 이동을 “머물렀다”로
풀어낸 사례가 있어 문장 규칙을 더 조정할 여지가 있다. 근거 ID 검사는 의미 검증이 아니다.

후속 작업은 실제 환경 수집/저장, 앱 미리보기 연결, 채택한 정책의 발행 경로 연결이다.
현재 CLI와 API를 이용해 먼저 조건과 프롬프트를 조정할 수 있다.
