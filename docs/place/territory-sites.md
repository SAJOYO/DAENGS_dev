# 운영 점령지 읽기 경계

점령 게임의 중립 게임판은 `place-db`의 `territory_site`에 저장하고 `place-search`가
`GET /territory/sites/nearby`로 제공합니다. 이 배치는 시설 검색 데이터를 소유한다는 뜻이
아니라, PostGIS 런타임을 새로 하나 더 만들지 않기 위한 물리 배치입니다. 산책 세션과 이후의
접촉·촬영·판정·점령 상태는 이 API에 넣지 않습니다.

촬영 뒤 10m 위치 판정과 비동기 사진 판정 상태는
[점령지 방문 인증 워킹 스켈레톤](../territory/visit-attestation.md)에 따로 정의합니다.

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
보안등 NDJSON에서 다시 선별해 원자적으로 교체합니다. 운영 서버에서는 Actions의
**Territory Site gameboard sync**를 수동 실행합니다. 이 workflow가 아래 네 값을 코드에
함께 고정하고, 하나라도 다르면 DB를 건드리기 전에 실패합니다.

- Geo 공개 릴리스 태그와 파생 스냅샷 파일명
- 압축 파일 SHA-256
- 선별 결과의 정확한 건수(현행 362,309건)
- `territory-site:hex-v1:140:*` 격자 세대

스냅샷 변경은 workflow 상수 변경 PR로 리뷰합니다. 운영 입력을 자유 형식 URL로 받지 않습니다.
[현행 140u 릴리스](https://github.com/rkbuhtig/DAENGS_geo/releases/tag/territory-sites-140u-20260903)는
원본 전체가 아니라 셀당 한 시설만 남긴 파생 NDJSON이며, 적재가 끝나면 러너와 컨테이너의
임시 파일을 모두 지웁니다. 로컬에서 같은 검증을 재현하려면 다음처럼 실행합니다.

```powershell
$containerPath = '/tmp/territory-lamps.ndjson'
docker cp <원본.ndjson> "daengs-place-search:${containerPath}"
try {
  docker compose exec -T place-search uv run --no-sync `
    python -m daengs_place.ingest.territory_sites $containerPath `
      --expected-sites 362309 --dry-run
  docker compose exec -T place-search uv run --no-sync `
    python -m daengs_place.ingest.territory_sites $containerPath `
      --expected-sites 362309
} finally {
  docker compose exec -T place-search rm -f $containerPath
}
```

교체는 한 DB 트랜잭션입니다. 선별이나 INSERT가 실패하면 기존 `lamp` 게임판을 유지합니다.
잘린 원본으로 전국 게임판을 덮지 않도록 기본 30만 개 미만 스냅샷도 거부합니다. 운영에서는
하한뿐 아니라 `--expected-sites`로 정확한 건수까지 확인합니다. 의도적으로 더 작은 전국
스냅샷을 채택할 때만 측정 후 `--minimum-sites`와 고정 건수를 함께 바꿉니다. 원본 파일 자체는
저장소와 컨테이너에 정본으로 남기지 않습니다.

## 적재 대상은 DB마다다 — 로컬 서버와 GCP는 각각입니다

**Territory Site gameboard sync는 로컬 서버 place-db만 채웁니다.** `runs-on: [self-hosted]`라
서버 PC의 러너가 자기 `docker compose`를 잡기 때문입니다. GCP의 place-db는 별개 인스턴스이고
두 DB 사이에 복제가 없으므로(`docs/deploy/roadmap.md` §2-5), **workflow를 몇 번 돌려도 GCP는
바뀌지 않습니다.**

GCP는 `docs/deploy/runbook.md` §6 "점령 게임판 적재 (GCP)"의 같은 명령을 손으로 밟습니다.
네 상수(태그·자산명·SHA-256·건수)가 workflow와 그 절 두 군데에 있으므로 세대를 바꿀 때 같이
고칩니다.

이것을 빠뜨렸을 때의 증상이 특히 조용합니다. GCP는 09-02 덤프에서 왔고 그 안에는 옛
`anchor-hex:115:q:r` 행이 있는데, Alembic `0021`이 그것을 `territory-site:hex-v1:115:q:r`로만
바꿉니다(세대를 올리지 않는 것이 의도입니다). 읽기 질의는 현행 세대만 거르므로 결과는
**어느 좌표에서도 200 + 빈 `sites`**입니다. 마이그레이션도 서비스도 정상이고 로그에도 아무
문제가 없는데 앱 지도에만 점령지가 하나도 없습니다.

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
