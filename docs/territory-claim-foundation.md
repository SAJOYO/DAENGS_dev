# 점령 게임 1단계 — 공유 점유 판정 모델

> 이 문서는 1단계 범위를 기록한다. 후속 서버 저장·API 연결은
> [온라인 점유 계약](territory-ownership-api.md)과 DAENGS_dev#260을 참조한다.

[Geo 제작 계획](https://github.com/rkbuhtig/DAENGS_geo/pull/230)의 1단계다.
`backend/src/daengs_backend/services/territory_claim.py`에 HTTP·DB와 독립인 불변 모델과
상태 전이 함수를 추가한다. 운영 라우터·방문 인증 서비스·DB 스키마는 변경하지 않는다.

## APP과 맞춘 모델

| Python | APP Kotlin | 의미 |
|---|---|---|
| `ClaimSession` | `ClaimSession` | 로컬 산책 ID, 행동 계정, 대표견 |
| `ClaimSite` / `Occupancy` | `TerritoryClaimSite` / `TerritoryOccupancy` | 장소 ID와 공유 점유 스냅샷. null 점유는 중립 |
| `SiteInteraction` | `SiteInteraction` | 내 세션의 대상별 접근 판정 |
| `ClaimAttempt` | `ClaimAttempt` | 세션·장소별 시도, 점유 결과와 사진 진행 |
| `mark` / `submit_photo` / `resume` | `mark` / `submitPhoto` / `resume` | 영역표시·인증 추가·재시도 액션 |
| `resolve_photo` | 페이크 공급자의 `resolvePhoto` | 검증된 사진 결과를 점유 규칙에 적용 |

enum 이름은 양쪽에서 동일하다. ID는 1단계 도메인에서 문자열, 시각은 epoch milliseconds다.
이는 배포된 JSON API 계약이 아니다. UUID·timezone 등의 HTTP 표현은 기존 서비스 경계에서
변환한다. 장소 좌표는 기존 site lookup으로 읽고 `site_id`로 점유와 결합한다.

`GRANTED`는 해당 시도의 과거 결과다. 현재 소유자는 항상 `ClaimSite.occupancy`로 읽는다.
인증 추가는 같은 시도의 진행이며, 원래 미인증 점유 시각을 보존한다. 이미 내 영역이면
무사진 액션은 점유를 갱신하지 않지만 새 산책에서 사진 인증을 추가할 수 있다.

## 기존 서비스와 후속 연결

현재 `services/territory.py`와 `/app/territory/attempts`는 촬영 단위 방문 인증이다.
`VerifiedVisit`이나 사진의 `VERIFIED`를 공유 소유권으로 바로 취급하지 않는다. 실제 연결은
4단계에서 사진 결과 → 이 점령 판정 → 점유 저장으로 구성한다.

- 산책 종료 전에도 기존 `client_session_id`로 시도를 식별한다. 서버 산책 ID로 대체하지 않는다.
- 인증 계정과 산책 참여견 관계를 확인한 뒤 선택된 대표견을 `ClaimSession`에 넣는다.
- `evaluate_access`의 recording/trusted_location과 거리·정확도는 서버가 확인한 접촉 근거로
  구성한다. 클라이언트의 `READY`나 임의 `ACCEPTED`를 신뢰하는 새 API를 만들지 않는다.
- `mark(existing=...)` 호출 전 세션·장소의 기존 시도를 읽는다. 4단계 저장 어댑터에서
  세션 identity의 계정 귀속, 유일성, 읽기·점유 변경의 원자성을 보장해야 한다. 순수 함수
  자체가 DB 중복이나 동시 요청을 막아주는 것은 아니다.
- 사진은 같은 산책·장소·접촉 기회에 귀속시킨 뒤 `submit_photo`에 연결한다. 촬영 ID의
  장소 간 재사용 방지는 저장 어댑터에서 보장한다. APP 페이크는 메모리 인덱스로 이를 재현한다.

미인증 점유 간 무사진 경쟁은 `POLICY_UNDECIDED`이며 무사진 탈취 금지를 확정한 것이 아니다.
사진 인증 우선권은 실행할 수 있다. 점유 버전이 바뀐 뒤 늦은 인증이 도착하면 `site_changed`로
반환해 조용히 덮어쓰지 않는다. 동시 요청·지연 결과의 최종 해결 정책과 미인증 시즌 점수는
후속 단계에서 정한다. 별도 이탈·재진입이나 대기시간 규칙은 추가하지 않는다.

## 검증과 완료 범위

```text
cd backend
uv run pytest tests/test_territory_claim.py tests/test_territory_attempts.py tests/test_territory_vision.py -q
uv run ruff check src/daengs_backend/services/territory_claim.py tests/test_territory_claim.py
```

`tests/fixtures/territory-claim-scenarios.tsv`는 APP의 테스트 리소스와 동일한 20단계 예시다.
양쪽 테스트가 상태와 동일 시도 재사용을 검증한다. fixture 변경 시 두 PR에서 같이 갱신한다.
DB·API·실제 VLM을 연결한 온라인 점유는 아직 완료 범위가 아니다.
