# 반려견별 시설 조건 평가

관련 앱: [DAENGS_APP #147](https://github.com/SAJOYO/DAENGS_APP/pull/147).
서버 변경: [#253](https://github.com/SAJOYO/DAENGS_dev/pull/253).

`POST /v2/places/search`는 기존 `conditions` 한 마리 계약을 유지하고, 선택적 `dogs` 배열을 받는다.
두 형태를 동시에 보내면 422다. dogs는 최대 20개(검색 계산 상한, 계정 등록 상한과 별개)이며
ref는 요청 안에서 유일해야 한다. dog_size/weight/age 검증 범위는 기존과 같고 모두 null이어도
선택된 개 하나로 남는다. snapshot의 알 수 없는 키는 거부한다.

```json
{
  "lat": 37.54,
  "lng": 127.05,
  "kinds": ["cafe"],
  "dogs": [
    {"ref": "pet-a", "revision": "2026-09-05T00:00:00Z", "dog_weight_kg": 9},
    {"ref": "pet-b"}
  ]
}
```

ref/revision은 호출자가 해석한 값의 상관관계 표식이다. Place가 프로필 ID의 소유권이나 최신성을
보증하지 않는다. 프로필은 기존 인증된 `/app/pets`로 앱이 읽고, 전체보기 세 요청에는 같은
선택 스냅샷을 사용한다. Place의 프로필 DB 독립성을 유지하며 별도 인증 검색 브리지는 추가하지 않는다.
`GET /app/pets`의 각 PetResponse에 nullable `updated_at`을 추가했다. 기존 DB 컬럼/트리거를
읽으므로 스키마 마이그레이션은 없다. DB의 실제 갱신 트리거 실행은 단위 테스트로 검증하지 않았다.

응답 최상위 dogs는 사용한 스냅샷을 순서대로 echo하고 evaluated_at은 서버 평가 시각이다.
각 hit.evaluations.dogs는 같은 ref 순서로 dog_access/restrictions를 돌려준다. 의료 업종은 두 축이
null이다. 기존 단일 조건 요청의 응답에는 새 빈 필드가 생기지 않는다. 후보 조회는 기존처럼
업종당 수행하며 마릿수만큼 DB 검색을 반복하지 않는다.

앱은 echo와 모든 hit의 평가 목록을 확인해야 한다. 구버전 서버는 최상위 dogs를 무시할 수 있으므로
HTTP 200만으로 지원을 판단하지 않는다. 나이 계산 기준일은 호출자의 스냅샷 생성 시각에 따르며
서버가 생일이나 프로필을 다시 조회하지 않는다.

## 판정 범위

- 크기·명시 kg·숫자 나이 제한을 각각 기존 결정론적 함수로 평가한다.
- 크기 제한 any는 개 크기를 몰라도 크기 축 충족이다. 명시 강아지 불가/체중 제한은 먼저 확인한다.
- 미상 술어 뒤에 확정 불일치가 있어도 확정 불일치를 반환한다.
- 추가 조건, 부분 판독, soft 술어, 미검증 원천 연결은 보수적으로 유지한다.
- 마릿수 제한은 자동 비교하지 않는다. 객실당/혼합 크기 조건을 포함해 원문·술어를 보존한다.
- 후보를 제거하거나 모든 개의 입장을 보증하는 합계 판정을 만들지 않는다.

## 검증

가짜 후보 조회를 쓰는 HTTP 테스트로 다견 echo, 서로 다른 체중 결과, 전체 미상 프로필,
원문 보존, 중복/오타/예산/단일 조건 충돌 거부와 기존 요청 호환을 확인한다. 평가 순열과
프로필 응답·사진 회귀 테스트도 포함한다. PostGIS 쿼리나 인증된 실제 계정 데이터는 변경하지 않았다.

```powershell
cd backend
uv run pytest -q tests/place/api/test_multi_dog_search.py tests/place/api/test_contract_validation.py tests/place/search/test_evaluations.py tests/place/search/test_restriction_projection.py tests/pets/test_pets.py tests/pets/test_pet_photo.py
```
