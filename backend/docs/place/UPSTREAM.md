# 출처와 소유권

```
origin:            rkbuhtig/DAENGS_geo
source commit:     c5f0d5f738410e90cac294fd5f407cd88f5330ac  (Place, 2026-08-29)
territory source:  4d384106a52c814faf006e4b033354f7d4e686fd               (main, 2026-09-03)
핵심 선행 PR:      #148  검색은 identity가 아니라 값을 받는다 (dog_id 제거, extra="forbid")
                   #149  검색 전용 진입점 — provider/LLM 키 없이 PostGIS만으로 부팅
경계 결정:         이 저장소 docs/decisions.md D-026
```

**이관 이후 Place 검색의 canonical 구현은 이 저장소(SAJOYO/DAENGS_dev)다.**
DAENGS_geo 쪽 사본은 동결이며 — geo의 산책(walk) 연구가 facility corpus를 참조해서
삭제하지 못하고 남아 있는 것뿐이다 — 검색 관련 수정은 여기서만 한다.

## 무엇을 가져왔나

- `src/daengs_place/` — `main` 진입점의 import closure 전체(api/core/geo/place) + 장소
  적재 배치(`ingest`). 최초 이관에서는 제외했던 점령지 축을 운영 승격하면서
  `territory`와 `ingest/territory_sites.py`로 별도 편입했다
- `infra/place/alembic/` — 리비전 0001~0020 **그대로**. 중간의 walk/anchor 마이그레이션도 포함이다.
  히스토리를 개조하면 기존 geo DB와 갈라진다 — walk용 빈 테이블 몇 개가 생기는 것이
  히스토리 분기보다 싸다. place-db 를 새로 받는 이 저장소에서는 어차피 빈 테이블이다.
- `tests/place/` — search-owned 테스트만: `place`·`integration`(전부 facility/place 계층)·
  `ingest`(kcisa/mois)·`geo`(hours/icons/pet_axes) + 경계 테스트(`test_boundary.py`).
  geo 전체 스위트(walk/journey/discovery 등)는 가져오지 않았다.

## 무엇을 바꿨나 (원본과의 의도된 차이)

- `src/daengs_place/core/config.py` — provider/LLM/deeplink 설정 제거, 검색·적재가 실제로 읽는
  필드만 (파일 docstring 참고)
- `tests/place/conftest.py` — walk/journey/provider 팩토리를 뺀 발췌
- `tests/place/test_boundary.py` — geo의 `test_search_closure.py` 번안. geo에서는
  "provider·profile 이 closure 에 없다"를 쟀지만, 여기는 그 코드가 아예 없으므로
  **화이트리스트**(하위는 api/core/geo/place 뿐)와 "daengs_backend 를 모른다"로 잰다
- `tests/place/api/test_contract_validation.py` — geo `tests/api/test_input_validation.py` 중
  Place v2 계약 테스트만 `daengs_place.main` 기준으로 이식
- 최초 이관 때 유지했던 원본 패키지명 `app`은 D-039에서 `daengs_place`로 바꿨다.
  원본과 대조할 때는 이 파일에 기록된 source commit을 기준으로 한다.
