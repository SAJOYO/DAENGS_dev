# daengs_gait — Claude 용 메모

전체 안내는 [README.md](README.md) 에 있습니다. 여기에는 **코드를 봐도 안 보이는 것**만 적습니다.

## 이 패키지의 성격

`backend/src/` 밑에 있지만 **`daengs_backend` 와 다릅니다.**

- **런타임이 갈라져 있습니다** (D-038). 소스와 의존성만 backend 로 통합했고, 실행은
  compose 의 `gait-worker`(Celery)에서만 합니다. `daengs_backend` 웹 프로세스에 붙지 않습니다.
  옛 `gait-analysis` HTTP 서비스(`service.py`)는 D-063 4단계에서 제거됐습니다.
- **접점은 한 방향, 함수 안에서만입니다** (D-043 ⓒ · D-063). `daengs_backend` 가
  `daengs_gait` 를 부르는 자리는 워커의 `engines.get_engine(...)`(엔진 선택·실행)과 웹의
  `compare`·`contract`(비교·계약) 뿐이고, 전부 **함수 안 지연 import** 입니다. backend
  설정값(`GAIT_ENGINE`·`GAIT_V4_*`)은 인자로 넘어옵니다 — **`daengs_gait` 는 `daengs_backend`
  를 import 하지 않습니다** (`tests/test_gait_engines.py` 가 소스를 훑어 지킵니다). 접점을
  늘려야 할 것 같으면 D-063 을 먼저 다시 보세요.
- **`engines/` 는 가벼워야 합니다.** `engines/__init__` 은 하위 모듈을 `get_engine` 안에서만
  import 합니다. `legacy.py` 가 `pipeline`(torch)을, `v4.py` 가 서브프로세스를 다룹니다 —
  둘 다 워커에서만 실행됩니다.
- **영상 입력 판정은 `intake.py` 한 곳입니다** (D-063 3단계). 읽을 수 있으면 원본 그대로,
  못 읽을 때만 H.264 변환, 그래도 못 읽으면 `VideoDecodeError`. 워커가 엔진 직전에
  `prepare_for_analysis` 로 부르고, 엔진은 판정을 모릅니다. `cv2`·`imageio_ffmpeg` 는 함수
  안에서만 import — 모듈 import 는 가볍습니다.
- `daengs_backend` 의 MVC2 계층 규칙(D-011)이 여기에는 걸려 있지 않습니다. 평평합니다.
- **의존성은 `backend/pyproject.toml` 의 `gait` 그룹 하나**입니다. `ml` 그룹과 겹치지
  않습니다 — gait 는 sentence-transformers · transformers · pyarrow 를 안 씁니다.
  (v4 엔진 코드 `backend/gait_v4/` 는 같은 pyproject 의 `gait-v4` 그룹 — `==` 핀, 별도 venv.
  D-063 5A 에서 그 폴더의 자기 lock 을 없애고 여기로 모았습니다. 5B 에서 코드도 들어옵니다.)
- **원본 `YH-KIKI/walk_demo` 에서의 일방향 이전**입니다. 되돌려 보낼 일이 없어서 실험
  코드와 얽힌 부분을 정리해서 가져왔습니다 (`skin-screening/` 은 외부 저장소의 *사본*
  이라 구조를 못 바꾸는 것과 다른 점입니다).

## 절대 하지 말 것

- **`config.py` 의 임계값을 임의로 바꾸지 마세요.** 전부 walk_demo 에서 실측으로
  정해진 값이고, 결과를 보기 전에 고정하고 사후 조정하지 않는다는 원칙으로 잡혔습니다.
  바꾸면 **`GAIT_FILTER_VERSION` 도 함께 올려야 합니다** — 안 올리면 옛 기록과 새 기록이
  같은 기준인 척 비교됩니다 (`pipeline.compare_records` 가 이 값으로 경고를 붙입니다).
  `backend/tests/test_gait_filter.py` 가 값들을 박아 두고 있습니다.
- **`_dev_only_*` 와 `internal_feature_vector` 를 사용자에게 보이는 응답으로 승격하지
  마세요.** 이건 빠뜨린 게 아니라 일부러 가둬 둔 것입니다. 수백 개의 숫자가 화면에
  나오면 사용자는 그것을 건강 점수로 읽습니다. **이 서비스는 진단이 아니라 같은 개체의
  시간 변화 관찰입니다.**
- **`pandas` · `scipy` · `scikit-learn` 을 다시 넣지 마세요.** 원본에서는 실험 모듈이
  module 최상단에서 이것들을 부르는 바람에 따라왔을 뿐, 알고리즘은 하나도 쓰지 않습니다.
  넣어야 할 것 같으면 무엇이 그것을 부르는지부터 확인하세요.
- **가중치를 커밋하지 마세요.** 최상단 `.gitignore` 가 `*.pt` 를 막고 있습니다(D-038 로
  이관하면서 폴더별 규칙을 전역으로 올렸습니다). 그런데 더 흔한 실수는 walk_demo 쪽
  `models/experimental/*.pt` (6개, **미채택 실험 가중치**)를 production 과 헷갈려
  가져오는 것입니다. **production 은 `best.pt`(50.7MB) 와 `yolov8n.pt`(6.2MB) 둘뿐입니다.**
- **`daengs_gait` 의 모듈을 `daengs_backend` 쪽에서 최상단 import 하지 마세요.**
  torch·ultralytics·opencv 가 `gait` 그룹에만 있어서, 기본 설치(`uv sync`)의 backend 가
  ImportError 로 죽습니다.

## 조용히 틀리는 자리

- **`overlay.OVERLAY_FPS` 와 추론의 `TARGET_FPS` 가 어긋나면** overlay 의 그림과 원본
  프레임이 어긋납니다. 예외는 안 납니다 — 그래서 `config.TARGET_FPS` 하나를 둘 다 씁니다.
- **`apply_gait_filter` 의 `sample_fps` 에 `TARGET_FPS` 를 넣으면 안 됩니다.** 실제
  샘플링 fps 는 `native_fps / step` 이고 step 이 정수라 반올림 오차가 있습니다.
  그 차이가 정지 판정(시간 기반)에 그대로 들어갑니다.
- **정지 판정은 "거리"가 아니라 "속도"입니다.** 거리로 비교하던 시절 30fps 영상에서
  정지 오탐이 급증했습니다(usable 128 → 93). 되돌리지 마세요.
- **`features._feature_key` 의 이름 규칙이 `feature_engine` 과 어긋나면** UI 요약이
  조용히 텅 빕니다. 예외가 안 나서 알아채기 어렵습니다 — 테스트가 지키고 있습니다.
- **overlay 크기는 `CAP_PROP_FRAME_WIDTH/HEIGHT` 가 아니라 첫 디코드 프레임에서
  가져옵니다.** 회전 메타데이터가 붙은 영상(안드로이드 세로 촬영)은 OpenCV 가 `read()`
  에서 자동으로 세워 주는데 `CAP_PROP` 은 회전 전 값을 냅니다. 되돌리면 overlay 가
  찢어지는데 **예외가 안 납니다.**
- **`config.py` 의 `ROOT` 는 `parents[2]`(= `backend/`) 입니다.** 폴더 깊이를 바꾸면
  같이 고쳐야 합니다 — 안 고치면 예외 없이 엉뚱한 곳에 `_models/` 와 `_data/` 가 생깁니다.
  컨테이너에서는 `GAIT_RELEASE_DIR` · `GAIT_DATA_DIR` 이 덮어써서 안 드러납니다.
- **`compose` 의 `--group gait` 와 `pyproject.toml` 의 그룹 이름이 어긋나면** gait-worker 가
  기동에서 멈춥니다(`--frozen`). 웹은 멀쩡하고 상태 화면의 보행만 `absent` 로 보입니다.

## 배포

compose 의 `profiles: ["gait"]` 뒤에 있어 기본 `docker compose up -d` 로는 안 뜹니다.
`dev` 에 머지돼도 서버 상태가 바뀌지 않습니다 — 스크리닝과 같은 방식입니다 (D-024).

**`gait-venv` 를 `backend-venv` 와 공유하면 안 됩니다.** backend 는 `--group ml`,
워커는 `--group gait` 로 동기화하는데 인자 없는/다른 `uv sync` 는 exact 동기화라
같은 볼륨에 돌리면 서로의 그룹을 지웁니다. `crawler-worker` 가 `crawler-venv` 를 따로
쓰는 것과 같은 이유입니다.

**`gait-data` 볼륨에는 개인 데이터가 들어갑니다** (옛 HTTP 서비스의 분석 기록 JSON ·
overlay 영상 · 업로드 원본). 마운트하는 서비스는 이제 없지만 **선언을 지우지 않습니다** —
옛 기록의 처분은 별도 결정이고, `docker compose down -v` 로 지워집니다.

`nginx/default.conf` 의 `location /app/gait/` 는 `client_max_body_size 200m` 과
`proxy_read_timeout 600s` 를 **그 블록 안에서만** 올려 뒀습니다. 영상은 사진과 달라서
서버 기본값(20m / 60s)으로는 정상 요청이 413·504 로 끊깁니다. 앱 한도
(`GAIT_MAX_UPLOAD_BYTES`, 기본 150MB)는 **항상 그보다 낮아야** nginx 의 맨 HTML 대신
이유가 담긴 JSON 413 이 나갑니다. 옛 `location /gait/` 는 410 만 돌려주는 묘비입니다.
