# 산책 파트 슬롯 미리보기

저장된 DEV 입력 → 기존 기본 보드 선정 → 공간·환경·동선 조건과 용량 →
장면별 스탬프 → Gemini 배경 서술 → 원문과 조립하는 워킹 스켈레톤이다.
`POST /app/walks/{walk_id}/diary-slots/preview`에서 실행한다.
`storyboard`의 기본 보드 생성 준비에도 같은 슬롯 계산을 적용한다. 실제 서술·발행은
아직 기존 writer를 사용하며, 미리보기 API를 호출하거나 그 결과를 저장하지 않는다.

## 현재 연결 범위

| 단계 | 동작 |
| --- | --- |
| 실제 입력 | 기존 `read_input`의 소유자 확인·저장된 기록/사진/맥락·확정 동선 재생을 재사용 |
| 장면 | 기존 records-first 기본 보드. 사용자 기록을 보존하고 동선 보충·시작/종료 포함 |
| 공간 | 기존 projector에서 검증된 스키마·reference로 등록 지점/형상 거리·영역 집계·주소를 구분 |
| 환경 | `weather-observation`의 `regional-weather-v1` 입력 어댑터. **실제 날씨 수집기는 미연결** |
| 동선 | canonical 관측을 재생하고 실제 연결 구간의 시간·위치 대응 확인 |
| 서술 | `gemini-3.1-flash-lite` 1회, 최대 15초. 장면·슬롯 선택은 코드, Gemini는 배경 문장만 작성 |
| 출력 | 기본 보드·파트 스탬프·조건/적재 판정과 실제값/기준값·생성 문장·인용 ID·입력/정책 revision |

파트 스탬프는 **장면마다 다시 계산하는 스냅샷**이다. 장면 사이의 변경은
각 장면의 후보와 조건을 다시 적용해 반영한다. 실시간 공유 캐시의 TTL/퇴출 상태나
영속 슬롯 테이블, 발행된 스탬프 교체 트랜잭션까지 구현한 것은 아니다.
공간 저장 거리를 다른 장면으로 이월하거나 새 장면의 거리로 해석하지 않는다.
시작/종료·동선 보충 장면은 대응하는 공간·환경 자료가 없으면 그 파트를 비워 둔다.

## 조건과 용량

`SlotPolicy`의 기본값은 초기 매개변수다. 미리보기 요청마다 변경할 수 있다.
서비스 생성 준비는 서버의 기본 정책을 사용하며, 일반 API 요청에서 변경할 수 없다.
현재 정책 버전은 `diary-part-slots-v2`다. 이전에 저장한 v1 정책 JSON을 재사용할 때는
이 버전으로 명시적으로 바꾼다. v1의 공간 순위·대상 중복 처리 의미를 재현하지 않는다.

| 항목 | 기본값 | 처리 |
| --- | ---: | --- |
| 공간 용량 / 거리 | 3 / 250m | 위치가 유효한 해당 코어의 근거만. 등록 지점/형상 거리 초과 제외 |
| 환경 용량 | 1 | 관측 유효 시간 `[from, until)`과 자료에 명시된 지역 범위 확인 |
| 동선 용량 / 직전 허용 | 1 / 30초 | 현재 구간 우선, 다음으로 가까운 직전 구간. 단절을 건너지 않음 |
| 위치 샘플 시차 / 동선 대응 거리 | 30초 / 30m | 낡거나 대응 불명인 위치에 현재 맥락을 부여하지 않음 |
| 전체 용량 | 8 | 각 파트 순위 큐를 공간→환경→동선 순으로 한 개씩 순회 |
| 위치 설명 포함 | true | `include_location_reference`: 주소 최대 1개를 별도 메타데이터로 보존 |

조건 결과 `pass/fail/unknown`과 적재 결과 `kept/excluded/duplicate/conflict/part_capacity/total_capacity`를
분리한다. 공간은 **등록 지점 거리 → 형상 거리 → 영역 집계**의 비어 있지 않은 큐에서
하나씩 꺼내고 반복한다. 각 거리 큐 안에서만 가까운 순이며, 집계 반경은 거리 순위에 쓰지 않는다.
같은 종류만 있어도 남는 용량을 채울 수 있으며 종류별 필수 개수는 없다. 거리가 없는 집계끼리는
안정적인 ID로 순서를 정한다. 서로 다른 공간 의미 사이의 순환 순서는 이 실험 정책의 명시적 선택이다.
주소는 `location_reference`에 따로 담아 공간/전체 슬롯 용량과 경쟁하지 않는다. 최대 1개이며
writer의 입력 바이트 예산에는 포함되고 인용도 가능하다. 세 파트 근거가 최대 16개이므로
주소까지 포함한 장면별 인용 상한은 17개다. `space_slots:0`과 주소 비활성화는 독립적이다.
환경은 적용 가능한 최신 관측 구간, 동선은 현재/가까운 직전 구간을 우선한다.
`space_radius_m`을 키워도 기존 공급자가 저장한 250m 검색 결과 밖의 자료가 새로 생기지는 않는다.

**중복/충돌은 순위·용량보다 먼저 처리한다.** 대상(`entity_key`), 장면·관계·적용 범위
(`claim_scope`), 정규화한 주장 내용(`claim_key`)을 분리한다. 조회 시각과 단순 숫자 표기 차이
(`40`/`40.0`)는 다른 주장이 아니다. 같은 주장은 하나로 묶고 모든 `sources`의 ID·버전을 유지한다.
관계나 적용 범위가 다르면 별도 근거로 판단한다. 동일 조건에서 값이 다르면 모두 `conflict`로
제외하고 경쟁한 원자료와 값들을 `details`에 남긴다. 현재 어댑터에는 우선권을 입증할 버전 순서가
없으므로 조회 시각·가까운 거리로 승자를 고르지 않는다. 공간 반경 필터보다도 충돌 확인이 먼저여서
40m/200m 상충 입력에서 정책을 60m로 낮춰도 40m 주장만 조용히 살아남지 않는다.

판정의 `details`에는 계산 가능한 실제값·정책 기준값·단위를 담는다. 예: 거리 190m,
정책 100m, `outside_space_radius`. HTML의 ‘판정값과 기준값’ 표에서 확인한다.
직전 동선은 ‘이 기록에 앞선 구간’이며 장소 도착을 뜻하지 않는다. `interpretation`과
`temporal_relation`을 함께 전달해 상대 저속을 정지로 바꾸지 않도록 서술 의미를 명시한다.

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

실제 저장 산책은 앱 회원 인증 및 **두 플래그 모두 true**일 때만 접근 가능하다:
`DAENGS_WALK_DIARY_ENABLED`, `DAENGS_WALK_DIARY_SLOTS_PREVIEW_ENABLED`.
미리보기 전용 플래그는 기본 false이며 일반 일기 활성화만으로 이 경로가 열리지 않는다.
이 플래그가 별도 사용자 허용 목록이나 호출 빈도 제한을 구현한 것은 아니다.

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

### 서비스 연결 1단계: 장면별 근거 고정

`prepare_board_slots(source, board, policy, route=...)`는 **이미 선정된 기본 보드**에
규칙을 적용한다. 장면을 다시 고르지 않고, 각 장면 ID 순서대로 `PartStamp`를 만든다.
`BoardSlotSnapshot`은 세션·입력 revision·보드 계획 revision·정책·스탬프를 묶으며,
다른 세션이나 입력 revision의 보드와 섞으면 거절한다. 자료가 없는 장면도 빈 스탬프로 남는다.

`assemble_saved_base_board`가 장면 조립 직후 이를 계산해 `PreparedSavedBaseBoard.slots`에
보관한다. 기존 `storyboard`의 기본 보드 생성 경로가 사용하는 준비 객체에 들어가므로,
다음 단계의 writer가 이 근거를 그대로 받을 수 있다. 스탬프는 현재 호출의 메모리 객체이며
장면 사이의 영속 슬롯이나 DB에 저장한 서술 영수증은 아니다.

기본 보드의 생성 revision은 슬롯 정책과 결과 revision도 포함한다. 생성 중 그 내용이
바뀌면 기존 완료 검사의 대상이 되며, 이미 발행한 보드는 기존 보존 규칙을 따른다.
구형 `walk-diary-bundle-v1` 생성 revision과 미리보기 v1의 revision 계산은 유지한다.
슬롯·선정/탈락 사유·실제값/기준값은 일반 서비스 응답이나 발행 JSON에 추가하지 않는다.

이번 단계는 실제 writer의 입력을 바꾸지 않는다. 새 슬롯을 서술에 사용하고 기존 마감·실패
대체·발행 흐름에 연결하는 것은 다음 작업이다. 실제 환경 자료 수집기도 아직 미연결이다.

직접 영향 테스트는 `uv run pytest tests/walk/diary/test_diary_slots.py tests/walk/diary/test_diary_slot_claims.py tests/walk/diary/test_diary_slots_api.py -q`.
공간 범위·환경 시간/지역·동선 단절·용량·원문 보존·잘못된 인용·실패 대체·소유자·잠금 경계를 본다.
등록 지점/하천 형상/집계/주소가 실제 projector를 통과하는 혼합 사례, 동일 대상의 상충 값,
출처 병합, 주소 인용과 비활성화, 판정값의 HTML 전달, 별도 활성화 플래그도 확인한다.
LLM 출력의 내용 정확성은 자동 검증하지 않는다. v1 생성에서 느린 이동을 “머물렀다”로
풀어낸 사례를 바탕으로 v2의 의미 설명을 보완했다. 근거 ID 검사는 의미 검증이 아니다.
v2 재실행에서도 ‘머물고 있다’는 표현이 남았다. 프롬프트 보강이 이 오류를 해결했다고
판정하지 않는다. 실제 출력과 근거는 [v2 실행 기록](../../backend/evals/diary_slots/review-fixes-v2.json)에 남긴다.

서비스 준비 연결은 `test_diary_board_slots.py`에서 세 파트·장면 보존·입력 결합·빈 근거·
생성 revision·서술 전 준비·일반 응답 비노출을 확인한다. 기존 기본 보드/API/발행 테스트도
같이 실행해 영향을 확인한다.

후속 작업은 고정된 파트 스탬프의 실제 서술·발행 연결, 실제 환경 수집/저장이다.
현재 CLI와 API를 이용해 먼저 조건과 프롬프트를 조정할 수 있다.
