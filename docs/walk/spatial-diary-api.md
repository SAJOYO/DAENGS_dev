# Walk 공간 일기 조회 API

PR #162의 순수 `daengs_walk` View 계약을 제품 백엔드에서 읽는 첫 표면이다. 공간 일기는
지도 snapshot을 저장하지 않고, 요청할 때마다 봉인된 Walk Capsule과 Cellophane을 선택해
하나의 field로 겹친다.

```text
POST /app/walks/spatial-diary/views/query
```

앱 회원 access token이 필요하다. `walk_selector.pet_id`가 현재 회원의 강아지가 아니거나
존재하지 않으면 둘을 구분하지 않고 404를 반환한다.

## 요청

```json
{
  "view_version": 1,
  "walk_selector": {
    "pet_id": "11111111-1111-1111-1111-111111111111",
    "since": "2026-09-01",
    "until": "2026-09-30",
    "context_facets": [
      {
        "axis": "precipitation",
        "values": ["rain"],
        "policy_version": 2
      },
      {
        "axis": "daylight",
        "values": ["night"],
        "policy_version": 2
      }
    ]
  },
  "field_metric": "visit_rate"
}
```

날짜는 `Asia/Seoul` 달력의 양끝 포함 범위다. 기간은 최대 366일이며, facet 축끼리는
AND, 같은 축의 값은 OR이다. 현재 metric은 `visit_rate`와 `walk_utilization`뿐이다.

강수 facet policy v2는 KMA 관측의 `precipitation_kind`를 우선하고, 그 값이 없을 때만 앱이
보낸 WMO `weather_code`를 사용한다. 값은 `rain`, `snow`, `mixed`, `dry`, `unknown`이며
KMA의 비·눈 혼합 관측(`rain_snow` · `drizzle_snow`)을 다른 형태로 접지 않고 `mixed`로
보존한다. 접히는 것은 **세기**뿐이다 — 빗방울(`drizzle`)과 소나기(`shower`)는 `rain`,
눈날림(`snow_flurry`)은 `snow` 가 된다 (RT-004).

## 응답

응답은 요청 명세, q/r 셀을 지상 좌표로 복원할 Paint projection, 셀별 값·분자·공통
분모, 그리고 선택·기여·context known/unknown 수를 담은 receipt로 구성된다. 빈 cohort도
오류가 아니라 분모 0과 빈 `cells`로 반환한다.

```text
spec
projection  paint_version · grid_version · radius_u · profile · paint_fp
field       metric · unit · normalization · denominator · cells[]
receipt     total · selected · contributing · context known/unknown · 정책 세대
```

## 일관성과 상한

앱 인증 세션은 active 회원 확인을 위해 이미 DB statement를 실행한다. PostgreSQL에서는 그 뒤
격리 수준을 바꿀 수 없으므로, View 조립은 별도의 read-only repeatable-read 세션에서 수행한다.
소유권 확인, 전체 Capsule 수, 후보 index, 선택된 Cellophane payload가 같은 snapshot을 본다.
재분석과 재도색 이력은 지우지 않는다. 조회에서는 Walk마다 가장 최근에 봉인된 Analysis와
그 Analysis에서 가장 최근에 생성된 Paint sheet 한 장만 대표로 골라 분모 중복을 막는다.
대표 sheet들의 `paint_fp`가 서로 다르면 좌표계가 같은지 추측하지 않고 409로 실패한다.

동기 응답은 다음 상한을 넘으면 부분 결과 대신 413을 반환한다.

| 대상 | 상한 |
| --- | ---: |
| KST 기간 | 366일 |
| 후보 Capsule index | 2,000 |
| 선택 Capsule | 400 |
| 선택 원시 Cell | 100,000 |
| 결과 Cell | 5,000 |

서로 다른 `paint_fp`가 선택되거나 같은 Walk가 중복되면 409다. 봉인된 Capsule과
Cellophane의 identity·fingerprint·metadata가 어긋나면 손상된 서버 원판이므로 500으로
실패하며 일부 결과를 만들지 않는다.

## 경계

- Walk가 실제 산책, Capsule, Cellophane, pet cohort와 이 조회 결과를 소유한다.
- Place의 주변 세계 사실과 Journey의 계획 경로는 이 조회에 필요하지 않아 호출하지 않는다.
- Pin·Attestation·Memory Place·일기 문장과 App 지도 렌더링은 후속 작업이다.
- DB 스키마를 추가하지 않고 기존 `walk_pets`, `walk_capsules`,
  `walk_cellophane_sheets`만 읽는다.

## 산책별 봉인 원판 조회

`POST /app/walks/spatial-diary/sheets/query`는 앱 산책 기록 탐색이 확정한 산책 집합의
원판을 각각 읽는 API다. 연결 작업은 [DAENGS_APP#271](https://github.com/SAJOYO/DAENGS_APP/pull/271)에서 다룬다.
위 `/views/query`와 달리 여러 산책의 셀을 합치거나 원본 GPS로 새 원판을 만들지 않는다.

```json
{"client_session_ids":["00000000-0000-0000-0000-000000000101"]}
```

입력은 **서버 Walk ID가 아니라 기기의 `client_session_id`**다. 중복 없는 UUID 1~400개를
받고, 현재 계정 소유 조건으로만 읽는다. 날짜·반려견·날씨 조건을 서버에서 다시 적용하지 않는다.
요청 개수·UUID·중복 검증 실패는 422다.

응답은 `{"schema_version":1,"items":[...]}`이며 요청 ID마다 **동일한 순서로 한 항목**을
반환한다. 각 항목에는 `client_session_id`, `walk_id`, `status`, `analysis_id`,
`sheet_fingerprint`, `sheet`가 항상 있다.

| status | 의미 | 나머지 필드 |
| --- | --- | --- |
| `ready` | 검증된 봉인 원판이 있음. 셀 0개인 원판도 포함 | 서버 Walk/Analysis ID, fingerprint, native `sheet` |
| `pending` | 내 서버 산책은 있으나 아직 봉인된 Capsule이 없음 | 서버 `walk_id`만 있고 analysis/fingerprint/sheet는 null |
| `unavailable` | 현재 계정에 해당 기기 ID의 산책이 없음 | `walk_id`·analysis·fingerprint·sheet 모두 null |

없는 산책과 다른 계정의 산책은 구분하지 않는다. **봉인됐는데 원판이 없거나 손상된 경우는
`pending`이 아니라 500**이다. 최신 sealed 원판이 손상됐다고 이전 원판을 대신 반환하지 않는다.

`sheet`는 저장 encoder의 native JSON을 그대로 보존한다:

```text
v: 1
walk_id: 서버 Walk UUID
at: UTC ISO 시각
paint: paint_version, grid_version, radius_u, profile, profile_fp, sample_step_m, paint_fp
cols: ["q", "r", "occupancy_s", "peak"]
cell_count: 원본 셀 수
cells: [[q, r, occupancy_s, peak], ...]   # q/r 오름차순
```

현재 canonical paint는 `paint_version=2`, `grid_version="hex-v1"`, `radius_u=8.0`,
`sample_step_m=1.5`다. `occupancy_s`는 0 이상이며 `peak`는 0 초과 1 이하라서
**occupancy가 0인 유효 셀도 원판 support에 포함**된다. 앱 표시 alpha를 occupancy/peak로
재계산한다는 계약은 아니다. `sheet_fingerprint`는 저장된 canonical JSON의 SHA-256이다.

현재 계정의 ID 매핑, 최신 sealed 대표 선택, payload 검증은 같은 read-only repeatable-read
snapshot에서 수행한다. 대표 선택 순서는 위 집계 API와 공유한다. 조회는 독립 원판을
반환하므로 mixed paint도 허용하고 각 원판의 메타데이터를 보존한다.

선택된 원본 셀 합계가 100,000을 넘으면 payload를 읽기 전에
`413 / spatial_diary_raw_cell_limit`을 반환한다. 부분 결과나 셀 절삭은 하지 않는다.
호출자는 요청 ID 묶음을 더 작게 나눌 수 있지만, 여러 HTTP 요청 전체가 하나의 DB snapshot이라는
보장은 없다. 봉인 identity·fingerprint·metadata 불일치는
`500 / spatial_diary_capsule_incomplete`이며 저장 상세는 응답에 노출하지 않는다.

DB schema·migration·worker 변경은 없다. 이 endpoint의 서버 배포 전까지 앱이 사용할 수 있는
공개 API가 생긴 것은 아니며, 배포 여부와 실제 데이터 적재는 이 계약의 코드 검증과 별개다.
