# -*- coding: utf-8 -*-
"""SuperAnimal-Quadruped ssdlite 검출기를 DeepLabCut 없이 직접 돌린다 (walk_demo src/gait_demo/ssdlite_detector.py 와 동일).

체크포인트 `ssdlite.pt` 는 순수 torchvision `ssdlite320_mobilenet_v3_large`(클래스 2 = 배경+동물) state_dict 에
`model.` 접두어가 붙은 것이다. DLC 설정은 리사이즈/패딩 없이 **ImageNet mean/std 선정규화** 를 먼저 적용한다.
torchvision SSD 가 내부에서 (0.5, 0.5, 0.5) 로 다시 정규화하므로 "이중 정규화" 상태로 학습·추론된 모델이다 —
이 선정규화를 빼면 점수는 비슷한데 박스 폭이 2배로 벌어진다(DLC 박스와 IoU 0.39 → 넣으면 0.975).

동작: score_thresh 0.01, 프레임당 점수 최대 박스 1개, 박스 좌표는 원본 픽셀. 모델은 프로세스에 1회 로드해 상주.
가중치 출처·라이선스: weights/README.md (academic / non-commercial — 상업 적용 전 확인).
"""
import os

import cv2
import numpy as np

from .config import SSDLITE_SCORE_THRESH, SSDLITE_WEIGHTS

_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_cache = {}


def _load():
    if "model" in _cache:
        return _cache["model"]
    import torch
    from torchvision.models.detection import ssdlite320_mobilenet_v3_large
    # 스레드 수는 결과에 1e-3 px 수준의 차이를 만든다(스레드 6 vs 1 에서 R_Hip 1좌표가 0.01px 반올림 경계에서 갈림, 2026-09-07).
    # walk_demo 골든은 스레드 1 로 만들어졌다. 완전 동일 재현이 필요하면 GAIT_V4_TORCH_THREADS=1 (검출 속도는 ~2.5배 느려짐).
    if os.environ.get("GAIT_V4_TORCH_THREADS"):
        torch.set_num_threads(int(os.environ["GAIT_V4_TORCH_THREADS"]))
    if not SSDLITE_WEIGHTS.exists():
        raise FileNotFoundError(f"ssdlite 가중치가 없습니다: {SSDLITE_WEIGHTS} (weights/README.md 참조, GAIT_V4_WEIGHTS 로 위치 지정 가능)")
    model = ssdlite320_mobilenet_v3_large(weights=None, weights_backbone=None, num_classes=2,
                                          score_thresh=SSDLITE_SCORE_THRESH).eval()
    ck = torch.load(SSDLITE_WEIGHTS, map_location="cpu", weights_only=False)
    sd = ck["model"] if isinstance(ck, dict) and "model" in ck else ck
    sd = {k[len("model."):] if k.startswith("model.") else k: v for k, v in sd.items()}
    missing, unexpected = model.load_state_dict(sd, strict=False)
    if missing or unexpected:
        raise RuntimeError(f"ssdlite state_dict 불일치: missing {len(missing)} unexpected {len(unexpected)}")
    _cache["model"] = model
    return model


def detect(frame_bgr):
    """프레임 1장 → (box [x1,y1,x2,y2] 원본 픽셀, score) 또는 검출 없으면 None."""
    import torch
    model = _load()
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    x = torch.from_numpy((rgb - _IMAGENET_MEAN) / _IMAGENET_STD).permute(2, 0, 1).contiguous()
    with torch.no_grad():
        out = model([x])[0]
    if len(out["scores"]) == 0:
        return None
    i = int(out["scores"].argmax())
    return [float(v) for v in out["boxes"][i].tolist()], float(out["scores"][i])
