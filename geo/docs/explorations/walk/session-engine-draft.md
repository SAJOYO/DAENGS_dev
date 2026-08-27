---
status: exploring
implementation: draft
last_verified: 2026-08-24
owner: 사용자
---
# 산책 세션 엔진 (초안)

> **2026-08-22** — 세션 API 흐름(start/locations/finish)은 수집 코어와 맞는다. 그러나 아래 **판정 트리거 · 서술 · 진행도** 절은
> 코어가 아니라 `WalkFacts` 의 소비자 후보다. 확정된 건 [`contracts/walk-record.md`](../../contracts/walk-record.md) 뿐이다.
> `POST /walks` 의 "오늘 목표 계산, 출발 전 메시지" 도 마찬가지 — 목표는 옵션이다 ([loop-and-balance](loop-and-balance.md)).

## 왜 요청-응답 구조에 안 들어가나

팀 아키텍처(Agent Router → Agent N)는 전부 "질문 → 답"이다. 산책은:

- 질문이 없다. **Kotlin 앱이 위치 배치를 계속 던진다**
- 세션 상태가 있다 (진행 중 산책, 누적 거리, 발동된 트리거)
- LLM 호출은 요청마다가 아니라 **트리거 발생 시에만**, 서술용

→ 산책 세션 API는 **Agent Router 바깥**에 별도로 존재하고, Agent Orchestrator는 서술 생성 시 소비자로만 호출한다.

## 세션 흐름 (초안)

```
POST /walks                     세션 시작  ← 프로필 + 날씨로 오늘 목표 계산, 출발 전 메시지
POST /walks/{id}/locations      위치 배치 업로드 (앱 백그라운드 주기)
                                → 판정 → 트리거 있으면 짧은 서술 응답
POST /walks/{id}/finish         종료 → 경로 요약 에피소드, 보상 확정
GET  /walks/{id}                조회
```

## 판정 (코드, 결정론)

트리거 후보:
- 거리 이정표 (프로필 목표 대비 %)
- 정지 (임계값은 개별 — 콩이 3분 ≠ 할매 3분)
- 새 구역 진입 / 새 길 발견
- 평소 코스 이탈
- 권장량 초과 (→ 보상 감소 + 귀가 권유)
- 환경 임계 (기온·미세먼지, brachy/senior 가중)

빈도 상한: 이동 중 알림은 세션당 N회, 최소 간격 M분. 값은 미결.

## 서술 (LLM)

입력: 확정된 트리거 + 프로필(temperament → 톤, health_flags → 경고 강도) + 실제 수치
출력: 개의 목소리 한두 문장. 판타지 없음. 수치는 입력값 그대로.

## 진행도 (유일한 메타)

동네 구역 탐험률 · 연속 일수 · 새 길 수. 레벨/스탯/전투 없음.

## 리스크

- **어뷰징**: 차 타고 이동. 속도/가속도 기반 검증 계층
- **프라이버시**: 시작·종료 좌표 = 집 주소. 저장 정책(절삭·지연 저장·보관 기간) 먼저
- **배터리 vs 반응성**: 업로드 주기가 트리거 반응성을 직접 깎는다. 앱 팀과 초반에 못 박을 것
- **Android 백그라운드 위치**: Foreground Service + 상시 알림, 배터리 최적화 예외, `ACCESS_BACKGROUND_LOCATION` 심사 — 앱 팀이 가장 오래 잡을 부분
