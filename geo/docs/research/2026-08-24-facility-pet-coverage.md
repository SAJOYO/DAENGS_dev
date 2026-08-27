# `facility.pet` 커버리지 — 필터로 쓸 수 있나 (2026-08-24)

`scripts/facility_pet_coverage.py` · 원표는 [`facility-pet-coverage/size-values.csv`](facility-pet-coverage/size-values.csv).

병원에서 배운 순서를 그대로 밟는다. 간판 태그는 **커버리지를 재고 나서** 필터에서 뺐다
(활성 5,457곳 중 night 1 · emergency 2 → `WHERE` 금지, 부스트만 —
[name-tagging](../explorations/hospital-search/name-tagging.md)). `pet` 도 같은 질문을 받아야 한다:
**필터로 쓸 수 있나, 부스트로만 써야 하나.**

답은 **병원과 정반대**였다. 쓸 수 있다.

## 표본

측정 시점 DB. KCISA 23,914행 · KTO 9,692행.

> CSV 원본 70,650행에서 23,914행이 된 것은 `(source, source_ref)` UPSERT 때문이다
> (원천에 안정 ID 가 없어 `md5(정규화 이름 + 5자리 좌표)` 로 만든다 — `app/ingest/kcisa.py`).
> 같은 시설이 카테고리별로 여러 행에 나오는 원천 구조가 여기서 접힌다.

## 1. `pet` 은 원천별로 **다른 봉투**다 — 키가 하나도 안 겹친다

| source | rows | allowed | exclusive | size | restrictions | extra_fee |
|---|--:|--:|--:|--:|--:|--:|
| kcisa | 23,914 | 23,914 | 23,914 | 23,914 | 23,914 | 427 |
| kto | 9,692 | 0 | 0 | 0 | 0 | 0 |

KTO 가 주는 키는 전혀 다른 이름이다 (`detailPetTour2` 응답을 그대로 담는다):

```
acmpyTypeCd 246 · etcAcmpyInfo 245 · acmpyPsblCpam 243 · acmpyNeedMtr 242
relaAcdntRiskMtr 148 · relaPosesFclty 15 · relaFrnshPrdlst 2 · relaPurcPrdlst 2 · relaRntlPrdlst 1
```

그리고 KTO 9,692행 중 상세가 붙은 건 **246행(2.5%)** 뿐이다. 나머지는 `pet = {}`.

**이게 지금 계약의 문제다.** `app/api/facility.py` 의 `_BORROWABLE` 에 `pet` 이 들어 있어서,
`pet` 이 빈 KTO 행은 링크된 KCISA 행에서 `pet` 을 통째로 빌린다. 결과적으로 `/facility/search`
응답의 `pet` 필드는 **행마다 KCISA 스키마이거나 KTO 스키마이거나**다. 소비자(앱·챗봇)는
어떤 키가 올지 모른 채 받는다. 원천이 셋이 되면 그대로 셋이 된다.

정규화가 최적화가 아니라 **다원천의 전제 조건**인 이유가 이것이다.

## 2. `allowed`·`exclusive` 는 이미 열거형이다 — 파싱이 필요 없다

| allowed | rows | | exclusive | rows |
|---|--:|---|---|--:|
| Y | 21,120 | | 해당없음 | 23,638 |
| N | 2,794 | | 반려동물 전용 | 276 |

값이 둘뿐이고 채움률 100%다. `_flag()` 가 `parking`·`indoor`·`outdoor` 에 이미 하는 일과 **똑같다** —
같은 CSV, 같은 성격의 컬럼인데 이 셋만 `BOOLEAN` 이 됐고 pet 쪽은 봉투에 남았다.
설계 판단이 아니라 아직 안 한 것에 가깝다.

## 3. `size` 는 100개 고유값이지만 상위 둘이 96%다

| 값 | rows | 비율 |
|---|--:|--:|
| 모두 가능 | 20,184 | 84.4% |
| 해당없음 | 2,789 | 11.7% |
| (구체 제약 98종) | 941 | **3.9%** |

## 4. `allowed` 와 `size` 는 서로 모순되지 않는다

| allowed | size | rows |
|---|---|--:|
| N | 해당없음 | 2,781 |
| N | (구체 제약) | 9 |
| N | 모두 가능 | 4 |
| Y | 모두 가능 | 20,180 |
| Y | (구체 제약) | 932 |
| Y | 해당없음 | 8 |

동반 불가면 크기도 `해당없음`이다 (N 의 99.5%). 어긋나는 건 **21행(0.09%)** 뿐이고 원천 노이즈로 본다.

**여기서 결론이 갈린다.** `해당없음` 2,789곳 중 2,781곳이 `allowed=N` 이라 어차피 동반 불가로 빠진다.
남는 실질 미상은 **8곳**이다. 병원 `open_now` 는 미상이 거의 전부라 "모름을 빼지 않는다"가
결과를 지키는 규칙이었는데, 여기서는 **뺄 미상이 사실상 없다.**

## 5. 구체 제약 941곳 중 93%가 축으로 떨어진다

| | rows | 비율 |
|---|--:|--:|
| kg 숫자 표기 (`10kg 미만`, `5kg 이하`) | 459 | 48.8% |
| 크기 라벨 (`소형`, `소형/중형`, `대형`) | 530 | 56.3% |
| 둘 다 없음 | 66 | 7.0% |

(kg 과 라벨은 겹친다 — `5kg 미만 소형` 112행 등.)

### 잔여 66행은 크기가 아니라 **종(種)** 이다

```
고양이 17 · 개, 고양이 2 · 포유류 특수동물 2 · 해양동물 2 · 어류 2 · 말 2 · 조류/파충류 …
```

원천 컬럼명은 "입장 가능 동물 크기"인데 **크기와 종이 한 칸에 섞여 들어온다.**
개 서비스 입장에서 이건 크기 축의 잔여가 아니라 **별개의 축**이다 — `size` 가 `고양이` 뿐인
17곳은 크기 미상이 아니라 **개가 안 되는 곳**이다.

`app/geo/tagging.py` 의 `dog_ok()` 가 병원 쪽에서 이미 같은 일을 한다 (`cat_only` 태그 제외).
문화시설에도 같은 이름의 축이 필요하다.

## 결론

1. **`pet` 은 축으로 승격할 수 있다.** 병원 태그와 달리 커버리지가 100%이고 값이 열거형이다.
   `allowed`·`exclusive` 는 `WHERE` 에 써도 된다 — 모름을 없음으로 취급하는 문제가 여기선 안 생긴다
2. **`size` 는 필터가 아니라 게이트다.** 96%가 제약 없음/미상이라 결과 집합을 거의 안 줄인다.
   값은 대형견 견주에게 몰려 있다 — 932곳이 "여긴 우리 개가 못 들어간다"를 말한다
3. **`size` 는 두 축으로 갈라야 한다** — 크기(kg·라벨, 93%)와 종(개 가능 여부, 7%)
4. **원문은 버리지 않는다.** 파싱 실패분과 `restrictions` 자유 텍스트는 표시용으로 남는다

설계는 [`explorations/facility/pet-axes.md`](../explorations/facility/pet-axes.md) 로 이어진다.

## 다시 재려면

```bash
uv run python scripts/facility_pet_coverage.py
uv run python scripts/facility_pet_coverage.py --csv docs/research/facility-pet-coverage/size-values.csv
```

원천 스냅샷이 바뀌면 위 숫자는 바뀐다. 이 문서는 2025-03-24 KCISA 스냅샷 + 2026-08-24 KTO 적재
시점의 관찰이며, 뒤 결정이 바뀌어도 본문을 현재형으로 다시 쓰지 않는다.
