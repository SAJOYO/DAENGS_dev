"""rag 의 경로·상수.

**경로 탐색은 `crawler.core.config` 것을 그대로 쓴다** — "data/ 는 어디인가"의 답이 둘이 되면
컨테이너에서 크롤러와 파서가 서로 다른 곳을 보게 된다 (RAG-014). 그 외의 설정(DATABASE_URL,
GEMINI_API_KEY, 임베딩 모델명)은 rag 가 자기 Settings 로 갖는다 — 필요해지는 4·7·9단계에 추가한다.
"""
from __future__ import annotations

from pathlib import Path

from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

from daengs_life.crawler.core.config import _ENV_FILES  # noqa: F401  — env 탐색 규칙을 두 벌 두지 않는다 (RAG-014)
from daengs_life.crawler.core.config import DATA_DIR, KST, RAW_DIR, require_data_dir  # noqa: F401

PROCESSED_DIR: Path | None = DATA_DIR / "processed" if DATA_DIR else None
PARSED_DIR: Path | None = PROCESSED_DIR / "parsed" if PROCESSED_DIR else None
CHUNK_DIR: Path | None = PROCESSED_DIR / "chunks" if PROCESSED_DIR else None
EMBED_DIR: Path | None = PROCESSED_DIR / "embeddings" if PROCESSED_DIR else None
# 6단계 3파전 덤프. **미추적이다** — 기계 산출물이고 chunk_id 의 수집 날짜에 묶여 재수집하면
# 통째로 낡는다. 판단이 남는 자리는 `docs/decisions-rag.md` 의 RAG-024 다 (RAG-024 ④).
EVAL_DIR: Path | None = PROCESSED_DIR / "eval" if PROCESSED_DIR else None
# 9단계 1랩 답변 덤프 (RAG-028 ⑥). **미추적**이고 2랩(소스 확장 후 재관통) 비교의 재료다.
# eval/ 과 같은 이유로 여기 둔다 — 기계 산출물이고 재수집하면 chunk_id 가 바뀌어 통째로 낡는다.
ANSWER_DIR: Path | None = PROCESSED_DIR / "answers" if PROCESSED_DIR else None

# ---------------------------------------------------------------- 7단계 이후: DB (RAG-025 ②)
class Settings(BaseSettings):
    """rag 가 쓰는 값. **crawler 와 공유하지 않는다** — 공유 Settings 를 만들면 크롤러가
    `DATABASE_URL` 까지 알게 되어 `app → rag → crawler` 한 방향(RAG-014)이 흐려진다.

    루트 `.env` 는 compose 용이라 접속 정보가 `POSTGRES_*` 조각으로 흩어져 있다. 조각으로 받아
    여기서 합치되 **`DATABASE_URL` 이 오면 그것이 이긴다** — 배포에서는 오케스트레이터가 통째로
    주는 형태이고, 그때 조각을 다시 조립하게 하면 두 표현이 어긋날 수 있다.
    """

    database_url: str = ""
    postgres_ip: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = ""
    postgres_db: str = "lifedb"

    # 7단계 첫 관통이 쓰는 모델. **판정 승자(qwen3)가 아니라 기준선이다** — RAG-024 `판정 이후`.
    # 교체는 이 기본값 한 줄이거나 `--model` 한 번이어야 한다("교체가 싸다"의 실제 장치)
    embedding_model_key: str = "bge-m3"

    # 9단계 생성 (RAG-028). 키는 `backend/.env` 에 이미 있다 — env 이름이 그대로 필드명이다.
    # **모델명을 상수로 박지 않는 이유**: 세대가 바뀌면 이름이 바뀌는데, 그때 코드를 고치는 것과
    # `.env` 한 줄을 고치는 것은 되돌리는 비용이 다르다 (RAG-025 ⑤ 와 같은 판단).
    gemini_api_key: str = ""
    # 실측 2026-08-25 — `gemini-2.5-flash` 는 404 를 내며 *"no longer available to new users,
    # use models/gemini-3.6-flash"* 라고 API 가 직접 알려줬다. 상수로 안 박아 둔 판단이 첫날 값을 했다.
    gemini_model: str = "gemini-3.6-flash"

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def dsn(self) -> str:
        """psycopg 접속 문자열. 비밀번호는 특수문자가 섞일 수 있어 인코딩한다."""
        if self.database_url:
            return self.database_url
        pw = quote_plus(self.postgres_password)
        return (f"postgresql://{self.postgres_user}:{pw}"
                f"@{self.postgres_ip}:{self.postgres_port}/{self.postgres_db}")


settings = Settings()
