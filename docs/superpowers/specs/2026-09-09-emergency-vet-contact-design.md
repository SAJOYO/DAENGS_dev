# 응급 동물병원 연락 — `vet_contact` 설계

```
상태:        설계 승인됨 (구현 전)
날짜:        2026-09-09
사람 결정:   3건 (아래 §2)
영향 범위:   daengs_backend/orchestration — 능력 1개 추가
             place-search 변경 없음 (순위 부스트를 후속으로 미룬 결과)
```

## 0. 이 카드가 열린 경위

질문은 이것이었다 — **"위급 상황에서 특정 질병 수술을 잘하는 병원을 추천하려면,
카카오맵·구글맵 후기나 인터넷 정보를 모아 쓰는 게 낫지 않나?"**

기각했다. 근거는 §1 에 있다. 대신 열린 것이 이 카드다: 실력이 아니라 **도달**을 다루고,
사실이 없는 자리에서는 **전화**로 넘긴다.

## 1. 조사 — 왜 후기가 아닌가, 그리고 왜 야간 축도 지금은 못 세우는가

### 1-1. 후기·평판은 이 저장소가 이미 한 번 지운 축이다

`geo/contract.py:37` —

> 같은 재료를 쓰던 과목 축은 아예 없앴다 (#64) — 한국 수의 진료에 과목 제도가 없어서
> 태그가 자격이 아니라 상호였다. **신뢰도가 낮은 것과 존재하지 않는 것은 다른 처분을 받는다.**

"이 수술을 잘하는 곳"은 정확히 그 지워진 축이다. 신뢰도가 낮은 데이터는 더 모으면
나아지지만 **없는 제도는 데이터를 모아도 생기지 않는다.** 후기를 아무리 모아도 없는
자격을 상호와 평판으로 추정하게 될 뿐이다.

후기가 재는 것은 응대·주차·가격·대기시간이지 수술 성적이 아니다. 어려운 케이스를 받는
병원일수록 나쁜 결과와 나쁜 후기를 받으므로 **역상관 위험**이 있다.

부수 사유: 카카오맵·구글맵 후기는 ToS 상 스크래핑 금지이고, 실명 의료기관의 비교·순위
표시는 의료광고 규제 영역이다. 이 저장소가 MOIS·KCISA 같은 공공 원천만 쓰는 것은
`docs/place/UPSTREAM.md` 가 원천 소유권을 명시적으로 관리하기 때문이다.

### 1-2. "지금 응급을 받는가"의 상시 공개 원천은 존재하지 않는다 (2026-09-09 조사)

| 원천 | 실제 내용 | 판정 |
| --- | --- | --- |
| [행안부 동물병원 조회서비스](https://www.data.go.kr/data/15154952/openapi.do) | 지금 우리가 쓰는 것. 이름·주소·전화·좌표·영업상태 | 진료시간·응급 **없음** |
| [animal.go.kr 동물병원 목록](https://www.animal.go.kr/front/awtis/shop/hospitalList.do?menuNo=6000000002) | 병원명·전화·소재지·인허가번호 4개. *"지자체에서 인허가된 사항으로 1일 1회 자동연동"* 이라고 화면에 명시 | 위와 **같은 원천** |
| [서울시 동물병원 인허가 정보](https://data.seoul.go.kr/dataList/OA-16007/S/1/datasetView.do?tab=A) | 지자체 인허가 | 위와 **같은 원천** |
| [animal.go.kr 24hospitalList](https://www.animal.go.kr/front/awtis/loss/24hospitalList.do?menuNo=9000000003) | 진료시간 + *"야간응급진료 21:00~익일10:00"* 같은 시간대까지 실재. 전국 753건 | **명절 연휴 한정**, 다운로드·OpenAPI 없음 |
| [animalclinicfee.or.kr](https://animalclinicfee.or.kr/) | 시군구별 최저·최고·중간·평균 진료비 | 축이 다름. 병원별 공개는 [2026 하반기 추진 중](https://www.mafra.go.kr/bbs/home/792/566998/artclView.do) |

**정부 시스템조차 24시 병원을 상호명으로 추정한다.** animal.go.kr 에서 24시 병원을 찾는
공식 방법은 병원명 검색창에 `24` 를 넣는 것(`searchCoNm=24`)이고, 이는 우리
`geo/tagging.py:12` 의 `re.compile(r"24\s*시|24시간|24H")` 와 같은 일이다. 우리 태그의
신뢰도가 낮은 것은 구현 문제가 아니라 **원천이 없어서**다.

### 1-3. 세 재료 중 지금 있는 것은 거리 하나뿐이다

- **"지금 여는가"** — `ingest/mois_store.py:21` 의 UPSERT 가 `is_night=false, is_24h=false,
  hours=NULL` 을 **무조건** 쓰고 ON CONFLICT 절에도 그 세 칼럼이 없다. 따라서
  `geo/search.py:75` 의 `is_open_at(place.hours, ...)` 는 **모든 동물병원에서 항상 미상**이다.
  간판에 "24시"가 박혀 `tags` 는 붙어도 `is_24h` 불리언은 false 다.
- **"응급을 받는가"** — `geo/contract.py:35` 실측(2026-08-20): 활성 병원 5,457곳 중
  night 1 · emergency 2.
- **"가까운가"** — 있다. PostGIS 거리순.

`24h` 태그 개수는 **이 저장소 어디에도 기록이 없다.** 근거로 참조된
`docs/explorations/hospital-search/name-tagging.md` 는 이관 대상이 아니어서 여기 없다.

## 2. 사람 결정 (2026-09-09)

1. **목표는 실력이 아니라 도달이다 — 전화 우선, 헛걸음 방지.** 앱은 "어디가 잘한다"를
   말하지 않고 가까운 순 + 전화번호를 준다. 새 원천 0개.
2. **응급 판정은 결정론적 사전 게이트와 명시 신호 둘 다로 한다.** 게이트는 좁게 두고,
   좁아서 새는 부분은 명시 경로가 받는다.
3. **야간 분기는 문구만 넣고 순위 부스트는 `24h` 실측 뒤로 미룬다.** 그 결과
   place-search 계약을 이번에 건드리지 않는다.

## 3. 설계 ① — 응급 게이트

**위치.** `planner.py` 의 결정론적 층 맨 앞. 순서는
**응급 게이트 → 명시 신호(`resolve_deterministic_route`) → 의미 라우터**다.
`service.py:124` 에서 보듯 명시 신호가 이미 LLM 호출 전에 끝나므로, 같은 자리에 두면
응급 경로가 **모델 호출 0회**로 끝난다.

**두 진입점이 한 함수로 들어간다.** 어휘 게이트가 참이거나 `requested_capability="vet_contact"`
이거나 — 둘 다 `_emergency_plan(context)` 하나를 부른다. 계획이 한 곳에서 만들어져야 두
경로가 서로 다른 답을 낼 수 없다 (D-051 ② 가 같은 이유로 세운 규칙).

**배타 실행.** 응급이면 `vet_contact` 하나만 돌리고 의미 라우터를 부르지 않는다. 응급 답에
산책 조건이나 훈련 요령이 섞이면 보호자의 인지 부하만 늘린다.

**좌표가 없어도 되묻지 않는다.** `vet_contact` 를 `_NEEDS_COORDINATES` 에 **넣지 않는다.**
지금 규칙대로면 CLARIFY 가 배타로 걸려 "장소를 찾을 위치의 위도를 알려주세요"가 나가는데,
응급에 그 문장은 최악이다. 대신 좌표를 `float | None` 로 두고 없으면 adapter 가 즉시
`ABSTAINED` 를 낸다 (§4).

**어휘는 `daengs_backend/orchestration/emergency.py` 가 소유한다.** `daengs_training` 에서
import 하지 않는다. 이유는 둘이고 둘 다 실측이다:

1. `guardrails/medical.py` 의 모듈 docstring 이 자기 한계를 적어 뒀다 — whitelist 가
   사전보다 우선이라 **"산책 중에 발작을 일으켜요"가 통과한다.** 훈련 가드레일에는 맞는
   판단이지만 응급 게이트가 물려받으면 정확히 응급 문장 하나를 놓친다.
2. backend→training 새 의존을 이걸 위해 만들 이유가 없다 (CLAUDE.md · D-021 의 원칙).

`UPSTREAM.md` 의 동기화 방식과 같게 — 씨앗을 출처와 함께 복사하고, 운영 동작은 이 저장소의
고정 테스트가 소유한다.

### 3-1. 어휘 v1 초안

씨앗은 `daengs_training/data/guardrail/medical_terms_v2.json` 의 `응급·증상` 8개
(중독·발작·구토·설사·출혈·탈수·호흡곤란·고열)다. 그중 **구토·설사·고열·탈수는 단독
어휘에서 뺐다** — 단독으로는 응급이 아니고, "어제 한 번 토했어요"에 병원 목록이 뜨면
게이트 신뢰가 먼저 무너진다.

**HIGH (단독으로 응급)**

```
발작 경련 호흡곤란 숨을 못 숨을 쉬 숨을 헐떡 숨을 잘 못
중독 의식이 없 실신 쓰러 마비 휘청 몸을 떨 거품을 물 혀가 파래
출혈 피를 토 토혈 혈변 혈뇨 각혈
교통사고 차에 치 삼켰 이물 섭취 주워 먹었 주워먹었 주워 먹어
열사병 배가 부풀 소변을 못 난산
초콜릿 양파 포도 자일리톨
응급실 응급 진료 응급진료 위급 다쳤
개한테 물렸 개에게 물렸 뱀에 물렸 벌에 쏘
```

**AMBIG (단독으로는 아님)** `구토 토해 토하 토했 설사 고열 탈수 기력`

**URG (위급 수식어)** `계속 자꾸 멈추지 않 밤새 하루 종일 여러 번 몇 번을 축 늘어 못 일어나 반응이 없`

**판정** `HIGH 하나라도` **또는** `(AMBIG 하나 이상 AND URG 하나 이상)`

> **머지된 #350 과 같은 모양이다.** `fix/training-medical-gate-both-directions` 가
> "남은 것"으로 *"두 글자 일반 명사의 오폭(수술·병원·검사·접종·진단) — D-064 ② 의 결합 규칙이
> 서야 닫힙니다"* 를 적어 뒀고, D-064 ② 는 *"일반 의료 명사는 context signal 로만 쓰고 단독
> 차단어로 승격하지 않는다"* 다. 위 AMBIG×URG 가 정확히 그 결합 규칙의 한 사례다.
> **D-064 는 2026-09-09 에 `dev` 에 머지됐다** (#350) — 이 문서는 그것을 확정된 결정으로
> 인용한다(§5-3 이 D-064 ② 를 짝 대조군 요구의 근거로 이미 쓰고 있다). 두 게이트의 어휘와
> 모양을 맞출지는 §6 에서 별도 카드로 미룬다 — 이 게이트가 `emergency.py` 를 계속 따로
> 소유할지도 그때 다시 본다.
> (#350 본문이 한때 `D-063` 이라 적고 있었으나 2026-09-09 에 `D-064` 로 정정됐다.
> `decisions.md` 에 실제로 들어간 것도 `## D-064` 다.)

### 3-1-1. #350 의 의료 어휘와 겹치는 자리

응급 게이트가 라우터 앞에 서고 **배타 실행**이라, 여기서 켜진 낱말은 훈련·의료 게이트에
도달하지 못한다. 그래서 #350 이 싣는 `medical_terms_v1_curated.json`(50개)과의 부분일치
충돌을 확인했고, 걸린 것이 둘이다.

| 겹침 | 처분 |
| --- | --- |
| `이물질` ⊂ **`귓속 이물질`** | **뺐다.** 귀 이물질은 응급이 아니라 진료다. `이물 섭취`(#350 과 같은 어휘) + `삼켰`·`주워 먹었` 로 대체 |
| `마비` ⊂ **`안면 마비`** | **남긴다.** `마비` 단독이 뒷다리 마비(추간판탈출)도 잡고 그것은 진짜 응급이다. 안면마비도 뇌·전정 원인이면 응급이라, 이 오탐은 미탐보다 싸다 |

`이물질` 을 뺀 것은 재현율에 영향이 없다 — §5-1 실측에서 이물 섭취 문항들은 `삼켰`·
`주워 먹었` 로 이미 잡혔다.

매칭은 **형태소 분석 없는 평문 부분일치**다. `guardrails/medical.py` 가 그 이유를 적어 뒀다 —
코드베이스에 이미 있는 방식과 두 번째 매칭 전략을 만들지 않는다. 활용형은 어간으로 끊는다
(`토해`·`토하`·`토했`).

### 3-2. 어휘를 좁히며 측정으로 배운 것 두 가지

**시제가 응급을 가른다.** `주워 먹` 을 넣었더니 동결 골든셋의 훈련 질문
*"주워 먹지 말라는 '놔' 신호를 처음부터 어떻게 알려줘야 해요?"* 가 걸렸다. 배타 실행이라
훈련 답이 통째로 사라지는 진짜 회귀다. `주워 먹었`·`주워 먹어` 로 좁히니 재현율은 100% 그대로인
채 사라졌다. **이미 일어난 일이 응급이고, 가르치는 법은 훈련이다.** `guardrails/medical.py` 가
겪은 겹침 문제와 같은 구조인데 해법이 whitelist 가 아니라 시제라는 것이 다르고, 그래서 그
모듈의 known tradeoff 를 물려받지 않는다.

**부분일치는 도메인 밖 동음이의에 샌다.** `물렸` 이 *"주식 시장에서 크게 물렸는데"* 를,
`응급` 이 *"집에서 해줄 수 있는 응급처치"* 를 잡았다. `개한테 물렸`·`뱀에 물렸`,
`응급실`·`응급 진료` 로 좁혀 둘 다 없앴다.

## 4. 설계 ② — `vet_contact` 계약과 문구

### 4-1. 능력 등록

`CapabilityName.VET_CONTACT = "vet_contact"`. **의미 라우터 목적지가 아니다** —
`semantic.ExecuteName` 에 넣지 않는다. `GENERAL` 과 같은 처지지만 이유가 정반대라 주석에
그렇게 적는다: GENERAL 은 모델이 근거 있는 능력과 바꿔치기하지 못하게 뺐고, VET_CONTACT 는
**모델을 아예 안 태우려고** 뺀다.

`planner.py:96` 에 한 줄을 더하는 것으로는 안 된다. 지금 구조는 `_EXECUTE_NAMES`(라우터도
고르고 명시 신호로도 부름)와 `GENERAL`(둘 다 안 됨) 둘뿐인데, VET_CONTACT 는 **명시 신호로만
부르는 세 번째 종류**다. 별도 분기가 필요하다.

### 4-2. Payload

```python
class VetContactPayload(ContractModel):
    model_config = ConfigDict(extra="forbid")
    lat: float | None = Field(None, ge=33.0, le=39.0)
    lon: float | None = Field(None, ge=124.0, le=132.0)
    at_night: bool
```

좌표가 `None` 가능한 것이 §3 "되묻지 않는다"의 구현이다. validator 로 **둘 다 있거나 둘 다
없거나**를 강제한다 — 반쪽 좌표는 좌표가 아니다 (D-051 ③ 이 범위 밖 좌표에 내린 것과 같은
처분). `at_night` 은 planner 가 채운다. adapter 가 `datetime.now()` 를 부르면 테스트가 시계에
묶이는데, 이 저장소는 이미 `SearchMust.judge_at`·`evaluated_at` 으로 **시각을 인자로 넘기는
관례**를 갖고 있다.

### 4-3. Adapter

`POST {place_search_base_url}/v2/places/search`, 본문
`{"lat":…, "lng":…, "radius_m": 10000, "kinds": ["hospital"], "limit_per_kind": 5}`.

place-search 쪽 새 코드는 **0**이다. `/v2/places/search` 는 `kinds=["hospital"]` 을 받으면
`place/search.py:323` 에서 `resolve_medical_places` 로 가고, 그것은 MOIS 권위 원천만 읽으며
`PlaceFacts.phone` 을 실어 준다. LLM 을 하나도 거치지 않는다.

**반경이 Place discovery 의 3km(`adapters/place.py:35`)와 다르다.** 응급에 "반경 안에
없습니다"는 답이 되면 안 되고, 결과는 어차피 거리순이라 넓혀도 가까운 것부터 나온다. 지방에서
3km 는 0곳이 흔하다. 대신 후보마다 `distance_m` 을 반드시 실어 25km 짜리를 "가까운 병원"으로
읽지 않게 한다.

좌표가 없으면 **HTTP 를 부르지 않고** 즉시 반환한다.

### 4-4. 상태

| 상황 | status | 부가 |
| --- | --- | --- |
| 후보 ≥ 1 | `OK` | `data` |
| 좌표 없음 | `ABSTAINED` | `abstention = {code: "vet_contact.location_required", message: …}` |
| 반경 안 0곳 | `OK` | 빈 `candidates` + 그 사실을 말하는 문구. 응급에 ERROR 를 내지 않는다 |

`ABSTAINED` 를 고른 것이 **Quick CTA 에 대한 답**이다. 백엔드는 버튼을 그리지 않는다
(D-051: *"표시 규칙은 클라이언트의 몫이고 이 결정은 그 근거 데이터를 보존할 뿐"*).
`OutcomeDetail` 이 `{code, message}` 이고 `aggregate.py:166` 이 그 `message` 를 렌더하므로,
Android 는 **한국어 문장을 파싱하지 않고 `code` 를 보고** "현재 위치 설정하고 가까운 병원
전화번호 보기" 버튼을 단다. 단독 ABSTAINED 는 `AssistantStatus.UNCERTAIN` 이 되는데
(`aggregate.py:125-126`), "당신이 어디 있는지 모른다"에 정확한 상태다.

### 4-5. `data` 모양 (`vet-contact-v1`)

```
{
  "answer": str,
  "searched_radius_m": 10000,
  "at_night": bool,
  "candidates": [
    {"name":…, "phone":…, "distance_m":…, "address":…,
     "license_status_name":…, "open_now": null}
  ],
  "notices": [ {code, message}, … ]
}
```

**평점·리뷰·추천 이유·순위 근거 필드가 없다.** 그런 필드가 없는 것이 이 계약의 목적이다 —
나중에 누가 후기를 붙이자고 해도 넣을 자리가 없어야 한다.

`open_now` 는 **싣되 전량 `null`** 이다. 지금 `place.hours` 가 NULL 이라 값이 없지만, 계약에
자리를 두면 진료시간 원천이 생겼을 때 계약 변경 없이 채워진다. D-051 이 "unknown 상태는
projection 이 그대로 실어 보낸다"고 한 것과 같은 처리다.

`MedicalFacts` docstring(`place/contracts.py:67`)이 *"이름에서 만든 태그·영업 형태 추론은 넣지
않는다"* 라고 못 박아, `/v2/places/search` 응답에는 `24h`·`night` 태그가 애초에 실리지 않는다.
순위 부스트를 미룬 결정이 계약 쪽에서도 맞다.

### 4-6. 답변 문구 — 네 줄

```
응급 상황으로 보여요. 지금 바로 동물병원으로 가세요.            ← redirects.py 재사용
전화로 [야간 진료 여부를 | 지금 진료 가능한지] 먼저 확인하세요.   ← at_night 분기
진료 시간과 응급 진료 여부는 공공 데이터에 없어서 확인해 드릴 수 없습니다.
현재 기기 위치를 기준으로 가까운 순으로 N곳을 찾았습니다.
```

첫 줄은 새로 짓지 않고 `SCOPED_REDIRECT_MESSAGES["emergency"]` 를 쓴다. 같은 상황이 경로에
따라 다른 문장으로 나오면 안 되고, `redirects.py` 가 정확히 그 이유로 만들어진 모듈이다.

셋째 줄이 이 기능의 정직성 전부다. **조건 없이 나간다** — D-051 ⑤ 가 위치 고지를 무조건
내보내기로 한 것과 같은 판단이다. 가끔만 나오는 고지는 사용자가 기댈 수 없고, 이 문장은
후보가 있든 없든 항상 참이다. notice 코드는 `vet_contact.hours_unknown` 이고, 위치 고지
`place.searched_around_current_location` 과 함께 **notice 예산 절단 뒤에** 넣는다.

좌표가 없으면 첫 줄 + `"현재 위치를 알 수 없어 가까운 병원을 찾지 못했습니다."` 둘로 끝난다.

`_LABELS[CapabilityName.VET_CONTACT] = "응급"`. 배타 실행이라 화면에 안 찍히지만 빠뜨리면
`KeyError` 다 (`GENERAL` 주석이 같은 이유로 붙어 있다).

## 5. 설계 ③ — 테스트와 측정

### 5-1. 측정 결과 (2026-09-09, §3-1 어휘로 실측)

```
[응급 재현율]  evals/answer_quality/questions_v1.jsonl 의 emergency 21건   21/21 = 100.0%
[비응급 오탐]  같은 파일의 나머지 133건                                    1/133 = 0.8%
[동결 회귀]    evals/orchestration_router/gold_v1.jsonl        80건        0건
[동결 회귀]    evals/orchestration_router/gold_place_v1.jsonl  15건        0건
[동결 회귀]    evals/training_quality/questions_v1.jsonl       24건        0건
[동결 회귀]    evals/answer_quality/questions_screening_v2.jsonl 28건      0건
```

`evals/answer_quality/questions_v1.jsonl` 에 **`emergency__*` stratum 21건이 이미 있었다.**
초콜릿·경련·호흡곤란·이물질 섭취이고, 여럿이 *"여기 근처에 문 연 동물병원 어디 있는지 좀
찾아줘"* 를 함께 묻는다 — 이 기능을 요구하는 문장이 이미 동결돼 있었다.

남은 오탐 1건은 *"사료를 거의 안 먹고 구토를 계속하는데, 지금 바로 병원에 가봐야 하는
상황일까요"* 다. stratum 은 `medical_boundary` 지만 실제로 응급에 가깝고 사용자가 직접 병원
여부를 묻는다. **켜지는 쪽이 맞다고 보고 그대로 둔다.**

### 5-2. 테스트

1. `tests/test_orchestration_emergency_gate.py` — 게이트는 순수 함수라 모델 호출 0회.
   위 301건을 회귀로 고정한다. `test_router_benchmark_place_gold.py` 가 이미 `evals/` 의
   gold 를 읽는 선례가 있어 새 방식이 아니다.
2. `tests/test_orchestration_vet_contact.py` — planner + adapter, fake transport
   (`tests/fakes.py`). 좌표 있음/없음, 반경 안 0곳, `at_night` 두 값의 문구 분기, 그리고
   **어휘 게이트와 명시 신호가 같은 plan 을 만드는지**. 마지막 것은 D-051 이 결정적 경로와
   의미 경로의 동치성을 테스트로 고정한 것과 같은 장치다.
3. **fixture 커밋** — Android 가 실제로 받는 JSON 을 `backend/tests/fixtures/` 에 넣고 바이트
   대조한다. `tools/place_fixtures` 의 `--write` 패턴을 그대로 쓴다. 계약이 움직이면 Android
   저장소가 아니라 여기서 깨져야 한다.
4. **동결 라우터 벤치마크는 재실행하지 않는다.** 게이트가 95건에 0건 켜지는 것을 확인했으므로
   라우터 결정이 바뀔 수 없다. 유료 호출 0으로 회귀를 증명한다.

### 5-3. 새 골든셋과 지금 숫자의 한계

**실제로 만들어진 것.** 회귀는 `evals/orchestration_emergency/gold_pairs_v1.jsonl` 이다 —
아래 짝 대조군 요구(D-064 ②)만 담고, `pair_id`·`term`·`emergency`·`normal` 네 필드로
`AMBIGUOUS_TERMS`(`구토`·`설사`·`고열`·`탈수`·`기력`) 각각의 응급/정상 짝을 고정한다. 테스트는
`tests/test_orchestration_emergency_gate.py` 에 있다. 짝 대조군이라는 **아이디어**는
`tests/test_medical_gate_pairs.py`(#350)가 선례다 — 저장 형식은 그것을 따르지 않는다.
`test_medical_gate_pairs.py` 는 코드 안 파이썬 튜플(`MEDICAL_CONTROLS` 등)로 대조군을
쥐고 있고 데이터 파일이 없다. 여기는 반대로 `gold_pairs_v1.jsonl` 이라는 JSONL 데이터
파일을 만들고 테스트가 그것을 읽는다. 형식이 갈린 이유는 중요하지 않다.
**`evals/orchestration_emergency/gold_v1.jsonl`(위 21건을 포함한 응급 유형
전체의 골든셋)은 만들지 않았다** — 이 문단이 원래 요구한 것이지만, 그 작업은 §6 로 미뤄진
"골든셋 쏠림 보정" 그대로 남아 있다. 아래 쏠림 문제도 `gold_v1.jsonl` 을 만들 때 같이 푼다.

씨앗은 위 21건이지만 **그대로 두면 안 된다.**

지금 코퍼스는 **중독과 호흡곤란에 쏠려 있다** — 21건 중 초콜릿이 6건, 양파·포도까지 합치면
절반이 중독이다. 그 쏠림이 100% 라는 숫자를 실제보다 좋아 보이게 한다. 코퍼스에 **없는** 응급
유형을 사람이 채워야 한다: 교통사고, 난산, 열사병, 요도폐색, 위염전, 저혈당, 출혈. §3-1 어휘에는
넣어 뒀지만 **한 건도 검증되지 않았다.**

**짝 대조군은 D-064 ② 가 거는 요구다.** 그 결정이 결합 규칙에 대해 이렇게 못 박았다 —
*"추가할 때는 같은 의료 명사를 배경으로 쓰는 정상 훈련 질문을 **짝 대조군으로 함께
고정한다**."* §3-1 의 AMBIG×URG 가 정확히 그 결합 규칙이므로 이 요구가 그대로 걸린다.
`구토`·`설사`·`고열`·`탈수`·`기력` **각각에 대해** 그 낱말을 배경으로만 쓰는 정상 질문을
골든셋에 짝으로 넣는다 — 예: "밤새 계속 토해요"(응급) ↔ "사료 바꾸면 구토하는 애들이
있다던데 천천히 바꾸는 방법이 있나요?"(정상). 짝 대조군을 함께 고정한다는 아이디어는
#350 이 세운 `tests/test_medical_gate_pairs.py` 가 선례고 새로 만든 것이 아니다 — 다만
저장 형식은 그 파일의 코드 내 튜플이 아니라 `gold_pairs_v1.jsonl` 이라는 JSONL 데이터
파일이다.

지표는 둘이고 값이 다르다 — **미탐(응급인데 못 잡음)이 오탐보다 훨씬 비싸다.** 오탐은 병원
목록이 뜨는 것이고 미탐은 기능이 없는 것이다. 이 비대칭을 골든셋 문서에 명시해, 나중에 누가
"정확도"라는 한 숫자로 합치지 못하게 한다.

이 축은 라우팅 정확도 같은 범용 지표가 아니라 **"응급 발화에서 병원 연락 수단에 도달하기까지의
실패율"** 이라는 이 서비스 고유의 축이다.

## 6. 범위 밖 · 후속

- **`24h` 태그 실측 → 순위 부스트.** `geo/ranking.py:24` 의 `preference_tags(night=, emergency=)`
  는 이미 구현돼 있고 docstring 이 *"상황 정책은 여기 없다 — 호출자가 결정해서 불 값으로
  넘긴다"* 고 적어 뒀다. **그런데 지금 아무도 부르지 않는다** — `resolve_medical_places` 는
  `SearchPlan(must=...)` 만 만들고 `prefer` 를 넘기지 않으며, `PlaceSearchPreferences` 는
  `parking: bool` 하나에 `extra="forbid"` 다. 배선하려면 place-search 계약 변경이 필요하고,
  그 값어치가 `24h` 태그 개수 하나에 달려 있다. 3곳이면 문구뿐이고 300곳이면 순위가 움직인다.
  `SearchPrefer` 의 계약이 **"빼지 않는다. 순위만 올린다"**(`geo/contract.py:32`)이므로,
  상호명 추정으로 거르는 일은 어느 경우에도 하지 않는다.
- **두 게이트를 합칠지는 별도 카드.** #350 이 머지되어 훈련 게이트는 양방향으로 고쳐졌고
  (D-064), 서빙 사전은 `load_serving_medical_terms()` 한 곳으로 합쳐졌다. 그래도 물을 것은
  남는다 — ⓐ AMBIG×URG 결합 규칙을 두 게이트가 **같은 구현**으로 쓸지, ⓑ 그렇게 합쳐도 §3 의
  "whitelist 를 물려받지 않는다"가 유지되는지. **지금은 합치지 않는다** — 두 게이트는
  §3 의 이유(응급 게이트는 whitelist 를 안 두고, 훈련 게이트의 AMBIG 취급과 목적이 다름)로
  각자의 어휘를 따로 소유하고 있고, 그 이유는 D-064 머지와 무관하게 그대로 서 있다. 합치는
  것은 그 자체로 검증이 필요한 별도 카드다.
- **골든셋 쏠림 보정** (§5-3).
- **Android 의 CTA 버튼.** 이 저장소의 산출물은 `vet_contact.location_required` 코드가 계약에
  있고 fixture 에 커밋되는 것까지다.
- **지역명 지오코딩.** D-051 ⑤ 의 별도 카드 그대로. 응급은 현재 위치가 오히려 맞다.
- **진료시간 원천 확보.** 1-2 표의 연휴 목록은 API 가 없고 상시가 아니다. 새 원천이 생기면
  `open_now` 자리가 이미 계약에 있다 (§4-5).

## 7. 근거

| 주장 | 파일 |
| --- | --- |
| 과목 축을 지운 이유 | `backend/src/daengs_place/geo/contract.py:31-41` |
| 야간·응급 태그 실측 (5,457 중 night 1 · emergency 2) | `geo/contract.py:35` · `geo/pet.py:7` · `geo/search.py:53` |
| 병원 `hours` 가 항상 NULL | `backend/src/daengs_place/ingest/mois_store.py:21` |
| MOIS 원천 필드에 진료시간 없음 | `backend/src/daengs_place/ingest/mois.py:108-135` |
| 24시 추정이 상호명 정규식 | `backend/src/daengs_place/geo/tagging.py:12` |
| v2 검색이 MOIS 권위 원천으로 감 | `backend/src/daengs_place/place/search.py:323` |
| v2 응답에 이름 파생 태그를 안 싣는다 | `backend/src/daengs_place/place/contracts.py:67` |
| `prefer` 가 의료 경로에 배선돼 있지 않음 | `place/medical_resolver.py:30-40` · `place/search.py:56` |
| 명시 신호가 LLM 앞에서 끝남 | `backend/src/daengs_backend/orchestration/service.py:124` |
| payload 는 능력별 명시 분기 | `orchestration/planner.py:195-240` (D-051 ②) |
| CLARIFY 배타 · 좌표 게이트 | `orchestration/planner.py:135-150` (D-051 ③) |
| `RoutePlan` 이 requests+handoffs 공존 허용 | `orchestration/contracts.py:241` |
| ABSTAINED 가 `abstention.message` 를 렌더 | `orchestration/aggregate.py:166` |
| 단독 ABSTAINED → UNCERTAIN | `orchestration/aggregate.py:125-126` |
| 응급 거절 문장의 주인 | `orchestration/redirects.py:27` |
| general 에 좌표를 주지 않는 이유 | `orchestration/planner.py:216` (D-057) |
| 훈련 가드레일의 known tradeoff | `daengs_training/guardrails/medical.py` 모듈 docstring |
| 씨앗 어휘 | `daengs_training/data/guardrail/medical_terms_v2.json` (`응급·증상` 8개) |
| 응급 코퍼스 21건 | `backend/evals/answer_quality/questions_v1.jsonl` (`emergency__*`) |
