# daengs_journey 실행

Journey 서비스의 역할과 이주 범위는
[프로젝트 문서](../../../docs/journey/README.md)에 있습니다. 이 문서는 실행 방법만 다룹니다.

`backend/`에서 실행합니다.

```powershell
uv sync
uv run uvicorn daengs_journey.main:app --reload
uv run pytest tests/journey
uv run ruff check src/daengs_journey tests/journey
```

## Route provider

외부 TMAP 실측을 쓰려면 다음 환경 변수를 설정합니다.

```text
DAENGS_WALK_ROUTE_PROVIDER=tmap
DAENGS_USAGE_POLICY=dev
DAENGS_TMAP_APP_KEY=<app-key>
```

기본 provider는 키를 사용하지 않는 `fake`입니다. 이때 응답에는
`status=estimate`와 `status_reason=provider_is_fake`가 표시됩니다.
