---
status: adopted
decision: 67
adopted_at: 2026-08-26
---
# 패키지 축은 넷이다 — 최상위 신설은 결정을 거친다

`app/` 최상위가 패키지 13개(+ 에셋 `static/`)가 됐다. 문제는 개수가 아니라
**나누는 기준이 섞인 것**이다.
`core`·`providers`·`usage`·`api` 는 기술 계층이고, `geo`·`place`·`journey`·`profile` 은
도메인이고, `features/*` 는 수직 기능인데, `planning`·`refine`·`scene`·`ingest` 는 또
워크플로·소비자·ETL 이라는 다른 축이다. 이 넷이 한 레벨에 나란히 서 있다.

기준이 없으면 새 개념이 생겼을 때 "이건 어디 들어가지"에 구조가 답을 못 준다. 그래서
최상위를 하나 더 발급하는 쪽으로 흘렀다. `place`·`planning`·`scene` 이 그렇게 생겼다.
`place` 는 결정 #65 를 업고 917줄의 도메인으로 자랐지만 `planning`(602줄)과
`scene`(54줄)은 그렇지 않다 — 최상위 자리를 받을 만큼의 독립된 책임이 아니었다.

증거가 코드에 남아 있다. `app/planning/plans.py:23` 이다.

```python
Companion = str  # journey.models.Companion 과 같은 값. 순환 import 를 피한다
```

타입을 `str` 로 뭉개서 순환을 피하고 있다. 경계가 틀렸다는 것을 코드가 이미 알고 있었고,
구조를 고치는 대신 타입을 버리는 쪽을 택한 기록이다.

이 결정은 기능을 바꾸지 않는다. 패키지 소유권과 import 방향만 정하고, 그 규칙을 테스트로
잠근다.

## 결정

### 1. 축은 넷이다

| 축 | 패키지 | 무엇인가 |
|---|---|---|
| 기반 | `core` | config·db·clock. 아무도 모른다 |
| 인프라 | `providers` `usage` | 외부 세계의 어댑터와 그 정책 |
| 도메인 | `profile` `geo` `discovery` `place` `journey` | 제품 어휘 |
| 응용 | `api` `features` `ingest` `main` | 도메인을 조립해 사용자 기능을 만든다 |

`core` 는 특정 제품 도메인을 모르는 공용 기반만 소유한다. 판정 기준은 **"그 모듈이 어떤
도메인 어휘도 import 하지 않는가"** 이고, 애매하면 core 가 아니다. `clock` 이 여기 있는 이유는
`datetime`·`Protocol` 외에 아무것도 모르면서 네 패키지가 공유하기 때문이다 — 갈 곳이 없어서
온 것이 아니다.

**응용 층이 곧 진입점은 아니다.** 진입점(HTTP·CLI)은 이 층 안에 있지만 전부는 아니다 —
`features/*/api.py` 와 `ingest/__main__.py` 가 진입점이고, `features/scene/judgment.py` 나
`features/walk/facts.py` 는 도메인을 조립한 기능 로직이지 바깥에서 들어오는 문이 아니다.
층을 가르는 기준은 "문인가"가 아니라 **"도메인을 조립하는가"** 이다.

같은 층 안(`features/*` 형제끼리, `api` ↔ `features`)의 의존은 방향 규칙이 아니라 **DAG
규칙**을 받는다 — `features.scene → features.walk`(소비자 → 생산자)는 허용이고 그 역은 아니다.
`features` 를 한 점으로 보면 형제 순환이 자기 자신으로 접혀 사라지므로, 방향 테스트는 형제를
갈라서 잰다.

`ingest` 는 인프라가 아니라 **batch 응용**이다. HTTP 가 `api`·`features` 로 들어오듯
`python -m app.ingest` 로 들어온다. `ingest/anchors.py:29` 가 `geo.cells` 를 쓰는 것은
위반이 아니라 응용이 도메인을 조립하는 정상 동작이다. 그래서 `ingest` 는 최상위에 남고
`place` 밑으로 내려가지 않는다.

### 2. 화살표는 응용 → 도메인 → 인프라 → core 한 방향

**이것은 clean architecture 가 아니다.** 도메인이 인프라를 직접 안다 —
`geo/search.py:18` 이 `providers.base` 의 `LatLng`·`MapMarker` 를,
`journey/models.py:12` 가 `Mode`·`RouteStatus` 를 가져간다. 이 규모에서 port/adapter 를
끼우는 것은 과잉이다. 실용적 layered 구조이며, 문서에서 "도메인은 인프라와 무관하다" 같은
말은 하지 않는다.

### 3. 계약 모듈은 선언된 잎이다

패키지 간 역방향 에지는 **계약 모듈로 향하는 것만** 허용한다. 새 계약은 `*/contract.py` 로
만들고, 계약 모듈은 `core` 와 다른 계약 모듈만 import 하며 로직을 담지 않는다.

`providers/base.py` 는 이미 `MapProvider`·`Mode`·`RouteStatus` 등 제공사 계약을 소유한
**기존 계약 모듈**로 선언한다. 파일명만 `base.py` 일 뿐 같은 규칙을 적용한다. PR 5의
import-direction 테스트는 파일명 패턴이 아니라 이 선언된 계약 모듈 집합을 기준으로 검사한다.

계약의 주인은 그것을 **실행하는 쪽**이다. 지금 `planning/plans.py` 에 세 실행자의 입력이
한 파일에 섞여 있다.

```
SearchPlan · SearchMust · SearchPrefer   → geo.search 가 실행     → geo/contract.py
JourneyPlan · WalkPlan · Companion       → journey.engine 이 실행 → journey/contract.py
RuntimeFacts · ViewPlan                  → resolver 의 입력·출력  → discovery 에 남는다
```

`RuntimeFacts` 는 `now`·`DogProfile`·`OwnerProfile`·`temp_c`·`urgency_signals` 다.
이번 요청에 대해 서버가 아는 것이지 공간 어휘가 아니므로 `geo` 로 보내지 않는다.
`Companion` 은 `Literal["dog", "none"]` 이고 "이번 이동에 개가 동반하는가"를 뜻한다.
프로필 종류가 아니므로 `profile` 로 보내지 않는다 — `none` 프로필은 없다.

이 규칙으로 `discovery ↔ journey` 의 양방향 패키지 참조를 허용 가능한 모듈 방향으로
분해했다 — `discovery.resolver` 가 `journey.contract` 를 향하는 역방향은 contract 이므로
허용이고, 반대 방향은 `journey.api` 가 `discovery` 를 보는 순방향이었다.

**PR 6 이후 그 쌍은 아예 사라진다.** `journey → discovery` 에지는 전부 `journey/api.py`
세 줄이었고, 그 파일이 §4 에 따라 `features/journey/` 로 나가면서 `journey` 는 `discovery`
를 모르게 됐다. 남는 것은 `discovery → journey.contract` 단방향뿐이라, **양방향 쌍을 정당화하기 위한
예외 설명이 더는 필요 없다.** `journey.contract` 의 계약 지위와 §3 제약은 그대로다 —
선언된 계약 모듈 집합에서 빼지 않는다. 규칙 집행이 구조를 한 단계 더 정리한 경우다.

패키지 이름만으로 순환 여부를 판정하지 않고 모듈 단위 import-direction 테스트로 이 규칙을
잠근다.

### 4. HTTP 엔드포인트 소유

```
features/*/api.py   사용자 워크플로 — 편집·기록·행동을 동반하는 것
app/api/*           공용 조회 어댑터 — places · anchor · static_map
```

`app/api` 를 없애는 것은 목표가 아니다. `place` 는 resolver·contracts 를 가진 도메인
패키지라, 조회 엔드포인트를 `features/place` 로 승격하면 place 가 두 곳으로 찢어지거나
도메인과 기능의 경계가 다시 흐려진다. 목표는 폴더 제거가 아니라 **어떤 엔드포인트가
feature 인가**의 규칙이다.

`main.py:11·14-17` 이 지금 세 곳에서 라우터를 가져온다. 이 규칙을 적용하면
`journey/api.py` 하나가 위반이었다 — 도메인 패키지 안의 워크플로 엔드포인트다.
**PR 6 에서 `features/journey/api.py` 로 옮겨 해소했다.** 세 곳에서 오던 라우터는
이제 `api/*`(공용 조회)와 `features/*/api.py`(워크플로) 둘로 정리됐다.

### 5. 최상위 패키지 신설은 결정 문서를 거친다

새 최상위는 위 네 축 중 하나에 속함을 증명해야 만들 수 있다. 이 조항이 없으면 정리 직후
다시 13 → 14 → 15 로 는다.

관할의 단위는 패키지 디렉토리가 아니라 **`app/` 아래에서 `.py` 를 담는 모든 최상위**다 —
최상위 모듈(`main.py`)과 `__init__.py` 없는 디렉토리(namespace package 는 그래도 import
된다)를 포함한다. 단위를 패키지로 좁히면 규칙 밖에 세 우회로가 생긴다: 진입점으로 들어오는
역방향 에지가 스캔에서 사라지고, 층 배정 없는 잡동사니 모듈이 자라고, `__init__.py` 를 빼는
것이 관할 회피 수단이 된다. 방향 테스트의 `_units()` 가 이 정의를 집행한다.

## 목표 구조

```
app/
├── core/          config · db · clock                  아무도 모름
├── providers/     base(계약) · kakao · naver · tmap · fake
├── usage/         gate · policy · ledger · metered · composition · http
├── profile/
├── geo/           contract.py(SearchPlan 계열) · search · cells · ranking …
├── discovery/     state · facts · semantics · resolver · trace
│   └── refine/    engine · tools · diff · actions · labels · nl(parked)
├── place/
├── journey/       contract.py(Companion · WalkPlan · JourneyPlan) · engine · models …
├── features/      hospital/ · pharmacy/ · walk/ · scene/ · territory/ · journey/
├── ingest/        batch 응용 — __main__ 이 진입점
├── api/           공용 조회 어댑터
└── main.py        HTTP 진입점 조립
```

`planning` 과 `refine` 은 다른 bounded context 가 아니라 한 컨텍스트를 읽기/쓰기로 찢어
놓은 것이다. README 가 이미 `refine/` 을 "state(target/journey/view) · tools · nl · diff"
한 덩어리로 서술하는데 `state.py` 는 `planning/` 에 있다. 합치고 `discovery` 로 부른다 —
`search` 가 아닌 이유는 `EditableState` 에 `journey`·`view` 가 함께 들어 있고
`/journey` 도 같은 resolver 를 쓰기 때문이다. 결정 #65 의 place-first discovery 와도
어휘가 맞는다.

`territory`(`paint` · `region` · `layers`)도 같은 이유로 `features/` 로 내려간다. 셋 다
`features.walk.facts.Segment` 를 소비하는데 `geo/` 에 앉아 있어서 `geo → features` 역방향이었고,
그 두 줄이 `discovery → journey → geo → features → discovery` **4-패키지 순환을 닫는 마지막
고리**였다. `paint`·`region` 만 옮기면 `geo.layers → features` 가 새로 생기므로 셋을 한 소비
기능으로 본다. `geo/` 에는 산책을 모르는 공간 원시(`cells.py`)만 남는다.

`scene` 은 `features/` 로 내려가되 `walk` 와 **형제로 남는다.** `features/walk/__init__.py`
가 "수집한다. 판정·보상·서술·알림은 하지 않는다"고 못 박은 경계는 결정 #51 의 산물이다.
54줄이라고 `walk/judgment.py` 로 넣으면 파일은 줄지만 생산자와 소비자의 경계가 흐려진다.

### provider + usage 조립의 현재 위치

초안은 provider 생성과 Usage Gate 래핑을 최상위 `app/wiring.py` 로 옮기려 했다. 실제 호출
그래프를 따라가니 `journey.engine` 이 `route_provider()` 를 직접 당기고 있었다. 이 상태에서
조립을 응용 층인 `wiring.py` 로 옮기면 도메인 → 응용 역방향 import가 생긴다.

그래서 PR 1은 **현재 이미 존재하는 방향**인 `usage → providers` 를 따라
`usage/composition.py` 에 metered provider 조립을 둔다. `providers.registry` 는 raw provider
선택만 담당하고 usage를 모른다. 이것은 최상위 composition root가 아니다.

진짜 composition root는 도메인이 provider를 직접 당기지 않고 주입받도록 바꾸는 별도 결정
이후에만 만든다. 그 전에는 이름만 `wiring.py` 인 파일을 만들기 위해 화살표를 뒤집지 않는다.

### 알려진 방향 위반 — `features ↔ geo`

PR 1에서 전체 import 그래프를 다시 검사해 추가 위반을 찾았다. `geo/paint.py`와
`geo/region.py`가 `features.walk.facts.Segment`를 import 한다. 현재 축 정의대로면 도메인이
응용을 아는 방향이라 위반이다.

여기서 **`Segment`를 어디로 옮긴다고 선결정하지 않는다.** `Segment`는 `WalkFix`·moving·
chain_index 같은 산책 어휘를 가진 타입이라 단순히 `geo`로 내리는 것도 소유권을 왜곡할 수
있다. PR 5의 방향 테스트를 넣기 전에 `features.walk`의 경계와 `paint/region`의 실제 소유권을
다시 검증해 해결한다. 이 결정이 고정하는 것은 "현재 위반이 존재한다"는 사실까지다.

## 이행 순서

| PR | 내용 | 완료 조건 |
|---|---|---|
| 1 | `providers.registry` 의 게이트 조립 → `usage/composition.py` | `git grep 'from app.usage' app/providers/` 0건 |
| 2 | 계약 소유권 분할 (§3) + 공용 시간 원천 `core.clock` 이동 | `plans.py:23` 의 `Companion = str` 삭제 **그리고** `git grep 'app\.planning' app/geo` 0건 |
| 3 | `planning` + `refine` → `discovery` | `git grep 'app\.planning\|app\.refine' -- . ':!docs/decisions/'` 0건 |
| 4 | `scene` → `features/scene` | `git grep 'app\.scene\|app/scene' -- . ':!docs/decisions/'` 0건 |
| 5 | import 방향 테스트로 잠금 | `tests/test_import_direction.py` — 다섯 게이트가 각각 **일부러 실패시켜** 잡히는 것을 확인 |
| 6 | API 소유 집행 (§4) — `journey/api.py` → `features/journey/` | `app/journey/api.py` 부재 **그리고** `git grep 'from app\.journey import api'` 0건. 덤으로 `git grep 'from app\.discovery' -- app/journey` 0건 |

PR 2 에 `Clock` 이 함께 들어가는 이유: 계약만 옮기면 `geo → planning` 에지가 하나 남아
(`geo/search.py` 의 `SystemClock`) 순환이 안 풀린다. 착수 전 그래프 시뮬레이션으로 확인했다.
소유권으로 봐도 같은 종류의 작업이다 — `planning/facts.py` 에 planning 개념을 모르는 시간
원천과 진짜 planning 어휘(`RuntimeFacts`)가 섞여 있었고, 넷이 이미 밖에서 꺼내 쓰고 있었다.

순서가 강제인 이유: 계약이 먼저 빠져야 3의 diff 가 순수 이동으로 읽힌다.

**방향 테스트는 위반이 0 이 될 때까지 기다리지 않는다.** 초안은 그렇게 적었지만 틀렸다 —
남은 부채를 `KNOWN_VIOLATIONS` 에 **정확히 일치**하도록 박으면 지금 넣을 수 있고, 그러면
여기까지 끊어낸 것이 즉시 보호된다. 목록은 새 위반이 생겨도 실패하고 **고친 것을 안 지워도
실패한다** — 부채가 사라진 것을 고친 사람이 기록하게 만드는 쪽이다.

모든 PR 은 **행동 변화가 0**이다. 테스트가 import 경로 갱신 외의 수정 없이 통과하는 것이
각 PR 의 기본 완료 조건이고, 테스트 본문을 고쳐야 통과한다면 이동에 변경이 섞인 것이다.

### 검증 게이트

`pytest` 는 실행되지 않는 CLI·문서·문자열 module path 를 보지 못한다. 이번 조사에서
`ingest/anchors.py` 가 import 그래프 분석에서 빠졌던 것과 같은 사각지대다.

`docs/decisions/` 를 제외하는 이유: 결정문의 옛 경로는 **역사 서술**이다. "plans.py 의
`Companion = str` 을 지웠다" 같은 문장의 경로를 현재 이름으로 바꾸면 그 문장이 거짓이 된다.
역사는 남기고, 검증 범위가 역사를 밟지 않게 게이트 쪽에서 비켜 간다.

게이트를 테스트로 승격할 때는 **다섯 개를 각각 일부러 실패시켜** 실제로 잡히는지 본다.
이 규칙을 처음 코드로 옮길 때 두 개가 거짓 통과였다 — 계약 잎 검사는 패키지 **내부** import
를 훑지 않아 `geo/contract.py` 가 `geo/ranking.py` 를 부르는 것을 못 봤다. 그냥 초록을 믿고
머지했으면 §3 의 절반이 처음부터 빈 껍데기였다.

게이트 자체도 틀릴 수 있다. `grep -E` 에서 `\|` 는 alternation 이 아니라 **리터럴 파이프**라
`from app\.(geo\|place)` 는 아무것도 못 찾는다 — 0건이 "깨끗하다"가 아니라 "검사가 안 돌았다"가
된다. PR 5 가 이 명령들을 테스트로 승격할 때 정규식을 그대로 옮기지 말고 각각 **일부러 실패하는
입력**으로 한 번 확인한다.

```
uv run pytest -q
uv run ruff check .
python -m compileall -q app
uv run python -m app.ingest --help
git grep -n 'app\.planning\|app\.refine\|app\.scene' -- . ':!docs/decisions/'
git grep -En 'from app\.(geo|place|journey|planning|profile|features|providers|usage)' -- app/core
```

## 하지 않는 것

- **port/interface 분리** — §2. 이 규모에서 과잉이다
- **`app/api` 소멸** — §4. 규칙이 목표이지 폴더 제거가 목표가 아니다
- **`usage` 를 `providers` 밑으로** — usage 는 "돈 드는 외부 호출의 정책·계량"이고 지도
  provider 전용이 아니다. `refine/nl.py`·`journey/engine.py`·`features/hospital/api.py`·
  `api/static_map.py` 가 직접 쓴다. providers 가 usage 의 부모가 될 관계가 아니다
- **`RuntimeFacts` → `geo`, `Companion` → `profile`** — §3. 순환만 보고 옮기면 새 구조가
  처음부터 거짓말을 한다
- **`features.walk.Segment` 의 목적지 선결정** — 방향 위반은 확인했지만 소유권은 PR 5 전에
  별도로 검증한다
- **패키지 병합으로 개수 줄이기** — 9개인데 소유권이 틀린 것보다 11개라도 기준이 명확한
  편이 낫다
