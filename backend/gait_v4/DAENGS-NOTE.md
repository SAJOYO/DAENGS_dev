# 이 폴더가 DAENGS 안에서 갖는 자리 (#304)

`README.md` · `KNOWN_LIMITATIONS.md` · `gait_v4/` · `tests/` 는 **walk_demo 에서 그대로 가져온 것**입니다
(`YH-KIKI/walk_demo` · `experiment/animal-pose-models` · `6bbdb73` · `port/gait_v4/`). 이 파일만 DAENGS 쪽 메모입니다.

## 의존성은 backend lock 하나, 코드만 아직 이 폴더 (D-063 5A)

backend 는 `backend/pyproject.toml` 하나로 여러 패키지를 관리합니다 (D-039). 처음(#304)에는 이 폴더가
**자기 `pyproject.toml` · `uv.lock` 을 가진 별도 uv 프로젝트**였는데, 5A(2026-09-10)에서 그 두 파일과
`requirements.txt` 를 없애고 **핀을 backend `pyproject.toml` 의 `gait-v4` 그룹으로 옮겼습니다.** 이제 워커
컨테이너의 두 venv(gait-venv · gait-v4-venv)는 같은 `backend/uv.lock` 에서 나옵니다. 코드(`gait_v4/`)와
venv 는 여전히 갈라져 있습니다 — 그건 5B(inference 를 `daengs_gait` 로) 몫입니다.

옮긴 핀은 **버전 그대로**입니다 (walk_demo `port/gait_v4` 의 pyproject, torch 는 cpu 인덱스). 골든
(`tests/golden_v4_rear.json`, 0.01px)이 이 조합에서 나왔으므로, 올리려면 골든을 다시 만드는 결정이 먼저입니다
— `backend/tests/test_gait_v4_lock.py` 가 lock 의 버전을 이 표와 대조합니다.

| 원본 핀 (walk_demo) | backend `gait-v4` 그룹 | 비고 |
| --- | --- | --- |
| `torch==2.13.0` | 같음 | linux 는 cpu 인덱스(서버), win32 는 cu126 — 코드가 CPU 고정이라 수치 동일 |
| `torchvision==0.28.0` | 같음 | |
| `onnxruntime==1.29.0` | 같음 | |
| `rtmlib==0.0.16` | 같음 | |
| `opencv-python==5.0.0.93` (GUI) | **`opencv-python-headless==5.0.0.93` + `opencv-contrib-python-headless==5.0.0.93`** | 옛 compose 가 런타임에 손으로 하던 GUI→headless 교체를 lock 으로 옮긴 것. 같은 버전·같은 API. GUI 판은 `[tool.uv] override-dependencies` 로 막음 |
| `numpy==2.5.2` | 같음 | |
| `imageio-ffmpeg==0.6.0` | 같음 | |

**라이선스 결정은 아직입니다.** `weights/ssdlite.pt` 는 academic / non-commercial (`weights/README.md`).
`gait-v4` 는 기본 그룹이 아니라 `uv sync` 로는 안 깔리고, compose 도 `GAIT_ENGINE=v4` 일 때만 그 venv 를
만듭니다 — 운영 이미지에 저절로 들어가지 않는 성질은 그대로입니다.

워커(`daengs_gait.engines.v4.V4Engine`)는 이 폴더 코드를 `.venv/…/python -m gait_v4 analyze` **서브프로세스**로
부릅니다. 비교(`_load_v4_compare`)는 `gait_v4/compare.py` 를 **파일로** 불러옵니다 — 패키지 `__init__` 을
타면 torch 를 끌고 오는데 backend 웹 venv 에는 없습니다.

## 켜는 법 (내부 검증)

```
GAIT_ENGINE=v4          # 기본은 legacy. 운영에서 바꾸지 마세요 — 라이선스 결정 전
GAIT_V4_DIR=            # 비우면 이 폴더
```

이 폴더에는 pyproject 가 없으므로 **여기서 `uv sync` · `uv run` 을 치면 안 됩니다** — 상위 backend
프로젝트를 잡아 torch v4 가 없는 backend venv 로 돕니다. backend 에서 그룹 하나만 이 폴더의 `.venv` 로 동기화하고,
그 venv 의 python 을 직접 부릅니다:

```powershell
cd backend
$env:UV_PROJECT_ENVIRONMENT = "$PWD\gait_v4\.venv"
uv sync --frozen --only-group gait-v4 --no-install-project      # torch 휠 — 수 분 (win32 는 cu126, 약 2.5GB)
Remove-Item Env:UV_PROJECT_ENVIRONMENT
$env:CUDA_VISIBLE_DEVICES = "-1"; $env:GAIT_V4_TORCH_THREADS = "1"   # 골든 조건: CPU · 스레드 1 (빈 문자열은 Windows 에서 안 먹음 — -1)
gait_v4\.venv\Scripts\python tests\verify.py <IMG_8628_13.mp4 경로>   # 기대: 결과: PASS
```

(`tests/verify.py` 는 `sys.path` 에 상위 폴더를 넣어 `gait_v4` 를 설치 없이 import 합니다. 컨테이너의
`python -m gait_v4` 도 같은 이유로 설치가 필요 없습니다.)

## 손대지 말 것

- `gait_v4/config.py` 의 상수 · 필터 · 정규화 · 판정 로직. 바꾸면 walk_demo 골든과 어긋납니다.
- backend `pyproject.toml` 의 `gait-v4` 그룹 버전. 위 표와 같은 이유입니다 (`test_gait_v4_lock.py` 가 잡습니다).
- DeepLabCut · ultralytics · CUDA 를 더하지 마세요. CPU 단독이 조건입니다.

## 가중치

| 파일 | git | 어디서 |
| --- | --- | --- |
| `weights/ssdlite.pt` (8.7 MB) | **루트 `.gitignore` 의 `*.pt` 에 걸려 추적 안 됨** | walk_demo 커밋 `6bbdb73` 의 `port/gait_v4/weights/`. SHA256 `6c550a5f…7160d` |
| `weights/rtmpose-m_ap10k/end2end.onnx` (52 MB) | 제외 | 첫 실행 때 자동 다운로드, 또는 손으로. SHA256 `1cfd1c86…c7f28` |

walk_demo 는 `ssdlite.pt` 를 커밋했지만 이 저장소는 가중치를 git 에 두지 않는 쪽입니다 (루트 `.gitignore`
의 이유 참고 — 바이너리는 델타가 안 돼 히스토리에 영구히 남습니다). 어느 쪽으로 갈지는 #304 에서 정합니다.
