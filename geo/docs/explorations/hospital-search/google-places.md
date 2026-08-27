---
status: rejected
date: 2026-08-19
---
# Google Places API — 기각

**있는 것**: `rating`, `userRatingCount`, `reviews`, `reviewSummary`, `regularOpeningHours`, `currentOpeningHours`, `photos`, `primaryType(veterinary_care)`. 국내 리뷰 API 중 유일하게 리뷰·별점·영업시간을 줌.

**걸리는 것**:
1. 국내 커버리지 — 동네 동물병원 구글 리뷰 0~5개. "네이버 지도만큼"은 절대 안 됨
2. SKU — 별점·영업시간은 Enterprise, 리뷰는 Enterprise+Atmosphere (최고가 구간)
3. 약관 — `place_id` 외 저장·캐시 금지, 리뷰 표시 시 작성자 표기 + `googleMapsUri` 접근 보장 + Google 로고

**기각 사유 (사용자 결정)**: 신뢰도가 너무 낮다.
**다시 볼 조건**: 국내 커버리지가 유의미해지거나, 해외 진출.
출처: research/2026-08-19-review-sources.md
