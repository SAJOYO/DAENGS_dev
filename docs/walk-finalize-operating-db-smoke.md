# Walk finalize 운영 DB smoke

- 실행일: 2026-09-02 (KST)
- 대상 코드: `dev` `8d68f14` (#140 finalize API 포함)
- 대상 DB: 공유 운영 `vectordb`
- 결과: **FAIL — 선행 migration 누락**

접속 host, 비밀번호, 합성 테스트 UUID는 기록하지 않는다.

## 실행한 검증

### 로컬 회귀

`backend/` 기준으로 실행했다.

```powershell
uv run pytest -q tests/walk
uv run ruff check src/daengs_backend/repositories/walk.py `
  src/daengs_backend/routers/walk.py `
  src/daengs_backend/schemas/walk.py `
  src/daengs_backend/services/walk.py tests/walk
```

| 검증 | 결과 |
| --- | --- |
| Walk 테스트 | PASS, 91개 |
| 변경 경계 Ruff | PASS |

### 운영 DB rollback smoke

실제 SQLAlchemy repository/service를 운영 DB에 연결했다. 합성 AppUser·Walk만
외부 transaction 안에서 만들고, service의 `commit()`은 savepoint로 가두었다.
실행 후에는 외부 transaction을 rollback하고 합성 AppUser ID를 정확히 지우는
cleanup을 한 번 더 실행했다.

| 단계 | 기대 | 실제 |
| --- | --- | --- |
| 합성 산책 + 7개 좌표 chunk 저장 | `walk_point_chunks` INSERT | FAIL |
| 첫 finalize | analysis + sheet + `derived` | 미실행 |
| 같은 finalize 재시도 | 같은 `analysis_id` | 미실행 |
| finalize 후 append | `walk_already_finalized` | 미실행 |
| 불완전 manifest | `point_count_mismatch`, `collecting` 유지 | 미실행 |

첫 저장에서 PostgreSQL이 다음을 반환했다.

```text
UndefinedTableError: relation "walk_point_chunks" does not exist
```

## 운영 DB의 실제 상태

smoke 실패 후 읽기 전용으로 확인했다.

| 항목 | 상태 |
| --- | --- |
| `walks` | 9행 |
| `walk_points` | **932행, 존재** |
| `walk_point_chunks` | **없음** |
| `walk_pets` | 4행, 존재 |
| `walk_analyses` | 존재 |
| `walk_cellophane_sheets` | 존재 |
| 합성 음수 Kakao user | 0행 |

즉 `2026-09-02_walk_analyses.sql`은 적용됐지만, 그 전제인
`2026-09-02_walk_point_chunks.sql`은 적용되지 않았다. #140은 ORM에서
`walk_point_chunks`를 사용하므로 현재 운영 DB에서 산책 upload·append·finalize가
정상 완주할 수 없다.

## 다음 조치

`db/migrations/2026-09-02_walk_point_chunks.sql`은 932개 `walk_points`를 산책별
JSONB chunk로 옮긴 뒤 예전 `walk_points` 테이블을 `DROP`한다. 실행 전에
운영 DB 백업 또는 해당 테이블 백업을 확보하고 다음 순서로 진행한다.

1. `walk_points` 932행과 산책별 `MIN/MAX(client_seq)`, `COUNT(*)` 기준 고정
2. `db/migrations/2026-09-02_walk_point_chunks.sql` 적용
3. `db/migrations/verify_2026-09-02_walk_point_chunks.sql` 실행
4. 전체 point count 932, payload 길이, 첫·마지막 sequence 일치 확인
5. 이 문서의 운영 DB rollback smoke 재실행

smoke가 끝날 때까지 #140의 운영 DB 완주는 확인된 것이 아니다.
