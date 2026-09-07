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
# 통째로 낡는다. 판단이 남는 자리는 `docs/life/decisions-rag.md` 의 RAG-024 다 (RAG-024 ④).
EVAL_DIR: Path | None = PROCESSED_DIR / "eval" if PROCESSED_DIR else None
# 9단계 랩 답변 덤프 (RAG-028 ⑥). 랩간 비교의 재료다.
# ⚠ **이 폴더만 추적한다** — 여기 나란한 eval/ · embeddings/ 와 반대다
# (`.gitignore` 의 `!data/processed/answers/*.jsonl`, RAG-017 '예외' 절). LLM 출력이라
# 다시 돌려도 같은 글이 안 나오고, RAG-028 ⑥ 이 그것을 랩끼리 비교 축으로 지정했다.
ANSWER_DIR: Path | None = PROCESSED_DIR / "answers" if PROCESSED_DIR else None
# LLM judge 판정 (RAG-074 · `D15`). **랩 파일 옆이 아니라 따로 둔다** — 랩은 그때 뜬 기록이고
# 판정은 나중에 다른 모델·다른 프롬프트로 **여러 번** 다시 매길 수 있다. 랩 안에 섞으면
# 판정을 고칠 때마다 랩 파일이 바뀌어 `score-laps` 의 대조선이 흔들린다.
# ⚠ 여기도 추적한다 (`.gitignore` 의 두 번째 예외).
JUDGMENT_DIR: Path | None = PROCESSED_DIR / "judgments" if PROCESSED_DIR else None

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

    # 서빙과 적재가 **같은 모델이어야 한다.** 2026-08-27 에 `qwen3-embedding-0.6b` 로 올린다 —
    # RAG-024 가 교체 조건을 발동시켜 놓고 재적재 비용 때문에 미뤄 뒀는데, 코퍼스를 0에서 다시
    # 만드는 지금(#34) 그 비용이 0 이다. 가중치도 1.2GB 로 `bge-m3`(6.5GB)의 1/5 이라 상주
    # 비용이 줄고, 차원은 셋 다 1024 라 `db/init` 스키마는 그대로다.
    # 교체는 이 기본값 한 줄이거나 `--model` 한 번이어야 한다("교체가 싸다"의 실제 장치).
    #
    # ⚠ **이 값이 코퍼스와 어긋나면 조용히 틀린다.** 차원이 같아서 예외가 안 나고, 그럴듯한
    # 순위가 그냥 나온다. 기동 때 `deps.warm_up_encoder` 가 DB 와 대조해 경고를 남기는 이유다.
    embedding_model_key: str = "qwen3-embedding-0.6b"

    # 9단계 생성 (RAG-028). 키는 `backend/.env` 에 이미 있다 — env 이름이 그대로 필드명이다.
    # **모델명을 상수로 박지 않는 이유**: 세대가 바뀌면 이름이 바뀌는데, 그때 코드를 고치는 것과
    # `.env` 한 줄을 고치는 것은 되돌리는 비용이 다르다 (RAG-025 ⑤ 와 같은 판단).
    gemini_api_key: str = ""
    # 실측 2026-08-25 — `gemini-2.5-flash` 는 404 를 내며 *"no longer available to new users,
    # use models/gemini-3.6-flash"* 라고 API 가 직접 알려줬다. 상수로 안 박아 둔 판단이 첫날 값을 했다.
    #
    # 2026-08-27 — `gemini-3.1-flash-lite` 로 내린다. 9단계는 아직 관찰 단계라 체급보다 회전이
    # 중요하고, **되돌리기가 `.env` 한 줄**이라 싸다. 실재하는 이름인지는 `models.list()` 로
    # 확인했다(preview 판과 별도로 있다) — 위 404 가 남긴 습관이다.
    # ⚠ 프롬프트가 일부러 순진해서(RAG-029 를 한 랩 미뤘다) 체급이 조항 인용에 그대로 드러난다.
    # 그것을 숫자로 보는 자리는 검문소④의 `ungrounded` 비율이고, 거기서 3.6-flash 와 비교한다.
    gemini_model: str = "gemini-3.1-flash-lite"

    # Gemini 호출 타임아웃. **이름에 단위를 박아 둔다** — google-genai 의 `HttpOptions.timeout`
    # 은 초가 아니라 **밀리초**다(SDK 설명: *"Timeout for the request in milliseconds"*).
    # 30 을 넣으면 30밀리초가 돼서 전부 504 가 되는데, 그 실수는 이름 없이는 안 보인다.
    #
    # 30초인 이유: flash 급이 근거 5건을 받아 답을 쓰는 데 보통 수 초라 여유가 있고, nginx 기본
    # `proxy_read_timeout`(60초) 안쪽이라 **우리가 먼저 504 를 낸다** — 상류 판정이 우리 손에 남는다.
    # 걸어 두지 않으면 무제한이라, 상류가 물리면 스레드풀 워커를 그대로 잡고 있는다.
    gemini_timeout_ms: int = 30_000

    # LLM-as-a-judge (RAG-007 · `D15`). **일부러 다른 계열이다** — 생성이 Gemini 인데 judge 도
    # Gemini 면 같은 훈련 계보가 자기 계열의 문장을 후하게 보는 self-preference 가 남는다.
    # RAG-007 이 요구한 것은 *"급 분리"*(flash → pro)까지였는데, 2026-09-07 에 사람이
    # **계열까지 가르기로** 정했다 (§6).
    #
    # ⚠ **키가 없으면 judge 만 안 돈다.** 생성(`gemini_api_key`)과 갈라 둔 이유가 그것이다 —
    # 채점 도구 하나 때문에 `/life/ask` 가 뜨지 않으면 안 된다.
    openai_api_key: str = ""
    # ⚠ **`-latest` 류를 안 쓴다** — 움직이는 이름이면 두 판정 파일의 차이가 답변 때문인지
    # judge 때문인지 안 갈린다. `judge.PROMPT_VERSION` 과 같은 이유로 **못 박는 값**이다.
    # `gemini_model` 을 상수로 안 박은 것과 방향이 반대인데, 그쪽은 생성이라 되돌리기가 싸고
    # 이쪽은 **채점자**라 흔들리면 옛 판정과 비교가 통째로 끊긴다.
    openai_judge_model: str = "gpt-5.2"
    # OpenAI 호출 타임아웃. **여기는 초다** — `gemini_timeout_ms` 와 단위가 다르므로 이름에
    # 박아 둔다. judge 는 서빙 경로가 아니라 배치라 넉넉하게 준다.
    openai_timeout_s: float = 120.0

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
