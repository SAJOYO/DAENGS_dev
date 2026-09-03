# 출처와 소유권

```
origin:            rkbuhtig/DAENGS_geo
source commit:     c5f0d5f738410e90cac294fd5f407cd88f5330ac  (main, 2026-08-29)
핵심 선행 PR:      #148  검색은 identity가 아니라 값을 받는다 (dog_id 제거, extra="forbid")
                   #149  검색 전용 진입점 — provider/LLM 키 없이 PostGIS만으로 부팅
경계 결정:         이 저장소 docs/decisions.md D-026
```

**운영 Place 검색의 canonical 구현은 이 저장소(SAJOYO/DAENGS_dev)다.**
DAENGS_geo는 검색 실험을 계속할 수 있지만 그 변경이 자동으로 운영 코드가 되지는 않는다.
검증된 실험은 아래처럼 source commit과 포함·제외 범위를 고정한 promotion PR로만 가져오며,
승격 뒤의 운영 수정은 이 저장소에서 한다.

## 무엇을 가져왔나

- `src/daengs_place/` — `main` 진입점의 import closure 전체(api/core/geo/place) + 장소
  적재 배치(`ingest`, anchors 제외 — 그건 walk/territory 축이다)
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

## Source facts 승격 기준점 (2026-09-03)

```
promotion source:  rkbuhtig/DAENGS_geo PR #217
source head:       3ff268a17d85fd0b641396c213ad8706a2f5bf40
source merge:      e3aea61aa7f0c12f041a9b0fb7c1874eb4df44f3
target PR:         SAJOYO/DAENGS_dev #183
```

검색 결과를 꾸미기 전에 KTO와 KCISA가 실제로 제공한 사실을 잃지 않는 기반을 먼저
승격했다. 제품 검색용 `facility`는 필터·연결·보강 결과이고 원천 자체가 아니므로,
`facility_source_record`에 목록 원문과 상세 획득 상태를 별도로 보존한다.

- 포함: KTO/KCISA shadow ingest, KTO detail 획득 lifecycle, 원천별 순수 projection,
  후보별 variant/conflict bundle, 최대 1,000개 후보를 한 번에 읽는 내부 reader
- 호환성: 기존 `/v2/places/search` 응답은 그대로이며 새 fact 계약은 아직 내부 전용이다.
  `daengs_place`의 PostGIS-only 부팅과 `daengs_backend` 비의존 경계도 유지한다.
- 기존 데이터: KTO `facility.raw`는 0022에서 backfill한다. 과거의 빈 `pet` 값은 원인을
  복원할 수 없으므로 `unknown`으로 기록한다. 과거 KCISA 제품 행에는 CSV 원문이 없어
  추측해 채우지 않고 다음 KCISA snapshot부터 shadow를 만든다.
- 의도적 제외: Gemini/OpenAI proposer, intent/lens, presentation/assembly, lab UI,
  intent 관측 migration(Geo 0025~0027·0031), orchestration 및 외부 HTTP 계약.
  이들은 source facts를 소비하는 후속 promotion PR에서 각각 경계를 검증한다.
- 동기화 방식: Geo 코드를 runtime import하거나 subtree로 연결하지 않는다. fixture와
  parser 기대값을 함께 복사한 뒤 이 저장소의 고정 테스트가 운영 동작을 소유한다.
