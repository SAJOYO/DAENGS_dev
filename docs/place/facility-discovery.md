# 시설 검색 화면의 AI 검색·후속 선택 연결

기준: Geo `c5d2b7f`, Dev `a3e4a4c`, App `cf5b022`. 로컬 구현이며 배포 상태를 뜻하지 않는다.

## 범위

시설 검색은 산책과 독립이다. 기존 Place intent/planning/discovery/presentation을 재사용해
시설 화면에 AI 결과를 반환하고 검색 방향 확정·조건 보완을 이어서 처리한다.
채팅의 저장/응답 계약과 일반 `/v2/places/search`는 유지한다.
웹은 App `ConnectedPlaceSearchScreen`·`PlaceSearchLabScreen`·`PlaceDogEvaluationPresentation`을
대응시킨 검토 화면이다. Compose를 브라우저에서 실행하지 않으며 Android 소스는 이번 범위에서 바꾸지 않는다.

```text
시설 웹 → POST /app/places/discovery (앱 회원 인증)
             → POST /internal/place/facility-discovery (backend → Place 내부 HTTP)
             → 기존 Gemini intent → grounding → hypotheses/lenses → guarded plan
             → 수동 카테고리 교집합·주차 선호 반영 → 기존 검색·표시 조립
             → 기존 다견 평가 함수 → 시설 화면용 응답
```

전용 진입점은 목적지가 시설로 정해져 있으므로 전역 기능 선택 LLM을 호출하지 않는다.
원문은 Place proposer에 그대로 전달한다. 반려견 값과 지도 좌표는 LLM 프롬프트에 덧붙이지 않는다.

## 공개 요청

`POST /app/places/discovery`, `Authorization: Bearer <app access token>`.
후속 검색 상태가 계정에 귀속되므로 앱 회원만 받는다. 토큰 확인 뒤 짧은 DB 트랜잭션으로
active 회원임을 확인하고, 계정 행 잠금을 해제한 후 Place를 호출한다. 대화 turn이나
사용자 프로필을 생성·수정하지 않는다. 최초 검색에서 허용했던 관리자 토큰은 받지 않는다.

```json
{
  "client_request_id": "dd3095cb-4e94-45b9-87a8-f9cce3ad2bf5",
  "query": "주차되면 좋은 카페",
  "spatial": {"lat": 37.4979, "lng": 127.0276, "radius_m": 10000},
  "kinds": ["cafe"],
  "preferences": {"parking": true},
  "dogs": [{"ref": "selected-a", "revision": "profile-revision", "dog_size": null,
            "dog_weight_kg": 5, "dog_age_years": 3}]
}
```

- 문장: 공백만 있는 값 거부, 최대 1,000자. 입력 원문 보존.
- 공간: lat 32~40, lng 123~133, 반경 100~20,000m. 문장 속 지역명으로 지오코딩하지 않는다.
- `kinds`: 최대 6개·중복 금지. 빈 배열은 수동 제한 없음. 앱의 '전체'는 AI에서 빈 배열이다.
- `dogs`: 최대 20개·ref 중복 금지. 크기/체중/나이를 모르는 선택도 유효하다.
  ref/revision은 요청-응답 대조값이며 서버가 인증한 프로필 identity가 아니다.
  앱은 공통 프로필 상태에서 받은 기존 Pet 목록 중 선택된 아이의 snapshot을 검색 요청에
  동봉한다. 웹 호스트도 기존 인증 `GET /app/pets`로 목록을 받고 같은 변환 규칙을 적용한다.
  시설 화면에 체중·나이 등의 별도 입력/설정은 없다. `ref=id`, `revision=updated_at`이며
  생일인 날짜만 나이로 계산한다. 가족이 된 날은 나이로 사용하지 않고 크기는 추정하지 않는다.
  표본 모드는 동일한 PetListResponse 형식의 가상 프로필을 자동으로 함께 제공한다.
- 등록되지 않은 필드는 422. `conditions`, `active_dog_id`, 임의 plan/gate는 받지 않는다.

## 수동 설정과 자연어의 합성

1. 먼저 기존 intent 정책으로 실행 가능한 검색 방향을 만든다. 미지원 필수조건·제외 요구·근거
   실패를 수동 설정으로 우회하지 않는다.
2. 수동 카테고리가 있으면 각 실행 가능한 방향의 종류와 **교집합**을 취한다. 빈 교집합은
   그 방향을 검색하지 않고 `manual.kind_conflict`를 반환한다. 모든 방향이 충돌하면
   `needs_clarification`이다. 종류가 좁아지면 `applied.kinds`에 실제 적용값을 표시한다.
3. 카테고리는 이미 가능한 방향의 범위를 제한한다. 이번 단계에서는 대상이 없는 문장에
   수동 카테고리를 끼워 넣어 원문 해석을 다시 만드는 기능은 없다.
4. 주차 체크는 선호 추가다. 체크 해제는 자연어의 주차 선호를 금지한다는 뜻이 아니다.
   최종 값은 수동 주차 선호 또는 실행 가능한 자연어 주차 선호다.
5. 주차만 체크해도 AI가 추론한 장소 대상의 origin/locked가 사용자 확정으로 바뀌지 않는다.
6. '주차 필수'의 미지원 강제 필터를 '주차 우선'으로 바꾸지 않는다. 조용함·가격의 한계도
   기존 정책과 signal로 보존한다.
7. dogs는 검색 후 평가에만 사용한다. 후보 제외·재정렬·전원 입장 가능 종합 판정을 하지 않는다.
   `search_place_groups`와 AI 경로가 같은 `evaluate_search_dogs`를 사용한다.

## 공개 응답

`facility-discovery-v1`:

- `search_id`: 계정에 귀속된 검색 식별자. 같은 계정의 인증과 함께 후속 선택에 사용한다.
- `revision`: 최초 1, 후속 선택이 저장될 때마다 1 증가.
- `expires_at`: 최초 검색 저장 후 15분. 후속 선택으로 연장되지 않는다.
- `action_request`: 최초에는 null, 후속 응답에는 처리한 선택 요청 전체를 에코한다.
- `confirmed_lens_id`: 확정한 검색 방향. 확정 전에는 null.
- `request`: 검증된 요청값 전체 에코. 클라이언트는 client_request_id, spatial, kinds, dogs 등을
  대조하고 현재 상태와 다른 결과를 표시하지 않는다.
- `outcome`: `results`, `empty`, `needs_clarification`, `unsupported`.
- `lenses[]`: `id`, `label`, `note`, `applied {kinds, parking}`, `search`, `presentations`.
  `search`는 기존 공개 `PlaceSearchResponse`이고 facilities·좌표·원천 사실·개별 평가를 유지한다.
  `presentations`는 서버가 만든 Place 표시 모델이다. provider 원출력이나 내부 plan/trace는 제외한다.
- `signals[]`: 보존한 요구, 적용 가능 상태, 안내, 보완 선택지와 `selected_option_id`.
  `proxy` 선택지만 활성화하며 `unavailable`은 이유와 함께 비활성 표시한다.
- `notices[]`: 위치 정책·카테고리 충돌·미지원 조건·후보 축약 안내.

기존 discovery의 기본 3방향·방향당 5곳·총 15곳 정책을 사용한다. 최종 다견 평가와 에코까지
포함한 응답은 256KiB를 넘지 않는다. 줄인 후보에는 `truncated`와 `response.trimmed`를 남긴다.
이 최초 연결은 지도 반경 내 모든 시설을 반환하는 무제한 검색/페이지네이션이 아니다.
backend는 Place 런타임을 import하지 않으며 공개 봉투와 에코를 검증한다. 중첩 search/presentation
계약은 Place의 응답 모델이 소유한다.

오류: 인증 401, 입력/미지원 선택 422, 상태 충돌 409, 만료/소유하지 않은 검색 410,
연결·저장소 설정 불가 503, 시간 초과 504, 비정상 응답 502.
실패 시 일반 검색이나 fixture로 자동 대체하지 않는다.

## 후속 선택

`POST /app/places/discovery/actions`, 같은 앱 회원 인증.

```json
{
  "client_request_id": "6fa74dc4-4e01-4d10-8138-05f302a45e0a",
  "search_id": "최초 응답의 UUID",
  "expected_revision": 1,
  "action": {"type": "confirm", "lens_id": "응답에 표시된 검색 방향 ID"}
}
```

조건 보완은 `action`을 `{"type":"refine","signal_id":"응답의 ID","option_id":"cost.travel_distance"}`로 보낸다.
클라이언트는 plan·프로필·반경을 재전송하지 않는다. 공개 응답에 제시한 선택지만 허용한다.

- Backend는 기존 `REDIS_URL`의 Redis에 `facility:continuation:v1:<search_id>`를 저장한다.
  계정·원래 요청·Place 소유 continuation·마지막 응답을 보관한다. 각 항목은 최대 1MiB,
  TTL 900초다. 운영 경로에 프로세스 메모리 대체는 없다.
- Place의 최초 내부 응답은 `{result, continuation}`이다. Backend는 continuation을 해석하지
  않고 저장하며 브라우저에는 result만 반환한다. provider 원출력은 저장하지 않는다.
- 후속 호출은 Backend가 저장한 자료를 `/internal/place/facility-discovery/actions`로 보낸다.
  기존 `resolve_search_facet`·`confirm_search_lens`를 사용하고 Gemini를 재호출하지 않는다.
- `confirmation_context`는 기존 공개 직렬화에서 제외되는 값이다. 내부 continuation에만
  별도로 보관·복원해 원문의 주차 선호 등 확인 맥락이 유실되지 않게 한다.
- 원래 검색어·좌표·반경·카테고리·주차 선호·반려견 snapshot은 그대로 유지한다.
  웹에서 이 값이 바뀌면 기존 후속 요청을 취소하고 새로운 검색으로 시작한다.
- Redis Lua CAS로 같은 revision의 결과를 하나만 저장한다. 직전과 완전히 같은 요청의
  재전송은 저장된 응답을 반환한다. 다른 오래된 요청은 409, 만료/다른 계정은 410이다.
  동시 도착은 읽기 검색을 각각 실행할 수 있으나 한 결과만 저장되며 LLM은 호출하지 않는다.
- 웹은 응답의 원래 요청·검색 ID·revision·선택 요청을 대조한다. 응답 유실 후 재시도는
  같은 client_request_id를 사용하고, 409/410에서는 새 검색을 안내한다.

현재 기존 엔진에서 실행 가능한 보완은 비용 기준의 `가까운 곳` 선택이다. 실제 가격·입장료·
추가요금 비교가 구현된 것으로 표시하지 않는다. `이 방향으로 검색`은 명시적인 확정이며,
방향 탭을 눌러 이미 받은 카드만 살펴보는 동작과 구분한다.

## 실행·배포 설정

- Backend의 `DAENGS_PLACE_SEARCH_BASE_URL`은 기존 Place 내부 주소를 사용한다.
- 새 backend 설정 `DAENGS_FACILITY_DISCOVERY_TIMEOUT_MS` 기본 45,000ms.
  기존 assistant의 15초 설정은 변경하지 않는다. Place Gemini 기본 30초와 DB 실행을 포함한
  대기 예산이다. 운영에서 Gemini 시간을 늘리면 바깥 timeout도 함께 검토한다.
- Place의 기존 `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TIMEOUT_MS`를 사용한다.
- Backend의 기존 `REDIS_URL`을 검색 상태에도 사용한다. compose에 이미 전달되는 값이며
  Place 프로세스에 Redis나 앱 회원 DB 연결을 추가하지 않는다.
- 새 내부 경로를 nginx에 공개하지 않는다. `/app/places/discovery`는 기존 backend 경로로 간다.
- DB 스키마 변경 없음. 실제 검증에는 설정된 개발 계정·Gemini·시설 DB가 필요하다.

## 검증 화면

[실행 안내](../../tools/facility-review/README.md). `http://127.0.0.1:8766/`.
실제 개발 서버 모드와 명시적인 저장 표본 모드를 분리했다. 서버 모드에 설정이 없거나 요청이
실패하면 오류를 표시한다. 표본 결과로 바꿔 보여주지 않는다.
