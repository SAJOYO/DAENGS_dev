# API 컨테이너. DB 는 기존 db 서비스(postgis) 그대로 쓴다.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /srv

# 의존성 레이어를 소스와 분리해 캐시한다
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
# 마이그레이션 러너도 이미지에 넣는다. 스키마 적용이 배포 단계의 일이라, 컨테이너가
# `alembic upgrade head` 를 못 돌리면 "경로를 하나로" 가 로컬에서만 참이 된다.
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
RUN uv sync --frozen --no-dev

ENV PATH="/srv/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
