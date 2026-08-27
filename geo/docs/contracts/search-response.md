# 검색 응답 계약

마지막 코드 대조: 2026-08-24. 검색 표면은 두 개이며 응답 크기가 다르다.

## 공용 장소 검색

`GET /places/search`, `GET /pharmacy/search`는 `SearchOut`을 반환한다.

```jsonc
{
  "params": { "lat", "lng", "radius_m", "kind", "open_now", "night", "limit", "at" },
  "results": [
    { "id", "kind", "name", "lat", "lng", "distance_m", "address", "phone",
      "open_now", "hours_today", "is_night", "is_24h", "tags",
      "area_m2", "staff_count", "prefer_hit" }
  ],
  "map": { "preview_url", "deeplink", "web_url" }
}
```

`open_now: null`은 영업시간 미상이다. `open_now=true` 검색에서도 미상은 제외하지 않고 확정
영업 종료만 제외한다. `map`의 세 값은 같은 `results`에서 파생한다.

## 병원 검색·편집

`POST /hospital/search`는 위 장소 필드에 상태 편집, 추정 이동정보와 안전·복구 표면을 더한
`HospitalSearchOut`을 반환한다.

```jsonc
{
  "state": { "state_version": 4, "lat", "lng", "time_intent", "urgency",
             "target", "journey", "sort", "history" },
  "results": [
    { "...PlaceOut", "transport", "boost" }
  ],
  "map": { "preview_url", "deeplink", "web_url" },
  "changes": [],
  "changes_by_policy": { "context": [], "target": [], "journey": [], "view": [] },
  "applied": [],
  "question": null,
  "reply": "...",
  "resolution": [],
  "show_call_cta": false,
  "call_reasons": [],
  "actions": []
}
```

- `transport=estimate`가 기본이며 목록에서는 실제 provider를 호출하지 않는다. 실측은 목적지를
  선택한 뒤 `POST /journey`에서 요청한다.
- `resolution[]`은 서버 사실이 사용자 설정을 덮은 경우의 추적 정보다.
- `actions[]`는 자동 실행 명령이 아니라 사용자가 선택할 수 있는 `edits[]` 묶음이다.
- 자연어 refine 과 suggested actions 는 구현과 계약을 유지하지만 결정 #51 이후 제품 코어에서는
  parked다. 커뮤니티 근거(`evidence[]`)는 결정 #63 으로 계약에서 **제거**됐다 — parked 가 아니다.

구현: `app/geo/schemas.py`, `app/features/hospital/api.py`,
`app/features/hospital/actions.py`, `app/discovery/refine/actions.py`.
