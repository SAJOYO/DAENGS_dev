"""도감 카드 생성 GPU 서비스 (D-078).

Cloud Run L4 에서 diffusers 로 편집 모델 하나를 올리고 `POST /generate` 로 카드를 만든다.
backend 는 이 패키지를 import 하지 않고 HTTP 로만 부른다(`daengs_cardimage.engine.HttpCardImageEngine`).
이 패키지도 backend·cardimage 를 import 하지 않는다 — GPU 이미지에는 이 폴더만 들어간다.
torch·diffusers 는 함수 안에서만 import 한다 (`tests/test_cardgen_boundary.py`).
"""
