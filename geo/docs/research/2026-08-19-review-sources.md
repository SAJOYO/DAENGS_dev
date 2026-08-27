# 조사: 동물병원 리뷰/평가 데이터 출처 (2026-08-19)

**결론: 국내 동물병원 리뷰를 API로 주는 곳은 없다.**

| 후보 | 리뷰/별점 API | 실상 |
|---|---|---|
| 네이버 지도/플레이스 | ❌ | 지역검색 API는 이름·주소·전화·카테고리·`link`뿐, 5건 제한 |
| 카카오맵 | ❌ | 로컬 API 동일. `place_url` 딥링크만 |
| 펫닥 | ❌ | 수의사 상담 앱. 전국 병원 절반과 MOU. 공개 API 없음 |
| 핏펫 / 마이펫플러스 / 펫트라슈(진료비+AI후기) / 말캉 / 델고 | ❌ | 개발자 프로그램 없음. 경쟁·참고 서비스 |
| 공공데이터 | ❌ 리뷰 없음 | [행안부 동물병원 조회서비스](https://www.data.go.kr/data/15154952/openapi.do) = POI 원천. 좌표 EPSG:5174, 일 1만 |
| 국가동물보호정보시스템 병원 목록 / 서울시수의사회 병원찾기 | ❌ | 이름·전화·주소뿐 |
| AI Hub | ❌ | 건강·영상 데이터. 병원 평가 아님 |
| Google Places (New) | ✅ 있음 | rating/reviews/opening hours. 국내 커버리지 낮음, Enterprise SKU, `place_id` 외 저장 금지 → **기각** (`explorations/hospital-search/google-places.md`) |

**대신 찾은 것**
- 인허가 데이터에 `면적`·`종사자수` 있음 → 규모 지표 (`name-tagging.md`)
- 네이버 개발자센터 **검색 API**(블로그·카페글·지식iN·웹문서) — 무료, 앱당 일 25,000, `display`≤100 → 커뮤니티 근거 (`community-search.md`)

출처: [Google Places Details](https://developers.google.com/maps/documentation/places/web-service/place-details) · [Google Places 정책](https://developers.google.com/maps/documentation/places/web-service/policies) · [펫닥](https://verticalplatform.kr/archives/7629)
