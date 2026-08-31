# 보행 분석(gait) 작업 기록 — YH-KIKI

이 문서는 **보행 분석 담당(YH-KIKI)이 한 작업만** 시간순으로 남깁니다.
팀 전체 이력이 아닙니다 — 다른 사람의 카드는 여기 적지 않습니다.

- **왜 따로 두나** — `docs/decisions.md` 는 "무엇을 정했나"(결정)이고, PR 본문은
  "이 카드에서 무엇을 하나"(작업 명세)입니다. 그 둘 사이에 **"내가 어디까지 했고 다음에
  뭘 이어야 하나"** 를 놓을 자리가 없어서 이 파일을 둡니다.
- **어디를 봐야 하나** — 결정의 근거는 `decisions.md` 의 D-번호로, 작업의 세부는 PR
  번호로 각각 넘어가세요. 여기는 **연결과 상태**만 담습니다.
- **갱신 시점** — 카드를 닫을 때(머지) 또는 상태가 바뀔 때 한 줄씩. 매 커밋마다 쓰지 않습니다.

## 지금 상태 한 줄

`daengs_gait` 는 **`dev` 에 머지되어 있고 서버에서는 꺼져 있습니다**(`profiles: ["gait"]`).
소스를 backend 로 옮기는 PR #98 이 **draft 로 열려 있습니다.**

## 미해결 — 다음에 이어야 할 것

| # | 무엇 | 왜 아직 | 어디서 이어지나 |
| --- | --- | --- | --- |
| 1 | **서버에서 gait 컨테이너 실기동** | 개발 PC 에 Docker 데몬이 안 떠 있어 `compose config` 검증으로만 갈음 | PR #98 |
| 2 | **production 가중치로 실제 inference** | 서버에 올려 둔 가중치가 개발 PC 것과 같은 파일인지 **SHA256 대조가 아직 안 됨** | PR #62 → #98 |
| 3 | **원본 walk_demo 대비 parity 실측** | 이관 전후 대조는 했으나(아래) 원본과의 재대조는 미수행 | PR #62 |
| 4 | **기록 저장 구조 확정 (DB vs 파일)** | 설계안까지만 나옴. 앱이 부르는 URL 을 정하는 결정이라 앱 연동 전에 정해야 함 | `gait-record-data-design.md` |
| 5 | **인증** | 켜는 순간 `/gait/` 가 인증 없는 업로드 엔드포인트가 됨 (스크리닝과 같은 상태) | D-024 · D-029 |
| 6 | `bbox_center` 정의 이원화 | 고치면 수치가 바뀌어 parity 주장이 흔들림 — 사람 판단 필요 | PR #62 리뷰 |
| 7 | record_id 8-hex 충돌 · 경로 검증 · Content-Length 선검사 | 머지를 막을 수준이 아니라 후속으로 미룸 | PR #62 리뷰 |

⚠️ **2번이 1번보다 먼저입니다.** 서버 가중치가 다른 파일(예: walk_demo 의
`models/experimental/*.pt` — 미채택 실험 가중치 6개)이면 켜도 결과가 다릅니다.
개발 PC 에서 검증에 쓴 파일의 해시는 아래에 적어 두었습니다.

## 검증에 쓴 가중치 (개발 PC)

| 파일 | SHA256 | 크기 |
| --- | --- | --- |
| `best.pt` | `c9596bb4e06363067ef9b4c82228d1296f8f2eb47a4d43cde765fdded1aaede8` | 53,169,600 |
| `yolov8n.pt` | `f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36` | 6,549,796 |

출처는 `walk_demo/data/2.AI학습모델파일/키포인트/best-pth/weights/best.pt` 와
`walk_demo/models/pretrained/yolov8n.pt` 입니다. 서버(`C:\deploy\daengs\models\release\gait-analysis`)
쪽 해시와 같으면 아래 실측 결과가 서버에도 그대로 성립합니다.

```powershell
Get-FileHash "C:\deploy\daengs\models\release\gait-analysis\best.pt",
             "C:\deploy\daengs\models\release\gait-analysis\yolov8n.pt" -Algorithm SHA256 |
  Format-List Path,Hash
```

## 이력

### 2026-08-29 — 보행 분석을 독립 서비스로 들여옴 (PR #62, D-029)

`YH-KIKI/walk_demo` 의 실험 코드를 `gait-analysis/` 로 이전. 실험 모듈이 최상단에서
끌고 오던 `pandas`·`scipy`·`scikit-learn` 을 걷어내고 실제로 쓰는 상수·함수만 재구성.

- 가중치는 git 밖(서버 디스크 + `:ro` 마운트)으로 확정 — 57MB vs 저장소 12MB
- 업로드 한도 `GAIT_MAX_UPLOAD_BYTES`(기본 150MB). nginx `client_max_body_size`(200m)보다
  **항상 낮아야** 맨 HTML 대신 JSON 413 이 나감
- 기록 저장 정책·DB 구조를 설계안으로만 남김 (확정 안 함)

**결정 번호가 세 번 밀렸습니다** — D-026 → D-027 → D-028 → **D-029**. Place 검색 카드들이
매번 먼저 머지되며 번호를 선점했습니다. `docs/life/decisions-rag.md` 는 `RAG-` 번호에
"예약 중" 표를 두는데(RAG-042) `docs/decisions.md` 에는 없어서 생긴 일입니다.
**같은 표를 `decisions.md` 에도 두는 것을 제안해 둔 상태입니다.**

### 2026-08-29 — 코드 리뷰 5건 수정 (PR #62, `069cf0d`)

| 심각도 | 무엇 | 성격 |
| --- | --- | --- |
| 높음 | `/v1/analyze` 가 이벤트 루프를 막음 | **이식이 만든 회귀** — 원본은 ThreadingHTTPServer 라 동시 처리가 됐음 |
| 중간 | 원본 파일명이 저장 기록에 안 남음 | 원본에도 있던 문제 |
| 중간 | `message_for_ui` 가 고정 문자열 | 원본에도 있던 문제 |
| 중간 | 세로 영상 overlay 가 찢어짐 | 원본에도 있던 문제 |
| 중간 | ffmpeg 종료 코드를 버림 | 원본에도 있던 문제 |

수정하면서 파생 2건을 같이 막았습니다 — threadpool 로 동시 요청이 가능해지며 생기는
**모델 캐시 경합**(torch 모델 두 벌 = 메모리 두 배), 그리고 윈도우에서 ffmpeg 가 먼저
죽을 때 `BrokenPipeError` 가 아니라 **`OSError: [Errno 22]`** 가 오는 것.

수치를 바꾸는 지적(`bbox_center`)은 parity 주장과 얽혀 **일부러 손대지 않았습니다.**

### 2026-08-31 — 소스를 backend 로 이관 (PR #98, D-038 · draft)

`gait-analysis/` → `backend/src/daengs_gait/`. **소스와 의존성만** 합치고 런타임은
그대로 갈라 둡니다 — 별도 컨테이너 · 프로세스 · `gait-venv` · `gait-data` · nginx `/gait/`.

핵심 근거: **이 레포에서 의존성은 이미지가 아니라 venv 볼륨에 삽니다.**
`docker/uv/Dockerfile` 은 20줄이고 코드도 의존성도 없어서, 7개 서비스가 `image: uv:1`
하나를 공유하고 `command` 의 `uv sync` 가 각자 볼륨에 설치합니다. `crawler-worker` 가
이미 같은 구조입니다(backend 와 같은 pyproject 를 쓰면서 `--group ml` 을 빼서 torch 를
안 받음). 그래서 **pyproject 를 공유해도 backend 컨테이너는 ultralytics 를 안 받습니다.**

- 의존성은 **`gait` 그룹 하나** — `ml` 을 붙이지 않습니다. import 전수 조사 결과
  `ml` 의 sentence-transformers·transformers·pyarrow 를 하나도 안 씁니다
- **`daengs_backend` 와의 접점은 0 개.** training(#94)과 다른 점입니다 — 저쪽은 프로세스를
  합쳤기에 라우터·서비스 접점이 필요했지만 여기는 합치지 않습니다
- `*.pt` 방어를 **전역 `.gitignore` 로 올렸습니다.** 규칙이 `gait-analysis/.gitignore`
  에만 있었고 `backend/.gitignore` 는 존재조차 안 해서, 폴더만 지웠으면 가중치 57MB 를
  커밋할 수 있는 상태가 됐습니다

**이관 전후 수치 대조 — 완전 일치** (같은 영상 `9xX_bXOE534.mp4`, 개발 PC).
이관 전 실행 기록이 `_data/records/` 에 남아 있어 직접 대조했습니다:

| 항목 | 결과 |
| --- | --- |
| feature vector | 121차원, **값 불일치 0개** |
| `quality` · `video_meta` · `trajectories` · `summary_for_ui` | 일치 |
| openapi 경로 5개 | 이관 전과 동일 |
| 추론 시간 | 152초 → 142초 (같은 수준, CPU) |

## 알아 두면 좋은 것 (반복해서 부딪힌 자리)

- **결정 번호는 착수할 때 예약하세요.** `dev` 의 목차만 보면 부족합니다 — 열린 브랜치가
  이미 그 번호를 쓰고 있을 수 있습니다. 확인은
  `for b in $(git ls-remote --heads origin ...); do git show origin/$b:docs/decisions.md ...`
  D-038 을 잡을 때 PR #80 이 D-030~D-037 을 쓰고 있는 것을 이렇게 찾았습니다.
- **`gait-venv` 를 `backend-venv` 와 공유하면 안 됩니다.** `uv sync` 는 exact 동기화라
  같은 볼륨에 다른 그룹으로 돌리면 서로를 지웁니다.
- **compose 의 `--group gait` 와 pyproject 의 그룹 이름이 어긋나면 `/gait/` 만 503**
  이 되는데 다른 API 는 멀쩡해서 로그에 아무것도 안 뜹니다.
- 코드에는 안 보이는 알고리즘 주의사항은 `backend/src/daengs_gait/CLAUDE.md` 에 있습니다
  (임계값 · `GAIT_FILTER_VERSION` · `sample_fps` · `OVERLAY_FPS`).
