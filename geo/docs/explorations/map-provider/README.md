# 지도 제공사 — 탐색 갈래

**질문**: 카카오 vs 네이버, 무엇을 어디에.

**원칙 (adopted)**: 지도는 빌린다. 우리가 만드는 건 지도 위의 우리 데이터뿐. 백엔드가 제공사를 만지는 곳은 정적 지도 URL + 지오코딩 → `MapProvider` 3메서드 어댑터, 메서드별로 제공사 다르게 꽂을 수 있음. `app/providers/`

| 갈래 | 상태 | 한 줄 |
|---|---|---|
| 클라이언트 SDK 선택 | **adopted: naver** | 먼저 실제로 돌려볼 지도 표면. `/dev`도 네이버 Dynamic Map, 키 누락·SDK 로드 실패면 OSM 개발 폴백 |
| 정적 지도 | **adopted: naver** | 챗봇 카드용. 기존 서버 프록시로 key secret을 숨김 |
| 지오코딩 | parked | 좌표 결손은 복구 가능하나 현재 우선순위 밖. 검색 중 호출하지 않음 |
| 보강 검색 (카테고리·place_url) | leaning kakao | 네이버 지역검색 5건 제한. 이건 지도 SDK와 무관 |
| 길찾기 | parked | 이번 공급자 연결 범위 밖. 기존 estimate/fake와 제공사 앱 딥링크 유지 |

지금의 공급자 픽은 **네이버 지도 표면만**이다. 병원 검색은 계속 PostGIS가 하고, 지오코딩·
자동차·대중교통·날씨 공급자를 함께 싣지 않는다. 지도 선택이 다른 capability의 선택으로 번지지 않는다.

브라우저 SDK는 공개 식별자인 `DAENGS_NAVER_NCP_KEY_ID`만 받고,
`DAENGS_NAVER_NCP_KEY`는 정적 지도 프록시의 서버 호출에만 사용한다. Naver Cloud 애플리케이션에
Dynamic Map과 Static Map을 활성화하고 개발/운영 웹 서비스 URL을 등록해야 한다.

요금·쿼터·코드 예시: `research/2026-08-19-map-provider-pricing.md`
