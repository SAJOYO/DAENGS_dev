# uv 만 얹은 공용 베이스 이미지.
#   docker build -t uv:1 docker/uv
#
# 공식 ghcr.io/astral-sh/uv 이미지도 있지만 uv 버전이 낮아서 직접 굽습니다.
# uv 를 올릴 때는 아래 UV_VERSION 과 태그(uv:2 ...)를 함께 올리세요.
ARG PYTHON_VERSION=3.12.13
ARG UV_VERSION=0.12.3

# uv 바이너리만 꺼내 오기 위한 스테이지. 빌드 도구를 설치하지 않아도 됩니다.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uvbin

FROM python:${PYTHON_VERSION}-slim
COPY --from=uvbin /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
