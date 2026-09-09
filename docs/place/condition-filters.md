# 시설 조건 필터 엔진

`place.filters.service.search_filtered_places(session, state)`는 수동 입력과 자연어 해석이 함께 사용할 내부 실행 진입점이다. 현재 HTTP endpoint·Android UI·LLM 편집기는 연결하지 않았다. 기존 `/v2/places/search`와 discovery의 요청 의미는 유지한다.

## 입력과 실행

`FilterState`의 `place-filter-v1`은 후보 업종·공간·이름 경계 안에서 `hard.all AND OR(hard.any의 각 all)`을 평가한다. `hard.any=[]`는 대안 제한 없음이며 true다. 빈 분기는 허용하지 않는다. bool은 엄격한 true/false만 받고, 조건 제거와 false를 구별한다.

```python
from daengs_place.place.filters.contract import FilterState
from daengs_place.place.filters.service import search_filtered_places

state = FilterState.model_validate({
    "candidate_kinds": ["cafe", "restaurant"],
    "spatial": {"lat": 37.5665, "lng": 126.978, "radius_m": 3000},
    "hard": {"all": [
        {"id": "parking", "capability": "operations.parking", "op": "eq", "value": True},
    ], "any": []},
    "unknown_policy": "separate",
    "result_policy": {"limit_per_kind": 20, "uncertain_limit_per_kind": 10},
})
response = await search_filtered_places(session, state)
```

등록된 속성은 `purpose.kind`(`in/not_in`), `operations.parking`(`eq`), `pet_access.exclusive`(`eq`)다. 선호는 주차=true만 지원하고 업종 범위를 지정한다. 추가요금·실내 동반·개별 반려견 입장 가능 여부는 hard capability로 등록하지 않았다. `dogs`는 기존 개별 판정을 붙이는 데만 사용하며 후보를 제거하지 않는다.

후보 업종 6개, 공통 atom 8개, 분기 4개·분기당 atom 8개, 총 hard atom 24개, 선호 4개를 상한으로 둔다. 모순은 각 분기의 가능한 업종·bool 조합을 검사해 검색 전에 거부한다. 알려진 주차 true/false를 OR한 조건은 미상을 제외하므로 무조건 true로 단순화하지 않는다.

## 사실과 결과

기존 facility resolver의 후보·동일 업종 차용 CTE와 결과 adapter를 공유한다. 필터 SQL은 허용된 컬럼명과 바인딩 값으로만 컴파일한다. effective 사실 위에서 WHERE와 window rank를 적용하므로, 결과 상한 밖의 적합 후보를 놓치는 후처리 필터가 아니다.

true 후보는 matched, unknown은 요청에 따라 uncertain에 별도로 반환한다. 각 bucket에서 limit+1을 읽어 절단 여부를 판단한다. 같은 후보가 여러 분기를 만족해도 한 번만 반환한다. 전체 건수는 계산하지 않으며 total은 null이다. 예외·DB 실패는 호출자에게 전파하며 빈 정상 결과로 바꾸지 않는다.

선호가 있는 업종은 500m 거리 구간 안에서 주차 true를 먼저 둔다. 다른 업종에는 전파하지 않는다. 원래 거리 정밀도로 정렬한 뒤 표시값만 정수화한다. source/ref와 내부 id가 동률을 닫고, 차용 대상 선택에도 안정적인 동률 순서를 둔다. 의료는 해당 필드가 일괄 미상이므로 조건을 먼저 판정한 뒤 기존 authoritative resolver를 사용한다. 새 경로에서만 소수점 거리·source/ref 순서를 보존한다.

각 hit는 필터 판정, 만족한 분기, atom별 관측값·비교값·출처·기준일·차용 여부·미상 사유를 제공한다. 이 판정은 SQL이 사용한 사실과 대조한다. 원천 기록의 모든 raw JSON을 검색 응답으로 전달하지 않는다.

## 기존 false 데이터

KCISA `_flag`를 기존 source-fact 파서와 동일하게 Y/N/미상으로 맞췄다. 과거 적재 코드의 false에는 ‘Y 이외의 문자열’이 섞일 수 있다. 새 엔진의 주차 false는 보존된 원문의 N을 확인할 수 있을 때만 인정한다. 원문이 없으면 기존 false를 미상으로 표시하고, 파싱 불가 원문도 미상으로 둔다. 기존 true는 원문이 없는 경우에도 이전 Y 판독 근거로 유지한다.

이 규칙은 새 필터 조회에 적용한다. 운영 DB를 갱신하거나 마이그레이션을 추가하지 않았다. 과거 false를 확정하려면 원천 snapshot을 확보한 뒤 별도 보정 작업이 필요하다. 임의의 기본값이나 파생 태그로 채우지 않는다. 같은 원천의 shadow-record 충돌 전반을 새로 판정하는 기능은 이번 엔진에 포함하지 않았으며 기존 제품 후보의 effective-fact 권위를 사용한다.

## 검증과 후속 연결

합의된 가상 후보·예상 결과는 `backend/tests/place/place/filters/examples.json`에 고정했다. 순수 평가기와 실제 PostGIS SQL을 같은 기대 결과에 대조하고, 별도 사례로 filter-before-limit, 미상 bucket 상한, 차용 사실, 소수점 거리, 중복 분기, 업종별 선호, 기존 v2 후보·개별 반려견 판정 보존을 검증한다.

DB 검증은 임시 PostGIS 18/3.6의 빈 DB에 기존 Alembic 0001~0022를 적용한 뒤 수행한다. 운영 DB를 사용하지 않는다. `DAENGS_DATABASE_URL`은 backend 공통 설정과 충돌하므로 사용하지 않는다. CI와 같은 `DAENGS_PLACE_DATABASE_URL` 또는 `DAENGS_DB_HOST/PORT/USER/PASSWORD/NAME`을 사용한다. 이번 로컬 실행은 조각 설정을 썼으며 root conftest가 HOST와 PASSWORD를 덮어쓰는 값에 맞췄다. 임시 DB는 loopback 전용 포트로 노출한다.

다음 단계는 버전이 분리된 공개 요청·응답 어댑터와 앱 필터 UI다. 세션 revision, 수정안의 원자적 반영, 출처/잠금 권한, 미지원 요구의 사용자 안내는 그 경계에서 구현한다. 자연어 근거 구간·명시적 정정과 추론 구분은 이후 LLM 연결 단계의 대상이다. 새 조건을 기존 v2 DTO로 축약해 필터를 누락시키는 fallback은 만들지 않는다.

앱은 각 그룹의 서버 순서를 유지해야 한다. 표시용 정수 distance만으로 다시 정렬하면 SQL이 보존한 소수점 거리 순서가 사라진다. 그룹 간 별도 표시 집계를 만들 때도 원래 그룹 순위·조건 근거를 잃지 않도록 한다.

2026-09-09 로컬 검증: 아래 타겟 테스트 123개 통과, skip 없음. 변경한 소스·테스트의 ruff check와 git diff --check 통과. 전체 저장소 테스트·운영 DB 조회·실제 LLM 호출은 수행하지 않았다.

```powershell
uv run --no-sync pytest -q --tb=short tests/place/place/filters tests/place/integration/test_condition_filters.py tests/place/place/test_search_v2.py tests/place/place/test_name_search.py tests/place/ingest/test_kcisa_concept_filter.py tests/place/integration/test_facility_layer.py tests/place/integration/test_facility_ranking.py
```
