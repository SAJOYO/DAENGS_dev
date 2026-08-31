# Journey upstream

이 폴더는 `SAJOYO/DAENGS_geo`의 `main@c5f0d5f`에서 현재 DAENGS_APP이 실제로 호출하는
`POST /journey` 실행 경로만 이주한 것입니다.

## 유지한 계약

- APP의 좌표 기반 `origin`과 `dests[{lat,lng,name}]`
- `companion`, `measured`, `with_polyline`, `arrive_note`
- 원본 state v4와 legacy `prefs`가 Journey 실행계획에 주는 의미
- profile이 없을 때의 수단 우선순위와 반려견 도보시간 계수
- TMAP 보행 실측, fake/none 강등, 응답의 measured/estimate/unavailable 구분
- 외부 호출의 요청당·시간당 Usage Gate와 프로세스 메모리 캐시
- NAVER/Kakao/TMAP 앱 handoff

## 가져오지 않은 것

- Place id 조회와 PostGIS: APP은 내부 id 대신 좌표를 보냅니다.
- Dog/Owner Profile source: 현재 APP은 dog_id를 보내지 않으며 dev에 프로필 선행 계약을
  만들지 않습니다. non-empty dog_id는 조용히 무시하지 않고 422로 거부합니다.
- Kakao/Naver route: 원본 커밋에서도 route_modes가 비어 있어 실제 경로를 제공하지 않습니다.
- 산책 기록·실시간 위치·Place 검색 API

새 Journey 정책을 여기서 만들지 않습니다. APP 요청 표면이 바뀌면 APP 계약과 원본 동작을
다시 대조한 뒤 별도 PR로 다룹니다.
