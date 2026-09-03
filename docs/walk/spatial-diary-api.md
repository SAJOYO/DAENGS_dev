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
        "policy_version": 1
      },
      {
        "axis": "daylight",
        "values": ["night"],
        "policy_version": 1
      }
    ]
  },
  "field_metric": "visit_rate"
}
```

날짜는 `Asia/Seoul` 달력의 양끝 포함 범위다. 기간은 최대 366일이며, facet 축끼리는
AND, 같은 축의 값은 OR이다. 현재 metric은 `visit_rate`와 `walk_utilization`뿐이다.

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

동기 응답은 다음 상한을 넘으면 부분 결과 대신 413을 반환한다.

| 대상 | 상한 |
| --- | ---: |
| KST 기간 | 366일 |
| 후보 Capsule index | 2,000 |
| 선택 Capsule | 400 |
| 선택 원시 Cell | 100,000 |
| 결과 Cell | 50,000 |

서로 다른 `paint_fp`가 선택되거나 같은 Walk가 중복되면 409다. 봉인된 Capsule과
Cellophane의 identity·fingerprint·metadata가 어긋나면 손상된 서버 원판이므로 500으로
실패하며 일부 결과를 만들지 않는다.

## 경계

- Walk가 실제 산책, Capsule, Cellophane, pet cohort와 이 조회 결과를 소유한다.
- Place의 주변 세계 사실과 Journey의 계획 경로는 이 조회에 필요하지 않아 호출하지 않는다.
- Pin·Attestation·Memory Place·일기 문장과 App 지도 렌더링은 후속 작업이다.
- DB 스키마를 추가하지 않고 기존 `walk_pets`, `walk_capsules`,
  `walk_cellophane_sheets`만 읽는다.
