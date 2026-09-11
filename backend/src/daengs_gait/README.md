# 강아지 보행 영상 분석

산책 영상에서 관절 움직임을 기록하고, **같은 개체의 이전 기록과 비교**합니다.

> **진단이 아닙니다.** 질병·이상·악화를 판정하지 않습니다. 이 서비스가 하는 말은
> "이전 기록과 비교해 일부 움직임 지표에 차이가 관찰된다" 까지입니다.

원본은 실험 저장소 [`YH-KIKI/walk_demo`](https://github.com/YH-KIKI/walk_demo) 이고,
그중 **서비스에 실제로 필요한 것만** 옮겨 재구성했습니다 (아래 "무엇을 옮겼나").

## 빠르게 보기

이 패키지에는 **자기 HTTP 서버가 없습니다** (옛 `gait-serve`·`service.py` 는 D-063 4단계에서
제거). 실행부는 backend 의 Celery 워커입니다:

```powershell
cd backend
uv sync --group gait              # torch·ultralytics·opencv (약 2GB)
$env:GAIT_RELEASE_DIR = "C:\어딘가\release"
uv run celery -A daengs_backend.tasks.gait worker --queues gait --concurrency 1
```

앱 쪽 계약은 backend 의 `/app/gait/*` (`daengs_backend/routers/gait.py`) 입니다.

테스트 (가중치 없이 돕니다 — cv2 가 있으면 실영상 테스트까지):

```powershell
uv run pytest tests/test_gait_*.py
```

## 가중치

**저장소에 없습니다.** 원본 walk_demo 에서도 gitignore 대상이라 `git clone` 만으로는
따라오지 않습니다 — 파일을 직접 받아 두어야 합니다.

| 파일 | 엔진 | 역할 | 크기 |
| --- | --- | --- | --- |
| `best.pt` | legacy | 반려견 전용 12-keypoint pose (YOLOv8m-pose, nc=1) | 약 50.7MB |
| `yolov8n.pt` | legacy | crop-assist 용 범용 검출기 (COCO 원본, 파인튜닝 안 함) | 약 6.2MB |
| `ssdlite.pt` | v4 | SuperAnimal-Quadruped ssdlite 검출기 (sha256 `6c550a5f…7160d`). **academic / non-commercial** — 출처·라이선스는 [WEIGHTS_v4.md](WEIGHTS_v4.md) | 약 8.7MB |
| `rtmpose-m_ap10k/end2end.onnx` | v4 | RTMPose-m AP-10K 관절망 ONNX (sha256 `1cfd1c86…c7f28`, mmpose Apache-2.0). 없으면 첫 실행 때 OpenMMLab 공식 zip 에서 자동 다운로드 | 약 52MB |

네 파일을 **한 폴더**에 넣고 그 폴더를 가리킵니다 — 두 엔진이 같은 `GAIT_RELEASE_DIR` 을
씁니다(D-063 5B). 파일 이름이 달라 안 겹칩니다:

```
release/
  best.pt
  yolov8n.pt
  ssdlite.pt
  rtmpose-m_ap10k/
    end2end.onnx
```

```powershell
# 최상단 .env (서버 PC)
GAIT_RELEASE_DIR=C:\deploy\daengs\models\gait\release
```

배포 폴더(`releases\<커밋해시>`) **바깥**에 두세요 — 그 안에 두면 재배포 때 사라집니다.
`.env`·암호화 키와 같은 취급입니다.

> ⚠️ walk_demo 의 `models/experimental/*.pt` (6개, 각 ~50MB)는 **미채택 실험
> 가중치**입니다. 크기가 비슷해 헷갈리기 쉬우니 가져오지 마세요. production 모델은
> 프로젝트 시작 이후 한 번도 교체되지 않은 위 두 개뿐입니다.

경로는 환경변수로 각각 덮어쓸 수도 있습니다 (`GAIT_POSE_WEIGHTS`,
`GAIT_DETECTOR_WEIGHTS`). 원본 walk_demo 는 이 경로를 소스에 하드코딩하고 있었습니다.

## 서버에서 켜기

기본 `docker compose up -d` 에서는 **안 뜹니다.** profile 뒤에 있습니다.

```powershell
docker compose --profile gait up -d gait-worker
docker compose logs -f gait-worker            # `celery@… ready.`
```

앱이 부르는 주소는 `http://daengback.~/app/gait/*` 이고 backend 가 받습니다 — 영상은
bridge 업로드로 backend 를 지나 저장소에 놓이고, 워커가 큐에서 꺼내 분석합니다 (D-043 · D-052).
`backend/src` 가 바뀌면 워커는 자동 reload 되지 않으니 `docker compose restart gait-worker`.

옛 `location /gait/` 는 `nginx/default.conf` 에 **410 Gone** 묘비로만 남아 있습니다 — 옛 앱
빌드가 "서버가 잠깐 이상한가" 하고 재시도하지 않게 하려는 것입니다.

## API

**앱이 쓰는 계약은 backend 의 `/app/gait/*` 입니다** (`daengs_backend/routers/gait.py`,
`schemas/gait.py`). 이 패키지가 직접 내던 옛 `/gait/*` 계약은 [`API.md`](API.md) 에
역사 기록으로만 남아 있습니다 (D-063 4단계에서 제거).

- **`record_id` 는 UUID** 입니다 (DB `gait_records.id`).
- **응답에 디스크 경로가 나가지 않습니다.** overlay 는 bridge 다운로드 URL 로 냅니다.
- 소유권 검증은 `daengs_backend` 의 auth 계층 몫입니다.

### 업로드 크기 제한 (413)

`GAIT_MAX_UPLOAD_BYTES` (기본 150MB). skin-screening 의 12MB(사진 한 장)를 그대로
쓰지 않은 이유와 근거는 `docs/gait/record-data-design.md` 참고. `nginx/default.conf`
의 `location /app/gait/` 가 `client_max_body_size 200m` 로 바깥 상한을 잡아 두었으므로
**이 값은 항상 그보다 낮게** 유지하세요 — 그래야 nginx 의 맨 HTML 대신 앱이 이유가
담긴 JSON 413 을 먼저 돌려줍니다.

### 앱이 지켜야 할 것

1. **`_dev_only_` 로 시작하는 필드를 화면에 띄우지 마세요.** `_dev_only_raw_feature_cosine`
   은 개발·검증용 수치이고, 화면에 나오면 사용자가 "보행 건강 점수"로 읽습니다.
2. **`features.internal_feature_vector` 도 마찬가지입니다.** 수백 개의 숫자이고
   비교 계산 전용입니다. 화면에는 `summary_for_ui` 만 씁니다.
3. **overlay 영상은 원본보다 느리게 재생됩니다.** 5fps 로 서브샘플한 프레임에만 그리기
   때문입니다 — 그 사실을 화면에 적어야 "우리 개가 느려졌다"로 오해하지 않습니다.
4. `version_warning` 이 있으면 **반드시 함께 보여주세요.** 분석 버전이 다른 두 기록은
   같은 영상이라도 수치가 달라 보입니다.
5. `status` 가 `unavailable` 이면 수치가 아예 없습니다. `recommendation` 을 보여주고
   재촬영을 안내하세요.

## 무엇을 옮겼나

원본 walk_demo 의 `src/gait_demo/` 는 같은 폴더의 **실험 스크립트를 루트에서 직접
import** 하고 있었습니다. 그 스크립트들은 이름과 docstring 상 "실험"인데 실제로는
production 코드였고, 동시에 module 최상단에서 `pandas` · `scipy` · `scikit-learn` 과
다른 실험 모듈까지 끌고 왔습니다 — **알고리즘에는 하나도 쓰이지 않는 것들입니다.**

그래서 실제로 쓰는 상수·함수만 뽑아 재구성했습니다:

| 원본 | 여기 |
| --- | --- |
| `e3_common.KEYPOINT_NAMES`, `e13.TARGET_FPS`/`CONF_THRESH`/`KP_MIN_CONF`, `e14` 임계값 전부, `keypoint_extractor.DEFAULT_KEYPOINT_WEIGHTS` | `config.py` |
| `e14.apply_gait_filter`, `e14._kp_spread_ratio` | `gait_filter.py` |
| `e13.build_tracks`, `e3_trajectory_features._track_static_temporal`/`_stats`, `e14.kp_static_feats_from_recs` | `feature_engine.py` |
| `gait_demo/*.py` | `daengs_gait/*.py` (같은 이름) |
| `frontend/server.py` 의 `_ensure_mp4` · `_download_url` | `video_intake.py` → 판정만 `intake.py` 로 (3단계), 나머지는 4단계에서 제거 |
| `frontend/server.py` (HTTP 어댑터) | `service.py` (FastAPI) → 4단계에서 제거, 역할은 backend `/app/gait/*` + `gait-worker` |

**계산은 한 줄도 바꾸지 않았습니다.** 같은 영상을 두 구현으로 분석해 대조했고,
feature vector 121차원 · quality 통계 · trajectory 가 전부 일치했습니다.
유일한 차이는 사용자 문구에서 walk_demo 연구 문서의 절 번호(`§21`) 참조를 뺀 것입니다 —
그 문서가 이 저장소에 없어서 가리킬 곳이 없기 때문입니다.

옮기지 않은 것: severity(중증도) 실험 전체, Dog-Pose 백본 개선 실험 전체(전부 기각됨),
실험 가중치 6개, 연구 리포트, 원본 데이터셋, 데모 웹 UI.

## v4 엔진 — walk_demo v4 (ssdlite + RTMPose AP-10K)

walk_demo 에서 확정한 **v4**(SuperAnimal ssdlite 검출기 직접 호출 + RTMPose-m AP-10K 17 관절,
kp_conf 0.30, 후면 좌/우 x-순서 정렬)를 **DeepLabCut·CUDA 없이 CPU 로** 돌립니다. 원본은
`YH-KIKI/walk_demo` `experiment/animal-pose-models` `6bbdb73` 의 `port/gait_v4/` 이고, #304 에서
`backend/gait_v4/` 별도 폴더·별도 venv 로 들어왔다가 D-063 5A(의존성)·5C(계산)·5B(추론·실행)를
거쳐 이 패키지 안으로 완전히 합쳐졌습니다.

```
inference/model.py          v4 모델·가중치 경로 상수 (AP10K 관절·스켈레톤·ssdlite/RTMPose 경로)
inference/ssdlite_detector  torchvision ssdlite 직접 로드 (ImageNet 선정규화 필수 — 빼면 박스 2배)
inference/pose.py           5fps 샘플 → 박스 → RTMPose ONNX → 좌/우 정렬  (run_pose)
inference/analyze.py        영상 → record dict — 필터·품질·궤적·feature·overlay 는 **공유 모듈** 호출
inference/__main__.py       python -m daengs_gait.inference analyze|compare
compare_v4.py               v4 비교 출력(message_kind·side_summary·condition_flags) — 판정 계산은 compare.direction_note 재사용
engines/subprocess_bridge   워커가 sys.executable 로 위 CLI 를 서브프로세스로 부름
```

- **켜는 법**: `GAIT_ENGINE=v4` (기본 legacy — `ssdlite.pt` 라이선스 결정 전 운영에서 바꾸지 마세요).
  별도 설치 없음 — `uv sync --group gait` 하나로 legacy·v4 둘 다 깔립니다.
- **골든 조건**: CPU · torch 스레드 1 (`CUDA_VISIBLE_DEVICES=-1` · `GAIT_V4_TORCH_THREADS=1` —
  스레드 수가 0.01px 반올림 경계를 가릅니다). 검사는 `tests/test_gait_v4_golden.py`
  (`GAIT_V4_GOLDEN_VIDEO=<IMG_8628_13.mp4>`; 영상·가중치가 없으면 skip — **로컬 관문에서는 skip 을
  통과로 치지 않습니다**).
- **손대지 말 것**: `inference/model.py` 의 상수 · `gait` 그룹의 `==` 핀(`tests/test_gait_v4_lock.py`) ·
  DeepLabCut·CUDA 추가. 바꾸면 walk_demo 골든과 어긋납니다.
- **알려진 한계**(단일 프레임 극값이 판정을 뒤집을 수 있음 · 원거리 저점수 장면에서 검출기가 배경을
  잡음 · 골든은 "정답"이 아니라 "현재 출력"): [KNOWN_LIMITATIONS_v4.md](KNOWN_LIMITATIONS_v4.md) —
  walk_demo 에서 그대로 가져온 문서입니다. 가중치 출처·라이선스·해시: [WEIGHTS_v4.md](WEIGHTS_v4.md).

## 임계값을 바꿀 때

`config.py` 의 값은 전부 walk_demo 에서 **실측으로** 정해진 것입니다. 결과를 보기
전에 고정하고 사후 조정하지 않는다는 원칙으로 잡혀서, 임의로 바꾸면 그 검증이 무의미해집니다.

바꿔야 한다면 **`GAIT_FILTER_VERSION` 을 함께 올리세요.** 같은 영상이라도 이 값이 다르면
어떤 프레임을 유효로 볼지 기준 자체가 달라져 관절 이동범위가 달라 보입니다 —
`compare_records` 가 두 기록의 이 값을 대조해 경고를 붙입니다.

`tests/test_gait_filter.py` 가 값들을 박아 두고 있어, 바꾸면 테스트가 먼저 알려 줍니다.

## 아직 정하지 않은 것 (TBD)

- **기록을 DB 로 옮길지.** 지금은 `GAIT_DATA_DIR` 아래 JSON 파일입니다(walk_demo 그대로).
  DAENGS 에는 PostgreSQL + SQLAlchemy 가 있지만, 이 서비스가 DB 를 직접 볼지 아니면
  `daengs_backend` 가 기록의 주인이 될지가 먼저 정해져야 합니다. 스크리닝이 DB 를 안 보는
  무상태 서비스인 것과 같은 자리입니다. 역할 분리안·gait record 스키마안·삭제 시 정리
  범위는 `docs/gait/record-data-design.md` 에 설계만 해 두었습니다 — 이 카드에서
  실제 테이블·migration 은 만들지 않았습니다.
- **자동 삭제(보관 기간)는 아직 없습니다.** 사용자가 부르는
  `DELETE /gait/records/{id}` 는 있고 원본·overlay 까지 지웁니다. 하지만 **기간이 지나면
  스스로 지우는 코드는 없습니다** — 보관 정책(동의 문구·기간·탈퇴 시 파기)이 정해져야
  하는 자리이고, 공용 저장소 카드(#78)가 그것을 다룹니다.
  ⚠️ 그때까지는 `gait-data` 볼륨이 **계속 쌓입니다.** 영상 10초가 5~20MB 라
  1,000건이면 5~20GB 이고, 서버 디스크가 차면 같은 PC 의 DB 까지 위협합니다.
- **URL 업로드(`yt-dlp`)를 유지할지.** 유지하지 않으면 `--extra url` 을 통째로 뺄 수 있습니다.
  현재 `service.py` 에는 엔드포인트를 두지 않았습니다.
- **`dog_id` 소유권 검증.** 이 서비스는 넘어온 값을 그대로 믿습니다 — 그리고
  **앞으로도 여기서 검증하지 않습니다.** 계정·세션·소유 관계를 아는 것은
  `daengs_backend` 이고, 검증은 그쪽 auth 계층 몫입니다 (`API.md` §소유권).
  gait 는 상위가 강제할 수 있도록 **모든 기록이 `dog_id` 를 갖는 것**만 지킵니다 —
  DB 로 갈 때 그것이 외래 키가 되는 자리입니다.
- **인증.** 위 "서버에서 켜기" 의 경고 참고.
- ~~가중치를 git 에 넣을지~~ — **정했습니다: 넣지 않습니다** (D-029 → D-038 이 유지).
  57MB 이고 git 은 바이너리를 델타로 못 줄여 재학습본마다 옛 것이 히스토리에 영구히
  남습니다. 서버 디스크 + `GAIT_RELEASE_DIR` read-only 마운트입니다.
- **GPU.** 지금은 CPU 판 torch 를 받습니다. 영상 길이에 따라 분 단위가 걸립니다.
