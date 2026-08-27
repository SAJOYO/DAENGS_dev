---
status: exploring
implementation: working
---
# `pet` 봉투 → 축 — 자유 텍스트를 필터 가능한 컬럼으로

근거는 [커버리지 측정](../../research/2026-08-24-facility-pet-coverage.md) (2026-08-24).
[결정 #65](../../decisions/2026-08-26-place-first-discovery.md)은 이 축을 `Place` 후보군 안의
결정론적 사실 파생으로 재사용한다. 이 갈래의 status는 새 Place 계약에서 가능/불가/미상과
필터·선호 순서를 실제로 고정할 때 별도로 정한다.

## 왜 필요한가

**하나. 문자열은 축이 안 된다.** "대형견 되는 데만"을 SQL 로 못 쓴다. `pet->>'size'` 에
`소형/중형` · `10kg 미만` · `5kg 미만 소형` · `모두 가능` 이 섞여 온다. `LIKE` 로 때우면
병원 간판 정규식의 재판이다 — 그때 얻은 교훈이 "정규식 태그를 `WHERE` 에 쓰지 마라"였다.

**둘. 원천마다 봉투가 다르고, 게다가 서로 빌려진다.** KCISA 와 KTO 의 `pet` 은 키가 하나도
안 겹친다(측정 §1). 그런데 `app/api/facility.py` 의 `_BORROWABLE` 에 `pet` 이 들어 있어
빈 쪽이 상대 봉투를 통째로 빌린다. 소비자는 어떤 키가 올지 모른 채 받는다. 원천이 셋이 되면
셋이 된다. **정규화는 다원천의 전제 조건이다.**

**셋. 같은 파일에 이미 기준이 있다.** `parking`·`indoor`·`outdoor` 는 `_flag()` 로 `BOOLEAN`
컬럼이 됐다. 같은 CSV·같은 성격인데 `pet` 쪽만 봉투에 남았다.

**넷. 차별화의 오른쪽 항이 여기 있다.** 개 프로필과 매칭할 축(크기·전용·추가요금)이 전부
`pet` 안이다. 축으로 못 뽑으면 개인화가 "거리순 + 실내외"까지고, 거기까지는 지도앱이 이미 한다.

## 축

원문(`pet`)은 **버리지 않는다.** 파생 축을 옆에 둔다 — 파싱이 틀렸을 때 되돌릴 근거가 남고,
`restrictions` 같은 자유 텍스트는 어차피 표시용으로 계속 쓴다.

| 컬럼 | 타입 | 원천 | NULL 의 뜻 |
|---|---|---|---|
| `pet_allowed` | BOOLEAN | `allowed` Y/N | 미상 |
| `pet_exclusive` | BOOLEAN | `exclusive` = `반려동물 전용` | 미상 |
| `pet_dog_ok` | BOOLEAN | `size` 의 종 표기 | 종 표기 없음 (= 개 전제) |
| `pet_size_class` | TEXT | `size` 라벨·kg | 미상 |
| `pet_max_kg` | NUMERIC | `size` 의 kg 숫자 | 숫자 표기 없음 |

`pet_size_class` 는 **입장 가능한 최대 크기**이고 순서가 있다: `small < medium < large < any`.
"대형견 가능한 곳"은 `pet_size_class IN ('large','any')` 다.

### kg → class 문턱값은 잠정이다

```
소형 ≤ 10kg < 중형 ≤ 25kg < 대형
```

국내 통용 기준을 그대로 썼고 **측정으로 정한 값이 아니다.** 산책 계산 문턱값과 같은 성격이라
같은 방식으로 다룬다 — 코드 상수 한 곳(`app/geo/pet.py`)에 두고, 바뀌면 재적재로 다시 파생한다
(`(source, source_ref)` UPSERT 라 재적재가 곧 재계산이다).

### `size` 는 두 축이다 — 크기와 종

원천 컬럼명은 "입장 가능 동물 크기"인데 실제로는 종이 섞여 들어온다(측정 §5): `고양이` 17행,
`포유류 특수동물`, `해양동물`, `어류`, `말`, `조류/파충류`.

개 서비스에서 `고양이` 만 적힌 곳은 **크기 미상이 아니라 개가 안 되는 곳**이다. 원천이 종을
열거하면서 개를 빼놓은 것은 결측이 아니라 **명시적 진술**이라, "모름 ≠ 없음" 규칙의 예외가 아니다.

`app/geo/tagging.py` 의 `dog_ok()` 가 병원에서 이미 같은 일을 한다(`cat_only` 제외).
문화시설도 같은 이름의 축을 갖는다.

## 필터로 쓰는가, 부스트로만 쓰는가

**필터로 쓴다.** 병원 태그와 갈리는 지점이고, 근거는 측정이다:

- `allowed`·`exclusive` 채움률 **100%**, 값 2종 → 모름을 없음으로 취급하는 문제가 안 생긴다
- `size` 의 `해당없음` 2,789곳 중 2,781곳이 `allowed=N` → 실질 미상 **8곳**

병원 `open_now` 는 미상이 거의 전부라 "빼지 않는다"가 결과를 지키는 규칙이었다.
여기서는 뺄 미상이 없다. **같은 원칙을 적용한 결과가 반대로 나온 것**이지 원칙을 바꾼 게 아니다.

단 `pet_size_class` 는 결과 집합을 거의 안 줄인다 — 96%가 제약 없음/미상이다. 대형견 필터가
걸러내는 건 932곳뿐이다. **그래도 그 932곳이 대형견 견주에게는 전부**다. 필터의 가치는
결과를 줄이는 데 있지 않고 "여긴 우리 개가 못 들어간다"를 미리 말해주는 데 있다.

## 구현

| 조각 | 무엇 |
|---|---|
| `alembic/versions/0013_facility_pet_axes.py` | 컬럼 5개 + CHECK. alembic 도입 뒤 첫 신규 리비전이라 `downgrade()` 가 실제로 동작한다 |
| `app/geo/pet.py` | 순수 파생 (`derive_axes`). kg 문턱값 상수도 여기 한 곳 |
| `app/ingest/pet_axes.py` | 저장된 `pet` → 컬럼. 원천 재호출 없음 |
| `app/api/facility.py` | `pet_axes` 응답 + `dog_size`/`only_dog_ok` 필터 |
| `scripts/facility_pet_coverage.py` | 커버리지 재측정 |

```bash
uv run alembic upgrade head
uv run python -m app.ingest pet-axes                     # 축이 빈 행만
uv run python -m app.ingest pet-axes --all --source kcisa  # 문턱값 바꾼 뒤 한 원천만
```

리비전을 추가하면 `app/core/schema_revision.py` 의 `LEGACY_MARKERS` 에도 지표를 넣어야 한다.
`HEAD` 가 그 목록의 끝이라, 빠뜨리면 `detect_schema_revision` 이 뒤처진 DB 에게
"최신이다"라고 말한다 (그래서 `tests/test_schema_revision.py` 가 깨진다).

## 안 하는 것

- **`restrictions` 파싱.** 자유 서술이고 값이 제각각이다. 표시용으로만 낸다
- **`extra_fee` 를 금액으로.** 채움률 1.8%(427행). 유무 판단조차 표본이 모자라 이번엔 축을 안 만든다
- **KTO `acmpy*` 매핑.** 상세가 붙은 게 246행(2.5%)뿐이라 지금 매핑표를 만들면 근거 없이 굳는다.
  KTO 상세 수집률이 오르면 그때 같은 축으로 흡수한다
- **`pet_size_class` 를 순서형 ENUM 으로.** 값이 넷이고 순서 비교는 질의에서 배열 포함으로 푼다.
  타입을 만들면 마이그레이션이 무거워지고, 종 축이 늘어날 때 같이 걸린다
