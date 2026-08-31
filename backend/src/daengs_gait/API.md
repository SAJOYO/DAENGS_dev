# 보행 분석 API 계약 v1 — 앱이 볼 문서

앱이 이 서비스에 붙을 때 지켜야 하는 것. 코드는 `service.py` 이고
`backend/tests/test_gait_*.py` 가 감시합니다.

`skin-screening` 과 같은 모양으로 nginx 뒤에 붙습니다 — 앱이 부르는 주소는
**`http://daengback.~/gait/...`** 이고, nginx 가 `/gait` 접두사를 떼서 넘깁니다.
아래 경로는 전부 접두사를 뗀 뒤 기준입니다.

⚠️ **분석은 분 단위입니다.** 사진 한 장이 아니라 영상 전체를 5fps 로 훑고 overlay 까지
   인코딩합니다 (실측: 480×854 · 37초 영상에 CPU 약 2분). nginx 가
   `proxy_read_timeout 600s` 로 열어 두었지만, 앱은 **긴 대기를 전제로** 만들어야 합니다.

⚠️ **인증이 없습니다.** `dog_id` 는 넘어온 값을 그대로 믿습니다 — 아래 §소유권 참고.

---

## 엔드포인트

| Method · Path | 용도 |
| --- | --- |
| `POST /v1/analyze` | 영상 업로드 + 분석 |
| `GET /v1/records` | 기록 목록 (`dog_id` 필수) |
| `GET /v1/records/{record_id}` | 기록 단건 |
| `GET /v1/records/{record_id}/overlay` | 스켈레톤 영상 |
| `DELETE /v1/records/{record_id}` | 기록 삭제 (영상 포함) |
| `POST /v1/compare` | 두 기록 비교 |
| `GET /healthz` | 상태 · 가중치 유무 |

---

## `record_id`

**32자 소문자 16진수**입니다 (`uuid4().hex`).

```
5389c92e7f4b41d8a3c6e0192b7d4f8a
```

이 값이 **유일한 공개 식별자**입니다. 서버 안의 파일 이름은 별개이고 앱에 노출하지
않습니다 — 영상은 항상 `record_id` 로 된 URL 로 가져갑니다.

---

## `POST /v1/analyze`

```
POST /v1/analyze        multipart/form-data
  video    (필수)  영상 파일. 기본 150MB 까지 (GAIT_MAX_UPLOAD_BYTES)
  dog_id   (선택)  같은 개체의 기록을 묶는 값
  date     (선택)  촬영일 "YYYY-MM-DD"
  note     (선택)  메모
```

⚠️ **`dog_id` 를 안 주면 목록 조회로 다시 찾을 수 없습니다.** 목록은 `dog_id` 로만
   거르기 때문입니다. 앱은 항상 주는 것을 권합니다.

⚠️ **H.264 로 올리세요.** 서버 컨테이너에 **AV1 디코더가 없습니다.** AV1 을 올리면
   프레임을 하나도 못 읽어 `quality.status: "unavailable"` 이 나오는데, 그 안내 문구가
   "다시 촬영해 주세요" 라 사용자가 원인을 알 수 없습니다 (알려진 문제 —
   `docs/worklog-gait.md` 3-2). 스마트폰 촬영본은 H.264 라 보통 문제없습니다.

### 응답 200 — 분석 성공

```jsonc
{
  "record_id": "5389c92e7f4b41d8a3c6e0192b7d4f8a",
  "source_file": "walk_0831.mp4",        // 사용자가 올린 이름 (화면 표시용)
  "date": "2026-08-31",
  "note": null,
  "dog_id": "dog-123",
  "created_at": "2026-08-31T09:42:59.412993+00:00",
  "video_meta": {"resolution": "480x854", "native_fps": 30.0},

  "quality": {
    "status": "ok",                      // ok | unavailable
    "quality_tier": "low",               // good | low   (status=ok 일 때만)
    "quality_note": "분석은 가능하지만 유효 프레임이 적어 …",
    "reason": null,                      // status=unavailable 일 때 사유
    "recommendation": null,              // status=unavailable 일 때 안내
    "n_frames_sampled": 186,
    "n_frames_detected": 47,
    "n_frames_gait_usable": 15,
    "detection_rate": 0.253,
    "gait_usable_rate": 0.081,
    "exclude_reason_counts": {"keypoints_collapsed": 14, "insufficient_keypoints": 12}
  },

  "gait_filter_version": "v5-stationary-speed-based-20260826",

  "has_overlay": true,
  "overlay_url": "/v1/records/5389c92e…/overlay",   // 앱은 /gait 를 앞에 붙여 부릅니다
  "overlay_error": null,                             // 인코딩 실패 시 사유

  "trajectories": [ … ],                 // 관절별 좌표 시계열
  "features": {
    "summary_for_ui": { "Iliac crest": {"x_range": 1.0, "y_range": 0.5}, … },
    "internal_feature_vector": { … },    // ⚠️ 화면에 절대 노출 금지 — 아래
    "n_frames_used": 15
  }
}
```

`status: "unavailable"` 이면 `overlay_*` · `trajectories` · `features` 가 **없습니다.**

### ⚠️ 화면에 내면 안 되는 값

**`internal_feature_vector` 와 `_dev_only_*` 접두어가 붙은 값은 UI 에 노출하지
마세요.** 빠뜨린 게 아니라 일부러 가둔 것입니다 — 수백 개의 숫자가 화면에 나오면
사용자는 그것을 **건강 점수로 읽습니다.** 이 서비스는 진단이 아니라 **같은 개체의
시간 변화 관찰**입니다.

화면에 쓸 것은 `features.summary_for_ui` 와 `/v1/compare` 의 `message_for_ui` 입니다.

### 오류

| 코드 | 언제 |
| --- | --- |
| 400 | 빈 파일 · 영상을 읽을 수 없음 |
| 413 | 파일이 한도(기본 150MB)를 넘음 |
| 503 | 가중치가 서버에 없음 (환경이 덜 갖춰진 것) |

---

## `GET /v1/records`

```
GET /v1/records?dog_id=dog-123&limit=20&cursor=<record_id>
```

| 파라미터 | 필수 | 기본 | |
| --- | --- | --- | --- |
| `dog_id` | ✅ | — | 없으면 **400** |
| `limit` | | 20 | 1~100 |
| `cursor` | | — | 이전 응답의 `next_cursor` |

### 응답 200 — **요약만** 담습니다

```jsonc
{
  "records": [
    {
      "record_id": "5389c92e…",
      "date": "2026-08-31",
      "created_at": "2026-08-31T09:42:59.412993+00:00",
      "source_file": "walk_0831.mp4",
      "note": null,
      "quality_status": "ok",
      "quality_tier": "low",              // status=unavailable 이면 null
      "gait_filter_version": "v5-…",
      "has_overlay": true,
      "comparable": true                  // /v1/compare 대상이 될 수 있는가
    }
  ],
  "next_cursor": null                     // 더 없으면 null
}
```

**`trajectories` · `features` 는 목록에 없습니다.** 한 건이 15KB 라 20건이면 300KB 가
되고, 앱이 목록 화면에서 쓰지 않는 값입니다. 필요하면 단건 조회를 쓰세요.

- **`comparable`** — `quality.status == "ok"` 인지. 비교 화면에서 고를 수 있는 것만
  보여주는 데 씁니다. `unavailable` 기록으로 compare 하면 거절됩니다.
- **`gait_filter_version`** — 서로 다른 버전끼리 비교하면 응답에 경고가 붙습니다.
  고르기 **전에** 앱이 알 수 있게 목록에 넣었습니다.

**정렬은 오래된 것부터**입니다 (`date`, 없으면 `created_at`). 시간 변화를 보는
서비스라 시계열 순서가 자연스럽습니다.

---

## `DELETE /v1/records/{record_id}`

기록 JSON 과 **영상 파일(원본·overlay)까지 즉시 지웁니다.**

### 응답 200

```jsonc
{
  "record_id": "5389c92e…",
  "deleted": {"record": true, "original": true, "overlay": true}
}
```

### 응답 500 — 일부만 지워졌을 때

```jsonc
{
  "detail": {
    "message": "기록 삭제가 완전히 끝나지 않았습니다.",
    "record_id": "5389c92e…",
    "deleted": {"record": true, "original": false, "overlay": true},
    "errors": {"original": "…"}
  }
}
```

⚠️ **부분 실패를 성공으로 감추지 않습니다.** 개인 데이터 삭제라, 파일이 남았으면
응답이 그것을 말해야 합니다. 서버 로그에도 남습니다.

`404` 는 없는 `record_id` 입니다.

---

## `POST /v1/compare`

```jsonc
POST /v1/compare
{"record_id_a": "…", "record_id_b": "…"}
```

두 기록이 모두 `quality.status == "ok"` 여야 합니다. 아니면 `status: "unavailable"` 과
사유가 돌아옵니다.

응답에서 화면에 쓸 것:

- **`message_for_ui`** — 비교 결과 한 줄. 실제 계산에서 유도됩니다
  (차이 있음 / 뚜렷한 차이 없음 / 비교할 수 있는 지표가 없음)
- `joint_movement_range_comparison` — 관절별 `"차이 관찰됨"` / `"비슷함"`
- `reliability_note` — 유효 프레임이 적을 때
- **`version_warning`** — 두 기록의 필터 버전이 다를 때. **표시하세요** — 같은 영상이라도
  버전이 다르면 이동범위가 달라 보입니다
- `diff_threshold_note` — "차이 관찰됨" 의 기준

⚠️ `_dev_only_raw_feature_cosine` 과 `_dev_only_n_common_feature_dims` 는 **노출 금지**입니다.

---

## `GET /healthz`

```jsonc
{"status": "ok",
 "ready": true,                          // 가중치가 실제로 있는가
 "weights": {"pose": {"found": true}, "detector": {"found": true}},
 "gait_filter_version": "v5-…"}
```

`ready: false` 면 분석 요청이 503 입니다. **컨테이너는 떠 있어도 가중치가 없을 수
있습니다** — 그 상태를 조용히 넘기지 않으려고 둔 필드입니다.

---

## 소유권 — ⚠️ **이 서비스가 검증하지 않습니다. 상위 계층의 몫입니다**

**`dog_id` 필터는 보안 장치가 아닙니다.**

- 목록 조회가 `dog_id` 를 요구하는 것은 **남의 기록이 전부 쏟아지는 것을 막기 위한
  최소한**이지, 그 `dog_id` 가 부르는 사람의 것인지 **검증하지 않습니다.**
- 남의 `dog_id` 를 넣으면 **남의 기록이 보이고 삭제됩니다.**

### 검증은 `daengs_backend` 의 auth 계층에서 합니다

이 서비스는 **인증·인가를 구현하지 않습니다.** 별도 프로세스로 떼어 둔 추론 전용
서비스이고(D-029 · D-038), 계정·세션·반려견 소유 관계를 아는 것은 `daengs_backend`
입니다. 여기에 auth 를 넣으면 그 지식이 두 곳으로 갈라지고 서로 어긋납니다.

앱을 붙일 때의 전제:

```
앱 → daengs_backend (인증 · dog_id 소유권 검증) → gait
```

**gait 를 앱에 직접 노출하지 마세요.** 지금 `daengback.~/gait/*` 가 열려 있는 것은
개발·검증 편의이고, compose `profiles: ["gait"]` 뒤에 꺼둔 채로 들여온 이유가
그것입니다 (스크리닝과 같은 상태 — D-024). **켜는 시점은 사람이 정합니다.**

### gait 쪽이 지키는 것

상위 계층이 소유권을 강제할 수 있도록 이쪽은 **연결 고리만** 유지합니다:

- **모든 기록이 `dog_id` 를 가집니다.** 분석 요청에서 받은 값을 기록에 그대로 담고,
  목록·삭제가 전부 그 값을 통해 접근합니다.
- **DB 로 옮길 때 `dog_id` 가 외래 키가 되는 자리입니다.** 지금은 검증 없는 문자열이지만
  구조상 관계를 맺을 지점이 이미 열려 있습니다 —
  `docs/gait-record-data-design.md` 의 스키마 제안이 그 모양입니다.
- **이 서비스는 `dog_id` 의 의미를 해석하지 않습니다.** 묶는 열쇠로만 씁니다.
  그래야 상위가 어떤 인가 모델을 고르든 여기를 안 고칩니다.

⚠️ `dog_id` 없이 분석하면 그 기록은 **목록으로 다시 찾을 수 없습니다** (목록이 `dog_id`
로만 거릅니다). 단건 조회로 `record_id` 를 직접 아는 경우에만 접근됩니다.

---

## 이번 범위에 없는 것

| | 왜 |
| --- | --- |
| `GET /v1/records/{id}/original` (원본 재생) | 앱 요구사항이 확정되지 않았습니다. 필요해질 때 추가합니다 |
| 비동기 job/poll | 지금은 동기입니다. 미래 모양은 `docs/orchestration-contracts.md` 의 `job: {job_id, poll}` |
| 인증·소유권 | 위 참고 |
| URL 업로드 (`yt-dlp`) | 코드는 있으나 엔드포인트를 두지 않았습니다 |
