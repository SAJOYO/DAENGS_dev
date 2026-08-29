# 출처와 소유권

```
origin:            rkbuhtig/DAENGS_geo
source commit:     c5f0d5f738410e90cac294fd5f407cd88f5330ac  (main, 2026-08-29)
핵심 선행 PR:      #148  검색은 identity가 아니라 값을 받는다 (dog_id 제거, extra="forbid")
                   #149  검색 전용 진입점 — provider/LLM 키 없이 PostGIS만으로 부팅
경계 결정:         이 저장소 docs/decisions.md D-026
```

**이관 이후 Place 검색의 canonical 구현은 이 저장소(SAJOYO/DAENGS_dev)다.**
DAENGS_geo 쪽 사본은 동결이며 — geo의 산책(walk) 연구가 facility corpus를 참조해서
삭제하지 못하고 남아 있는 것뿐이다 — 검색 관련 수정은 여기서만 한다.

## 무엇을 가져왔나

- `app/` — `search_main` 진입점의 import closure 전체(api/core/geo/place) + 장소
  적재 배치(`app/ingest`, anchors 제외 — 그건 walk/territory 축이다)
- `alembic/` — 리비전 0001~0020 **그대로**. 중간의 walk/anchor 마이그레이션도 포함이다.
  히스토리를 개조하면 기존 geo DB와 갈라진다 — walk용 빈 테이블 몇 개가 생기는 것이
  히스토리 분기보다 싸다. place-db 를 새로 받는 이 저장소에서는 어차피 빈 테이블이다.
- `tests/` — search-owned 테스트만: `place`·`integration`(전부 facility/place 계층)·
  `ingest`(kcisa/mois)·`geo`(hours/icons/pet_axes) + 경계 테스트(`test_boundary.py`).
  geo 전체 스위트(walk/journey/discovery 등)는 가져오지 않았다.

## 무엇을 바꿨나 (원본과의 의도된 차이)

- `app/core/config.py` — provider/LLM/deeplink 설정 제거, 검색·적재가 실제로 읽는
  필드만 (파일 docstring 참고)
- `tests/conftest.py` — walk/journey/provider 팩토리를 뺀 발췌
- `tests/test_boundary.py` — geo의 `test_search_closure.py` 번안. geo에서는
  "provider·profile 이 closure 에 없다"를 쟀지만, 여기는 그 코드가 아예 없으므로
  **화이트리스트**(app 하위는 api/core/geo/place 뿐)와 "daengs_backend 를 모른다"로 잰다
- `tests/api/test_contract_validation.py` — geo `tests/api/test_input_validation.py` 중
  Place v2 계약 테스트만 `app.search_main` 기준으로 이식
- 패키지 이름은 원본의 `app` 을 유지했다 — diff 를 원본과 맞대 볼 수 있는 상태가
  이관 직후의 리뷰 가능성보다 값지다. `daengs_place` 등으로의 개명은 안정화 후 선택.
