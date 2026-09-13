# 동선 패턴 실행

DEV `backend/`에서 실행한다. API·LLM 없이 저장 GPS를 canonical 커널로 읽어
직선·방향 전환·되짚기·국소 체류의 유한한 의미 재료를 만든다.

```powershell
uv run python -m daengs_walk.diary_route_normalize --list-cases
uv run python -m daengs_walk.diary_route_normalize --input <manifest.json> --output <new-result.json>
uv run pytest -q tests/walk/diary/test_diary_route_patterns.py tests/walk/diary/test_diary_route_integration.py
```

입력 예시의 `points.json`은 manifest 기준 상대 경로다. 배열 자체를 넣어도 된다.

```json
{
  "session_id": "example-walk",
  "started_at": "2026-09-08T06:00:00Z",
  "ended_at": "2026-09-08T06:10:00Z",
  "points": "points.json"
}
```

각 point는 DEV `WalkEvidencePoint`: `client_seq`, 타임존 있는 `at`, `lat`, `lng`,
`chain_index`와 선택적 `accuracy_m`·`is_mock` 등이다. 원래 좌표·시각·단절을 보존한다.
실제 행동핀이나 반려견 ID는 정규화 입력에 필요하지 않다.
최대 20,000개 point, 중복 없는 증가 순번을 받는다. 실제 개인 GPS는 Git 밖에 둔다.
출력 파일은 덮어쓰지 않는다. 출력에는 사전·정책·원본 revision, 각 재료의 `material`,
`support`, `anchor`, 판별 수치·품질, 단순화 구간과 추가 품질 거절 이력이 담긴다.

서비스 미리보기에서 활성화:

```json
{
  "target_scene_count": 3,
  "generate": false,
  "policy": {"route_patterns": {}, "motion_slots": 4}
}
```

`POST /app/walks/{walk_id}/diary-slots/preview`의 인증된 요청 본문이다.
`motion_slots: 4`는 호환 패턴을 함께 확인하는 비교 값이다. 서비스 기본값은 여전히 1개다.
기존 공간 수집을 함께 확인하려면 `collect_backgrounds: true`를 추가한다.

`DAENGS_WALK_DIARY_ROUTE_PATTERNS_ENABLED=true`는 새 기본 보드 생성과 정책 생략
미리보기의 기본 경로를 켠다. 기본은 false이며, 이 변수로 기존 발행본을 덮어쓰지 않는다.
변수를 바꾸는 운영 절차는 루트 README의 backend 환경 반영 절차를 따른다.

정규화·장면 적용·최종 용량을 구분한다. 회전의 support 전체를 회전 지속 시간으로
해석하지 않는다. 보존한 수치·관계·검증과 남은 정책은
[동선 패턴 계약](../../../docs/walk/route-patterns.md)에 있다.
