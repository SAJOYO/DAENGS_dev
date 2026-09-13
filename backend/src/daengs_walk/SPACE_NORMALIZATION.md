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

## 공간 적용 정책·스냅샷 재생

```powershell
uv run python -m daengs_walk.diary_space_replay --list-policy --park-radius-m 250
uv run python -m daengs_walk.diary_space_replay --input evals/walk-diary/space-policy-v1/input.json --output <new-result.json>
uv run python -m daengs_walk.diary_space_replay --input <manifest.json> --park-radius-m 500 --output <another-new-result.json>
uv run pytest -q tests/walk/diary/test_diary_space_memory.py
```

재생 입력의 `policy.park_radius_m`은 필수다. `points`는 `seq`, 타임존 있는 `at`,
`point`(위치 없음은 null)의 배열이다. 각 스냅샷 지점의 원래 순서와 시각을 보존한다.
`batches`에는 고유 `id`, 후보를 공급할 지점 `available_seq`, 정규화 결과 `materials`를
넣는다. 명시적 만료가 있으면 `expires_at`도 넣을 수 있다.
`points`와 각 `materials`는 객체 대신 manifest 기준 상대 JSON 파일 경로를 받아도 된다.
`comparison`으로 기존 SlotPolicy의 공간·전체 용량을 바꿔 비교할 수 있다.

API/LLM 호출 없이 `loaded` 전체, 후보별 `decisions`, 직전 스냅샷과의 `changes`,
별도의 `capacity_stamp`를 출력한다. 출력 파일은 덮어쓰지 않는다.
이 재생기를 서비스의 요청당 처리나 장면 선정기로 연결하지 않는다.
목적·정책·실제 동선 집계와 측정 범위는 [공간 정책](../../../docs/walk/space-policy.md)에 있다.
