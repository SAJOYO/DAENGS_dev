# 조사: 네이버 파노라마(거리뷰) — 산책 스토리보드용 (2026-08-19)

**결론: 뷰어로 열어보는 것까지만. 정적 이미지 API 없음, 저장·가공 금지(panoId 저장도), Android 네이티브 없음.**
국내 커버리지는 최강인데 약관 때문에 재료로는 못 쓴다 — Mapillary(써도 되는데 없음)와 정확히 반대.

## 뭘 주나 (JS API v3 `submodules=panorama`)
- 웹 전용. **Android 지도 SDK엔 파노라마 없음** → Kotlin 앱은 WebView
- `setPosition(coord)` → **반경 300m** 안 가장 가까운 파노라마 자동 선택 (Google/Mapillary 50m보다 넓음)
- 메타 `getLocation()`: `panoId`, `title`(도로명), `address`, `coord`(실제 촬영점 C), **`photodate`(촬영일)**
- POV `{pan, tilt, fov}` · `setPov()` · `getProjection()`(좌표↔POV) → `bearing(C,T)` 초기 시점 적용 가능
- 항공뷰(flightSpot) 있음
- **정적 이미지/썸네일 API 없음.** 캔버스 뷰어뿐

## 약관 — Maps 서비스 이용약관 v0.4 (2025-03-20 시행, PDF 원문 확인)
- **제7조 ⑨** "회사의 사전 동의 없이 '본 서비스'의 결과 데이터를 본 약관에서 허용한 범위를 넘어서서 무단으로 **복제, 저장, 가공, 배포**하거나 제3자에게 제공해서는 안됩니다."
- **제7조 ⑪** "결과 데이터를 별도로 저장해서는 안되며 … 데이터베이스화하여 이용해서도 안됩니다. … **모든 Maps API의 결과 데이터는 값을 리턴 받는 즉시 1회 자신의 서비스에서 사용하는 것만 허용**되며, 그렇지 않고 그 결과 값들을 별도로 저장, DB화, 재사용하는 것은 금지됩니다."
- 제7조 ⑩ 로고/지정 표시 게재 요청 준수 · ⑬ 산출물 모니터링·삭제 권한
- 사용 가이드: '거리뷰' 등 기능 명칭 임의 변경 금지, 정식 호출 경로 외 호출 = 어뷰징

→ 파노라마 캡처 = 복제·저장 = 위반. 개 합성 = 가공 = 위반. **panoId·photodate 저장도 ⑪ 문면상 금지** (Google은 pano ID 저장 예외 있음, 네이버는 없음). 열 때마다 좌표로 재탐색.

## 스토리보드 관점
| | |
|---|---|
| 정적 카드 | ✗ |
| 개 합성 | ✗ (⑨) |
| panoId 저장 | ✗ (⑪, Google보다 엄격) |
| 뷰어 열기 | ✓ 웹 / Android는 WebView |
| 촬영일 표시 | ✓ 즉시 |
| 300m 매칭 | ✓ |

**유일한 쓰임**: 장면 카드 → "이 지점 거리뷰 열기" → WebView JS 파노라마, `bearing(C,T)`로 초기 POV. 저장 없이 그때그때. 카카오 RoadView(Android 네이티브 있음, 단 "별도 협의")와 같은 역할 — 둘 중 하나.

## 세 제공사 + Mapillary 종합 (스토리보드용)
| | 실사 합성 | 정적 카드 | 저장 | 국내 커버리지 | 역할 |
|---|---|---|---|---|---|
| Google | ✗ | ✓ | ID만 | 중 | 링크/카드(합성 없이) |
| 네이버 | ✗ | ✗ | ✗ (ID도) | 상 | 뷰어 열기(WebView) |
| 카카오 | ✗ | ✗ | 협의 | 상 | 뷰어 열기(네이티브, 협의) |
| Mapillary | ✓ CC BY-SA | ✓ | ✓ | 하 (산책로 거의 0) | 보너스 합성 |

→ 본체는 **사용자 자기 사진 + 생성 일러스트**. 로드뷰 3사는 열어보기, Mapillary만 있으면 합성. (research/2026-08-19-mapillary.md 결론 유지)

## 출처
- https://navermaps.github.io/maps.js.ncp/docs/tutorial-Panorama.html
- https://navermaps.github.io/maps.js.ncp/docs/naver.maps.Panorama.html
- https://www.ncloud.com/policy/terms/maps (PDF: [민간]Maps서비스이용약관_v0.4)
- https://guide.ncloud-docs.com/docs/maps-spec
- https://navermaps.github.io/android-map-sdk/guide-ko/ (파노라마 항목 없음)
