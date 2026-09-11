# 코퍼스 파이프라인 이미지 (D-062). Cloud Run Job `corpus-refresh`(CPU) · `corpus-embed-full`(CUDA).
#
#   docker build -f docker/pipeline/Dockerfile -t pipeline:cpu  .
#   docker build -f docker/pipeline/Dockerfile -t pipeline:cuda --build-arg TORCH=cuda .
#
# 🔴 **이 이미지에는 우리 코드가 없다** (#427). 의존성과 모델 가중치만 굽고, `daengs_life` 는
# 런타임에 버킷(`gs://daengs-corpus/code/`, 잡에 `/data` 로 마운트됨)에서 `/app/src` 로
# 복사해 `PYTHONPATH` 로 잡는다 — `docker/pipeline/entrypoint.sh` 가 그 일을 한다.
#
# **왜 그랬나.** 코드가 이미지에 있으면 태그(= 내용 해시)가 코드마다 바뀌어서, 14MB 짜리
# `backend/src` 를 고칠 때마다 **CPU 2.3GB + CUDA 9GB 를 다시 굽는다.** 잡이 쓰지도 않는
# 패키지(`daengs_place` 등)를 고쳐도 똑같이 굽는다. 이제 태그는 `backend/pyproject.toml` ·
# `backend/uv.lock` · `docker/pipeline/` 만 보므로 **의존성을 바꿀 때만** 굽는다
# (`infra/gcp/pipeline.sh` 의 `SHA`). 코드 배포는 rsync 몇 초다.
#
# ⚠ **대가: 태그가 코드 버전을 안 말한다.** 배포가 `code/VERSION` 에 커밋 해시를 쓰고
# entrypoint 가 그것을 로그 첫 줄에 찍는 것이 그 자리를 메운다. (옛 태그도 `git ls-files`
# 해시라 커밋과 1:1 은 아니었다.)
#
# 빌드 컨텍스트는 저장소 루트지만 실제로 필요한 것은 `backend/` 의 세 파일과 entrypoint 뿐이고,
# `docker/pipeline/Dockerfile.dockerignore` 가 컨텍스트를 그것으로만 좁힌다.
#
# lock 의 torch 는 리눅스에서 CPU 인덱스로 고정돼 있다 (backend/pyproject.toml [tool.uv.sources]).
# 서버 backend 컨테이너가 NVIDIA 런타임 10GB 를 받지 않게 한 결정이라 lock 을 안 건드린다.
# CUDA 판은 **의존성 설치가 끝난 뒤** torch 만 cu126 인덱스에서 덮어쓴다
# (torchvision 은 이 이미지의 `ml` 그룹에 없다 — 아래 torch-cuda 스테이지 주석 참고).
#
# ⚠ **`uv sync --frozen` 은 이 파일에 한 번만 나온다** (base 스테이지). 그것이 규칙이다 —
# `uv sync --frozen` 은 매번 venv 를 lock 상태로 되돌리므로, torch-cuda 의 cu126 덮어쓰기
# **뒤에** 한 번이라도 부르면 CPU torch 로 도로 갈아치워 덮어쓴 게 없던 일이 된다
# (1라운드 리뷰에서 실측). 옛 판은 프로젝트 설치 때문에 sync 가 둘이어서 그 순서를 손으로
# 지켜야 했는데, 코드를 이미지에서 뺀 뒤로는 **부를 자리가 아예 없다.**
#
# 베이스는 `uv:1`(docker/uv/Dockerfile) 을 참조하지 않고 그 레시피를 그대로 인라인한다 —
# Cloud Build 에는 로컬에서만 구운 `uv:1` 이미지가 없어 `FROM uv:1` 이 "pull access
# denied" 로 실패한다(2라운드 실측). 그래서 이 Dockerfile 은 공개 이미지만으로 자립한다.
ARG TORCH=cpu
ARG PYTHON_VERSION=3.12.13
ARG UV_VERSION=0.12.3

# uv 바이너리만 꺼내 오는 스테이지 — 공식 이미지는 바이너리 출처로만 쓰고, 실행 환경은
# 아래 python:${PYTHON_VERSION}-slim 을 그대로 쓴다(docker/uv/Dockerfile 과 같은 이유:
# 빌드 도구를 새로 설치하지 않아도 된다).
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uvbin

FROM python:${PYTHON_VERSION}-slim AS base
COPY --from=uvbin /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    HF_HOME=/models \
    DAENGS_DATA_DIR=/data \
    DAENGS_WARM_UP_ENCODER=false
# 런타임에 버킷에서 복사해 온 코드가 여기 선다 (entrypoint.sh). 이미지에는 비어 있다 —
# 그래서 복사가 실패하면 `python -m` 이 ModuleNotFoundError 로 **크게** 죽는다.
ENV PYTHONPATH=/app/src

# 의존성만 굽는다. README.md 도 필요하다 — pyproject.toml 의 readme="README.md" 를 uv 가
# 빌드 메타데이터로 읽는다 (`--no-install-project` 라 내용은 안 쓰지만 없으면 sync 가 실패한다).
# pdf 그룹(PyMuPDF, AGPL)은 서빙 backend 이미지에는 안 들어가지만(RAG-032 ②) 이 이미지는 그 결정이 말하는
# "오프라인 파이프라인" 그 자체라 들어간다. raw/ 의 PDF 소스가 이것 없이는 전부 FAIL 이다 (2026-09-08 실측).
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-install-project --group ml --group pipeline --group pdf

FROM base AS torch-cpu
# lock 그대로 (CPU torch) — 할 일이 없다.

FROM base AS torch-cuda
# torch 2.13 의 CUDA 경로는 Triton 커널을 실행 시점에 컴파일한다 — C 컴파일러가 없으면
# "Failed to find C compiler" 로 인코딩 도중 죽는다 (2026-09-08 L4 실측). slim 베이스엔 gcc 가 없다.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
# `uv pip` 는 uv sync/run 과 달리 UV_PROJECT_ENVIRONMENT 를 보지 않고 VIRTUAL_ENV 나
# 현재 디렉터리의 .venv 를 찾는다 — 여기엔 둘 다 없으므로 --python 으로 /opt/venv 를
# 명시하지 않으면 uv 가 새 venv 를 만들거나 에러를 낸다.
# `uv pip show` 가 보고하는 버전에는 로컬 세그먼트가 붙는다(예: `2.6.0+cpu`) — 그 값을
# 그대로 cu126 인덱스에 물으면 그런 로컬 빌드는 없어서 해석이 안 된다. `+` 앞만 남긴다.
# cu126 인덱스는 nvidia-* 런타임 휠(cudnn·cublas 등)도 torch 의 의존성으로 같이 준다 —
# 별도로 지목하지 않아도 이 설치 한 줄이 그것들도 받는다.
# torchvision 은 핀하지 않는다 — 이 이미지가 까는 `ml` 그룹에는 torchvision 이 없다
# (`gait` 전용 그룹에만 있다). 없는 패키지를 `uv pip show` 로 찾으면 버전이 빈 문자열이
# 되고, 그걸 그대로 넣으면 `torchvision==` 로 파싱이 깨진다(3라운드 Cloud Build 실측:
# `V_TORCH=2.13.0` · `warning: Package(s) not found for: torchvision` · `V_TV=` ·
# `error: Failed to parse: torchvision==`). cu126 인덱스에 torch 2.13.0 이 있다는 것은
# lock 이 win32 에서 같은 인덱스로 그 버전을 해석해 둔 것으로 이미 확인됐다.
# `"torch==${V_TORCH}"` (로컬 세그먼트 없이) 는 **조용히 아무것도 안 한다** — PEP 440 은
# 스펙에 로컬 세그먼트가 없으면 그걸 무시하고 비교하므로, 이미 깔린 `2.13.0+cpu` 가
# `==2.13.0` 을 만족한 걸로 쳐서 uv 가 재설치를 건너뛴다(4라운드까지 이렇게 지나갔고,
# Cloud Build 는 성공한 것처럼 보였다 — 2026-09-08 GPU 잡이 실제로는 CPU 로 돎을 진단
# 실행에서 실측: `cuda_available False` · `torch 2.13.0+cpu cuda None`). cu126 휠은
# `+cu126` 로컬 태그를 달고 나오므로, 그 태그를 스펙에 그대로 박아야 CPU 판(`+cpu`)이
# 후보에서 아예 빠진다. `--reinstall-package torch` 는 그래도 uv 가 "이미 만족" 이라고
# 오판할 경우를 막는 이중 방어다.
RUN set -eux; \
    V_TORCH="$(uv pip show --python /opt/venv/bin/python torch | awk '/^Version/ {print $2}' | cut -d+ -f1)"; \
    uv pip install --python /opt/venv/bin/python --reinstall-package torch \
        --index-url https://download.pytorch.org/whl/cu126 "torch==${V_TORCH}+cu126"
# 빌드에서 CUDA 판인지 확인한다. 2026-09-08 실측 — 덮어쓰기가 조용히 건너뛰어져 GPU 잡이
# CPU 로 돌았다. `torch.cuda.is_available()` 은 빌드 시점(Cloud Build 에 GPU 없음)엔 항상
# False 라 못 쓴다 — `torch.version.cuda` 로 "CUDA 빌드인지"만 본다(실제 GPU 가용성과는 별개).
# ⚠ `uv run` 이 아니라 venv 의 python 을 **직접** 부른다 — 프로젝트를 설치하지 않으므로
# `uv run` 에 프로젝트 해석을 맡길 이유가 없고, 맡기면 `--no-sync` 를 빼먹는 날 lock 으로
# 되돌아가 이 덮어쓰기가 사라진다.
RUN /opt/venv/bin/python -c "import torch, sys; print('torch', torch.__version__, 'cuda', torch.version.cuda); sys.exit(0 if torch.version.cuda else 1)"

FROM torch-${TORCH} AS final
# 가중치를 굽는다 — 잡이 뜰 때마다 1.2GB 를 받지 않게. 모델 키는 rag/core/config.py 기본값과 같다.
# ⚠ **이 문자열은 코드의 기본값과 손으로 맞춰 둔 것이다.** 코드가 이미지 밖으로 나가서
# 여기서 import 해 확인할 수가 없다 (그전에도 `rag/core/config.py` 를 읽지는 않았다).
# `EMBEDDING_MODEL_KEY` 를 바꾸면 이 줄도 같이 고쳐야 하고, 안 고치면 **잡이 뜰 때마다
# 1.2GB 를 새로 받는다** — 느려질 뿐 결과는 맞아서 조용하다.
RUN /opt/venv/bin/python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('Qwen/Qwen3-Embedding-0.6B', device='cpu')"

COPY docker/pipeline/entrypoint.sh /entrypoint.sh
# 방어적 조치: 저장소 기본이 CRLF(core.autocrlf=true)라 워킹트리에서 다시 체크아웃하면
# 이 파일이 CRLF 로 바뀔 수 있다 (.gitattributes 의 `*.sh text eol=lf` 가 1차 방어).
# 여기서 한 번 더 벗겨 셔뱅이 항상 살아 있게 한다.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
