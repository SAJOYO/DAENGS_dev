# 조사: Mapillary — 산책 스토리보드 "개 합성" 재료로 (2026-08-19)

**질문**: 로드뷰 위에 사용자 개를 합성해 "그날 산책 풍경"을 되살리려면, 저장·재가공이 허용되는 실사 재료가 있나. Google/카카오/네이버는 약관상 불가.

## 결론
Mapillary는 **법적으로 유일하게 깨끗한 실사 합성 재료**(CC BY-SA). API·필드도 딱 맞음(`on_foot`). 그러나 **한국 산책 핵심 공간(주택가·하천변·공원) 커버리지가 거의 0** → 주력이 아니라 보너스.

## 라이선스·약관
| | |
|---|---|
| 이미지 | CC BY-SA. 수정·재배포·상업 이용 허용 = **합성 OK** |
| 출처 | 이미지별 `"제목" <링크> by "유저" <프로필>, CC BY-SA` + 앱에 **Mapillary 로고·홈페이지 링크** |
| 동일조건 | 합성 결과물도 CC BY-SA. 개인 열람은 무관, **공유·게시 시 고지 필요** |
| 금지 | 블러 해제 시도, 스크래핑 |
| 상업 | API 약관에 "제품 개발·개선 / 고객 대상 서비스" 한정 문구. 소비자 앱 배포 해당 여부 애매 → 출시 전 확인 |
| 저장 | 명시적 캐시 금지 없음 (Google과 결정적 차이) |

## API
- 클라이언트 토큰, 무료. 한도: 엔티티 6만/분 · 검색 1만/분 · 타일 5만/일
- 반경 검색 `images?lat&lng&radius(≤50m)&limit(≤100)&fields=…` — 20→50m 단계 탐색 그대로 됨. bbox는 0.01°² 미만
- 필드: `computed_geometry`(촬영점 C) · `computed_compass_angle` · `captured_at` · `is_pano` · **`on_foot`** · `quality_score` · `thumb_256/512/1024/2048/original_url` · `sequence` · `creator`
- 커버리지 벡터 타일 `tiles.mapillary.com/maps/vtp/mly1_public/2/{z}/{x}/{y}` (z≤14, image 레이어는 z14)

## 커버리지 실측 (웹앱 z15, 2026-08-19)
| 표본 | 결과 |
|---|---|
| 강남역 일대 (도심) | 테헤란로·서초대로·강남대로 있음. 골목은 블록별 편차 — 논현로 쪽 촘촘, 서초 골목 거의 없음 |
| 양재천 + 개포 주택가 (산책 핵심) | **거의 0.** 하천변 산책로·주택가 골목 없음 |

→ 개 산책이 실제로 일어나는 곳이 정확히 빈 곳. 도심 큰길만 있음.

## 설계 함의 (walk/route-storyboard 재검토)
합성 목표면 재료 우선순위:
1. **사용자 자기 사진** — 약관 0, 목표에 제일 맞음. 산책 끝/트리거 지점에서 한 장
2. **생성 일러스트** — 경로 의미(골목·큰길 횡단·공원)+시간대·계절 → 그림 + 개 캐릭터. 항상 나옴, 정직("재구성 장면"), 정책 깨끗
3. **Mapillary `on_foot`** — 있으면 실사 합성 보너스. `quality_score` 하한, 평면은 δ≤45°, 360은 재투영. 공유 시 CC BY-SA 자동 첨부
4. **Google/카카오/네이버** — 합성 없이 "이 지점 로드뷰 열기" 링크만

Google 중심 운영안(텍스트의 Map Tiles·panoIds·bearing(C,T))은 **합성 없는 버전(4)에서만 유효**. bearing(C,T)·통과 사건·회랑 설계는 3·4에서 그대로 쓰이고, 2의 장면 생성 입력으로도 쓰임.

## 출처
- https://www.mapillary.com/developer/api-documentation
- https://www.mapillary.com/terms
- https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data
- 스크린샷: `C:\Users\403\.claude\mapillary_gangnam.png`, `mapillary_yangjae.png` (로컬)
