# 공간 정규화 실행

실행 위치는 DEV의 `backend/`다. GEO·APP 코드 import, API 호출, LLM 없이 저장 응답을 재생할 수 있다.

```powershell
uv run python -m daengs_walk.diary_space_normalize --list-cases
uv run python -m daengs_walk.diary_space_normalize --input <manifest.json> --output <new-result.json>
uv run pytest -q tests/walk/diary/test_diary_space_materials.py tests/walk/diary/test_diary_space_integration.py
```

manifest의 `point`는 `{lat, lng}`. `commerce`와 `park`는 `query_point`, 명시적 `radius_m`,
`pages`를 받는다. 각 페이지는 JSON 객체 또는 manifest 기준 상대 파일 경로다.
`land_cover`는 `query_point`, `layer`, `response`를 받는다. 응답도 객체 또는 상대 파일 경로다.
생략한 자료는 `not_supplied`로 남으며 다른 자료를 지우지 않는다. 출력 파일은 덮어쓰지 않는다.

실제 서비스 수집·카탈로그 보존·발행 연결과 초기 수치는
[공간 정규화 계약](../../../docs/walk/space-normalization.md)에 있다.
