# 실시간 산책·날씨 서비스 (D-068) — Cloud Run.
#
# **파이프라인 이미지와 코드 취급이 반대다.** 저쪽(#427)은 코드를 GCS 에서 받는다 —
# 하루 한 번 도는 잡이라 기동 비용이 싸고, 코드 배포에서 이미지 굽기를 없애는 것이 값이었다.
# 여기는 **이미지에 굽는다**: ① scale-to-zero 는 콜드 스타트가 잦은데 거기에 파일 복사가
# 붙으면 그만큼 느려진다 ② 서비스는 "지금 어느 코드가 도는가"가 **배포 단위와 같아야**
# 롤백이 된다.
#
# **`--no-install-project` + `PYTHONPATH` 도 #427 이 찾은 것과 같은 수다** —
# pyproject.toml 의 `[tool.uv.build-backend] module-name` 이 아홉 패키지를 나열해서
# 일부만 이미지에 넣으면 설치 단계가 깨진다. 설치하지 않고 경로만 알려 주면 그 제약이 없다.
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
    PYTHONPATH=/app/src

# 의존성. **`--only-group` 이라 base 의존성이 안 들어온다** (pyproject.toml 의 그 블록 주석).
# README.md 도 필요하다 — pyproject.toml 의 readme= 를 uv 가 빌드 메타데이터로 읽는다.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --only-group realtime --no-install-project

# 코드. `realtime/` 만 넣지 않고 `daengs_life` 를 통째로 넣는 이유 —
# `realtime/config.py` 가 `daengs_life.crawler.core.config` 를 부른다(경로 탐색 · KST ·
# .env 병합 순서, RT-001 ①-2). 그 20줄을 복사하면 두 벌이 되고 한쪽만 늙는다.
COPY backend/src/daengs_life ./src/daengs_life

# `DAENGS_DATA_DIR` 을 주지 않는다. 이미지에 저장소 루트가 없어 `DATA_DIR` 이 스스로
# None 이 되고, realtime 은 파일을 안 읽으니 그게 맞는 값이다 (D-068 §6-5).
#
# 포트는 Cloud Run 이 `PORT` 로 준다. sh 를 거치는 이유가 그 치환이다.
CMD ["sh", "-c", "exec /opt/venv/bin/uvicorn daengs_life.app.realtime_main:app --host 0.0.0.0 --port ${PORT:-8080}"]
