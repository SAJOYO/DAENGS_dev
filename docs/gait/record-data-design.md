# 보행 기록 저장 정책 · DB 설계안

상태: **제안(proposal) — 아직 결정(D-xxx)이 아닙니다.** 일반 서비스 DB(사용자/강아지
스키마)가 확정되면 이 문서를 기준으로 실제 테이블·migration·backend 구현을 진행합니다.
작성일 2026-08-29.

관련 코드: `gait-analysis/`(D-029), `backend/`. 관련 결정: D-011(MVC2·SQLAlchemy),
D-021(daengs_life 상주 프로세스), D-022·D-024(스크리닝 분리), D-029(보행 분석 분리).

---

## 왜 이 문서가 필요한가

보행 분석은 사진 한 장을 보고 끝나는 스크리닝과 다릅니다. **같은 강아지의 시간에 따른
변화를 관찰**하는 기능이라 기록이 쌓여야 하고, 사용자가 과거 기록을 다시 열어보고
비교할 수 있어야 합니다. 그런데 지금 `gait-analysis`는 DB를 전혀 모르는 무상태
서비스이고(D-029), 일반 서비스 DB는 아직 스키마가 없습니다. 그래서:

- 이번 카드에서는 **테이블도 migration도 만들지 않습니다.**
- 대신 나중에 일반 DB가 확정됐을 때 바로 참고할 수 있도록 **역할 분리 · 데이터 구조 ·
  API 모양**을 여기 적어 둡니다.
- `dog_id`/`user_id`가 걸리는 자리는 전부 **TBD**로 남겨 둡니다 — dogs/users 테이블이
  없어서 FK도, 실제 column type도 지금은 확정할 수 없습니다.

---

## 1. 역할 분리안 — gait-analysis vs daengs_backend

### 지금 상태

`gait-analysis`는 이미 독립 서비스입니다(D-029). `backend/`와 다른 레이아웃, 자체
`pyproject.toml`·`uv.lock`, compose `profiles: ["gait"]`로 꺼둔 채, nginx는 새 포트
대신 `daengback` 아래 `/gait/`. 가중치는 git 밖. **DB 접속은 없습니다** — 기록은
`GAIT_DATA_DIR` 아래 JSON 파일 하나당 하나(`record_store.py`)입니다.

`daengs_backend`는 MVC2 계층(D-011), SQLAlchemy 2.0 async + asyncpg로 PostgreSQL을
봅니다. 지금 있는 테이블은 `app_users`·`admin_users`·`refresh_tokens`뿐이고
**dog/pet 테이블이 아직 없습니다.**

### 제안: gait-analysis는 추론만, daengs_backend가 기록의 주인

| | gait-analysis | daengs_backend |
| --- | --- | --- |
| 담당 | AI 추론 전용 | 서비스 데이터 관리 |
| 하는 일 | 영상 전처리 · keypoint 추론 · gait filter · quality 판정 · trajectory/feature 생성 · overlay 생성 · **저장된 feature로 두 record 비교 계산** | 사용자/강아지 ↔ gait record 연결 · 목록/상세 조회 · 파일 경로(식별자) 관리 · 기록 삭제 오케스트레이션 · **PostgreSQL persistence** |
| DB 접속 | 없음 (유지) | 있음 (유지) |
| 무거운 의존성 | torch·ultralytics (컨테이너 격리 유지) | 없음 |

이유는 D-022·D-024·D-029과 같습니다 — **torch 무게, 프로세스가 죽는 범위, 영상은
분 단위 요청이라 배포 API 프로세스가 물고 있을 이유가 없음.** 여기에 하나 더 붙습니다:

> `gait-analysis`가 asyncpg/SQLAlchemy를 직접 물면, "기록의 주인이 누구인가"가
> 두 군데로 쪼개집니다. 지금 이미 `record_store.py` 주석이 이 결정을 미뤄 둔 이유가
> 그것입니다("이 서비스가 DB를 직접 볼지, `daengs_backend`가 기록의 주인이 될지가
> 먼저 정해져야 합니다"). **일반 서비스 DB를 보는 계층은 backend 하나로 유지**하는
> 편이 D-011의 MVC2·SQLAlchemy 원칙과도 맞습니다.

### 두 서비스가 만나는 지점 — 동기 프록시가 아니라 "분석 먼저, 저장은 그 다음"

`daengs_backend`가 `daengs_life`를 부르는 접점을 일부러 세 줄로 좁혀 둔 것처럼
(D-021), 여기도 접점을 좁게 잡을 것을 제안합니다. **backend가 업로드를 그대로 받아
gait-analysis로 중계(proxy)하는 방식은 권장하지 않습니다** — 분석이 분 단위로 걸리는데
그동안 backend의 워커 하나가 그 요청을 물고 있게 되고, 로그인·`/life/ask` 같은 이미
상주하는 요청과 자원을 다투게 됩니다(D-021이 임베딩 모델을 backend에 상주시키면서도
"접점을 안 늘린다"고 못박은 것과 같은 이유).

대신 **2단계**를 제안합니다.

```
① 앱 → gait-analysis  (nginx /gait/, 지금과 동일)
   POST /v1/analyze  (분 단위 소요) → 분석 record(JSON) 반환

② 앱 또는 gait-analysis → daengs_backend
   POST /api/gait-records  (빠름 — 이미 계산된 결과를 저장만 함)
   backend: dog_id 소유권 검증 → PostgreSQL INSERT
```

②를 누가 부르는지는 두 갈래입니다 — **결정 필요, 이 문서에서 확정하지 않습니다.**

- **(a) 앱이 순서대로 두 번 호출.** 구현이 가장 단순하지만, ①은 성공했는데 앱이
  꺼지는 등으로 ②를 못 부르면 분석 결과가 고아가 됩니다(파일은 있는데 DB row가 없음).
- **(b) gait-analysis가 분석 직후 자체적으로 backend를 콜백.** 유실 위험은 줄지만
  gait-analysis가 backend의 존재를 알아야 하고(컨테이너 간 내부 네트워크 호출),
  "추론 전용"이라는 역할 경계가 살짝 흐려집니다.

지금 `gait-analysis`가 compose profile로 **기본값에서는 꺼져 있다**는 점도 이 결정에
영향을 줍니다 — backend가 상시 gait-analysis에 의존하는 설계라면, gait-analysis가
꺼진 채 배포되는 지금의 운영 방식(D-024와 같은 판단)과 부딪힙니다.

---

## 2. gait record 데이터 구조

### 지금 실제로 나오는 모양 (`record_store.py`가 저장하는 JSON, walk_demo 원본 그대로)

```jsonc
{
  "record_id": "0f5dfd26",
  "source_file": "20250721_192002.mp4",   // 화면 표시용 원본 파일명
  "original_video": "/data/uploads/7fab6065.mp4",  // 2026-08-29 추가 — 아래 §10 참고
  "date": "2026-08-29",                   // 사용자가 넣은 촬영일 (선택, 없을 수 있음)
  "note": "smoke-test-before-migration",
  "dog_id": null,                         // 지금은 문자열 그대로 신뢰 — §9 TBD
  "created_at": "2026-08-29T05:46:18Z",   // 서버가 찍은 분석 시각
  "video_meta": { "resolution": "1080x1920", "native_fps": 30.01 },
  "quality": {
    "status": "ok",                       // "ok" | "unavailable"
    "quality_tier": "low",                // "good" | "low" (status=ok일 때만)
    "reason": null, "recommendation": null, "quality_note": "...",
    "n_frames_sampled": 81, "n_frames_detected": 11, "n_frames_gait_usable": 4,
    "detection_rate": 0.136, "gait_usable_rate": 0.049,
    "exclude_reason_counts": { "keypoints_collapsed": 6 },
    "n_frames_flagged_night": 0, "n_frames_flagged_blur": 23
  },
  "gait_filter_version": "v5-stationary-speed-based-20260826",
  "overlay_video": "/data/overlays/7fab6065_overlay.mp4",   // status=ok일 때만
  "trajectories": [ /* 관절별 프레임 좌표 — 아래 참고 */ ],
  "features": {
    "summary_for_ui": { "Iliac crest": { "x_range": 0.478, "y_range": 0.452 }, ... },
    "internal_feature_vector": { /* 관절 × 통계 ≈ 100여 차원 */ },
    "n_frames_used": 4
  }
}
```

`status`가 `"unavailable"`이면(분석 가능한 프레임 부족) `overlay_video`·`trajectories`·
`features`가 아예 없습니다 — 없는 것을 억지로 만들면 숫자는 나오지만 뜻이 없기
때문입니다(`pipeline.py` 주석). **이 경우에도 원본 영상은 남습니다** — 재촬영 안내와
별개로, 사용자가 "그때 뭘 찍었는지"는 볼 수 있어야 합니다.

`trajectories`는 `compare_records()`가 **쓰지 않습니다** — 비교는 `features`만 봅니다.
지금은 overlay를 그리기 전 단계의 원시 keypoint를 프레임별로 남겨 둔 것이라, 나중에
"이동 경로를 그래프로" 같은 기능을 붙일 때 재료가 됩니다. DB로 옮길 때 필수는 아니고,
필요해지면 JSONB로 같이 넣거나 처음엔 빼도 됩니다(재inference 없이는 복원 불가 —
아래 §7 삭제 설계에서 다시 언급).

### 향후 PostgreSQL `gait_records` 설계안

기존 `app_users` 모델(`backend/src/daengs_backend/models/app_user.py`)의 관례를
그대로 따릅니다 — uuid PK + `gen_random_uuid()`, `DateTime(timezone=True)` +
`NOW()`, 상태값은 CHECK 튜플. 반정형 값(quality 세부 통계, feature vector)은
RAG 코퍼스의 `documents.metadata JSONB`(`db/init/01_schema.sql`)와 같은 방식으로
JSONB에 담고 **표준 키는 주석으로 문서화, DB가 강제하지 않고 애플리케이션(Pydantic)이
검증**하는 이 저장소의 기존 관례를 그대로 씁니다.

| 컬럼 | 타입(안) | 비고 |
| --- | --- | --- |
| `id` | `uuid PK` | `gen_random_uuid()` |
| `dog_id` | **TBD** | dogs 테이블이 없어 FK 미확정. §9 |
| `owner_user_id` | **TBD** | dog_id로부터 유도 가능하면 중복일 수 있음 — dogs 스키마가 정해지면 재검토 |
| `captured_at` | `date`, nullable | 지금 JSON의 `date`. 사용자 입력, 없을 수 있음 |
| `analyzed_at` | `timestamptz NOT NULL DEFAULT NOW()` | 지금 JSON의 `created_at` |
| `status` | `varchar(20) NOT NULL` + CHECK `('ok','unavailable')` | `quality.status` |
| `quality_tier` | `varchar(10)` + CHECK `('good','ok','low')`, nullable | `status='unavailable'`면 NULL. 목록 화면에서 배지로 쓰려고 승격(quality 안에도 그대로 남김). **엔진(legacy `quality_gate.py` · v4 `quality.py`)이 내는 어휘와 같아야 한다** — 처음(D-043)엔 `good/low` 둘만 적어서 `ok`(유효 프레임 20~80) 영상의 커밋이 죽었고(2026-09-09), `tests/test_gait_quality_tier_contract.py` 가 두 엔진 소스·SQL·ORM 을 읽어 대조한다 |
| `quality` | `JSONB NOT NULL` | 나머지 quality 세부 통계 전부 (표준 키는 `src/quality_gate.py` 출력 그대로 문서화) |
| `summary_for_ui` | `JSONB`, nullable | `status='ok'`일 때만. 이미 UI-safe |
| `internal_feature_vector` | `JSONB`, nullable | **비교 전용.** API 응답 스키마가 절대 내보내지 않도록 강제 — §5 |
| `trajectories` | `JSONB`, nullable | 선택 — 필요해지면 |
| `gait_filter_version` | `text NOT NULL` | 다른 버전끼리 비교 시 경고에 씀 |
| `video_meta` | `JSONB` | `{resolution, native_fps}` |
| `original_video_path` | `text NOT NULL` | 파일 저장 영역의 경로/식별자. §3 |
| `overlay_video_path` | `text`, nullable | `status='ok'`일 때만 |
| `source_file` | `text` | 사용자가 올린 원본 파일명(표시용) |
| `note` | `text`, nullable | |
| `created_at` / `updated_at` | `timestamptz NOT NULL DEFAULT NOW()` | 저장소 관례 |
| `deleted_at` | `timestamptz`, nullable | soft delete. §7 |

**컬럼/타입/FK는 전부 안(案)입니다.** `dog_id`가 실제로 uuid FK가 될지, 아니면 다른
식별 방식(§9)을 쓸지는 dogs/users 스키마가 나온 뒤 다시 봐야 합니다.

---

## 3. 원본 영상 / overlay 파일 저장 구조

### 지금 (이미 구현됨, 이번 카드 이전부터)

```
GAIT_DATA_DIR/            (컨테이너: /data, 볼륨: gait-data)
├── uploads/<uuid8>.mp4        # 원본 (mp4로 통일, 자동 삭제 없음)
├── overlays/<stem>_overlay.mp4 # skeleton을 그린 결과 (자동 삭제 없음)
└── records/<record_id>.json    # 분석 결과 전체
```

DB에는 **영상 바이트를 절대 넣지 않는다**는 요구사항과 지금 구조가 이미 맞습니다 —
파일은 Docker volume에, JSON(향후 DB row)에는 경로만 있습니다.

### 파일 소유권 — 제안: gait-analysis가 계속 물리적으로 소유

§1의 역할 분리를 따르면, **daengs_backend는 이 볼륨을 직접 마운트하지 않는 편을
제안합니다.** 대신:

- DB의 `original_video_path`/`overlay_video_path`는 참고·감사(audit)용 문자열로
  남기고,
- 앱이 실제로 영상을 재생할 때는 `record_id`로 만든 URL
  (`/gait/v1/records/{id}/overlay`, `/gait/v1/records/{id}/original` — 후자는
  아직 없음, §5)을 backend 응답에 실어 보냅니다.

이렇게 하면 파일시스템에 손대는 서비스가 gait-analysis 하나로 유지되고,
backend 컨테이너가 `gait-data` 볼륨을 몰라도 됩니다. 대가는 원본/overlay 재생이
항상 gait-analysis가 떠 있어야 가능하다는 것인데, 이건 이미 D-024/D-029이 받아들인
전제입니다(스크리닝·보행 분석 모두 profile로 꺼둘 수 있는 서비스).

### 디렉터리 구조를 더 다듬을지

지금은 `dog_id` 검증이 없어(§9) 폴더를 `uploads/<dog_id>/<record_id>.mp4`처럼
나누는 게 아직 이릅니다 — 검증되지 않은 값으로 경로를 만들면 그 자체가 취약점이
됩니다. dogs 스키마가 확정되고 서버가 `dog_id`를 검증한 뒤에 재고하는 것을
제안합니다. 지금 구조(flat + uuid)로도 DB가 `record_id → dog_id` 매핑을 가지므로
조회 성능에는 문제가 없습니다.

---

## 4. 강아지별 보행 기록 목록 조회

지금 `gait-analysis`에 `record_store.records_for_dog(dog_id)`가 있지만 **전체 JSON
파일을 순회**합니다(파일럿 규모 한정, 코드 주석에도 명시) 및 **`dog_id`를 그대로
신뢰**합니다 — 인증된 소유권 검사가 없습니다.

### 제안: 목록 API는 daengs_backend가, gait-analysis 쪽은 그대로 둔다

```
GET /api/dogs/{dog_id}/gait-records?cursor=...&limit=20
```

- backend가 요청자(app_user)와 `dog_id`의 소유 관계를 검증(dogs 스키마 확정 후,
  §9)한 다음 `SELECT ... FROM gait_records WHERE dog_id = :dog_id AND deleted_at
  IS NULL ORDER BY captured_at DESC, id DESC` — keyset(cursor) 페이지네이션 권장.
  반려견 한 마리의 보행 기록이 반복 촬영으로 쌓이는 시나리오(이미 walk_demo에서
  반복 촬영 파일럿을 실행한 적 있음)라 offset 페이지네이션은 나중에 밀립니다.
- 목록 항목은 가벼운 필드만: `record_id, captured_at, analyzed_at, status,
  quality_tier`. `internal_feature_vector`는 물론 `summary_for_ui`도 목록에는
  불필요 — 상세 조회에서만.
- `gait-analysis`의 `records_for_dog`는 지금처럼 파일 스캔 기반의 **개발·디버깅
  보조 함수**로 남겨 두는 것을 제안합니다(DB가 생기기 전까지는 유일한 방법이기도
  합니다).

---

## 5. 기록 상세 조회

```
GET /api/gait-records/{record_id}
```

- backend가 `deleted_at IS NULL` 행을 찾고 소유권 검증.
- 응답 스키마는 **명시적으로** 다음을 제외합니다 — 필드를 안 넣는 게 아니라
  Pydantic 응답 모델 자체에 그 필드가 없어야 합니다(직렬화 단계에서 걸러지도록):
  - `internal_feature_vector`
  - (향후 비교 응답에서) `_dev_only_raw_feature_cosine`,
    `_dev_only_n_common_feature_dims`
- 반환: `record_id, dog_id, captured_at, analyzed_at, status, quality,
  quality_tier, summary_for_ui, gait_filter_version, original_video_url,
  overlay_video_url, note`.
- `*_video_url`은 DB의 경로 문자열이 아니라 `record_id`로 만든 조회용 URL입니다
  (§3).

### 기존 gait-analysis 엔드포인트의 참고 사항

지금 `GET /v1/records/{id}`(gait-analysis)는 저장된 JSON을 **그대로** 돌려줍니다 —
`internal_feature_vector`까지 포함해서요. 이건 의도된 설계입니다(§1) — 이 엔드포인트의
소비자가 daengs_backend(신뢰된 내부 호출자)가 되는 것을 전제로 하고, **사용자에게
보이지 않게 거르는 책임은 daengs_backend의 응답 스키마 쪽**에 둡니다.
`gait-analysis/CLAUDE.md`에 이미 "`_dev_only_*`를 사용자 응답으로 승격하지 말라"는
경고가 있는데, 지금은 앱 쪽의 사회적 계약("표시하지 마세요" 문서화)에 기대고
있습니다 — daengs_backend가 앞단에 서면 이게 **서버 강제**로 바뀝니다. 이번 카드에서
이 필터링 로직을 만들지는 않았습니다(§10) — daengs_backend 쪽 스키마가 아직 없기
때문입니다.

원본 영상 조회 엔드포인트(`GET /v1/records/{id}/original`)는 gait-analysis에
**아직 없습니다** — `overlay`용 엔드포인트와 대칭으로 추가하는 것을 제안합니다
(파일은 이미 `original_video` 필드로 추적되므로 §10, 코드 몇 줄이면 됩니다).

---

## 6. record 간 비교

**이미 구현되어 있고, 요구사항과 일치합니다.** `gait-analysis`의 `POST
/v1/compare`(`pipeline.compare_records`)는 저장된 `features.summary_for_ui`와
`features.internal_feature_vector`만 읽습니다 — **원본 영상을 다시 열지 않습니다.**
`gait_filter_version`이 다르면 경고를 붙이고, 정성적 서술("차이 관찰됨"/"비슷함")만
반환하며 `_dev_only_*` 필드는 문서상 비노출 대상입니다.

### 제안: backend가 앞단에서 "같은 개체인지"만 확인하고 위임

```
POST /api/gait-records/compare  { "record_id_a": ..., "record_id_b": ... }
```

1. backend: 두 record 모두 조회 + 소유권 검증 + **`dog_id`가 서로 같은지 확인**
2. backend → gait-analysis: `POST /v1/compare` (내부 호출, 기존 그대로)
3. gait-analysis 응답에서 `_dev_only_*`를 제거하고 앱에 반환

**주의할 점 하나**: 지금 `compare_records()`는 두 record의 `dog_id`가 다른지
검사하지 않습니다 — 순수 파일 저장소라 신뢰할 방법이 없기 때문입니다(README의
`dog_id` 검증 TBD와 같은 자리). 다른 개체의 기록끼리 비교해도 지금은 그냥
숫자가 나옵니다. **"같은 개체인지" 검증은 자연스럽게 backend의 책임**이 됩니다 —
gait-analysis는 그걸 판단할 근거(계정·소유권)가 없고, backend는 있습니다.

---

## 7. 기록 삭제 시 데이터 정리 방식 (설계만 — 구현하지 않음)

```
DELETE /api/gait-records/{record_id}
```

정리 대상 4가지 (요구사항과 동일):

1. `gait_records` 행 (또는 아래 소프트 삭제)
2. `original_video_path`가 가리키는 원본 파일
3. `overlay_video_path`가 가리키는 overlay 파일
4. 분석 feature — 향후 DB로 옮기면 이건 **행 자체 안에 있으므로**(JSONB 컬럼) 행
   삭제가 곧 feature 삭제입니다. 지금 구조(파일)라면 `records/<id>.json` 삭제.

### 왜 "즉시 물리 삭제"가 위험한가 — gait-analysis는 상시 켜져 있지 않다

`gait-analysis`는 compose `profiles: ["gait"]`로 **기본적으로 꺼져 있는 서비스**입니다
(D-024와 같은 운영 방식). 삭제 요청이 왔을 때 gait-analysis가 내려가 있으면 DB
행만 지우고 파일은 못 지우는 상황이 생길 수 있습니다 — 반대로 파일 삭제를 먼저
기다리면 DB 삭제가 gait-analysis 가동 여부에 묶입니다.

### 제안: 소프트 삭제 + 비동기 정리

1. `DELETE` 요청 → `gait_records.deleted_at = NOW()`만 찍습니다(즉시, 목록/상세
   조회에서 바로 제외됨 — `WHERE deleted_at IS NULL`).
2. 파일 정리는 별도 정리 작업이 맡습니다 — gait-analysis가 살아있을 때
   `deleted_at IS NOT NULL` 행을 스캔해 파일을 지우고(또는 backend가 gait-analysis에
   `DELETE /v1/records/{id}` 내부 호출), 성공하면 DB 행을 하드 삭제하거나
   `files_purged_at`을 채웁니다.
3. gait-analysis가 꺼진 상태에서도 **사용자 경험은 즉시 삭제된 것처럼** 보입니다
   (소프트 삭제가 목록에서 바로 사라지므로) — 파일 정리 지연은 사용자에게 보이지
   않습니다.

대안(더 단순하지만 대가가 있음): gait-analysis가 반드시 떠 있어야 삭제가 완료되도록
동기 처리. 구현은 쉽지만 "삭제하려는데 서비스가 꺼져 있어 안 됩니다" 같은 에러를
사용자가 보게 됩니다. **소프트 삭제 쪽을 권장**하지만 최종 판단은 팀 결정입니다.

이번 카드에서는 위 흐름을 **설계만** 했습니다 — `deleted_at` 컬럼도, 정리 작업도,
gait-analysis 쪽 `DELETE` 엔드포인트도 만들지 않았습니다.

---

## 8. 업로드 크기 제한 — 제안값과 근거 (구현됨)

skin-screening의 12MB(사진 한 장)를 그대로 쓰지 않았습니다 — 사진과 영상은 자릿수가
다릅니다.

| 근거 | 값 |
| --- | --- |
| 파일럿 원본 클립 실측 (`walk_demo/data/external_pilot/videos/*.mp4`, 13개) | 0.57MB ~ 3.3MB |
| 실제 Android 1080p 촬영 추정 (15~20Mbps, 20~30초) | 약 40~75MB |
| nginx `location /gait/`의 `client_max_body_size` (이미 설정되어 있었음) | 200MB |

**기본값: 150MB** (`GAIT_MAX_UPLOAD_BYTES`, 환경변수로 조정 가능).

- 실측 파일럿 클립보다 훨씬 크게 잡은 이유: 파일럿 클립은 유튜브에서 내려받아
  이미 압축된 영상이고, **실제 스마트폰 촬영본은 훨씬 큽니다** — 위 추정치(약
  40~75MB)의 약 2배 여유를 뒀습니다.
- nginx의 200MB보다 **반드시 낮게** 유지해야 하는 이유: nginx가 먼저 자르면 사용자는
  이유 없는 HTML 에러 페이지를 보고, 앱보다 낮은 한도에서 앱이 먼저 자르면
  "왜 잘렸는지"가 담긴 JSON을 받습니다. 두 값 중 하나를 바꾸면 다른 쪽도 같이
  확인해야 합니다 — `gait-analysis/README.md`와 `.env.example`에 그 경고를
  남겨 두었습니다.

구현: `src/config.py`의 `MAX_UPLOAD_BYTES`, `serve.py`의 `/v1/analyze`가
`Content-Length` 헤더로 먼저 거절(본문을 다 읽기 전에) → 헤더가 없거나 틀렸을
경우를 대비해 실제로 읽은 바이트 수로 다시 확인(skin-screening과 같은 이중 확인
패턴). 초과 시 `413`, 사용자에게 실제 크기·한도·안내 문구를 포함한 메시지를
반환합니다.

---

## 9. 일반 DB가 없어 TBD로 남겨야 하는 항목

- **`dog_id`의 실제 타입/FK.** `dogs` 테이블 자체가 없습니다. uuid FK가 될지 다른
  식별자를 쓸지 미정.
- **소유권 체인.** `dog → owner(app_user)`가 정해져야 목록/상세/삭제의 권한 검사가
  가능합니다. 지금은 아무 `dog_id` 문자열이나 그대로 신뢰됩니다(gait-analysis
  README에 이미 있는 TBD).
- **`gait_records` 실제 column type·제약·인덱스.** 위 §2 표는 안(案)입니다.
- **daengs_backend ↔ gait-analysis 내부 호출 방식.** §1의 (a)/(b) 중 결정 안 함.
  요청 큐잉(Celery/Redis — `docs/decisions.md`의 D-019가 이미 "나중에 Celery
  워커가 같은 Redis 인스턴스를 쓴다"고 언급) 여부도 열려 있음.
- **삭제 정책.** 소프트 삭제 여부, 파일 정리 재시도 전략, "언제 하드 삭제할지".
- **기존에 이미 쌓인 로컬 JSON 기록의 이관 여부·방법.** walk_demo·gait-analysis
  로컬에 쌓인 기록을 새 DB로 옮길지, 옮긴다면 스크립트를 어떻게 짤지.
- **인증.** `gait-analysis`도 `daengback` 전체도 지금 인증이 없습니다(기존 TBD와
  같은 자리) — 이 문서의 모든 "소유권 검증"은 인증이 붙는 것을 전제로 합니다.

---

## 10. 이번 카드에서 실제로 반영한 것 / 반영하지 않은 것

### 반영함 (코드)

- `gait-analysis/src/config.py`: `MAX_UPLOAD_BYTES` (env `GAIT_MAX_UPLOAD_BYTES`,
  기본 150MB).
- `gait-analysis/serve.py`: `/v1/analyze`에 413 처리 (Content-Length 사전 확인 +
  실제 바이트 사후 확인, 이중 확인). **크기·빈 파일 검사를 `src.pipeline` /
  `src.video_intake` import보다 위에 둡니다** — 그 두 모듈이 torch·ultralytics를
  끌고 오기 때문입니다. 순서가 뒤바뀌면 기본 설치(`uv sync`, `--extra model` 없음)에서
  413이어야 할 응답이 ImportError가 되고, 거절할 요청 때문에 매번 모델을 올리게 됩니다.
- `gait-analysis/src/pipeline.py`: `record["original_video"]` 필드 추가 — 지금까지
  `overlay_video`만 기록에 남고 원본 경로는 record에 없던 비대칭을 해소했습니다
  (요구사항 §1 "각 기록은 최소한 원본 영상과 연결되어야 한다"에 대응). 분석 상태와
  무관하게(`quality.status`가 `unavailable`이어도) 항상 남깁니다 — 재촬영 여부와
  별개로 원본은 계속 보관되므로.
- `gait-analysis/tests/test_upload_limit.py`: 413/400 스모크 테스트 3개 추가.
  세 번째(`test_rejects_oversized_without_model_extra`)는 `sys.modules`에 None을 넣어
  **torch가 없는 환경을 흉내 내고**, 그 상태에서도 413이 나오는지 확인합니다 —
  위 import 순서를 되돌리면 이 테스트가 먼저 깨집니다.
- `gait-analysis/pyproject.toml` · `uv.lock`: `httpx`를 dev 그룹에 **선언**
  (`uv add --group dev httpx`). 아래 "의존성 확인" 참고.
- `.env.example`, `docker-compose.yml`(gait-analysis 서비스 `environment`),
  `gait-analysis/README.md`: 위 변경 반영 + API 표 갱신 + TBD 갱신.

### 의존성 확인 (2026-08-29, 지적받고 재검토)

처음 작성했을 때 **`httpx`를 선언하지 않고 썼습니다.** 로컬에서는 테스트가 통과했지만,
그건 `httpx`가 `ultralytics-platform`(→ `ultralytics` → `--extra model`)의 전이
의존성으로 우연히 깔려 있었기 때문입니다. 기본 설치(`uv sync`)만 한 개발자에게는
`fastapi.testclient` import부터 실패했을 상황입니다 — pyproject가 명시한
"기본 = 화면과 계약만 보는 데 필요한 것(torch 없음)"이라는 가름을 깨는 자리입니다.

두 가지를 고쳤습니다.

1. **`uv add --group dev httpx`** — `pyproject.toml`을 직접 고치지 않았습니다
   (CLAUDE.md 규칙: 직접 고치면 `uv.lock`과 어긋나고, 컨테이너 command가
   `uv sync --frozen`이라 거기서 멈춥니다). 잠금 파일 변화는 6줄, 다른 패키지
   버전 변동 없음. `dev` 그룹은 이미 `pytest`가 들어 있고 `uv sync`가 기본으로
   설치하는 그룹이라 컨테이너의 설치 범위에 새 선례를 만들지 않습니다.
2. **import 순서 교정** (위 `serve.py` 항목).

검증:

| 확인 | 결과 |
| --- | --- |
| 기본 설치 venv(`uv sync --frozen`, 별도 경로)에 torch·ultralytics 부재 | 확인 — 둘 다 ABSENT, httpx만 존재 |
| 그 환경에서 전체 스위트 | **17 passed** |
| 개발 venv(`--extra model` 포함) 전체 스위트 | **17 passed** |
| 테스트 소요 시간 (torch 로드 여부의 실측 지표) | 10.06s → **0.54s** |
| `uv lock --check` (컨테이너 `--frozen`이 요구하는 정합성) | 통과 |

### 반영하지 않음 (이 문서의 설계로만 남김)

- 일반 PostgreSQL 테이블 생성, migration, `db/init/` 수정, `POSTGRES_*` 설정 변경.
- `daengs_backend`의 gait 관련 router/service/repository/model/schema 전부.
- `dogs`/`users` 관련 테이블, `dog_id` FK 확정.
- 목록/상세/비교의 실제 backend API 구현(§4·§5·§6은 설계만).
- 삭제 엔드포인트 및 파일 정리 로직(§7, gait-analysis·backend 양쪽 다) — 소프트
  삭제 컬럼(`deleted_at`)도 추가하지 않았습니다.
- `GET /v1/records/{id}/original` (gait-analysis, overlay와 대칭인 원본 재생
  엔드포인트) — 파일 경로는 이제 record에 있지만(위 "반영함" 참고) 엔드포인트 자체는
  다음 단계로 미뤘습니다. 목록 조회(`records_for_dog`)를 HTTP로 노출하는 것도 마찬가지.
- `compare_records()`의 `dog_id` 일치 검증(§6에서 권장사항으로만 기록).
- daengs_backend 응답 스키마에서 `internal_feature_vector`/`_dev_only_*`를 거르는
  로직 — backend 쪽 스키마 자체가 아직 없어 만들 대상이 없습니다.
