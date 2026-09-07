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
# CUDA 판은 **프로젝트 설치(frozen sync)가 끝난 뒤** torch·torchvision 을 cu126 인덱스에서
# 덮어쓴다 — 순서가 중요하다. `uv sync --frozen` 은 매번 venv 를 lock 상태로 되돌리므로,
# 덮어쓰기가 그 앞에 있으면 뒤에 오는 frozen sync 가 CPU torch 로 도로 갈아치워
# 덮어쓴 게 없던 일이 된다(1라운드 리뷰에서 실측). 그래서 스테이지 순서를
# base(deps) → final 소스 COPY + frozen sync(프로젝트 설치, `torch-cpu`/`torch-cuda` 이전에 끝)
# → `torch-cpu`(no-op) / `torch-cuda`(cu126 덮어쓰기, frozen sync 없음) → 모델 굽기·entrypoint
# 순으로 재배치했다. `uv pip` 은 lock 을 안 보므로 이 덮어쓰기 이후로는 frozen sync 를 다시
# 부르지 않는 한 그대로 남는다.
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

# 프로젝트 자체(엔트리포인트 포함)를 여기서 설치한다 — **이게 이 이미지의 마지막 frozen sync 다.**
# 이 뒤로는 어느 스테이지도 `uv sync --frozen` 을 다시 부르면 안 된다. 그러면 torch-cuda 의
# cu126 덮어쓰기가 lock 의 CPU torch 로 되돌아간다(위 헤더 코멘트, 1라운드 리뷰 지적).
COPY backend/src ./src
COPY data/manifests/seed_sources.yaml /app/seed_sources.yaml
RUN uv sync --frozen --group ml --group pipeline

FROM base AS torch-cpu
# lock 그대로 (CPU torch) — frozen sync 를 다시 부르지 않는다.

FROM base AS torch-cuda
# `uv pip` 는 uv sync/run 과 달리 UV_PROJECT_ENVIRONMENT 를 보지 않고 VIRTUAL_ENV 나
# 현재 디렉터리의 .venv 를 찾는다 — 여기엔 둘 다 없으므로 --python 으로 /opt/venv 를
# 명시하지 않으면 uv 가 새 venv 를 만들거나 에러를 낸다.
# `uv pip show` 가 보고하는 버전에는 로컬 세그먼트가 붙는다(예: `2.6.0+cpu`) — 그 값을
# 그대로 cu126 인덱스에 물으면 그런 로컬 빌드는 없어서 해석이 안 된다. `+` 앞만 남긴다.
# cu126 인덱스는 nvidia-* 런타임 휠(cudnn·cublas 등)도 torch 의 의존성으로 같이 준다 —
# 별도로 지목하지 않아도 이 설치 한 줄이 그것들도 받는다.
RUN set -eux; \
    V_TORCH="$(uv pip show --python /opt/venv/bin/python torch | awk '/^Version/ {print $2}' | cut -d+ -f1)"; \
    V_TV="$(uv pip show --python /opt/venv/bin/python torchvision | awk '/^Version/ {print $2}' | cut -d+ -f1)"; \
    uv pip install --python /opt/venv/bin/python \
        --index-url https://download.pytorch.org/whl/cu126 \
        "torch==${V_TORCH}" "torchvision==${V_TV}"

FROM torch-${TORCH} AS final
# 가중치를 굽는다 — 잡이 뜰 때마다 1.2GB 를 받지 않게. 모델 키는 rag/core/config.py 기본값과 같다.
# frozen sync 는 이 스테이지에서 다시 부르지 않는다 (torch-cuda 의 덮어쓰기를 지키기 위해).
RUN uv run --no-sync python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('Qwen/Qwen3-Embedding-0.6B', device='cpu')"

COPY docker/pipeline/entrypoint.sh /entrypoint.sh
# 방어적 조치: 저장소 기본이 CRLF(core.autocrlf=true)라 워킹트리에서 다시 체크아웃하면
# 이 파일이 CRLF 로 바뀔 수 있다 (.gitattributes 의 `*.sh text eol=lf` 가 1차 방어).
# 여기서 한 번 더 벗겨 셔뱅이 항상 살아 있게 한다.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
