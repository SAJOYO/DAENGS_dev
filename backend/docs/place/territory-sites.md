# 운영 점령지 읽기 경계

점령 게임의 중립 게임판은 `place-db`의 `territory_site`에 저장하고 `place-search`가
`GET /territory/sites/nearby`로 제공합니다. 이 배치는 시설 검색 데이터를 소유한다는 뜻이
아니라, PostGIS 런타임을 새로 하나 더 만들지 않기 위한 물리 배치입니다. 산책 세션과 이후의
접촉·촬영·판정·점령 상태는 이 API에 넣지 않습니다.

## 앱 계약

- 입력: `lat`, `lng`, 지도 선로딩용 `radius_m`(50~3000), `limit`(최대 500)
- 출력: `site_id`, `lat`, `lng`, `distance_m`, `count`, `truncated`
- 출력하지 않음: 원천 `source`, 시설 `kind`, 제공기관 `instt`, 내부 PK
- `radius_m`은 지도 조회 범위이며 점령 가능 여부를 판정할 10m 반경이 아닙니다.
- 현행 게임판은 `territory-site:hex-v1:140:*`만 읽습니다. 과거 115u 세대와 섞지 않습니다.

140u는 미터가 아니라 Web Mercator 격자 단위입니다. 서울 위도에서 셀 반지름은 약 111m,
이웃 셀 중심 간격은 약 192m입니다. 대표점은 셀 중심이 아니라 실제 보안등 좌표이므로 실제
최근접 거리는 더 짧을 수 있습니다.

## 스키마 승격과 데이터 적재

Alembic `0021`은 과거 `anchor` 테이블을 `territory_site`로, `cell`을 `site_id`로 바꾸되
행의 격자 세대는 보존합니다. 115u 행을 140u로 재명명하지 않습니다. 현행 게임판은 원본
보안등 NDJSON에서 다시 선별해 아래 명령으로 원자적으로 교체합니다.

```powershell
$containerPath = '/tmp/territory-lamps.ndjson'
docker cp <원본.ndjson> "daengs-place-search:${containerPath}"
try {
  docker compose exec -T place-search uv run --no-sync `
    python -m daengs_place.ingest.territory_sites $containerPath --dry-run
  docker compose exec -T place-search uv run --no-sync `
    python -m daengs_place.ingest.territory_sites $containerPath
} finally {
  docker compose exec -T place-search rm -f $containerPath
}
```

교체는 한 DB 트랜잭션입니다. 선별이나 INSERT가 실패하면 기존 `lamp` 게임판을 유지합니다.
잘린 원본으로 전국 게임판을 덮지 않도록 기본 30만 개 미만 스냅샷도 거부합니다. 의도적으로
더 작은 전국 스냅샷을 채택할 때만 측정 후 `--minimum-sites`를 명시적으로 낮춥니다. 원본 파일
자체는 저장소와 컨테이너에 정본으로 남기지 않습니다.

## 배포 확인

```sql
SELECT split_part(site_id, ':', 3) AS radius_u, count(*)
FROM territory_site
GROUP BY 1 ORDER BY 1;
```

현행 140u 행이 존재하는지 확인한 뒤 공개 경로를 확인합니다. `dev` 배포의 Territory Site
스모크도 서울시청 반경 3km에서 한 건 이상을 요구합니다. 따라서 최초 승격에서 140u 데이터가
없으면 서비스와 마이그레이션은 반영되더라도 workflow는 실패로 남습니다. 위 적재를 마친 뒤
배포 workflow를 다시 실행해 data-ready까지 녹색으로 만들어야 합니다.

```powershell
$response = Invoke-RestMethod `
  'http://127.0.0.1:8000/territory/sites/nearby?lat=37.5665&lng=126.9780&radius_m=3000&limit=1'
if (@($response.sites).Count -lt 1 -or $response.count -lt 1) {
  throw '현행 140u 점령지 게임판이 비어 있습니다.'
}
$response
```

응답은 200이어야 하고 `count`, `truncated`, `sites`를 가져야 합니다. 데이터가 아직 적재되지
않았다면 API 자체는 200과 빈 `sites`를 반환하며 115u 행을 대신 내보내지 않습니다. 다만 빈
현행 게임판은 앱 출시 가능 상태가 아니므로 배포 workflow는 성공으로 판정하지 않습니다.
