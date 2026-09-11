# ssdlite.pt — 출처·라이선스·해시 (2026-09-07)

| 항목 | 값 |
|---|---|
| 파일 | `models/pretrained/superanimal_quadruped/ssdlite.pt` (9,144,611 bytes) |
| SHA256 | `6c550a5fe63c4a0e40d85f388c4b4b86d74b3c94ab6a0fef090b831cf257160d` |
| 원본 | DeepLabCut 3.0.1 model zoo 캐시 `deeplabcut/modelzoo/checkpoints/superanimal_quadruped_ssdlite.pt` — 동일 SHA256 확인 |
| 다운로드 출처 | Hugging Face `mwmathis/DeepLabCutModelZoo-SuperAnimal-Quadruped` / `superanimal_quadruped_ssdlite.pt` (dlclibrary `modelzoo_urls_pytorch.yaml` 의 `superanimal_quadruped.detectors.ssdlite`) |
| 구조 | torchvision `ssdlite320_mobilenet_v3_large`, num_classes=2(배경+동물), state_dict 키에 `model.` 접두어 |
| 전처리 | DLC 설정 `normalize_images: true` = ImageNet mean/std 선정규화 후 입력 (이중 정규화 상태로 학습된 모델. 빼면 박스가 2배로 벌어짐 — README §11-22) |
| 로더 | `src/gait_demo/ssdlite_detector.py` |

## 라이선스 — 서비스 적용 전 반드시 확인

- Hugging Face 모델 카드 기준 라이선스: **Modified MIT — "academic, non-commercial purposes only"** (2026-09-07 조회).
  인용 요구: Ye et al., SuperAnimal (arXiv 2203.07436). 동물 학대 목적 사용 금지 조항 포함.
- DeepLabCut 소프트웨어 자체는 LGPL-3.0-or-later 이지만, 이 파일은 소프트웨어가 아니라 **model zoo 가중치**라 위 모델 카드 조건을 따른다.
- 모델 카드 본문에는 `detector.pt`(Faster R-CNN)·`hrnet_w32`·`pose_model.pth` 만 언급되고 ssdlite 파일은 파일 목록에만 있다 — 별도 조건이 붙어 있지 않다면 같은 카드 라이선스가 적용된다고 보는 것이 안전하다.
- **상업 서비스(DAENGS)에 넣으려면 저작권자(Mathis Lab) 상업 라이선스 확보 또는 검출기 교체가 필요하다.** 이 레포에서는 결정하지 않는다.
  교체 후보(같은 인터페이스 `detect(frame)->(box,score)`): torchvision COCO 검출기(dog 클래스, Apache-2.0/BSD) 또는 YOLO 계열(AGPL 주의). §11-21 에서 yolov8n COCO-dog 는 후면·원거리 검출률이 ssdlite 보다 낮았다(README §11-2·§11-9).

## 같이 쓰는 관절망 (Git 제외, 별도 전달)

| 항목 | 값 |
|---|---|
| 파일 | `models/pretrained/rtmpose-m_ap10k/end2end.onnx` (54,478,120 bytes, `.gitignore` 로 제외) |
| SHA256 | `1cfd1c86e0d9e5d5f95178bcd95ee9a4e8386a624cd3c57519f27ff58cac7f28` |
| 출처 | OpenMMLab mmpose `rtmpose-m_simcc-ap10k_pt-aic-coco_210e-256x256-7a041aa1_20230206` ONNX SDK zip — `pose_backends.RTMPOSE_ONNX_URL` (`_ensure_rtmpose_onnx` 가 없으면 자동 다운로드·추출) |
| 라이선스 | mmpose Apache-2.0. 학습 데이터 AP-10K 의 데이터셋 라이선스는 별도 확인 필요 |
