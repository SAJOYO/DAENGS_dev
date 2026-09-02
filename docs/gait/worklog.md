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

**backend 소유의 새 인증 흐름 `/app/gait/*` 으로 넘어가는 중입니다** (D-043, PR #133,
2026-09-02). 앱→backend(인증·소유권·record/job·업로드 티켓)→별도 `gait-worker`→저장소
구조이고, 개발 PC LocalBridge 왕복이 통과했습니다. 저장소는 GCS 로 확정(자격증명·버킷은
#78 대기), 오늘은 임시 LocalBridge 로 서버 왕복을 엽니다. 아래 D-043 절 참고.

⚠️ **옛 무인증 `/gait/*`(gait-analysis) 는 앱 #64 전환 전까지 그대로 둡니다** — 지금 앱이
그걸 쓰기 때문입니다. 전환·검증이 끝나면 차단합니다(진행 순서 6번). 그 옛 계약 정본은
여전히 `backend/src/daengs_gait/API.md` 이고, 새 계약은 `/app/gait/*` 라우터·스키마입니다.

## 미해결 — 다음에 이어야 할 것

| # | 무엇 | 왜 아직 | 어디서 이어지나 |
| --- | --- | --- | --- |
| 1 | ~~서버 컨테이너 실기동~~ | ✅ 2026-08-31 완료 | — |
| 2 | ~~서버에서 실제 영상 추론~~ | ✅ 2026-08-31 완료 (아래) | — |
| 3 | **원본 walk_demo 대비 parity 실측** | 이관 전후 대조는 했으나 원본과의 재대조는 미수행 | PR #62 |
| 3-1 | ~~PR #98 머지 후 서버 재확인~~ | ✅ 완료 — 이관 구조로 추론 성공 | — |
| 3-2 | **AV1 등 못 읽는 코덱에 잘못된 안내** | 실사용(스마트폰 H.264)에는 영향 없어 후속으로 뺌 | 신규 |
| 4 | **PR #109 를 서버에 반영** | 머지했지만 `--force-recreate` 를 아직 안 돌림 | PR #109 |
| 5 | **인증·소유권** | gait 가 아니라 **backend auth 계층 몫**으로 정리됨. 그쪽 카드가 필요 | API.md §소유권 |
| 6 | **보관 정책 · 공용 저장소** | 사람이 정할 일. gait 도 같은 저장소를 쓰게 됨 | **#78** (담당자 지명됨) |
| 7 | **원본 영상 재생 엔드포인트** | 앱 요구사항이 확정되면 | — |
| 8 | **비동기 job/poll** | 지금은 동기 2~4분. 미래 모양은 orchestration-contracts.md | — |
| 4 | **기록 저장 구조 확정 (DB vs 파일)** | 설계안까지만 나옴. 앱이 부르는 URL 을 정하는 결정이라 앱 연동 전에 정해야 함 | `gait-record-data-design.md` |
| 5 | **인증** | 켜는 순간 `/gait/` 가 인증 없는 업로드 엔드포인트가 됨 (스크리닝과 같은 상태) | D-024 · D-029 |
| 6 | `bbox_center` 정의 이원화 | 고치면 수치가 바뀌어 parity 주장이 흔들림 — 사람 판단 필요 | PR #62 리뷰 |
| 7 | record_id 8-hex 충돌 · 경로 검증 · Content-Length 선검사 | 머지를 막을 수준이 아니라 후속으로 미룸 | PR #62 리뷰 |

⚠️ 서버 가중치가 개발 PC 검증본과 **바이트 단위로 같은 것을 확인**했습니다
(53,169,600 / 6,549,796). walk_demo 의 `models/experimental/*.pt`(미채택 실험 가중치 6개)와
헷갈릴 위험은 없어졌습니다.

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

## 2026-09-02 — D-043: backend 가 record·job 을 소유, gait 는 내부 워커로 (PR #133)

앱 카드(DAENGS_APP#64)가 무인증 `/gait/*` 를 발견한 것이 계기였고, 클라우드
저장(#78)이 들어오면 "gait 에 인증을 어떻게 붙이나"라는 질문 자체가 사라진다는
것이 결론이었습니다. 구조는 D-043 참고.

만든 것: `gait_records` 테이블(SQL+모델) · `/app/gait/*` 라우터 · `StoragePort`
(provider-neutral, 미설정 503) · backend 자체 Celery 앱(`gait` 큐) · 테스트 14개.

⚠️ **아직 end-to-end 로 돌지 않습니다** — 저장소 구현이 #78 대기입니다.
   `/app/gait/analyze` 는 지금 503 을 냅니다 (의도된 상태).

⚠️ **무인증 `/gait/*` 는 앱 전환 전까지 열려 있습니다** — 미해결 5번이 이것이고,
   완화는 앱 쪽 "테스트 빌드에서 끄기"입니다.

**확정된 진행 순서** (2026-09-02 사람 승인 — 이 순서대로 갑니다):

1. **#78** ~~provider~~ (GCS 확정) + **보관/파기 정책·버킷·리전 세부** ← 사람 결정
2. ~~`StoragePort` 실제 구현~~ ✅ GcsStorage 작성 완료 (자격증명·버킷은 #78 뒤 연결)
3. ~~gait worker compose 전환~~ ✅ `gait-worker` 서비스 추가 완료
   (`celery -A daengs_backend.tasks.gait worker --queues gait`, profile gait)
4. `/app/gait/*` 업로드 → confirm → 분석 → 결과 조회 **왕복 검증**
   — ✅ **개발 PC LocalBridge 왕복 통과** (아래), 서버 왕복은 진행 중
5. 앱 #64 를 새 API 로 전환
6. 새 앱 흐름 검증 후 기존 무인증 `/gait/*` 차단

### 2026-09-02(2) — A 확정: LocalBridge 로 왕복을 열고 #133 을 머지한다

앞선 "#133 은 4번 통과까지 draft" 를 **사람이 A 로 갱신**했습니다: GCS 자격증명(#78)을
기다리지 않고, **임시 LocalBridge** 로 `/app/gait/*` 왕복을 먼저 통과시켜 그것을 #133 의
Ready 조건으로 봅니다. 서버 기본값은 여전히 `GAIT_STORAGE=none`(503)이라 머지 자체는
프로덕션에 영향이 없고, 오늘 검증에서만 서버 `.env` 로 local 을 **명시적으로** 켭니다.

⚠️ **LocalBridge 는 오늘 검증을 위한 transitional path 입니다 — 최종 저장 방식이 아닙니다.**
   영상이 backend 웹의 bridge 엔드포인트를 지나 `gait-bridge` 볼륨에 잠깐 머뭅니다(원칙 1
   위반이지만 검증 한정). GCP/GCS 가 준비되면 `LocalBridge → GcsStorage` 로 바꾸고, 그때
   **GCS Signed URL 실제 왕복을 별도로 검증**합니다.

⚠️ **LocalBridge 를 써도 무거운 분석은 backend 가 하지 않습니다.** bridge 는 파일 I/O 만
   backend 를 지나고, torch·`daengs_gait` 분석은 언제나 별도 `gait-worker` 프로세스에서만
   돕니다 (test_main_stays_light 가 backend `main` 을 지킵니다). 이 격리가 LocalBridge/GCS
   어느 쪽에서도 그대로인 것이 이 구조의 핵심입니다.

**개발 PC LocalBridge 왕복 실측** (실제 `IMG_8631.mov` 116MB):

    analyze(키 생성·티켓) → upload(원본 그대로) → confirm(exists 확인)
    → 별도 분석 → sampled 298 · detected 99 · usable 3 (walk_demo 와 일치)

**서버 왕복 runbook** (오늘, 서버 PC 에서 — 스텝 3·4). 배포는 `dev` push 로 자동이지만
`gait-worker` 는 profile 뒤라 **명시적으로** 띄웁니다:

```powershell
# 0) gait_records 테이블을 서버 DB 에 1회 적용 (db/init 은 기존 볼륨엔 안 돕니다).
#    PowerShell 은 `<` 입력 리디렉션이 없어 Get-Content 로 파이프하고, 계정·DB 이름은
#    컨테이너 자신의 env 를 sh 가 확장하게 둡니다 (psql 은 -f 없으면 stdin 을 읽습니다).
#    파일은 IF NOT EXISTS 라 여러 번 돌려도 안전합니다.
Get-Content db\migrations\2026-09-02_gait_records.sql -Raw |
  docker compose exec -T pgvector sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

# 1) 서버 최상단 .env 에 LocalBridge 를 켜는 값 추가 (오늘 검증용 — GCS 오면 지웁니다)
#    GAIT_STORAGE=local
#    GAIT_LOCAL_STORAGE_DIR=/data/gait-bridge
#    GAIT_BRIDGE_BASE_URL=http://daengback.weareithero.cloud   # 지금은 평문 http

# 2) backend(새 env·볼륨 반영) + gait-worker 를 띄웁니다.
docker compose up -d backend                       # GAIT_* env·gait-bridge 볼륨 반영
docker compose --profile gait up -d gait-worker    # 별도 워커(celery, gait 큐)

# 2-1) nginx 의 /app/gait/ 블록(업로드 200m)을 반영. default.conf 는 bind-mount 라
#      배포가 자동 reload 하지 않습니다 — 문법 확인 후 reload 합니다.
docker compose exec nginx nginx -t
docker compose exec nginx nginx -s reload

# 3) 왕복: 인증→analyze→upload(bridge)→confirm→워커 분석→조회
#    (인증 토큰이 필요합니다 — 앱 계정으로 로그인해 얻은 access 토큰을 씁니다)
```

이후 5번(앱 #64 전환) → 실기기 검증 → 6번(무인증 `/gait/*` 차단) 순서입니다.

### 2026-09-02(3) — 서버 왕복 통과 ✅ 그리고 그것이 잡아낸 버그 2개

`IMG_8631.mov`(116MB)로 실제 서버에서 왕복했습니다. **인증(401) · 소유권(404) ·
analyze(201) · 업로드(200) · confirm(UPLOADED) · 별도 워커 분석(DONE) · 조회 · 삭제(404)**
전부 통과했습니다.

| 항목 | 서버 | 개발 PC |
| --- | --- | --- |
| `n_frames_sampled` | 298 | 298 |
| `n_frames_detected` | 100 | 99 |
| `n_frames_gait_usable` | 2 | 3 |
| `video_meta` | `1080x1920 / 30fps` | 같음 |

⚠️ **"완전 일치"가 아니라 ±1 입니다.** 임계값 근처 프레임이 플랫폼 부동소수점 차이
(Windows torch vs 리눅스 CPU torch)로 갈린 것으로 봅니다. `sampled` 가 정확히 같으므로
**입력은 동일**합니다 — 원본 `.mov` 를 재인코딩 없이 읽었다는 뜻입니다(#132). 예전
망가진 기록의 `-1x-1 / sampled 0` 과 대조됩니다.

**이 왕복이 아니었으면 못 잡았을 버그 둘:**

**① 워커의 두 번째 태스크부터 전부 죽습니다 (이벤트 루프)**

```
RuntimeError: Task <_cleanup() ...> got Future attached to a different loop
```

`core/database.py` 의 **모듈 전역 엔진**은 풀에 커넥션을 남기고, 그 커넥션은 **그것을
만든 이벤트 루프**에 묶입니다. Celery 태스크는 `asyncio.run()` 으로 매번 새 루프를 열고
그 루프는 끝나면 닫히므로, 다음 태스크가 죽은 루프의 커넥션을 꺼내며 터집니다.

**첫 태스크는 항상 성공합니다** — 그래서 분석은 되는데 뒤이은 cleanup 만 실패하는
모습으로 나타났고, 실제로는 **두 번째 분석 요청도 같은 이유로 죽습니다.** `pool_pre_ping`
때문에 스택이 ping 에서 끝나 원인이 더 가려집니다.

고침: `core/database.worker_session()` — 태스크마다 `NullPool` 엔진을 새로 만들고
`finally` 에서 dispose. 워커 경로(`_run_analysis` · `_cleanup`)가 그것을 씁니다.
테스트로 고정했습니다(`asyncio.run` 두 번 = 엔진 두 개).

**② 임시 bridge 가 무인증 임의 경로 쓰기였습니다**

`PUT /app/gait/_bridge/upload/<아무 경로>` 가 토큰 없이 200 이었습니다. 공개 도메인이라
**아무나 서버 디스크를 채울 수 있는 상태**로 잠깐 배포됐습니다(오늘 검증 중). "local 은
신뢰된 환경에서만 켠다"는 전제를 공개 서버에서 켜면서 깨뜨린 것입니다.

고침: 인증 헤더를 요구하지 **않고**(그러면 GCS 전환 때 앱이 또 바뀝니다),
**backend 가 실제로 발급한 키인지**를 DB 로 확인합니다 —
`gait_repo.find_by_storage_key(..., status="PENDING")`. 키는 uuid4 라 추측할 수 없고,
발급받은 사람은 소유자뿐이며, confirm 뒤에는 덮어쓰기도 막힙니다. 다운로드도 같습니다.

⚠️ **워커 코드가 바뀌었으므로 `gait-worker` 재시작이 필요합니다** — backend 웹은
`--reload` 라 배포가 알아서 반영하지만 celery 는 아닙니다:
`docker compose --profile gait restart gait-worker`

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

## 2026-08-31 — 서버 첫 추론에서 버그 발견: opencv GUI 빌드

서버에서 실제 영상으로 `/v1/analyze` 를 처음 부르자 **500** 이 났습니다.

```
ImportError: libxcb.so.1: cannot open shared object file
  ← daengs_gait/keypoint_infer.py 의 `import cv2`
```

`opencv-python` 은 **GUI 빌드**라 X11 공유 라이브러리(libxcb·libGL 등)를 요구하는데
`python:3.12-slim` 컨테이너에는 없습니다.

**왜 지금까지 몰랐나** — 세 겹으로 가려져 있었습니다.

1. **개발 PC 는 Windows** 라 그 의존성이 필요 없습니다. 테스트 30개도, 실제 추론
   (121차원 feature, overlay 생성)도 전부 통과했습니다.
2. **`/healthz` 는 `cv2` 를 import 하지 않습니다** — 가중치 파일 존재만 봅니다.
   그래서 컨테이너가 `ready:true` 를 내며 멀쩡해 보였습니다.
3. **`serve.py` 가 무거운 import 를 함수 안으로 미뤄 둡니다**(업로드 한도 검사를
   import 보다 먼저 하려고). 그래서 기동 시점이 아니라 **첫 분석 요청**에서 터집니다.

즉 **컨테이너에서 실제 분석을 한 번 돌려야만 나오는 버그**였습니다.

**고친 방법** — `opencv-python-headless` 로 교체. gait 코드가 쓰는 cv2 함수를 전수
조사했더니 `VideoCapture`·`line`·`circle`·`putText`·`resize`·`cvtColor`·`Laplacian`
뿐이고 **GUI 함수(`imshow`·`waitKey` 등)는 하나도 없어서** 동작이 같습니다.

⚠️ **`ultralytics` 가 `opencv-python` 을 필수 의존성으로 끌고 옵니다.** 그래서 그룹
선언만 바꾸면 GUI 판과 headless 판이 **둘 다** 깔리고, 같은 `cv2` 네임스페이스를
다퉈서 GUI 판이 이기면 다시 터집니다. `[tool.uv] override-dependencies` 로 GUI 판을
막았습니다 — **그 override 와 그룹 선언은 한 쌍이라 하나만 지우면 재발합니다.**

⚠️ **이 버그는 `dev` 에 배포된 코드(PR #62)에도 있습니다.** PR #98 이 만든 게 아닙니다.
#98 이 머지되면 같이 고쳐집니다.

대안으로 `docker/uv/Dockerfile` 에 X11 라이브러리를 넣는 방법도 있었지만, **그
Dockerfile 을 7개 서비스가 공유**해서 backend·place-search·crawler 까지 안 쓰는
라이브러리를 받게 되므로 택하지 않았습니다.

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

## 2026-08-31 — 서버에서 gait 컨테이너 첫 기동 ✅

서버(`192.168.0.22`)의 `C:\IDEctions-runner\_work\DAENGS_dev\DAENGS_dev` 에서
**`gait-analysis` 서비스만 지정해** 띄웠습니다 (`up -d` 만 쓰면 11개를 전부 건드립니다).

```powershell
Add-Content .env "`nGAIT_RELEASE_DIR=C:\deploy\daengs\models
elease\gait-analysis"
docker compose --profile gait up -d gait-analysis
curl.exe -s http://localhost:8000/gait/healthz
```

확인된 것:

| 항목 | 결과 |
| --- | --- |
| 리눅스 컨테이너 의존성 설치 | `torch==2.13.0+cpu` — **CPU 판을 집음** (CUDA 판이면 수 GB 더 받음) |
| production 가중치 로딩 | `pose`·`detector` 둘 다 `found:true` |
| FastAPI 기동 | `Application startup complete` |
| **nginx `/gait/` 라우팅** | ✅ `localhost:8000/gait/...` 로 물었으므로 nginx 경유가 함께 검증됨 |
| `gait_filter_version` | `v5-stationary-speed-based-20260826` |

⚠️ **이것은 이관 전 코드(PR #62)입니다.** `--extra model` 로 설치됐습니다. 다만 실제
설치된 패키지(torch·torchvision·ultralytics·opencv)가 PR #98 의 `gait` 그룹 내용과
같아서, 머지 후에도 같은 결과가 나올 것으로 봅니다.

⚠️ 서버 `.env` 의 `SCREENING_RELEASE_DIR` 이 `C:\deploy\daengs\models
elease` 이고
gait 가 그 **하위 폴더**입니다. 동작에는 문제없지만(스크리닝은 `checkpoints/stage1_*`
패턴만 찾습니다) 스크리닝이 가중치를 못 찾을 때의 에러 목록에 `gait-analysis/` 가 같이
뜹니다. 나중에 헷갈릴 수 있는 자리입니다.

## 2026-08-31 — 서버에서 실제 추론 성공 ✅ (PR #98 머지 후)

PR #98 머지(`6ef691c`) 후 서버에서 컨테이너를 재생성하고 실제 영상으로 확인했습니다.

```powershell
docker compose --profile gait up -d --force-recreate gait-analysis
```

⚠️ **`--force-recreate` 가 필요합니다.** 배포의 `docker compose up -d` 는 `profiles` 뒤의
gait 를 건드리지 않아서, 머지만으로는 새 코드가 반영되지 않습니다.

| 항목 | 결과 |
| --- | --- |
| `--group gait` 리눅스 설치 | ✅ `torch==2.13.0+cpu` |
| **opencv headless 수정** | ✅ `libxcb` 에러 사라짐 |
| production 가중치 로딩 | ✅ |
| **실제 모델 추론** | ✅ sampled 186 · detected 47 · gait_usable 15 |
| **feature 계산** | ✅ 121차원 |
| **overlay 인코딩** | ✅ `overlay_error: null` |
| nginx `/gait/` 경유 | ✅ |

`quality_tier: low` 는 정상 판정입니다 — Shorts 라 개가 작고 잘려
`keypoints_collapsed`(14) · `insufficient_keypoints`(12) 로 빠진 프레임이 많습니다.

### 이 과정에서 잡은 버그: opencv GUI 빌드

첫 분석 요청이 `ImportError: libxcb.so.1` 로 500. `opencv-python` 은 GUI 빌드라 X11
라이브러리를 요구하는데 `python:3.12-slim` 에는 없습니다. **개발 PC(Windows)에서는
테스트 30개도 실제 추론도 다 통과해서 안 드러났고**, `/healthz` 는 cv2 를 import 하지
않아 `ready:true` 로 멀쩡해 보였습니다. `opencv-python-headless` 로 교체 + `ultralytics`
가 끌고 오는 GUI 판을 `override-dependencies` 로 차단해 해결했습니다.

### 후속 과제 — AV1 영상에 잘못된 안내가 나갑니다

같은 영상의 AV1 판을 올리자 컨테이너가 프레임을 하나도 못 읽었습니다:

```
[av1] Your platform doesn't support hardware accelerated AV1 decoding.
[av1] Failed to get pixel format.
```

`CAP_PROP` 은 디코딩 없이 메타데이터만 읽으므로 `video_meta` 는 `608x1080/30fps` 로
정상이었고, `cap.read()` 가 첫 프레임부터 실패해 `n_frames_sampled: 0` 이 됐습니다.
**예외가 안 납니다.**

그 결과 사용자는 이런 안내를 받습니다 — **코덱을 못 읽은 건데 촬영을 탓합니다:**

> "밝은 환경에서 강아지 전신이 보이도록, 흔들림 없이 다시 촬영해 주세요."

몇 번을 다시 찍어도 같은 답을 받는 자리입니다. 원인은 `video_intake.ensure_mp4` 가
**확장자만 보고** 변환 여부를 정하는 것 — AV1 도 `.mp4` 라 그냥 통과합니다
(PR #62 코드 리뷰의 지적 ⑥과 같은 계열입니다).

스마트폰 촬영본은 H.264 라 **실사용 경로에는 영향이 없어** 후속으로 뺐습니다.
고친다면 ⓐ 프레임을 하나도 못 읽었을 때 "촬영 문제"가 아니라 "이 영상 형식을 읽을 수
없습니다"로 구분하거나 ⓑ `ensure_mp4` 가 코덱을 확인해 필요하면 재인코딩하는 쪽입니다.

## 2026-08-31 — 기록 목록·삭제 API 와 외부 계약 정리 (PR #109, `c3013ab`)

앱 흐름(업로드→분석→조회→**목록**→overlay→**삭제**)에서 **목록과 삭제가 없어서**
이 서비스의 존재 이유인 "이전 기록과 비교"가 성립하지 않던 것을 채웠습니다.
`/compare` 를 쓰려면 `record_id` 두 개가 필요한데 앱이 그걸 알아낼 방법이 없었습니다.

**계약 정본을 `backend/src/daengs_gait/API.md` 로 만들었습니다** — `skin-screening/API.md`
와 같은 자리·같은 모양("앱이 볼 문서"). 구현 전에 계약을 먼저 확정하고 시작했습니다.

### 외부 계약 (앱이 부르는 주소)

```
POST   /gait/analyze
GET    /gait/records?dog_id=&limit=&cursor=
GET    /gait/records/{record_id}
GET    /gait/records/{record_id}/overlay
DELETE /gait/records/{record_id}
POST   /gait/compare
GET    /gait/healthz
```

### 정한 것

| | 결정 | 이유 |
| --- | --- | --- |
| `record_id` | 8자 → **32자** `uuid4().hex` | 32비트는 수천 건에서 생일 문제로 충돌하고 `save_record` 가 덮어써서 **다른 개 기록이 조용히 사라집니다.** 소비자가 없는 지금이 공짜 |
| 응답의 파일 경로 | `/data/…` 제거 → `has_overlay` · `overlay_url` | 컨테이너 안 경로라 앱이 못 쓰고, 파일이 S3(#78)로 가면 거짓이 됩니다 |
| 삭제 | 즉시 물리 삭제 | gait 가 기록·파일을 다 갖고 있어 소프트 삭제의 전제가 없습니다 |
| 페이지네이션 | 처음부터 포함 | 나중에 붙이면 앱이 바뀝니다 |
| 전체 조회 | 열지 않음 | 인증이 없어 남의 기록이 다 보입니다 |
| **소유권 검증** | **gait 가 아니라 backend auth 계층** | 계정·세션·소유 관계를 아는 것은 backend 입니다. 여기 넣으면 그 지식이 두 곳으로 갈라집니다 |

### `/v1` 을 뗀 이유 — 원본에서 온 게 아니었습니다

backend 의 다른 API 가 전부 도메인 prefix 로 시작하는데(`/auth/…` · `/app/pets/…` ·
`/training/…` · `/screen/…`) gait 만 `/v1/…` 이라 튀었습니다.

확인해 보니 **walk_demo 원본은 `/api/upload` · `/api/records` · `/api/compare` 였습니다.**
`/v1` 은 이 레포로 이전할 때(#62) 새로 붙은 것이고 근거가 어디에도 없습니다 — 레포에
API 버전 규칙 자체가 없습니다(decisions.md · collaboration.md · 오케스트레이션 문서
전부 확인). **관례에 의한 모방이지 합의된 규칙이 아니었습니다.**

⚠️ **nginx 는 한 줄도 안 고쳤습니다.** `rewrite ^/gait/(.*)$` 가 이미 접두사를 뗍니다.
   그래서 **앱이 보는 주소와 FastAPI 안의 경로가 다릅니다** — `/gait/analyze` vs
   `/analyze`. 컨테이너에 직접 붙어 디버깅할 때 헷갈리는 자리입니다.

⚠️ `overlay_url` 은 **앱 기준**으로 냅니다 (`config.PUBLIC_PREFIX`, 기본 `/gait`).
   내부 경로를 내면 앱이 `/gait` 를 손으로 붙여야 하고 그건 틀리기 쉽습니다. 소스에
   박지 않은 이유는 nginx location 이 바뀔 때 **조용히 틀리기** 때문입니다 — 앱은
   404 나는 URL 을 받는데 서버 로그에는 아무 문제도 안 보입니다.

### 곁다리로 잡은 것

- **`/compare` 가 존재 확인보다 무거운 import 를 먼저** 했습니다. 기본 설치에서
  404 여야 할 응답이 ImportError 가 됩니다 — `/analyze` 가 이미 지키던 규칙인데
  compare 만 빠져 있었습니다. **신규 테스트가 잡았습니다.**
- `record_exists` 에 형식 검증을 넣어 **경로 조작**(윈도우 개발 실행에서 역슬래시로
  `RECORDS_DIR` 를 벗어나던 것, PR #62 리뷰 낮음 ③)도 같이 막혔습니다.
- **원본 walk_demo 에는 `/api/records` 목록이 이미 있었습니다.** 이전할 때 함수
  (`records_for_dog`)만 옮기고 엔드포인트를 빠뜨린 것이었습니다.

### 검증

기본 설치(torch 없음) 38 passed · 1 skipped / 모델 환경 49 passed /
실제 영상 E2E 9항목 PASS (analyze → 단건 → 목록 → compare → overlay → 삭제 → 404).

## 2026-08-31 — Swagger 가 남의 API 를 보여주던 버그 (root_path, PR #111)

`http://daengback.~/gait/docs` 에서 **gait API 가 안 보인다**는 제보로 찾았습니다.

```
브라우저가 /gait/docs 를 염
  → Swagger HTML 안에 url: '/openapi.json'   ← 절대 경로
  → 브라우저가 daengback.~/openapi.json 을 요청
  → nginx 의 location / 이 그걸 backend 로 보냄
  → "DAENGS API"(backend 것)가 뜸          ← gait 가 아님
```

nginx 가 `/gait` 를 떼고 넘기므로 FastAPI 는 **자기가 도메인 루트에 있다고 믿습니다.**

⚠️ **페이지는 200 으로 열리고 화면도 멀쩡해 보입니다. 내용만 남의 것입니다.**
   처음에 `/gait/docs → HTTP 200` 만 보고 "정상"이라고 판단했다가 틀렸습니다 —
   **상태 코드로는 못 잡는 종류**입니다.

**고침**: `FastAPI(root_path=config.PUBLIC_PREFIX)`. 프록시가 접두사를 떼는 구조를 위한
표준 옵션이고, docs·openapi.json·redoc 주소를 접두사 기준으로 생성합니다.
**라우트 정의는 안 바뀝니다** — 컨테이너는 여전히 `/analyze` 로 받습니다.

**같은 병을 이미 한 번 막았었습니다.** `overlay_url` 도 같은 자리였는데 그건 손으로
`PUBLIC_PREFIX` 를 붙여 막았고, **Swagger 는 FastAPI 가 자동 생성해서 놓쳤습니다.**
prefix stripping 구조에서는 "서비스가 자기 주소를 만드는 자리"를 전부 세어 봐야 합니다.

⚠️ 테스트에서 알게 된 것: **Starlette 은 `root_path` 를 관대하게 처리해서
`/gait/records/…` 도 200 을 냅니다.** 라우트에 접두사를 잘못 박아도 양쪽 다 200 이라
**테스트가 실수를 못 잡습니다.** openapi 의 경로 집합으로 고정했습니다 — 거기에
`/gait/...` 가 나타나면 이중 접두사입니다.

## Swagger 가 두 개인 이유

`http://daengback.~/docs` 는 **backend 프로세스의 문서**라 **gait 가 안 나옵니다.**
gait 는 별도 프로세스라 자기 문서를 따로 가집니다:

```
http://daengback.~/gait/docs
```

screening 과 정반대입니다 — 저쪽은 D-040 으로 런타임까지 backend 에 합쳐서 `/screen/*`
가 backend `/docs` 에 나오고, gait 는 D-038 로 런타임을 갈라 뒀습니다(영상이 분 단위라
동기 대화·같은 프로세스에 안 맞음). **버그가 아니라 그 결정의 결과입니다.**

⚠️ `/gait/docs` 안에 보이는 경로는 **컨테이너 내부 경로**(`/analyze`)입니다. 앱에 줄
   주소는 `API.md` 쪽(`/gait/analyze`)이 정본입니다.
