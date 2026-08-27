# 문화시설 — 탐색 갈래

**담당: 사용자.**

기반층(`facility`)은 [문화시설 기반·의료 오버레이 제안](../../decisions/2026-08-24-culture-base-medical-overlay.md)에서
왔다. 데이터 층과 필드 병합은 유지하지만 `/facility` 를 제품 최상위로 두는 phase 2는
[결정 #65](../../decisions/2026-08-26-place-first-discovery.md)의 `Place` 축으로 대체됐다.
현재 `/facility/search` 와 Android 병원 검색은 아직 새 공통 계약으로 이동하지 않았다.

이 폴더는 그 기반층을 **실제로 쓸 수 있게 만드는** 갈래들이다. 병원 쪽에서 답이 없었던 질문 —
"조건 편집기는 무엇을 편집하나" — 이 여기서는 답이 있다. 원천이 반려동물 동반을 목적으로
만들어진 데이터라 운영시간·휴무·주차·동반조건이 실제로 들어 있다.

| 갈래 | status | implementation | 한 줄 |
|---|---|---|---|
| [pet-axes](pet-axes.md) | exploring | working | `pet` 자유텍스트 봉투 → 필터 가능한 축. 근거는 커버리지 측정 |

## 병원과 무엇이 다른가

| | 병원 (`place`) | 문화시설 (`facility`) |
|---|---|---|
| 원천 목적 | 인허가 관리 | **반려동물 동반 안내** |
| 영업시간 | 0% | `hours_text` 보유 (원문) |
| 개 프로필과 매칭할 축 | 없음 (표방 태그뿐) | 크기·전용·추가요금·실내외 |
| 조건 필터 | 거리·kind·active | 위 + 동반조건 |
| 폐업 감지 | MOIS 인허가 상태 | **없음** ← 최대 리스크 |

마지막 줄이 이 폴더의 미해결 숙제다. `place` 는 인허가가 폐업을 알려주지만 `facility` 는
스냅샷이라 문 닫은 곳을 모른다. 병원은 "문 닫았네"지만 나들이는 차 타고 가서 문 닫힌 걸 본다.
`field_sources.as_of` 노출 · `phone`/`homepage` 병기까지는 코드에 있고, 사용자 피드백 경로는 없다.

## 관련

- 측정: [`research/2026-08-24-facility-pet-coverage.md`](../../research/2026-08-24-facility-pet-coverage.md)
- 적재: `app/ingest/kcisa.py` (CSV 스냅샷 교체) · `app/ingest/kto.py` (API 증분)
- 검색: `app/api/facility.py` — 필드 단위 병합, 빌린 값에 `field_sources`
