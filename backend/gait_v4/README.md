# gait_v4 — 보행 분석 이식 패키지 (walk_demo → DAENGS dev)

walk_demo 에서 확정한 **v4** (SuperAnimal ssdlite 검출기 직접 호출 + RTMPose-m AP-10K 관절망, kp_conf 0.30, 후면 좌/우 x-순서 정렬)를
**DeepLabCut·CUDA 없이 CPU 로** 돌리는 자족 패키지. 2026-09-07 기준 walk_demo 커밋의 알고리즘·판정 규칙과 동일하며,
`tests/verify.py` 가 walk_demo 회귀 골든과 픽셀 단위(0.01px)로 같음을 검사한다.

## ⚠ 먼저: 가중치 라이선스
`weights/ssdlite.pt` 는 SuperAnimal model zoo 가중치로 **academic / non-commercial only** (`weights/README.md`).
상업 서비스 적용 전에 라이선스 확보 또는 검출기 교체 결정이 필요하다. 이 패키지는 기술 이식 준비이지 그 결정을 대신하지 않는다.

## 구성
```
gait_v4/            파이썬 패키지 (walk_demo 함수를 로직 변경 없이 옮김 — 각 파일 머리에 출처 표기)
  config.py         상수·모델 정의 (값 하나라도 바꾸면 골든과 어긋남)
  ssdlite_detector  torchvision ssdlite 직접 로드 (ImageNet 선정규화 필수)
  pose.py           5fps 샘플 → 박스 → RTMPose ONNX → 좌/우 정렬  (run_pose)
  gait_filter.py    프레임별 gait_usable (정지·박스 크기·관절 수)
  quality.py        분석 가능 여부·tier
  trajectory.py     관절별 픽셀 궤적
  features.py       정규화·x/y 범위(max−min, P90−P10)   (build_features)
  compare.py        두 기록 비교 → message_kind / side_summary / condition_flags  (compare_records)
  overlay.py        skeleton mp4 (선택, imageio-ffmpeg libx264)
  analyze.py        영상 → record dict  (analyze_video)   ← dev 가 호출할 진입점
weights/            ssdlite.pt (git 포함) · rtmpose-m_ap10k/end2end.onnx (git 제외, 첫 실행 시 자동 다운로드 52 MB)
tests/verify.py     골든 일치·재현성·DLC 미사용 검사   tests/golden_v4_rear.json (walk_demo regress 골든 사본)
```

## 설치 (uv)
> **DAENGS 안에서는 이 절을 쓰지 마세요.** 이 폴더의 `pyproject.toml`·`uv.lock`·`requirements.txt` 는 D-063 5A 에서
> 없앴고 의존성은 `backend/pyproject.toml` 의 `gait-v4` 그룹입니다 — 절차는 `DAENGS-NOTE.md`. 아래는 walk_demo 원본 그대로입니다.

```bash
cd port/gait_v4
uv sync                      # .venv 생성, torch CPU 휠은 pyproject 의 pytorch-cpu 인덱스에서
uv run python tests/verify.py <황도13초 영상 경로>     # 기대: 결과: PASS
```
pip 라면: `pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu` 후 `pip install -e .`.
RTMPose ONNX 는 `weights/rtmpose-m_ap10k/end2end.onnx` 에 두거나(SHA256 `1cfd1c86…c7f28`), 없으면 첫 실행에서 OpenMMLab 공식 zip 을 받는다.
가중치 폴더를 다른 곳에 두려면 `GAIT_V4_WEIGHTS=/path/to/weights`.

## 사용
```python
from gait_v4 import analyze_video, compare_records

record, frame_records = analyze_video("walk.mp4", follow_cam=True, overlay_out="walk_overlay.mp4")
# record: walk_demo 기록 JSON 과 같은 필드 (quality, lr_fix, trajectories, features.summary_for_ui, timing.ssd_backend="direct" …)
# frame_records: 프레임별 kps/det_box/gait_usable — 저장은 dev 의 저장 로직이 결정
result = compare_records(record_a, record_b)   # message_kind: no_change | one_side | both_sides | change
```
CLI: `python -m gait_v4 analyze walk.mp4 --follow-cam --out rec.json` / `python -m gait_v4 compare a.json b.json`

- `follow_cam=True` 는 카메라가 개를 따라가는 영상(정지 판정 생략). 촬영 가이드에서 사용자가 고르게 할 것.
- `compare_records` 의 `message_for_ui` 문구는 서비스에서 바꿔도 된다. 계약은 `message_kind` · `side_summary` · `condition_flags`.
- `_dev_only_*` 필드는 UI 에 노출 금지.

## 재현성·스레드
- 같은 프로세스 안에서는 2회 실행이 0.0px 로 동일(결정적).
- torch 스레드 수가 다르면 ssdlite 박스가 1e-5 수준으로 달라져 관절 1좌표가 0.01px 반올림 경계에서 갈릴 수 있다
  (실측: 스레드 6 vs 1 → R_Hip 1좌표 520.68 vs 520.67). walk_demo 골든은 스레드 1 로 만들어졌다.
- `GAIT_V4_TORCH_THREADS=1` 로 고정하면 골든과 0.000px 일치(검출 ~2.5배 느려짐). 서비스에서는 고정하지 않아도 되고,
  회귀 비교 허용치 0.01px 안에 든다. 검증 결과(2026-09-07, 깨끗한 uv env): 기본 스레드 0.010px PASS · 스레드 1 → 0.000px PASS.

## 성능 (CPU, 5fps 샘플)
13초 영상 ≈ 15s, 60초 ≈ 58s. 첫 호출에 모델 로드 ≈ 4s. 검출 47 ms/f + 관절망 ≈ 0.15 s/f.

## 알려진 한계 (walk_demo `docs/KNOWN_LIMITATIONS.md` 를 그대로 가져갈 것)
- 단일 프레임의 넓은 박스 → 관절 크롭 붕괴 → max−min 포화 → 판정 뒤집힘 가능(황도60초 f178). 미해결.
- 원거리·저점수 장면에서 검출기가 나무·배경을 잡는다. 영상 단위 det_conf 게이트는 수치만 확인, 미구현.
- 골든은 "정답" 이 아니라 "현재 출력" 이다. 의존성 버전이 바뀌어 FAIL 이 나면 손 확인 후 갱신.
