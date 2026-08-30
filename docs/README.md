# 문서

| 파일 | 내용 |
| --- | --- |
| [decisions.md](decisions.md) | 의사결정 기록 (D-001 ~) |
| [collaboration.md](collaboration.md) | 협업 규칙 — 우선순위 · Iteration · PR 기준 · 데일리 · 회고 |

생활비서 RAG(①)·실시간 산책(②) 문서는 `choiyc05/daengs-life` 에서 이관했습니다 (D-018).
**ADR 접두사가 `RAG-` · `RT-` 로 갈려 있는 것은 의도입니다** — 위 `decisions.md` 의 `D-` 와
번호가 겹치면서 뜻이 남남이었기 때문입니다 (`D-011` 이 양쪽에서 다른 결정이었습니다).

| 파일 | 내용 |
| --- | --- |
| [life-roadmap.md](life-roadmap.md) | ①+② **생활 파트 로드맵** — 지금 상태 · 경계 · 트랙 A~F · 순서 · 하지 않기로 한 것. 결정의 "왜"는 없고 번호로만 가리킨다 (living doc) |
| [decisions-rag.md](decisions-rag.md) | ① 설계 결정 기록 (RAG-001 ~ RAG-052, `037` 결번) — 임베딩·청킹·저장 규약·골든셋·적재·검색·서빙·크롤 운영·PDF 약관 |
| [decisions-realtime.md](decisions-realtime.md) | ② 설계 결정 기록 (RT-) — 실시간 엔진 18결정 (계층·관측 모델·산책 적합도·캐시·부분 실패·응답 계약) |
| [data-sources.md](data-sources.md) | ① 데이터 소스 수집 체크리스트 — 시드 30개 진행 현황, 키 발급처. **2026-08-30 기준 문서형 23 중 16 수집, 남은 7은 막힘** |
| [realtime-apis.md](realtime-apis.md) | ② 날씨·대기질 API 정리 + 실측 로그. §1~§5 와 어긋나면 **§6 이 맞습니다** |

저쪽 `docs/workflow.md`(작업 방식)는 **가져오지 않았습니다** — 위 `collaboration.md` 가
같은 규칙을 더 자세히 담고 있어, 두면 같은 규칙의 두 번째 주장이 생깁니다.
이관 노트(`handoff-daengs-dev.md`)도 원본 레포에 남겨 뒀습니다. 결과는 D-018 에 있습니다.

운영 / 배포 절차는 루트 [README.md](../README.md), 코드 규칙은
[CLAUDE.md](../CLAUDE.md) 에 있습니다.

## 아직 없는 것

- **값 사전** — `documents.subcategory` 는 `CHECK` 제약 없이 문서로 관리하기로
  했는데 그 문서가 아직 없습니다. 데이터 적재를 시작하기 전에 필요합니다.
- **임베딩 모델 기준** — `embedding VECTOR(1024)` 의 1024 가 어떤 모델 기준인지
  적어두지 않으면, 모델을 바꿀 때 판단 근거가 사라집니다.
