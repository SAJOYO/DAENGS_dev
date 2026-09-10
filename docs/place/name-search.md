# 지도 장소명 검색 — PR2

일반 탐색의 이름 검색은 AI 목적 해석과 독립적이다. 의미·유사어·해시태그를 확장하지 않고, 선택한 종류와 반경 안에서 표기 이름의 부분 일치만 실행한다.

## HTTP 계약

`POST /v2/places/search`:

```json
{"lat":37.556,"lng":126.923,"radius_m":3000,"kinds":["cafe"],"name_query":"홍대","limit_per_kind":50}
```

- `name_query`: 선택적 문자열, 기본값 빈 문자열. 앞뒤 공백 제거 후 최대 120 Unicode code points. null/비문자열/길이 초과는 422.
- 미입력 또는 공백뿐이면 이름 조건 없음. 내부 공백은 보존한다.
- PostgreSQL `strpos(lower(name), lower(:name_query)) > 0`으로 비교한다. 영문 대소문자 처리는 DB collation을 따르며 %, _, 역슬래시, 작은따옴표는 패턴 문법이 아닌 검색 문자다. 입력은 bind parameter로 전달한다.
- 카테고리·공간·원천 권위·기존 중복 접기와 함께 LIMIT **전에** 적용한다. 최종 표기되는 canonical 이름만 검사한다. 보강한 KCISA의 별도 이름이나 주소·설명은 검색 대상이 아니다.
- `truncated`는 이름 조건까지 적용한 후보군의 절단 여부다. 종류 순서, 제한, 반려견 평가, 주차 선호 및 결과 상세 계약은 유지한다.
- 이름 조건이 적용된 응답에는 `name_query`를 정규화된 값으로 되돌린다. 조건이 없으면 생략한다.

## 내부 경계

`PlaceSearchRequest → compile → PlaceSearchPlan.name_query → guard → 의료/시설 resolver`.

이름은 명시적 문자 제약이며 AI capability gate가 아니다. 후속 plan editor는 이를 제거·변경할 수 없다. 사용자가 새 검색을 제출해야 바뀐다. 기존 plan 복사/preview는 이름 조건을 보존한다.

## 앱 연결과 배포

- APP PR #118: 입력 중 draft와 제출한 조건을 구분하고, 종류·주차·지도 기준점·내 위치·재시도에는 제출된 조건을 보존한다.
- 구버전 서버는 모르는 요청 필드를 무시할 수 있다. 앱은 이름 검색에 대해 정확한 응답 echo를 요구하며, 없거나 다르면 결과를 사용하지 않는다. 이름 조건 없는 기존 요청/응답은 그대로 호환된다.
- **서버 PR #206 먼저 배포 → 앱 PR #118 배포.** PR 작성 작업에서는 운영 서버 배포/DB 변경을 하지 않는다.
- 스키마·환경변수·원천 데이터 변경은 없다. 전문 검색/초성/유사어/임베딩/AI 질문은 별도 작업이다.

## 검증 범위

`tests/place/search/test_name_search.py`는 입력 검증, HTTP echo, guard 불변식, 의료/시설 SQL의 조건·바인딩·LIMIT 순서를 검증한다. 실제 DB는 대체한다.

```powershell
uv run --no-sync pytest tests/place/search/test_name_search.py tests/place/planning/test_plan.py tests/place/api/test_contract_validation.py tests/place/test_boundary.py -q
```

이 테스트는 실 PostGIS의 실행 계획/실데이터 적중률 검증을 대신하지 않는다. 배포 전후에 홍대 기준 실제 알려진 이름·0건·특수문자·작은 limit 사례를 확인해야 한다.
