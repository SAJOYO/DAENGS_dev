# 코퍼스 파이프라인 이미지 (D-062). Cloud Run Job `corpus-refresh`(CPU) · `corpus-embed-full`(CUDA).
#
#   docker build -f docker/pipeline/Dockerfile -t pipeline:cpu  .
#   docker build -f docker/pipeline/Dockerfile -t pipeline:cuda --build-arg TORCH=cuda .
#
# 빌드 컨텍스트는 **저장소 루트**다 — backend/ 와 data/manifests/seed_sources.yaml 둘 다 필요해서.
# `docker/pipeline/Dockerfile.dockerignore` 가 그 컨텍스트를 backend/pyproject.toml ·
# backend/uv.lock · backend/README.md · backend/src · seed_sources.yaml · entrypoint.sh 로만
# 좁힌다 — 안 그러면 frontend/node_modules 나 data/raw 같은 것까지 전부 올라간다.
#
# lock 의 torch 는 리눅스에서 CPU 인덱스로 고정돼 있다 (backend/pyproject.toml [tool.uv.sources]).
# 서버 backend 컨테이너가 NVIDIA 런타임 10GB 를 받지 않게 한 결정이라 lock 을 안 건드린다.
# CUDA 판은 sync 뒤에 **같은 버전**의 torch·torchvision 을 cu126 인덱스에서 덮어쓴다 — `uv pip`
# 은 lock 을 안 보므로 lock 은 그대로다. 버전은 lock 에서 읽어 어긋나지 않게 한다.
ARG TORCH=cpu

FROM uv:1 AS base
# uv:1 은 docker/uv/Dockerfile 로 굽는 공용 베이스 (python 3.12 + uv). 로컬에 없으면
#   docker build -t uv:1 docker/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    HF_HOME=/models \
    DAENGS_DATA_DIR=/data \
    DAENGS_WARM_UP_ENCODER=false

# 의존성만 먼저 — 소스가 바뀌어도 이 레이어는 캐시된다.
# README.md 도 필요하다 — pyproject.toml 의 readme="README.md" 를 uv 가 빌드 메타데이터로 읽는다
# (COPY 만 하고 실제 --no-install-project 라 내용은 안 쓰지만, 없으면 sync 가 실패한다).
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-install-project --group ml --group pipeline

FROM base AS torch-cpu
# lock 그대로 (CPU torch)

FROM base AS torch-cuda
# `uv pip` 는 uv sync/run 과 달리 UV_PROJECT_ENVIRONMENT 를 보지 않고 VIRTUAL_ENV 나
# 현재 디렉터리의 .venv 를 찾는다 — 여기엔 둘 다 없으므로 --python 으로 /opt/venv 를
# 명시하지 않으면 uv 가 새 venv 를 만들거나 에러를 낸다 (브리핑 초안 draft 에는 없었다).
RUN set -eux; \
    V_TORCH="$(uv pip show --python /opt/venv/bin/python torch | awk '/^Version/ {print $2}')"; \
    V_TV="$(uv pip show --python /opt/venv/bin/python torchvision | awk '/^Version/ {print $2}')"; \
    uv pip install --python /opt/venv/bin/python \
        --index-url https://download.pytorch.org/whl/cu126 \
        "torch==${V_TORCH}" "torchvision==${V_TV}"

FROM torch-${TORCH} AS final
COPY backend/src ./src
COPY data/manifests/seed_sources.yaml /app/seed_sources.yaml
RUN uv sync --frozen --group ml --group pipeline     # 프로젝트 자체(엔트리포인트) 설치

# 가중치를 굽는다 — 잡이 뜰 때마다 1.2GB 를 받지 않게. 모델 키는 rag/core/config.py 기본값과 같다.
RUN uv run --no-sync python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('Qwen/Qwen3-Embedding-0.6B', device='cpu')"

COPY docker/pipeline/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
