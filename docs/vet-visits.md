# 진료비 기록 — 영수증 사진에서 읽고, 유저가 확정한다

저장소 탭이 밥·약·간식까지 왔지만(`docs/care-events.md`) **병원비는 어디에도 안 남는다.**
보호자가 영수증을 찍으면 금액·병원·진료항목을 읽어 두고, 진단 사유는 **유저가 확정한 것만**
남긴다. 비서는 그 기록을 읽어 "피부로 1년간 32만원 썼고, 마지막은 9/2 ○○동물병원" 이라고
답한다.

이 문서는 스키마(§1)와 추출 계약(§2)이다. 라우터·서비스·앱 화면은 뒤따르는 카드에서 붙는다.

---

## §1 스키마

표가 **둘**이다. `db/init/25_vet_visits.sql` · `db/migrations/2026-09-09_vet_visits.sql`.

| | 무엇 | 누가 읽나 |
| --- | --- | --- |
| `vet_visits` | 유저가 [확인] 을 누른 기록 | 저장소 목록 · 채팅 맥락 |
| `vet_visit_drafts` | 확정 전 추측 | 확인 화면 하나뿐 |

### 왜 표를 갈랐나

한 표에 `status` 칸을 두고 `WHERE status='confirmed'` 로 거르는 쪽이 표 하나로 끝난다.
그쪽을 안 고른 이유는 하나다 — **언젠가 그 `WHERE` 를 빠뜨리는 조회가 생기는데, 그 자리가
하필 프롬프트면 기계가 지어낸 병명이 의료 기록처럼 읽힌다.** 표를 가르면 확정 안 된 추측은
거르는 게 아니라 **거기 없다.** `#344` 가 `note` 를 프롬프트에서 뺀 것과 같은 결의 판단이다.

같은 이유로 `reason_code` 가 `NOT NULL` 이다. 확인 단계를 건너뛰는 버그는 INSERT 에서
시끄럽게 죽지, 추측을 조용히 기록으로 만들지 않는다.

### 사유는 닫힌 목록이다

`reason_code` 는 자유 텍스트가 아니다. 자유 텍스트면 `피부염`·`피부질환`·`피부병` 이 서로
다른 키가 되어 **"피부로 1년간 얼마" 가 영영 안 모인다.** 사유별 누계가 이 기능의 존재
이유이므로 그 집계가 서는 쪽을 고른다. 사람의 말은 `reason_detail`(자유 텍스트 한 줄)이 받고,
그것은 집계에도 프롬프트에도 안 간다.

부수 효과로 [edit] 화면의 "흔한 질병 목록" 드롭다운이 공짜로 생긴다 — 목록이 곧 이 표다.

| code | 표시명 | code | 표시명 |
| --- | --- | --- | --- |
| `skin` | 피부 | `respiratory` | 호흡기 |
| `digestive` | 소화기 | `musculoskeletal` | 근골격 |
| `vaccination` | 예방접종 | `urinary` | 비뇨기 |
| `checkup` | 정기검진 | `tumor` | 종양 |
| `dental` | 치과 | `parasite` | 기생충 |
| `injury` | 외상 | `emergency` | 응급 |
| `neuter` | 중성화 | `other` | 기타 |
| `eye` | 안과 | | |
| `ear` | 귀 | | |

표시명은 코드에 둔다 (`models/vet_visit.py` 의 `VET_REASON_CODES`). DB 에는 코드만 앉는다 —
`care_events.CARE_EVENT_KINDS` 와 같은 규칙이다.

### `label_source` 칸을 두지 않는다

"AI 제안을 받아들였나, 고쳤나" 는 학습에 필요한 신호이지만 **칸이 필요 없다.**
`suggested_reason_code` 와 `reason_code` 를 비교하면 그대로 나온다:

| | 뜻 |
| --- | --- |
| `suggested_reason_code IS NULL` | 제안이 없었다 (사진이 안 읽혔거나 손입력) |
| `suggested = reason` | 유저가 제안을 받아들였다 |
| `suggested <> reason` | 유저가 고쳤다 |

칸을 따로 두면 셋 사이의 일관성을 CHECK 세 줄로 지켜야 하고, 그 셋이 어긋나는 날이 온다.

### 그 밖의 결정

- **금액은 `INTEGER` 원.** 부동소수도 `NUMERIC` 도 아니다 — 원에는 보조단위가 없다.
  상한 1억은 OCR 이 자릿수를 흘리는 실패(`15,000` → `150,000`)의 **터무니없는 쪽만** 거른다.
  그럴듯하게 틀린 값은 오직 확인 화면이 잡는다.
- **`visited_on` 은 `DATE`.** `care_events` 는 "아침 약" 을 가르려고 시각을 남기지만,
  진료비에 시분은 아무 질문도 안 가른다. 가짜 시각을 만들면 시간대 버그만 부른다.
- **`hospital_phone` 에 모양 CHECK 이 걸린다** (`^[0-9]{2,4}(-[0-9]{3,4}){1,2}$`).
  이 칸은 앱에서 `tel:` 링크가 되어 **사람이 눌러 전화를 건다** — 잘못 읽힌 번호는 금액 오류보다
  조용히 틀리고, 모르는 사람에게 전화가 걸릴 때까지 아무도 모른다. 병원 이름·주소보다 이 칸에만
  모양을 거는 이유이고, 네 묶음짜리 카드번호가 이 칸에 앉지 못하게 하는 두 번째 그물이기도 하다.
- **멱등키가 `(app_user_id, client_event_id)` 다.** `care_events` 는 `(pet_id, ...)` 로 묶지만,
  재시도 도중 활성 강아지가 바뀌면 pet 단위 키는 두 줄을 허용한다. 유저 단위는 막는다.
- **`raw_ocr_items` 는 `jsonb` 배열.** `[{"name": "초진료", "amount_krw": 15000}, ...]`.
  나중에 진단명 추천 모델을 고도화할 때 `항목 → 유저가 확정한 사유` 가 그대로 학습쌍이 된다.

### 초안 청소

24시간 지난 `vet_visit_drafts` 는 **업로드된 사진과 함께** 지운다. 확정 안 된 영수증 사진은
볼륨만 먹는 쓰레기다.

**Celery Beat 를 안 쓴다.** 실제로 도는 Beat 는 `crawler-beat` 하나이고 그것은
`daengs_life.tasks.celery_app` 이다 — `daengs_backend.tasks.activity` 에도 `beat_schedule` 이
있지만 **그것을 실행하는 서비스가 없다**(모듈 docstring: "run Beat only after explicit
activation"). 그러니 Beat 항목을 더하려면 ① `daengs_life` 쪽에 등록해 D-021 이 좁혀 둔
backend→life 접점을 넓히거나 ② compose 에 beat·worker 한 쌍을 새로 세워야 한다.
**쓰레기 수거에 치를 값이 아니다.**

대신 **초안을 만들 때 같이 쓸어낸다** — 요청당 최대 50건.

```sql
DELETE FROM vet_visit_drafts
WHERE id IN (SELECT id FROM vet_visit_drafts
             WHERE created_at < NOW() - INTERVAL '24 hours' LIMIT 50);
```

아무도 영수증을 안 올리는 동안에는 안 쓸린다. 그동안 남는 것은 그 한가한 사용자들의 초안
몇 줄이라 문제가 안 된다. 볼륨이 눈에 띄게 커지면 그때 ②로 올린다.

---

## §2 추출 계약

`services/vet_receipt.py`. Gemini 멀티모달 + **제약 디코딩**(`response_json_schema`),
`adapters/general.py` 가 이미 쓰는 것과 같은 `google-genai` 경로다. 새 벤더도 새 키도 없다.

Cloud Vision + 정규식(`shell-files/dev_skm` 의 `utils/ocr.py`)을 안 쓰는 이유: 그쪽이 통한 것은
**사업자등록증이 법정 서식이라 자리가 고정**이어서다. 동물병원 영수증은 서식이 제각각이라
자리로 못 찾는다.

### 나가는 모양

```python
class ReceiptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=40)
    amount_krw: int = Field(ge=0, le=100_000_000)

class ReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "unreadable"]
    unreadable_reason: Literal["blurry", "not_a_receipt", "no_amount"] | None = None
    visited_on: date | None = None
    total_krw: int | None = Field(default=None, ge=0, le=100_000_000)
    hospital_name: str | None = Field(default=None, max_length=60)
    hospital_address: str | None = Field(default=None, max_length=200)
    hospital_phone: str | None = Field(default=None, pattern=PHONE_PATTERN)
    items: list[ReceiptItem] = Field(default_factory=list, max_length=40)
    suggested_reason_code: VetReasonCode | None = None
```

`status="ok"` 는 `total_krw` 가 있어야 하고, `status="unreadable"` 은 `unreadable_reason` 이
있고 나머지가 비어야 한다 (`model_validator`). `GeneralAnswer.shape_matches_kind` 와 같은 결이다.

### 개인정보는 **칸이 없어서** 안 나온다

영수증에는 보호자 이름·전화·카드번호·사업자등록번호가 진료항목과 나란히 찍혀 있다.
이 스키마에는 **그것들을 담을 칸이 하나도 없고**, 제약 디코딩이라 모델이 스키마 밖의 키를
못 만든다. 프롬프트 규칙으로 "쓰지 마세요" 라고 부탁하는 것과 다르다 — 구조가 막는다.

남는 구멍은 **값 안에 숨어 들어오는 경우** 둘이고, 각각 그물이 있다:

| 구멍 | 그물 |
| --- | --- |
| `hospital_phone` 에 카드번호가 앉는다 | 패턴 CHECK. 네 묶음(`5432-1234-5678-9012`)은 `{1,2}` 에 안 맞아 떨어진다 |
| `items[].name` 에 이름·번호가 섞인다 | 서버측 스크러버 (아래) |

스크러버는 항목명이 아래 중 하나에 걸리면 **이름만 `"<redacted>"` 로 바꾸고 금액은 남긴다.**
항목을 통째로 버리면 합계가 안 맞아 확인 화면이 거짓말을 하게 된다.

- 연속 숫자 8자리 이상
- 사업자등록번호 모양 `\d{3}-\d{2}-\d{5}`
- 카드 모양 `\d{4}[- ]\d{4}[- ]\d{4}`

### 프롬프트 규칙

`_CARE_LOG_RULE`(#344) 과 같이 **영문 한 문단**으로 둔다. 요지:

- 한국 동물병원 진료비 영수증에 실제로 찍힌 것만 읽는다. 없는 값을 지어내지 않는다.
- **보호자 이름 · 개인 전화번호 · 카드번호 · 사업자등록번호는 어디에도 쓰지 않는다** —
  항목명 안에도. 그런 문자열을 만나면 그 부분을 빼고 항목명을 적는다.
- 금액은 정수 원. 쉼표와 `원` 을 떼고 숫자만.
- `hospital_phone` 은 **병원 대표번호**다. 보호자 번호가 아니다. 숫자와 하이픈만.
- `suggested_reason_code` 는 목록 안의 값이거나 `null` 이다. 항목만으로 사유가 분명하지
  않으면 **`null` 을 낸다** — `other` 는 "사유가 분명한데 목록에 없다" 일 때만.
  (`null` 이면 확인 화면이 아무것도 미리 고르지 않는다. 이 구분이 곧 명시적 동의의 세기다.)
- 영수증이 아니거나 합계를 못 읽으면 `status="unreadable"` 과 사유를 낸다.

모델은 `VET_RECEIPT_MODEL_ID` 설정으로 고른다. 기본은 라우터와 같은 계열이되,
**영수증 판독에 flash-lite 가 모자랄 수 있다** — 설정으로 뺀 이유가 그것이고, 첫 평가가
정한다. `temperature=0`.

### 못 읽었을 때

**500 을 내지 않는다.** 추출은 200 으로 돌아오고 상태가 갈린다.

| `extraction_status` | 뜻 | 화면 |
| --- | --- | --- |
| `ok` | 읽었다 | 값이 채워진 확인 화면 |
| `unreadable` | 영수증이 아니거나 흐리다 (모델 판단) | 빈 확인 화면 + 사유 안내 |
| `failed` | 우리 쪽 문제 (타임아웃·API 오류) | 빈 확인 화면 + "다시 시도" |

세 경우 모두 **유저는 손으로 채워 확정할 수 있다.** 손입력 화면을 따로 만들지 않는 이유가
이것이다 — 확인 화면이 곧 입력 폼이고, 실패 경로는 성공 경로에서 미리 채움만 빠진 것이다.
그때 `suggested_reason_code` 는 `NULL` 로 남아 "제안 없었음" 이 기록에 남는다.

전송 오류는 **한 번만 재시도**하고 그 다음은 `failed` 다.

### 초안 응답 (`VetVisitDraftResponse`)

```
draft_id, pet_id, receipt_image_url
extraction_status      ok | unreadable | failed
unreadable_reason      blurry | not_a_receipt | no_amount | null
visited_on, total_krw
hospital_name, hospital_address, hospital_phone
items[]                [{name, amount_krw}, ...]
suggested_reason_code  코드 | null
possible_duplicate     bool
reason_options[]       이 강아지의 최근 사유가 앞, 그 뒤 전체 목록
```

- **`possible_duplicate`** — 같은 `(pet_id, visited_on, total_krw)` 로 **확정된** 기록이 이미
  있으면 `true`. DB 제약이 아니다 (같은 날 같은 금액의 진짜 방문이 있을 수 있다) — 유저가
  손쓸 수 있는 유일한 순간인 확인 화면에서 "같은 날 같은 금액 기록이 있어요" 라고 말해 주려는
  플래그다.
- **`reason_options`** — [edit] 의 드롭다운. 이 강아지가 실제로 겪은 사유가 맨 앞에 오는 것이
  적중률이 제일 높다. 앱이 목록을 하드코딩하지 않게 서버가 준다.

### 확인 화면에서 고칠 수 있는 것

라벨만이 아니다. **병원 이름·주소·전화번호도 편집 가능해야 한다.** OCR 은 숫자를 뒤집고,
틀린 `hospital_phone` 은 `tel:` 링크가 되어 모르는 사람에게 전화를 건다. 금액 오류보다 조용히
틀리는 실패라 같은 확인 게이트에 얹는다.

확정은 `POST /app/vet-visits/{draft_id}/confirm` **하나뿐**이고, 라벨은 그 본문으로 온다.
제안값이 저장소로 새는 경로가 그래서 없다.

---

## 채팅이 받는 것

`general` 칸에만 간다 (`care_log` 와 같다, #344). Life · Training 은 안 받는다 — 조례·보조금
문서로 답하는 자리에서 이번 달 병원비는 답을 안 가른다.

```
VET_RECENT: {"month_total_krw": 180000, "visit_count_30d": 2,
             "last_visit": {"date": "2026-09-02", "reason": "피부",
                            "total_krw": 80000,
                            "hospital": "○○동물병원", "phone": "02-123-4567"},
             "by_reason_12m": {"skin": 320000, "vaccination": 80000}}
```

**안 가는 것** — `reason_detail`(유저 자유 텍스트), `raw_ocr_items`, `hospital_address`,
영수증 사진. 앞의 둘은 `#344` 가 `note` 를 뺀 것과 같은 이유다.

기록이 하나도 없으면 `None` 이고 블록이 아예 안 실린다. `care_events` 표가 없을 때와 같이
`SQLAlchemyError` 는 삼키고 로그 없이 답한다 — 마이그레이션이 서버에 적용되기 전에 코드가
배포돼도 조용하다.

---

## 열린 것

- **목적 외 이용 동의.** `raw_ocr_items` 가 "나중에 추천 모델을 학습시키려고" 존재하는 순간
  그것은 서비스 제공 범위 밖의 이용이고, **모으기 전에** 개인정보처리방침·이용약관에 근거가
  있어야 한다. 이미 쌓인 행에 소급하는 쪽이 훨씬 비싸다.
- 영수증 사진 보관 기간. 지금은 기록과 함께 살고 함께 지워진다. 펫보험 청구가 붙으면
  그때 다시 본다.
- 사유 목록 16개가 맞는지는 실제 영수증이 쌓인 뒤에 안다. `other` 비중이 높으면 쪼갠다.
