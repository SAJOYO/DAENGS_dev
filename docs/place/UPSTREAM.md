# 출처와 소유권

```
origin:            rkbuhtig/DAENGS_geo
source commit:     c5f0d5f738410e90cac294fd5f407cd88f5330ac  (Place, 2026-08-29)
territory source:  4d384106a52c814faf006e4b033354f7d4e686fd               (main, 2026-09-03)
핵심 선행 PR:      #148  검색은 identity가 아니라 값을 받는다 (dog_id 제거, extra="forbid")
                   #149  검색 전용 진입점 — provider/LLM 키 없이 PostGIS만으로 부팅
경계 결정:         이 저장소 [docs/decisions.md](../decisions.md) D-026
```

**운영 Place 검색의 canonical 구현은 이 저장소(SAJOYO/DAENGS_dev)다.**
DAENGS_geo는 검색 실험을 계속할 수 있지만 그 변경이 자동으로 운영 코드가 되지는 않는다.
검증된 실험은 아래처럼 source commit과 포함·제외 범위를 고정한 promotion PR로만 가져오며,
승격 뒤의 운영 수정은 이 저장소에서 한다.

Source facts와 typed planning 이후 자연어 발견 기능의 코드·프로세스·공개 계약 경계와
단계별 승격 순서는 [discovery-migration.md](discovery-migration.md)에 고정한다.

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

- `src/daengs_place/core/config.py` — 지도/route/deeplink 설정은 제거하고 검색·적재 필드만
  유지했다. PR #195부터 Place 내부 discovery가 쓰는 optional Gemini 필드만 추가했으며,
  keyless boot와 기존 공개 검색은 계속 보장한다(파일 docstring 참고).
- `tests/place/support/database.py` — walk/journey/provider 팩토리를 뺀 발췌.
  최초 이관의 `conftest.py`에서 일반 도구를 분리한 위치이며 DB 동작은 유지한다.
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

## Typed search planning 승격 기준점 (2026-09-03)

```
promotion source:  rkbuhtig/DAENGS_geo
source merge:      e3aea61aa7f0c12f041a9b0fb7c1874eb4df44f3
target PR:         SAJOYO/DAENGS_dev #184
stacked parent:    SAJOYO/DAENGS_dev #183
```

외부 검색 요청과 미래의 자연어 intent가 같은 실행기를 사용하도록, 검색 조건을 먼저
검증 가능한 `PlaceSearchPlan`으로 컴파일하는 내부 경계를 승격했다.

- 포함: 정적 capability catalog, gate/compiler/guard, 목적 catalog, typed intent planner,
  plan 실행 adapter, 후보 손실과 source evidence 상태를 계산하는 preview
- 실제 연결: 현행 `PlaceSearchRequest`도 내부 plan으로 컴파일한 뒤 기존 resolver를 호출한다.
  preview는 선호 적용 전 spatial 후보를 최대 1,000개 읽고 #183의 source-fact bundle을
  후보 identity와 정확히 대조한다.
- 호환성: `/v2/places/search`의 요청·응답과 정렬 의미는 바꾸지 않는다. planning과 preview는
  아직 내부 계약이며 새 HTTP endpoint를 만들지 않는다.
- 권한 경계: plan은 구조화된 값만 실행한다. 실행 직전 guard가 capability별 허용 mode,
  origin, unknown policy와 locked gate 불변식을 다시 검사하므로 LLM 출력이 곧 필터가 되지 않는다.
- 의도적 제외: Gemini/OpenAI proposer, 복수 hypothesis/lens/refinement,
  presentation assembly, 운영 discovery HTTP, main orchestration과 Android 연결.

## Provider-free intent core 승격 기준점 (2026-09-03)

```
promotion source:  rkbuhtig/DAENGS_geo
source head:       3ff268a17d85fd0b641396c213ad8706a2f5bf40
target PR:         SAJOYO/DAENGS_dev #189
```

자연어 모델이 제안한 값을 곧바로 검색하지 않도록, provider 호출보다 먼저 Place 소유의
의미 계약과 결정론적 정규화 계층을 승격한다.

- 포함: authority-free intent 계약과 원문 evidence grounding, 복수 hypothesis,
  open-discovery 정책, suggestion/lens, refinement/confirmation, provider-neutral prompt/schema,
  proposer 주입형 service
- 대상 구조: Geo의 범용 `app.discovery.place_intent`를 복제하지 않고
  `daengs_place.place.intent` 아래에 둔다. intent와 후속 presentation이 공유하는
  `InformationNeedId`는 `daengs_place.place.information_needs`로 분리한다.
- 운영 정합성: Geo 최신 planner와 달리 현재 운영 planner는 blocking semantic target을
  `UNSUPPORTED`로 분류한다. product fallback 후보는 그대로 제공하되 거절된 원 해석의
  상태를 다시 쓰거나 운영 planner를 이 PR에서 변경하지 않는다.
- 경계: intent core는 provider SDK, HTTP, FastAPI, SQLAlchemy, presentation 구현을 import하지
  않는다. 기존 Place 앱 import closure와 네 공개 경로는 변하지 않는다.
- 의도적 제외: Gemini/OpenAI adapter, usage/metering, lab/관측 DB, presentation과 discovery
  assembly, HTTP endpoint, main orchestration, Android 연결.

## Presentation 승격 기준점 (2026-09-03)

```
promotion source:  rkbuhtig/DAENGS_geo
source head:       3ff268a17d85fd0b641396c213ad8706a2f5bf40
target PR:         SAJOYO/DAENGS_dev #190
```

검색 후보를 단순 필드 묶음이 아니라 사용자 판단에 필요한 사실·한계·출처로 표시하기 위해
Place 내부 presentation 계층을 승격한다.

- 포함: source-neutral 표시 계약, information-need catalog, core/promoted/detail 배치 정책,
  KTO/KCISA source-fact bundle과 검색 hit를 결합하는 assembler
- 단일 타입 소유권: Geo의 `presentation.needs.InformationNeedId` 정의는 복제하지 않는다.
  PR2에서 분리한 `daengs_place.place.information_needs.InformationNeedId`를 catalog와 intent가
  함께 사용한다.
- 정합성: assembler는 검색 hit와 source-fact bundle의 `PlaceRef`가 같을 때만 조립하고,
  사실의 known/unknown/conflicting 상태와 primary/supporting provenance를 그대로 보존한다.
- 경계: presentation은 provider SDK, HTTP, FastAPI, SQLAlchemy, intent 구현을 import하지
  않는다. 기존 `/v2/places/search` 응답과 Place 앱의 공개 경로는 변하지 않는다.
- 의도적 제외: discovery assembly, Gemini adapter, 내부 endpoint, main orchestration,
  관측 migration과 Android 연결.

## Internal discovery assembly 승격 기준점 (2026-09-03)

```
promotion source:  rkbuhtig/DAENGS_geo
source head:       3ff268a17d85fd0b641396c213ad8706a2f5bf40
target PR:         SAJOYO/DAENGS_dev #192
```

분리해 승격한 intent·planning·source facts·presentation을 Place 내부 응용 서비스에서 처음
연결한다. 외부 transport를 붙이기 전 결정론적 실행 경계와 결과량을 고정하는 단계다.

- 포함: `PlaceDiscoveryRequest`, provider trace를 제거한 planning projection, lens별 검색과
  source-fact 일괄 읽기, 동일 identity presentation 조립
- 대상 adaptation: Geo의 `orchestration_bridge.py`와 `PlaceCapabilityInput` 이름을 복제하지
  않는다. `daengs_place.place.discovery`가 계약과 `PlaceDiscoveryService`를 직접 소유한다.
- 결과 정책: 기본 3 lens·lens당 5건·전체 15건·128 KiB, 하드 상한 3·10·20·256 KiB.
  검색 전에 lens와 후보 예산을 배분하고, byte 상한에는 뒤쪽 후보부터 줄이며 notice를 남긴다.
- 경계: provider SDK, HTTP/FastAPI, `daengs_backend` 오케스트레이션을 import하지 않는다.
  기존 Place 앱의 네 공개 경로와 `/v2/places/search` 계약은 변하지 않는다.
- 의도적 제외: Gemini adapter/config, 내부 endpoint, main orchestration, 관측 migration,
  공개 capability projection과 Android 연결.

## Internal discovery API·Gemini 승격 기준점 (2026-09-03)

```
promotion source:  rkbuhtig/DAENGS_geo
source head:       3ff268a17d85fd0b641396c213ad8706a2f5bf40
target PR:         SAJOYO/DAENGS_dev #195
stacked parent:    SAJOYO/DAENGS_dev #192
```

PR #192의 순수 discovery 조립을 컨테이너 네트워크의 내부 HTTP 경계와 연결한다. Geo holdout이
평가한 Gemini Interactions의 stateless structured-output transport를 유지하므로 provider
transport 변경에 따른 holdout 재실행은 필요하지 않다.

- 포함: `POST /internal/place/discovery`, Place 소유 Gemini adapter, optional key/model/timeout
  설정, request-time service/client 조립, provider 장애의 제한된 HTTP 오류 projection
- 대상 adaptation: Geo의 lab·usage/metering·관측 DB는 가져오지 않는다. HTTP 입력에서는
  `result_policy`를 받지 않고 PR #192의 서버 기본 예산만 주입한다.
- 실패 계약: 키 없음 503, provider timeout 504, HTTP·interaction envelope 실패 502.
  Geo lab은 모든 `httpx` 실패를 502로 묶지만 대상 레포의 기존 timeout 계약에 맞춰 504를
  분리했다. structured schema 불일치는 raw 없이 `needs_clarification` 결과로 반환한다.
- 공개 경계: place-search는 host port가 없고 nginx는 `/internal/`을 라우팅하지 않는다.
  기존 네 공개 경로와 `/v2/places/search` 계약은 변하지 않는다.
- 의도적 제외: `daengs_backend` capability adapter/projection, 전역 semantic router,
  provider 관측 저장소, Android 연결.
