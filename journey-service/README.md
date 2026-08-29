# DAENGS Journey

장소 검색 결과를 선택한 뒤 기존 DAENGS_APP이 호출하는 `POST /journey` 서비스입니다.
Place 검색이나 산책 기록을 소유하지 않으며, 선택한 좌표까지의 단발 이동 스냅샷과 지도 앱
handoff만 반환합니다. 이주 범위와 제외 항목은 [UPSTREAM.md](UPSTREAM.md)에 고정합니다.

```powershell
uv sync
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check app tests
```

외부 TMAP 실측을 쓰려면 `DAENGS_WALK_ROUTE_PROVIDER=tmap`,
`DAENGS_USAGE_POLICY=dev`, `DAENGS_TMAP_APP_KEY`를 설정합니다. 기본값은 키를 사용하지 않는
`fake`이며 응답에 `status=estimate`와 `status_reason=provider_is_fake`가 표시됩니다.
