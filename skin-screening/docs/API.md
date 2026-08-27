# 응답 계약 v1.0 — 앱이 볼 문서

앱(DAENGS_APP)이 이 서버에 붙을 때 지켜야 하는 것. 코드는 `src/agent.py` 의
`CONTRACT_VERSION` 과 `contract()` 이고, `tests/test_agent.py` 가 감시합니다.

`--mock` 으로 띄우면 **모양이 똑같은 가짜 응답**이 나옵니다 — 가중치 없이 붙여볼 수
있습니다 (`meta.mock` 이 `true`).

---

## 요청

```
POST /v1/screen        multipart/form-data
  photo  (필수)  사진 한 장. 12MB 까지
  box    (선택)  가이드 프레임. 정규화 JSON 문자열 "[x, y, w, h]" (0~1, 원본 기준)
```

```
GET  /healthz          살아있나 + mock 인가 + 임계값
GET  /                 데모 화면
GET  /docs             FastAPI 자동 생성 문서
```

**`box` 를 주세요.** 그 네모가 그대로 bbox 가 되어 학습과 **같은 함수**로 잘립니다.
안 주면 화면 중앙으로 물러서는데, 1단계는 중심만 쓰므로 큰 차이가 없지만 2단계는
학습 크롭과 어긋납니다 (`meta.box_source` 가 `"center"` 로 옵니다).

### 네모를 얼마나 정확히 그려야 하나

정답 대비 **크기 0.59~1.43배** 가 허용, **0.71~1.18배** 가 권장입니다.
1.5배로 그리면 이미 허용 밖입니다. 서버가 추론 **전에** 검사해서 밴드 밖이면
모델을 돌리지 않고 `verdict: "retake"` 로 돌려보냅니다 — `meta.retake_reason` 에
보호자에게 그대로 보여줄 한국어가 들어 있습니다.

---

## 응답

```jsonc
{
  "contract_version": "1.0",
  "verdict": "abnormal",              // "normal" | "abnormal" | "retake"
  "headline": "피부에 이상 소견이 보입니다.",
  "body":     "어떤 병변인지는 이 사진만으로 판단할 수 없습니다. …",
  "action":   "수의사 진료를 받아보시기를 권합니다.",

  "stage1": {
    "abnormal_prob": 0.9161, "abnormal_percent": 91.6,
    "threshold": 0.1823,
    "calibrated": false               // ⚠️ 아직 보정 안 됨 — 아래 "한계" 참고
  },

  "stage2": {
    "shown": true,
    "distribution": [                 // 확률 내림차순. 개수가 고정이 아닙니다 (아래 "한계")
      {"code": "A5", "name_ko": "미란·궤양", "name_en": "Erosion / Ulcer",
       "prob": 0.8843, "percent": 88.4},
      {"code": "A4", "name_ko": "농포·여드름", "name_en": "Pustule / Acne",
       "prob": 0.1092, "percent": 10.9},
      {"code": "A6", "name_ko": "결절·종괴", "name_en": "Nodule / Mass",
       "prob": 0.0065, "percent": 0.7}
    ]
  },

  "text": "…",                        // 터미널/디버그용 전문. 앱은 안 씁니다
  "disclaimer": "이 결과는 수의학적 진단이 아니며…",
  "meta": {
    "elapsed_ms": 614.7, "mock": false,
    "stage1_crop": "f320", "stage2_crop": "m2.5",
    "stage1_temperature": 1.0,
    "box_source": "user",             // "user" | "center"
    "guide": {"ok": true, "reason": "", "width_frac": 0.4, "center_off": 0.0},
    "crop_note": "…"                  // 그때그때 남는 한계를 글로
  }
}
```

### verdict 세 가지

| verdict | 언제 | 앱이 그릴 것 |
| --- | --- | --- |
| `normal` | 1단계가 임계값 미만 | "정상으로 보입니다" + 면책. 분포 없음 |
| `abnormal` | 1단계가 임계값 이상, 2단계가 거절 안 함 | 이상 가능성 + **분포** + 진료 권함 |
| `retake` | 네모가 밴드 밖 **또는** 2단계 확신이 너무 낮음 | "다시 찍어주세요" + 이유. 분포 없음 |

`stage2.shown` 이 `false` 면 분포 영역을 **통째로 그리지 않으면** 됩니다.

### 병변 6종

| 코드 | 병변 | 영어 |
| --- | --- | --- |
| A1 | 구진·플라크 | papule / plaque |
| A2 | 비듬·각질·상피성잔고리 | scale / crust / epidermal collarette |
| A3 | 태선화·과다색소침착 | lichenification / hyperpigmentation |
| A4 | 농포·여드름 | pustule / acne |
| A5 | 미란·궤양 | erosion / ulcer |
| A6 | 결절·종괴 | nodule / mass |

**병명이 아니라 병변 "형태" 입니다.** "아토피" 같은 진단명이 아닙니다.

---

## ★ "1등 병변" 필드가 없습니다 — 실수가 아닙니다

holdout 에서 2단계가 고른 이름이 **56.6% 틀렸습니다.** 필드로 주면 앱은 그걸 화면
제일 크게 띄웁니다. 그래서 계약에서 아예 뺐고, `tests/test_agent.py` 가 `top1`
`predicted` `diagnosis` 같은 키가 생기는지 감시합니다. 근거는
[`docs/decisions.md`](../../docs/decisions.md) **D-019**.

### 앱 쪽 화면 규칙

1. `distribution[0]` 을 뽑아 크게/굵게 쓰지 않습니다 — 줄들을 **같은 무게**로
2. 받은 줄을 **전부** 보여줍니다. 상위 1~2개로 더 자르면 그게 답처럼 읽힙니다
3. `body`("판단할 수 없습니다")를 숫자보다 **위에** 놓습니다
4. `disclaimer` 를 접거나 회색으로 숨기지 않습니다
5. 병변별 긴급도 문구를 앱이 자체적으로 붙이지 않습니다 — 이름을 단정하는 셈입니다

`demo/index.html` 이 이 규칙대로 그려진 화면입니다. Compose 로 옮길 때 쓸 값
(말풍선 모서리 dp, 막대 색, 한국어 줄바꿈 `LineBreak.Paragraph`)은 원본 저장소의
`docs/SERVING.md` "Compose 로 옮길 때" 절에 있습니다.

---

## 오류 응답

| 코드 | 뜻 |
| --- | --- |
| 400 | 빈 파일 |
| 413 | 12MB 초과 |
| 415 | 이미지로 열리지 않음 |
| 422 | `box` 가 정규화 `[x, y, w, h]` JSON 배열이 아님 |

FastAPI 기본 형식(`{"detail": "…"}`)이고 메시지가 한국어라 그대로 띄워도 됩니다.

---

## 한계 — 붙이기 전에 읽어주세요

* **`stage1.calibrated` 가 `false` 입니다.** `abnormal_percent` 는 순서는 맞지만
  "62% 면 100번 중 62번" 이라는 뜻이 아직 아닙니다. 게이지·색으로 쓰는 건 괜찮고,
  숫자를 확률처럼 설명하는 문구는 붙이지 마세요.
* ⚠️ **분포 줄 수가 `--mock` 과 진짜 모델이 다릅니다.**

  | | 줄 수 |
  | --- | --- |
  | `--mock` | **6줄** (6종 전부) |
  | `--release` (진짜 가중치) | **3줄** — `config.topk_report = 3` 이 상위 3개로 자릅니다 |

  6종을 다 주는 게 설계 의도인데 코드가 3으로 잘라 놓았습니다. **앱은 받은
  개수만큼 그리도록** 만들어 두세요 — mock 으로 6줄 화면을 만들어 놓고 실제에서
  3줄이 오면 놀라게 되고, 이 값이 6으로 고쳐져도 화면이 안 깨져야 합니다.
  (원본 저장소에서 정리할 일입니다)
* **사용자가 그린 네모는 라벨러가 그린 네모와 분포가 다릅니다.** 라벨러는 답을
  알고 그렸습니다. 그 차이가 2단계에 얼마나 영향을 주는지는 아직 안 쟀습니다 —
  재는 도구는 `tools/box_error.py` 입니다.
* **모델이 확정 전입니다.** 2단계 백본을 `resnet50` → `convnextv2_base` 로
  재학습할 예정입니다. **계약(이 문서)은 안 바뀌고 숫자만 바뀝니다.**
* **이 서버는 데모입니다.** 인증·레이트 리밋·HTTPS 가 없습니다. 사진은 디스크에
  저장하지 않고 메모리에서 처리한 뒤 버립니다. 공개 주소에 그대로 띄우지 마세요.
