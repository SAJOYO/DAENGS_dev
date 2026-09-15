# 도감 카드 생성 GPU 서비스 (D-078) — Cloud Run L4 (asia-southeast1).
#
#   gcloud builds submit 은 infra/gcp/cardgen.sh 가 한다. 로컬 docker build 는 하지 않는다.
#
# **코드는 이미지에 굽는다** (docker/realtime 과 같은 이유 — 서비스는 "지금 도는 코드" 가 배포 단위와
# 같아야 롤백이 된다). **가중치는 굽지 않는다** — 20~57GB 라 빌드·풀·콜드 스타트가 전부 느려진다.
# 버킷을 /models 로 마운트하고 HF_HOME 이 그것을 본다(`python -m daengs_cardgen.fetch` 가 채운다).
#
# lock 의 torch 는 리눅스 CPU 판으로 잠겨 있다(backend/pyproject.toml [tool.uv.sources]). CUDA 판은
# **설치가 끝난 뒤** torch 만 cu126 인덱스에서 덮어쓴다 — 방법과 함정은 docker/pipeline/Dockerfile 주석을
# 그대로 따른다: `torch==X` 는 `+cpu` 에 만족돼 조용히 건너뛰므로 `+cu126` 을 박고,
# `uv sync --frozen` 은 이 파일에 한 번만 나온다(덮어쓰기 뒤에 부르면 CPU 판으로 되돌린다).
ARG PYTHON_VERSION=3.12.13
ARG UV_VERSION=0.12.3

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uvbin

FROM python:${PYTHON_VERSION}-slim
COPY --from=uvbin /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONPATH=/app/src \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1

# torch 2.13 의 CUDA 경로는 Triton 커널을 실행 시점에 컴파일한다 — slim 에는 gcc 가 없다 (2026-09-08 L4 실측).
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --only-group cardgen --no-install-project

RUN set -eux; \
    V_TORCH="$(uv pip show --python /opt/venv/bin/python torch | awk '/^Version/ {print $2}' | cut -d+ -f1)"; \
    uv pip install --python /opt/venv/bin/python --reinstall-package torch \
        --index-url https://download.pytorch.org/whl/cu126 "torch==${V_TORCH}+cu126"
# 빌드 머신에는 GPU 가 없어 is_available() 은 못 쓴다 — "CUDA 빌드인지"만 본다.
RUN /opt/venv/bin/python -c "import torch, sys; print('torch', torch.__version__, 'cuda', torch.version.cuda); sys.exit(0 if torch.version.cuda else 1)"

COPY backend/src/daengs_cardgen ./src/daengs_cardgen

# 포트는 Cloud Run 이 PORT 로 준다. 모델은 CARDGEN_MODEL 로 고른다(cardgen.sh).
CMD ["sh", "-c", "exec /opt/venv/bin/uvicorn daengs_cardgen.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
