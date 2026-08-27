---
status: adopted
decision: 65
adopted_at: 2026-08-26
---
# 장소 발견의 최상위 도메인은 Place다 — 원천 분류부터, 의미 제안은 나중

Android 는 `HospitalRepository → HospitalResult` 를 지도 검색의 중심으로 삼고, 웹 검증
표면은 `/dev` 의 병원 검색과 `/facility-map` 의 비의료 시설 검색으로 갈라졌다. 그 결과 같은
사용자 목적(지도에서 갈 곳 찾기)이 표면마다 다른 요청·결과 계약을 사용하고, 시설 기반층에서
검증한 카페·여행·미용·숙박이 Android 로 이어지지 않았다.

이 결정은 테이블을 합치거나 모든 장소를 한 순위에 섞는 결정이 아니다. 제품이 공유할 최상위
어휘와 검색 순서를 먼저 고정한다. 기존 `place`·`facility` 테이블과 의료·비의료 검색 구현은
각 원천의 진실과 정책을 보존한 resolver 로 남을 수 있다.

## 결정

### 1. 제품의 최상위 장소 도메인은 `Place`다

병원·약국·카페·음식점·여행지·미용·숙박은 모두 `Place`다. `facility` 는 KCISA/KTO 기반층과
그 저장 경계를 가리키는 내부 용어이지 제품의 상위 분류가 아니다.

```
Place
├── source classification   원천이 무엇이라고 제공했나
├── facts                   위치·운영·동반조건·인허가 등
├── medical details         의료 원천에만 있는 사실
└── provenance              어느 원천의 언제 값인가
```

공통 외부 계약을 만든다는 것은 검색 SQL과 정책까지 하나로 합친다는 뜻이 아니다.

```
Place search
├── MedicalResolver     MOIS `place` 존재·인허가 권위
└── FacilityResolver    KCISA/KTO `facility` 동반·운영 정보
```

### 2. 검색의 입구는 원천 카테고리에서 정규화한 `kind`다

사용자가 먼저 무엇을 찾는지 고른다. 원천 분류를 결정론적으로 정규화한 `kind`가 1차 후보군을
만들고, 그 안에서 사용자 조건과 결과 순서를 적용한다.

```
원천 category
→ canonical kind
→ 카테고리 후보군
→ 명시한 사용자 조건
→ 후보군 안의 사실 기반 순서
→ 선택적인 의미 제안
```

`kind` 는 이름 추론이나 LLM이 덮어쓰지 않는다. 반려견 놀이터가 원천에서 `travel` 이면
`kind=travel` 을 유지하고, 나중에 이름 근거 태그를 별도로 붙일 수 있다. 정규화 결과에는
원천 category와 mapping version을 남겨 재현 가능하게 한다.

`mapping_version`은 현재 코드의 상수를 무조건 붙이는 라벨이 아니다. 저장된 canonical
`kind`가 해당 버전의 매핑표로 원천 category를 다시 해석한 결과와 같을 때만 노출한다. 매핑을
바꿀 때는 기존 행 전체를 같은 변경에서 재분류해야 하며, adapter는 불일치를 오류로 거부한다.
분류 이력을 여러 버전으로 함께 보존해야 할 시점에는 행별 mapping version을 저장한다.

카페와 병원을 하나의 전역 순위에서 경쟁시키지 않는다. 복수 kind 검색은 호출자가 명시하고,
전체 지도는 통합 순위보다 종류별 선택 레이어로 다룬다.

### 3. `goods` 는 `pet_shop` 과 `shopping` 으로 가른다

현재 `goods` 는 서로 다른 사실을 한 종류로 접었다.

| 원천 | 원천 분류 | canonical kind |
|---|---|---|
| KCISA | 반려동물용품 | `pet_shop` |
| KTO | 쇼핑(content type 38) | `shopping` |

`shopping` 은 이름·위치·원천 category는 있지만 현재 스냅샷에서 동반조건·주차·실내외·운영시간
정보가 거의 또는 전혀 없다. 이를 `pet_shop` 과 분리하면 결측이 어떤 원천 층에서 왔는지
설명할 수 있다.

- 명시적으로 `shopping` 을 고른 검색은 숨기지 않는다.
- 전체 혼합 지도에서는 정보량과 행 수를 근거로 레이어를 기본 비표시할 수 있다.
- 데이터가 없는 조건은 결과를 조용히 제거하지 않고, UI가 `미상`과 정보 부재를 표시한다.
- 이 커버리지는 스냅샷 관찰이다. 고정 숫자가 아니라 응답의 facet summary로 다시 센다.

### 4. 기본 제품 검색은 감사 가능한 사실을 사용한다

사실은 원천이 준 값과 그 값에서 버전 있는 코드로 재현한 결정론적 파생까지다.

- 원천 사실: 위치, category, 주차, 동반조건, 운영시간, 인허가 상태
- 결정론적 파생: 거리, `open_now`, 선택한 개와 크기 제한의 호환 여부
- 사실 아님: 노령견 추천, 걷기 좋음, 사회화에 좋음, 즉흥 방문에 좋음

조건의 상태는 `가능 / 불가 / 미상` 세 가지다. 태그나 필드가 없다는 이유로 `불가`로 읽지
않는다. 사용자가 hard filter를 명시하기 전에는 세 상태를 모두 보존하고, 사실 기반 선호 정렬은
`가능 → 미상 → 불가`처럼 근거가 보이는 순서를 사용할 수 있다.

정렬 응답은 자신이 한 일을 말한다.

```
선호 없음       → sort.type=distance
주차 선호 적용  → sort.type=distance_preferred, applied=[parking], band_m=500
```

결정 #20의 거리 밴드 안 사실 선호 부스트는 유지할 수 있다. 다만 이를 순수 거리순이라고
표시하지 않는다.

### 5. 태그·AI 의미 검색은 선택적 제안층으로 미룬다

태그는 원천 `kind`를 대체하지 않고, 여러 kind를 가로지르는 탐색 재료다. 카테고리 기본 태그,
행 원천 사실, 이름·텍스트 추론은 근거와 신뢰도가 다르므로 구현할 때 분리한다. 상위 `group`을
지금 enum으로 고정하지 않고, 나중에 태그 부분집합으로 정의할 수 있다.

AI와 태그 기반 검색은 현재 제품 검색 권한을 갖지 않는다.

- 기본 결과를 조용히 제외하거나 재정렬하지 않는다.
- 사용자가 적용할 수 있는 조건을 제안한다.
- 사용자가 적용한 뒤에도 사실과 추론 근거를 구분한다.
- LLM은 말에서 조건을 번역할 수 있지만, 결과 집합과 판정은 코드가 만든다(결정 #7).

구체 태그 사전과 점수 튜닝은 사용 데이터가 생긴 뒤 결정한다. 현재는 원문과 provenance를
보존하고 기획 문서만 남긴다.

### 6. 겹치는 웹·Android 장소 발견은 같은 계약을 쓴다

화면 표현과 플랫폼 고유 기능은 달라도 된다. 그러나 같은 장소 발견 의도의 `kind`, 조건 의미,
미상 처리, 결과 identity, 정렬은 같아야 한다.

`/dev` 와 `/facility-map` 은 제품 웹이 아니라 검증 표면이지만, 새 계약이 생기면 그 계약을
검증해야 한다. Android 는 병원 전용 모델을 공통 Place 계약으로 옮긴 뒤 같은 JSON 의미를
소비한다. 산책 foreground service와 Room은 Android 고유 기능이라 이 동등성 범위 밖이다.

## 구현 절단면

이 결정 자체는 동작을 바꾸지 않는다. 다음 변경을 작은 PR로 진행한다.

1. `goods → pet_shop / shopping` 적재·기존 데이터 정규화
2. `PlaceResult`와 `(source, ref)` key·alias·classification provenance 노출
3. 기존 경로와 겹치지 않는 `POST /v2/places/search` 추가 (`kinds` 필수, 거리순부터)
4. `/facility-map`을 새 계약으로 옮기고 facet의 가능/불가/미상 분포 표시
5. 사실 기반 선호 정렬과 정렬 metadata 추가; hard filter는 명시할 때만
6. Android를 Place 계약으로 옮긴 뒤 `MapViewModel`을 Place 기준으로 분리

현재 1~6을 구현했다. `/facility-map`과 Android 기본 장소 화면은 한 번에 canonical kind
하나를 선택해 서로 다른 후보군의 순위를 섞지 않는다. 두 화면 모두 모든 후보를 유지하면서
`compatible / incompatible / unknown` 분포와 명시적 주차 선호의 정렬 순서·사실 커버리지를
표시한다. Android의 기존 병원 대화 화면은 상담 기능으로 임시 유지하며, canonical hospital
카테고리로 장소 발견을 시작했다. `PlaceDiscoveryController`와 `LocationFeedCoordinator`가 검색
수명주기와 화면 위치 구독을 각각 소유하므로 `MapViewModel`은 두 부수효과를 직접 관리하지 않는다.
legacy 병원 검색 제거는 후속으로 진행한다.

기존 `/hospital/search`, `/pharmacy/search`, `/facility/search`, `GET /places/search`는 소비자가
새 계약으로 이동할 때까지 유지한다. HTTP method만 달리해 같은 경로를 legacy와 canonical로
나누지 않는다.

## 기존 결정과의 관계

- 결정 #51의 “장소 데이터 + 이용 과정의 사실 + 위치 인프라” 범위를 구체화한다.
- 결정 #20의 사실 기반 거리 밴드 부스트는 정직한 sort metadata와 함께 유지한다.
- 결정 #52의 공통 순위 의미 권위를 Place 계약까지 확장한다.
- `2026-08-24-culture-base-medical-overlay.md`의 데이터 층(시설 기반·MOIS 의료 오버레이)은
  유지한다. 다만 `/facility`을 제품 최상위로 두는 phase 2는 이 결정의 Place 축으로 대체한다.
- 결정 #61의 프로필 기반 시설 호환은 Place 조건의 결정론적 사실 파생으로 재사용한다.

## 이 결정이 하지 않는 것

- 두 DB 테이블을 하나로 합치지 않는다.
- 의료와 비의료 검색 정책을 하나의 SQL로 합치지 않는다.
- 상위 group·태그 사전·AI 추천 점수를 확정하지 않는다.
- 모든 종류를 한 전역 순위에 섞지 않는다.
- 이 문서만으로 현재 Android에 시설 기능이 구현됐다고 주장하지 않는다.
