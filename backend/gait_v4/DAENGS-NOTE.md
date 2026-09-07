# 이 폴더가 DAENGS 안에서 갖는 자리 (#304)

`README.md` · `KNOWN_LIMITATIONS.md` · `gait_v4/` · `tests/` 는 **walk_demo 에서 그대로 가져온 것**입니다
(`YH-KIKI/walk_demo` · `experiment/animal-pose-models` · `6bbdb73` · `port/gait_v4/`). 이 파일만 DAENGS 쪽 메모입니다.

## 왜 별도 uv 프로젝트인가

backend 는 `backend/pyproject.toml` 하나로 여러 패키지를 관리합니다 (D-039). 이 폴더는 **일부러 거기에
합치지 않았습니다** — 자기 `pyproject.toml` · `uv.lock` · `.venv` 를 갖습니다.

1. **버전이 `==` 로 못 박혀 있고, 골든이 그 조합에서 나왔습니다.** `numpy==2.5.2` · `opencv-python==5` ·
   `torch==2.13.0` 을 backend lock 에 넣으면 `gait`(ultralytics) · `ml` 그룹과 충돌하고 lock 이 통째로
   다시 풀립니다. 그러면 `tests/verify.py` 가 검사하는 0.01px 일치가 더는 같은 조건이 아닙니다.
2. **라이선스 결정 전입니다.** `weights/ssdlite.pt` 는 academic / non-commercial (`weights/README.md`).
   별도 venv 면 compose 가 모르는 채로 남아 **운영 이미지에 들어가지 않습니다.**

그래서 워커(`daengs_backend.services.gait._analyze_with_v4`)는 이 폴더의 `.venv/…/python -m gait_v4 analyze`
를 **서브프로세스**로 부릅니다. 비교(`_load_v4_compare`)는 `gait_v4/compare.py` 를 **파일로** 불러옵니다 —
패키지 `__init__` 을 타면 torch 를 끌고 오는데 backend 웹 venv 에는 없습니다.

## 켜는 법 (내부 검증)

```
GAIT_ENGINE=v4          # 기본은 legacy. 운영에서 바꾸지 마세요 — 라이선스 결정 전
GAIT_V4_DIR=            # 비우면 이 폴더
```

```powershell
cd backend/gait_v4
uv sync                                    # torch CPU 휠 — 수 분
uv run python tests/verify.py <영상>       # 기대: 결과: PASS
```

## 손대지 말 것

- `gait_v4/config.py` 의 상수 · 필터 · 정규화 · 판정 로직. 바꾸면 walk_demo 골든과 어긋납니다.
- `pyproject.toml` 의 버전. 위 1 과 같은 이유입니다.
- DeepLabCut · ultralytics · CUDA 를 더하지 마세요. CPU 단독이 조건입니다.

## 가중치

| 파일 | git | 어디서 |
| --- | --- | --- |
| `weights/ssdlite.pt` (8.7 MB) | **루트 `.gitignore` 의 `*.pt` 에 걸려 추적 안 됨** | walk_demo 커밋 `6bbdb73` 의 `port/gait_v4/weights/`. SHA256 `6c550a5f…7160d` |
| `weights/rtmpose-m_ap10k/end2end.onnx` (52 MB) | 제외 | 첫 실행 때 자동 다운로드, 또는 손으로. SHA256 `1cfd1c86…c7f28` |

walk_demo 는 `ssdlite.pt` 를 커밋했지만 이 저장소는 가중치를 git 에 두지 않는 쪽입니다 (루트 `.gitignore`
의 이유 참고 — 바이너리는 델타가 안 돼 히스토리에 영구히 남습니다). 어느 쪽으로 갈지는 #304 에서 정합니다.
