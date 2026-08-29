# gait-analysis — Claude 용 메모

전체 안내는 [README.md](README.md) 에 있습니다. 여기에는 **코드를 봐도 안 보이는 것**만 적습니다.

## 이 폴더의 성격

`backend/` 와 **다릅니다.**

- `backend/src/` 의 MVC2 계층 규칙(D-011)이 여기에는 걸려 있지 않습니다.
  `skin-screening/` 과 같은 레이아웃입니다 — `src/` 밑에 평평하게 두고
  `serve.py` 가 폴더 루트를 `sys.path` 에 넣어 `from src import ...` 로 부릅니다.
- 자체 `pyproject.toml` · `uv.lock` 을 갖습니다. `backend/` 의 의존성과 섞이지 않습니다.
- **`skin-screening/` 과 다른 점 하나**: 저쪽은 외부 저장소의 *사본* 이라 구조를 바꾸면
  재동기화가 diff 로 안 됩니다. 여기는 그렇지 않습니다 — 원본 `YH-KIKI/walk_demo` 는
  연구가 끝난 실험 저장소이고 이쪽으로의 **일방향 이전**입니다. 그래서 실험 코드와
  얽힌 부분을 정리해서 가져왔습니다. 되돌려 보낼 일이 없습니다.

## 절대 하지 말 것

- **`src/config.py` 의 임계값을 임의로 바꾸지 마세요.** 전부 walk_demo 에서 실측으로
  정해진 값이고, 결과를 보기 전에 고정하고 사후 조정하지 않는다는 원칙으로 잡혔습니다.
  바꾸면 `GAIT_FILTER_VERSION` 도 함께 올려야 합니다 — 안 올리면 옛 기록과 새 기록이
  같은 기준인 척 비교됩니다. `tests/test_gait_filter.py` 가 값들을 박아 두고 있습니다.
- **`_dev_only_*` 와 `internal_feature_vector` 를 사용자에게 보이는 응답으로 승격하지
  마세요.** 이건 빠뜨린 게 아니라 일부러 가둬 둔 것입니다. 수백 개의 숫자가 화면에
  나오면 사용자는 그것을 건강 점수로 읽습니다. 이 서비스는 진단이 아니라 같은 개체의
  시간 변화 관찰입니다.
- **`pandas` · `scipy` · `scikit-learn` 을 다시 넣지 마세요.** 원본에서는 실험 모듈이
  module 최상단에서 이것들을 부르는 바람에 따라왔을 뿐, 알고리즘은 하나도 쓰지 않습니다.
  넣어야 할 것 같으면 무엇이 그것을 부르는지부터 확인하세요.
- **가중치를 커밋하지 마세요.** `.gitignore` 가 `*.pt` 를 막고 있지만, walk_demo 쪽
  `models/experimental/*.pt` (6개, 미채택 실험 가중치)를 production 과 헷갈려 가져오는
  실수가 더 흔합니다. production 은 `best.pt` 와 `yolov8n.pt` 둘뿐입니다.

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

## 배포

compose 의 `profiles: ["gait"]` 뒤에 있어 기본 `docker compose up -d` 로는 안 뜹니다.
`dev` 에 머지돼도 서버 상태가 바뀌지 않습니다 — 스크리닝과 같은 방식입니다 (D-024).

`nginx/default.conf` 의 `location /gait/` 는 `client_max_body_size 200m` 과
`proxy_read_timeout 600s` 를 **그 블록 안에서만** 올려 뒀습니다. 영상은 사진과 달라서
서버 기본값(20m / 60s)으로는 정상 요청이 413·504 로 끊깁니다.
