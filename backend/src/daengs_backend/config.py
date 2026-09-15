import logging
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL

# backend/.env 를 가리킵니다. config.py 기준으로 잡아 두면
# 어느 디렉터리에서 실행하든 같은 파일을 읽습니다.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """환경 변수 / backend/.env 로 덮어쓸 수 있는 설정.

    예) DAENGS_CORS_ORIGINS='["http://localhost:3000","https://daengs.example"]'
    """

    model_config = SettingsConfigDict(
        env_prefix="DAENGS_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Celery 브로커 ---------------------------------------------------
    # 관리자 수동 트리거가 크롤 태스크를 **이름으로** 보냅니다 (RAG-047).
    #
    # ⚠ `daengs_life.tasks` 를 import 하지 않습니다. CLAUDE.md 가 "`daengs_backend` 가
    # `daengs_life` 를 부르는 접점은 main.py 의 세 줄뿐" 이라고 못박아 두었고, 그 선을
    # 태스크 하나 부르자고 넘으면 D-021 2단계(`/life/ask` 를 별도 프로세스로)가 그만큼 비싸집니다.
    # 대신 브로커에 태스크 **이름 문자열**을 던집니다 — 태스크가 `@app.task(name=...)` 로
    # 그 이름을 명시하고 있어서 그것이 계약입니다.
    #
    # 이름은 접두사 없는 `REDIS_URL` 입니다 (`daengs_life` 쪽과 같은 값을 읽습니다).
    # 둘을 한 이름으로 합치지 않는 이유는 `POSTGRES_*`/`DAENGS_DB_*` 와 같습니다 —
    # 두 패키지가 같은 env 를 각자 읽는 것이 서로를 import 하는 것보다 쌉니다.
    redis_url: str = Field(default="", validation_alias=AliasChoices("REDIS_URL"))

    # 실시간 산책·날씨를 어디서 부르나 (D-070).
    #
    # **비어 있으면 지금까지와 똑같다** — 같은 프로세스의 함수를 부른다. 값이 있으면 그
    # 주소의 Cloud Run 서비스를 HTTP 로 부른다. 개발 PC·개발서버는 비워 두고 GCP VM 의
    # backend/.env 에만 넣는다.
    #
    # **되돌리기가 이 한 줄이다.** 지우고 `docker compose up -d backend` 로 컨테이너를
    # 다시 만들면 분리 전 경로로 돌아온다 (`env_file` 은 컨테이너를 만들 때 굳는다).
    realtime_url: str = ""

    # 관리자 수동 크롤이 어디로 가나 (#326, D-062 §3). `celery` 는 집 서버(브로커에 태스크),
    # `cloudrun` 은 GCP(Cloud Run Job `corpus-refresh` 를 Jobs API 로 실행 — 크롤부터 적재까지).
    # GCP VM 의 backend/.env 에만 `DAENGS_CRAWL_BACKEND=cloudrun` 을 둔다. 인증은 VM 서비스
    # 계정(메타데이터 서버)이라 키 파일이 없고, 그 계정에 잡 실행·실행 조회 권한이 있어야 한다
    # (infra/gcp/README.md "관리자 트리거").
    crawl_backend: Literal["celery", "cloudrun"] = "celery"
    gcp_project: str = ""
    gcp_region: str = "asia-northeast3"
    corpus_job: str = "corpus-refresh"

    # 개발 서버가 바인딩할 주소.
    # 호스트에서 띄울 때는 루프백이면 충분하지만,
    # 컨테이너 안에서는 0.0.0.0 이어야 밖에서 닿습니다. (compose 가 DAENGS_HOST 로 넘겨줍니다)
    host: str = "127.0.0.1"
    port: int = 8000

    # 브라우저가 프론트 오리진에서 API 를 부를 때 허용할 목록.
    # 서버에 올릴 때는 프론트 도메인을 여기에 추가해야 합니다.
    # 등록하는 것은 '부르는 쪽'(프론트)이지 API 도메인이 아닙니다.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # 쿠키에 Secure 를 붙일지. **HTTPS 를 붙이면 반드시 true 로 바꾸세요.**
    #
    # 지금 배포가 http 라 기본값이 false 입니다. Secure 쿠키는 https 로만 전송돼서,
    # 지금 켜면 로그인이 되고도 쿠키가 안 실려 곧바로 로그아웃됩니다.
    # 반대로 https 로 옮긴 뒤에도 false 로 두면 토큰이 평문으로 오갑니다.
    cookie_secure: bool = False

    # 기동할 때 `/life/ask` 의 임베딩 모델을 미리 올릴지 (D-021).
    #
    # **끄는 자리가 필요한 이유는 둘입니다.** 테스트가 `TestClient(app)` 를 `with` 로 열면
    # lifespan 이 그대로 도는데, `ml` 그룹을 깐 개발 PC(#34 의 `rag embed` 가 요구합니다)에서는
    # 그때마다 1.2GB 가 올라오고 대조 때문에 **실서버 DB 에도 붙습니다.** `tests/conftest.py` 가
    # 이 값을 false 로 둡니다. 개발 PC 에서 `/life/ask` 말고 다른 걸 보는 동안에도 끌 수 있습니다.
    #
    # 끄면 모델이 안 사라지는 게 아니라 **첫 `/life/ask` 요청이 로드를 뭅니다** (5~7초).
    # 서버에서는 켜 두세요 — 그게 예열을 두는 이유입니다.
    warm_up_encoder: bool = True

    # Requires #260 + 21_activity_game.sql on web and worker DBs. Explicit activation only.
    activity_game_enabled: bool = False

    # Enable on web/worker only after 24_walk_entry_contexts.sql has been applied.
    walk_entry_context_enabled: bool = False

    # Apply 27_walk_public_context.sql before enabling address jobs on web/worker.
    walk_public_context_enabled: bool = False
    walk_sgis_key: SecretStr = SecretStr("")
    walk_sgis_secret: SecretStr = SecretStr("")
    walk_public_data_key: SecretStr = SecretStr("")
    walk_park_catalog_path: str = ""
    # Apply 28_walk_commerce_context.sql first; regional caches refresh separately.
    walk_area_context_enabled: bool = False
    walk_commerce_catalog_path: str = ""
    walk_river_catalog_path: str = ""
    # Shared read-only regional directory; only the dedicated catalog worker writes it.
    walk_public_catalog_root: str = ""
    walk_catalog_refresh_enabled: bool = False
    walk_catalog_daily_requests: int = Field(default=300, ge=1, le=1000)

    # Apply 25_walk_entry_pins.sql first. Once v2 data exists, keep reads enabled on rollback.
    walk_entry_v2_enabled: bool = False
    walk_entry_v2_write_enabled: bool = False
    # Enable only after 26_walk_photo_manifests.sql. Capability stays off on older DBs.
    walk_photo_metadata_enabled: bool = False
    # Opt-in diary bundle; enable only with a client that explicitly requests the new format.
    walk_diary_enabled: bool = False
    # Experimental endpoint must be enabled independently of published diaries.
    walk_diary_slots_preview_enabled: bool = False
    # Public normalization is independent of the legacy entry-context job schema.
    walk_diary_space_enabled: bool = True
    walk_diary_route_patterns_enabled: bool = False
    walk_diary_space_radius_m: int = Field(default=1000, ge=1, le=3000)
    walk_land_cover_layer: str = Field(
        default="EGIS:lv3_2025y", pattern=r"^EGIS:lv3_[a-zA-Z0-9_-]+$"
    )

    # ── DB ────────────────────────────────────────────────────────────
    # URL 한 줄이 아니라 조각으로 받습니다 (D-013). 개발 PC 와 서버가 다른 것은
    # 사실상 호스트 하나뿐인데, URL 로 받으면 그 한 글자 때문에 접속 정보 전체를
    # 두 군데에 각각 적게 됩니다.
    #
    # 조립은 아래 database_url 이 합니다.
    #   개발 PC : 팀 DB 가 있는 서버 IP (최상단 .env 의 POSTGRES_IP)
    #   서버 PC : compose 가 DAENGS_DB_HOST=pgvector 로 넘깁니다.
    #             컨테이너 안에서 localhost 는 컨테이너 자신이라 닿지 않습니다.
    db_host: str
    db_port: int = 5432

    # 최상단 .env 의 POSTGRES_USER / POSTGRES_PASSWORD 와 같은 값입니다.
    # 서버 컨테이너에는 compose 가 거기서 읽어 넘겨 주므로, 접속 정보의 원본은
    # 여전히 최상단 .env 하나입니다 (D-009).
    db_user: str = "postgres"
    db_password: SecretStr  # 기본값을 두지 않습니다. LAN 에 열린 DB 입니다

    db_name: str = "vectordb"

    # 실행되는 SQL 을 로그로 찍습니다. 쿼리를 들여다볼 때만 켜세요.
    db_echo: bool = False

    # ---- Process-local Training RAG -----------------------------------------
    # 인증 화면이 아직 연결되지 않은 로컬 데모에서만 명시적으로 true로 둔다.
    # 기본값은 기존 앱 access token을 요구한다.
    training_rag_allow_anonymous_demo: bool = False

    # ── 일반 답변 폴백 (#279) ─────────────────────────────────────────
    # 라우터가 전문 능력을 하나도 못 골랐을 때 거절(FAILED) 대신 Gemini 생성 답변
    # (`adapters/general.py`)을 붙일지. **기본값 false 라 켜기 전까지 운영은 지금과
    # 같습니다** — #277 의 판정 결과를 보고 서버 `backend/.env` 한 줄로 켭니다.
    #
    # 폴백은 라우터의 목적지가 아니라 `planner.assemble_route_plan` 의 결정론 규칙입니다.
    # 명시 신호 `requested_capability` 와 골드 회귀 러너는 이 값을 읽지 않습니다.
    general_fallback: bool = Field(
        default=False, validation_alias=AliasChoices("DAENGS_GENERAL_FALLBACK")
    )

    # ── 피부 판정 해설 킬 스위치 (D-079) ────────────────────────────────
    # `turn_resolver` 와 같은 쪽 기본값(**켜짐**)이다. 켜 둬도 운영이 달라지지 않는 이유가
    # 따로 있다 — 이 능력은 앱이 `requested_capability="skin"` 과 `screening_record_id` 를
    # **함께** 보내고 서버가 그 기록의 소유를 확인했을 때만 돈다(`planner.resolve_skin_route`).
    # 그 조합을 보내는 클라이언트가 생기기 전까지는 오늘과 같은 HANDOFF 이고, 끄면 그 뒤에도
    # HANDOFF 로 돌아간다. 장애 대응·비용 급증 때 한 줄로 끄는 자리다.
    skin_agent: bool = Field(default=True, validation_alias=AliasChoices("DAENGS_SKIN_AGENT"))

    # ── Turn Resolver 킬 스위치 (#416, R16) ────────────────────────────
    # `general_fallback` 과 정반대 기본값: 이건 **기본이 켜짐**입니다. 리졸버는 이미
    # 승인된 기능(Task 1~5)이라 배포 즉시 도는 것이 맞고, 끄는 쪽이 예외 상황(장애
    # 대응·비용 급증)입니다. 지금은 `prior_turns` 를 threading 하는 호출자가 없어
    # (Task 7 전) 빈 후보면 fast path 가 모델을 안 태우므로 이 값이 꺼져 있어도 관측되는
    # 차이가 없습니다 — 그래도 Task 7 이후 되돌릴 수 있는 자리를 배포 전에 먼저 파 둡니다.
    #
    # 켜져 있으면(기본) 애매한 발화마다 시맨틱 라우터보다 앞서 Gemini 왕복이 하나 더
    # 붙습니다. 끄면 `service._plan_and_execute` 가 리졸버를 아예 안 부르고 `resolved
    # = None` 으로 오늘처럼 진행합니다 — 이력 이어짐이 없어질 뿐 답은 그대로 나갑니다.
    turn_resolver: bool = Field(default=True, validation_alias=AliasChoices("DAENGS_TURN_RESOLVER"))

    # ── 채팅에서 케어 기록 쓰기 (#331 후속, D-075) ──────────────────────
    # `general_fallback` 과 같은 기본값(꺼짐)이고 같은 이유입니다 — **켜기 전까지 운영은
    # 지금과 같습니다.** 다만 여기서 "지금과 같다" 가 뜻하는 것이 하나 더 있습니다:
    # 꺼져 있어도 `"방금 밥 먹였어"` 는 **기록 화면 HANDOFF** 로 답합니다. 그것이
    # `docs/care-events.md` 가 적어 둔 순서의 가운데 칸이고, 플래그가 가르는 것은
    # 그 뒤(확인 되묻기 → 실제 쓰기)뿐입니다.
    #
    # ⚠ 이 플래그 하나로는 안 켜집니다. `routers/assistant.py` 가 요청마다
    # `CareLogCapabilityAdapter` 를 엔진에 넣고 `context["care_log_writable"]` 를 세울 때만
    # 제안이 나가므로(앱 회원 + 활성 강아지), 관리자 토큰·무상태 점검 요청은 이 값이
    # 켜져 있어도 HANDOFF 로 떨어집니다.
    care_log_write: bool = Field(
        default=False, validation_alias=AliasChoices("DAENGS_CARE_LOG_WRITE")
    )

    # ── 의미 라우터 (D-041) ───────────────────────────────────────────
    # backend/.env 에 이미 있는 GEMINI_API_KEY / GEMINI_TIMEOUT_MS 를 접두사 없이
    # 그대로 읽습니다. `daengs_life.rag` 의 Settings 와 같은 env 를 각자 읽는
    # 것이며, 위 redis_url 과 같은 판단입니다 — 두 패키지가 같은 env 를 각자 읽는
    # 것이 서로를 import 하는 것보다 쌉니다 (backend↔life 접점은 기계 강제됩니다).
    #
    # 라우터 모델은 계약상 고정이라(.env 로 안 뺍니다) orchestration/semantic.py 의
    # ROUTER_MODEL_ID 가 원본입니다. 키가 비면 앱은 뜨고, 의미 라우팅만 실패합니다.
    # 타임아웃은 **밀리초**입니다 (google-genai HttpOptions.timeout — _MS 가 이유).
    gemini_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("GEMINI_API_KEY")
    )
    gemini_timeout_ms: int = Field(
        default=30_000, validation_alias=AliasChoices("GEMINI_TIMEOUT_MS")
    )

    # ── 도감 카드 생성 (#496, docs/cardimage/) ─────────────────────────
    # 채팅용 gemini_api_key 와 **다른 GCP 프로젝트** 키입니다 (지출 상한·사용량이 프로젝트
    # 단위라 이미지 생성이 채팅 예산을 먹지 않게). 비면 GEMINI_API_KEY 로 떨어지지 **않고**
    # /admin/cardimage·/app/ai-cards 가 503 입니다 — 앱은 뜹니다.
    cardimage_gemini_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("DAENGS_CARDIMAGE_GEMINI_API_KEY")
    )
    # Nano Banana 2. 실험 16장 「됨」(worklog 09-14). 세대가 바뀌면 이 한 줄.
    cardimage_model: str = Field(default="gemini-3.1-flash-image", validation_alias=AliasChoices("DAENGS_CARDIMAGE_MODEL"))
    # 2K 여야 카드(994×1582)에 확대 없이 맞습니다. 1K 는 1.25배 확대.
    cardimage_size: str = Field(default="2K", validation_alias=AliasChoices("DAENGS_CARDIMAGE_SIZE"))
    cardimage_timeout_ms: int = Field(default=120_000, validation_alias=AliasChoices("DAENGS_CARDIMAGE_TIMEOUT_MS"))
    # 틀 12장·글꼴이 있는 폴더. 기본값을 상대 경로("cardimage")로 두면 CWD 에 따라 갈려서
    # `uv run dev` 를 backend/ 에서 돌리면 못 찾는다(#496 리뷰에서 실측). 그래서 이 파일
    # 위치에서 절대 경로로 계산한다 — 개발 PC 는 `backend/src/daengs_backend/config.py` 라
    # parents[3] 가 저장소 루트라 `<repo>/cardimage`, 컨테이너는 `/app/src/daengs_backend/
    # config.py` 라 parents[3] 가 `/` 라서 `/cardimage`(compose 마운트와 같은 자리). 둘 다
    # 맞아떨어지므로 `DAENGS_CARDIMAGE_DIR` 환경 변수는 이제 belt-and-braces 다.
    cardimage_dir: Path = Field(
        default=Path(__file__).resolve().parents[3] / "cardimage",
        validation_alias=AliasChoices("DAENGS_CARDIMAGE_DIR"),
    )
    # 허용된 달. 틀은 12장 다 있지만 이 카드(#496)는 4월만 엽니다. "4,9" 처럼 CSV.
    #
    # ⚠ pydantic-settings 는 env 값을 우리 before-validator 가 보기 전에 먼저 JSON 으로
    #   디코드하려 합니다 — frozenset[int] 는 "복합 타입"이라 CSV 문자열("4, 9,12")을
    #   JSON 으로 못 읽어 여기까지 오기 전에 실패합니다. `NoDecode` 로 그 선(先)디코드를
    #   끄고, 아래 before-validator 가 원문 문자열을 그대로 받아 직접 나눕니다.
    cardimage_months: Annotated[frozenset[int], NoDecode] = Field(
        default=frozenset({4, 9}), validation_alias=AliasChoices("DAENGS_CARDIMAGE_MONTHS")
    )
    # 유사도 검수. 텍스트 모델이라 채팅과 같은 계열이어도 됩니다 — 여기서는 "같은 개인가"만 묻습니다.
    cardimage_judge_model: str = Field(default="gemini-3.1-flash-lite", validation_alias=AliasChoices("DAENGS_CARDIMAGE_JUDGE_MODEL"))
    # 1~5 중 이 값 미만이면 한 번 다시 만듭니다. 실험에서 정면 사진은 6장 중 1장이 어긋났습니다.
    cardimage_judge_min: int = Field(default=3, ge=1, le=5, validation_alias=AliasChoices("DAENGS_CARDIMAGE_JUDGE_MIN"))
    # 앱 사용자 하루 생성 한도 (KST 하루, `ready` 만 셈). 0 이면 한도 없음. 테스트 단계라 1 이고,
    # 제품 규칙이 정해지면 `services/ai_card_quota.py` 의 함수를 통째로 바꿉니다 (D-076).
    cardimage_daily_limit: int = Field(default=1, ge=0, validation_alias=AliasChoices("DAENGS_CARDIMAGE_DAILY_LIMIT"))
    # 서버 전체 동시 생성 수. backend 프로세스 안 백그라운드 작업이라 스레드를 씁니다 (D-076).
    cardimage_concurrency: int = Field(default=2, ge=1, validation_alias=AliasChoices("DAENGS_CARDIMAGE_CONCURRENCY"))

    # GPU 카드 생성 서비스(D-078, Cloud Run asia-southeast1 L4). **비어 있으면 Nano Banana 2(D-074)
    # 그대로** — 되돌리기가 이 한 줄이다. ⚠ 앱 경로(`/app/ai-cards`)의 정리 기준은 아직
    # `cardimage_timeout_ms` 만 보므로 콜드 스타트(가중치 로드 수 분)를 모른다 — #544 에서는 VM 에 넣지 않는다.
    cardgen_url: str = Field(default="", validation_alias=AliasChoices("DAENGS_CARDGEN_URL"))
    # 콜드 스타트 + 생성. `infra/gcp/cardgen.sh` 의 `--timeout=900` 과 맞춘다.
    cardgen_timeout_s: float = Field(default=900.0, gt=0, validation_alias=AliasChoices("DAENGS_CARDGEN_TIMEOUT_S"))

    @field_validator("cardimage_months", mode="before")
    @classmethod
    def _parse_months(cls, v):
        if isinstance(v, str):
            return frozenset(int(x) for x in v.split(",") if x.strip())
        return v

    # ── LLM judge (RAG-007 · D15 · D-060) ────────────────────────────
    # **세 값의 원본은 `daengs_life.rag.core.config` 입니다** (#305). 여기 있는 것은
    # 그 값을 `daengs_backend` 쪽 도구(`src/daengs_evals/answer_quality/`)도 읽어야 해서이고,
    # 위 `redis_url`·`gemini_api_key` 와 **같은 판단**입니다 — 두 패키지가 같은 env 를
    # 각자 읽는 것이 서로를 import 하는 것보다 쌉니다.
    #
    # ⚠ **이름·단위·기본값을 #305 와 어긋나게 두지 마세요.** 같은 `backend/.env` 한 줄을
    #   둘이 읽으므로, 여기서만 바꾸면 judge 둘이 다른 모델로 채점하면서 그 사실이
    #   아무 데도 안 드러납니다.
    #
    # 판정자가 Gemini 가 아닌 이유는 #305 가 이미 정했습니다 — 생성이 Gemini 인데 judge 도
    # Gemini 면 같은 훈련 계보가 자기 계열 문장을 후하게 보는 self-preference 가 남습니다.
    # RAG-007 은 "급 분리"(flash 생성 → pro judge)까지 요구했고, 2026-09-07 에 계열까지
    # 가르기로 정했습니다. D-060 은 그 결정을 훈련 RAG 로 **이어받을 뿐 다시 정하지 않습니다.**
    #
    # ⚠ `os.getenv` 로 읽는 자리를 새로 만들지 마세요. `daengs_training/generation/gemini.py`
    #   가 그렇게 돼 있어서 개발 PC 에서는 셸에 키를 또 줘야 합니다 (`backend/.env` 는
    #   pydantic-settings 가 Settings 로 읽을 뿐 `os.environ` 에 올리지 않습니다).
    #   openai SDK 의 기본 생성자가 그 `os.environ` 을 보므로, 여기서 받아 클라이언트에
    #   **명시적으로** 넘깁니다.
    #
    # 키가 비면 judge 만 안 돕니다 — 앱은 정상으로 뜹니다.
    openai_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("OPENAI_API_KEY")
    )
    # ⚠ **`-latest` 류를 쓰지 않습니다** (#305). 움직이는 이름이면 두 판정 파일의 차이가
    # 답변 때문인지 judge 때문인지 안 갈립니다. `gemini_model` 과 방향이 반대인데, 그쪽은
    # 생성이라 되돌리기가 싸고 이쪽은 **채점자**라 흔들리면 옛 판정과의 비교가 통째로 끊깁니다.
    openai_judge_model: str = Field(
        default="gpt-5.4-2026-03-05", validation_alias=AliasChoices("OPENAI_JUDGE_MODEL")
    )
    # ⚠ **여기는 초입니다** — `gemini_timeout_ms` 와 단위가 다르므로 이름에 박아 둡니다.
    # judge 는 서빙 경로가 아니라 배치라 넉넉하게 줍니다.
    openai_timeout_s: float = Field(
        default=120.0, validation_alias=AliasChoices("OPENAI_TIMEOUT_S"), gt=0
    )

    # ── Place discovery internal HTTP boundary ───────────────────────
    # backend와 place-search는 소스를 공유해도 런타임은 분리돼 있습니다. 기본값은 compose
    # service DNS이고, 호스트에서 backend만 실행할 때는 backend/.env에서 바꿉니다.
    place_search_base_url: str = "http://place-search:8000"
    # Place 내부 provider timeout과 별개의 assistant 응답 경계입니다 (밀리초).
    place_discovery_timeout_ms: int = Field(default=15_000, gt=0)
    # Facility search includes the Place provider (30s default) and DB work.
    facility_discovery_timeout_ms: int = Field(default=45_000, gt=0, le=120_000)

    # 점령지 사진 판정은 대화/라우팅과 호출 예산이 다릅니다. 모델 이름과 12초 제한을
    # 따로 두어, 사진 판정 워커만 독립적으로 교체·튜닝할 수 있게 합니다. 키는 같은
    # Gemini 프로젝트를 쓰되 웹 요청에서는 이 설정을 소비하지 않습니다.
    territory_vision_model: str = Field(
        default="gemini-3.1-flash-lite",
        validation_alias=AliasChoices("TERRITORY_VISION_MODEL"),
    )
    territory_vision_timeout_ms: int = Field(
        default=12_000,
        validation_alias=AliasChoices("TERRITORY_VISION_TIMEOUT_MS"),
    )

    # 점령지 게임판은 별도 place-search 프로세스가 소유합니다. backend는 좌표를
    # 복제하지 않고 촬영 시점에 이 내부 HTTP 경계로 현행 140u 대표점을 확인합니다.
    territory_site_base_url: str = "http://place-search:8000"
    territory_site_timeout_seconds: float = 2.0

    # ── 공용 파일 저장소 (D-043, D-052) ───────────────────────────────
    # provider 는 **서버의 도커 볼륨**으로 확정 (D-052, 2026-09-04). GCS 버킷은 파지
    # 않습니다. 그래도 **세부값은 하드코딩하지 않습니다** — 환경으로 뺍니다.
    #
    # ⚠️ 이름이 gait_* 인 것은 보행에서 시작한 역사적 이름입니다. 지금은 점령지 사진도
    #    같은 값을 읽는 **공용 설정**이고, 프로필 사진·도감 카드도 여기로 옵니다.
    #    MEDIA_* 로 바꾸는 것은 볼륨이 실제로 도는 것을 본 뒤 별도 카드입니다.
    #
    # `gait_storage` 가 저장소 구현을 고릅니다:
    #   "none"  (기본) — 미설정. 모든 호출이 503. 서버에 아무 설정도 없을 때.
    #             기본값을 none 으로 두는 이유는 개발 PC 가 아무 설정 없이도 뜨게
    #             하려는 것입니다 — 운영 서버는 .env 에서 local 로 켭니다.
    #   "local" — **이게 운영값입니다** (D-052). 서버의 gait-bridge 볼륨에 두고
    #             업로드·다운로드가 backend 의 bridge 를 지납니다.
    #   "gcs"   — 지금은 안 씁니다. 서버가 부하를 못 받을 때 되돌아갈 길.
    gait_storage: str = Field(default="none", validation_alias=AliasChoices("GAIT_STORAGE"))
    # GCS 로 되돌아갈 때만 씁니다. 비워 두는 것이 정상입니다 — 비어 있는 채
    # gait_storage="gcs" 이면 기동이 아니라 첫 발급에서 명확히 실패합니다.
    gait_gcs_bucket: str = Field(default="", validation_alias=AliasChoices("GAIT_GCS_BUCKET"))
    gait_gcs_location: str = Field(default="", validation_alias=AliasChoices("GAIT_GCS_LOCATION"))
    # 만료(초). **아직 잠정 기본값**입니다 — 실기기 왕복을 보고 정합니다.
    # 업로드는 큰 파일이라 넉넉히, 다운로드(재생)는 짧게.
    #
    # ⚠️ local 저장소에서는 **다운로드 만료가 실제로 걸리지 않습니다.** bridge 주소에는
    #    서명도 만료도 없고, 대신 라우터가 DB 로 "backend 가 발급한 키인지"를 봅니다.
    #    이 값은 GCS 로 되돌아갈 때를 위해 계약에만 남아 있습니다.
    gait_upload_url_ttl_seconds: int = Field(
        default=15 * 60, validation_alias=AliasChoices("GAIT_UPLOAD_URL_TTL_SECONDS")
    )
    gait_download_url_ttl_seconds: int = Field(
        default=10 * 60, validation_alias=AliasChoices("GAIT_DOWNLOAD_URL_TTL_SECONDS")
    )
    # 파일을 두는 곳 — **컨테이너 안 경로**입니다 (윈도우 경로가 아닙니다).
    # compose 의 gait-bridge 볼륨이 backend · gait-worker · territory-vision-worker
    # 셋에 이 경로로 물립니다. 셋이 **같은 곳을 봐야** 분석이 됩니다.
    gait_local_storage_dir: str = Field(
        default="", validation_alias=AliasChoices("GAIT_LOCAL_STORAGE_DIR")
    )
    # 업로드/다운로드 URL 앞부분 (앱 기준, nginx 접두사 포함).
    # 앱이 여기에 /app/gait/_bridge/... 를 붙여 부릅니다.
    #
    # ⚠️ **서버마다 다른 유일한 값입니다.** 집 서버는 http://daengback.~ (평문),
    #    GCP VM 은 https://daengapi.~ 입니다. 릴리즈 앱이 보는 곳은 **반드시 후자**여야
    #    합니다 — 공개한 처리방침 6항이 "서버와의 통신은 HTTPS로 암호화합니다" 입니다.
    gait_bridge_base_url: str = Field(
        default="", validation_alias=AliasChoices("GAIT_BRIDGE_BASE_URL")
    )
    # bridge 업로드 한 건의 크기 상한(바이트).
    #
    # ⚠️ **없으면 안 됩니다.** D-052 로 모든 바이트가 backend 를 지나게 됐는데, 상한이
    #    없으면 한 요청이 컨테이너 메모리를 다 먹습니다. 라우터가 Content-Length 를
    #    먼저 보고, 스트리밍 중에도 누적으로 다시 봅니다.
    #
    # nginx 의 `location /app/gait/` client_max_body_size(200m) **보다 낮게** 두세요.
    # 높으면 nginx 가 먼저 끊어서 앱이 우리 413 대신 nginx HTML 을 받습니다.
    # 이름·기본값은 옛 gait-analysis HTTP 서비스(D-063 4단계에서 제거)의 것을 그대로 이었습니다.
    gait_max_upload_bytes: int = Field(
        default=150 * 1024 * 1024,
        validation_alias=AliasChoices("GAIT_MAX_UPLOAD_BYTES"),
    )

    # 공동 돌봄 초대 웹 안내(`/invite`)가 여는 `/.well-known/assetlinks.json` 의
    # 서명 지문. **Android App Links 검증에 쓰는 값입니다.** Play App Signing 을 쓰는
    # 앱은 우리가 올리는 업로드 키(`daengs.uploadKeyStore`)와 스토어가 배포하는 앱의
    # 서명 키가 **다릅니다** — 스토어 설치본을 열려면 Play Console → 릴리스 → 설정 →
    # 앱 서명의 「앱 서명 키 인증서」 SHA-256 이 들어가야 합니다. 업로드 키 지문을 같이
    # 넣어도 됩니다(업로드 키로 서명한 로컬 릴리스 빌드가 그것으로 검증됩니다) —
    # 배열이라 둘 다 넣을 수 있습니다.
    #
    # ⚠️ **비어 있으면 App Links 검증이 그냥 실패합니다** — 일부러 그렇게 둡니다.
    #    가짜 지문을 넣느니 검증이 안 되는 채로(=링크가 웹 안내로 떨어지는 채로) 배포하는
    #    편이 낫습니다. 값은 JSON 배열입니다:
    #    DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS=["AA:BB:…(32쌍)"]
    #    파일은 `backend/.env` 입니다 — 최상단 `.env` 는 compose 용이라 backend 가 안 읽습니다.
    #
    # ⚠️ **모양이 틀려도 부팅을 막지 않습니다** — 선택 기능이라서입니다. 틀리면 전부 버리고
    #    (일부만 채택하지 않음) 부팅 로그에 오류를 남기고 `/admin/status` 에 `app_links` 항목으로
    #    보입니다. 규칙은 `app_links.py` 입니다. 위의 필수 보안 설정(카카오 앱 키·DB·키)은
    #    여전히 부팅에서 막습니다.
    #
    # **원문 문자열로 받습니다.** `list[str]` 로 두면 pydantic-settings 가 검증기보다 먼저
    # JSON 디코드를 해서, JSON 이 아닌 값 하나에 `SettingsError` 로 부팅이 죽습니다.
    play_signing_sha256_fingerprints_raw: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS"),
    )

    @property
    def play_signing_sha256_fingerprints(self) -> list[str]:
        """검증을 통과한 지문. 설정이 틀렸으면 빈 목록이다."""
        from daengs_backend.app_links import parse_play_signing_fingerprints

        return list(parse_play_signing_fingerprints(self.play_signing_sha256_fingerprints_raw).fingerprints)

    @property
    def play_signing_config_error(self) -> str | None:
        """설정이 틀렸으면 그 사유(값은 싣지 않음). 비었거나 맞으면 None."""
        from daengs_backend.app_links import parse_play_signing_fingerprints

        return parse_play_signing_fingerprints(self.play_signing_sha256_fingerprints_raw).error

    # ── 보행 분석 엔진 (#304 · D-063) ──────────────────────────────────
    # **지금 값은 `v4` 하나입니다** — `daengs_gait.inference`(ssdlite + RTMPose AP-10K).
    # 워커가 자기 인터프리터(`sys.executable`)로 서브프로세스를 띄우고, 가중치는
    # `GAIT_RELEASE_DIR` 폴더입니다(`ssdlite.pt` · `rtmpose-m_ap10k/end2end.onnx`).
    #
    # 옛 `"legacy"` 는 6단계에서 추론 runtime 과 함께 없앴습니다. 그 값을 넣으면
    # `get_engine` 이 **조용히 넘어가지 않고 예외**를 냅니다 — 잘못 적힌 설정으로 분석이
    # 도는 것보다 FAILED 사유와 함께 멈추는 편이 낫습니다.
    #
    # ⚠️ **기본값이 곧 배포 기본입니다.** env 를 안 주는 환경(새 서버·CI)이 이 값으로
    #    돌므로 없어진 엔진 이름을 기본값에 두면 분석이 전부 실패합니다.
    #
    # 기록의 `gait_records.pose_model` 은 이 설정과 별개입니다 — 옛 legacy 기록은 그대로
    # 남고, 비교는 서버 설정이 아니라 **두 기록의 그 값**으로 함수를 고릅니다
    # (`services/gait._run_compare` — legacy↔legacy 비교는 계속 됩니다).
    gait_engine: str = Field(default="v4", validation_alias=AliasChoices("GAIT_ENGINE"))

    # ── 내부 서비스 주소 (#180 상태 페이지) ────────────────────────────
    # 상태 페이지가 "이 서비스가 살아 있나"를 물어보는 곳입니다. backend 와
    # **다른 컨테이너**라 프로세스 안에서는 알 수 없고, nginx 를 거치지도 않습니다
    # (compose 네트워크 안에서 서비스 이름으로 직접 닿습니다).
    #
    # **기본값이 compose 서비스 이름인 이유**는 서버의 `.env` 를 안 건드리려는 것입니다.
    # 개발 PC 에서 `uv run dev` 로 띄우면 이 이름들이 **애초에 안 풀리는데**, 그것이
    # 곧 "이 환경엔 없다" 이므로 상태 페이지가 `absent` 로 그립니다 — 이름 해석
    # 실패(DNS)와 연결 거부를 가르는 판정이 `services/status.py` 에 있습니다.
    #
    # ⚠ 한계: compose 안에서 컨테이너가 아예 안 떠 있어도 도커 DNS 가 이름을 못 풀어
    #   `absent` 로 보입니다. "이 환경에 있어야 하는가" 를 backend 가 따로 알지 못하는
    #   한 그 둘은 안 갈립니다. 갈라야 할 일이 생기면 그때 `expected` 를 더합니다.
    #
    # 빈 값으로 두면 그 항목을 아예 `absent` 로 둡니다 (물어보지도 않습니다).
    #
    # **place 는 여기 없습니다** — 위 `place_search_base_url` 을 그대로 씁니다. 같은
    #   컨테이너의 주소를 두 이름으로 두면 한쪽만 고치는 날 상태 화면과 실제 호출이
    #   서로 다른 곳을 봅니다.
    journey_service_url: str = "http://journey-service:8000"

    # 조각으로 바뀌기 전에 쓰던 이름입니다 (D-013).
    #
    # extra="ignore" 라서 .env 에 남아 있어도 조용히 무시되는데, 그러면 개발 PC 가
    # 엉뚱한 호스트로 붙어서 원인을 찾기 어려운 인증 실패를 봅니다. 옛 줄이 보이면
    # 무시하지 말고 뜨지 않는 편이 낫습니다. 언젠가 지워도 되는 필드입니다.
    legacy_database_url: str | None = Field(default=None, validation_alias="DAENGS_DATABASE_URL")

    # ── 암호화 키 ─────────────────────────────────────────────────────
    # 셋 다 32바이트 난수를 urlsafe base64 로 인코딩한 문자열입니다.
    #
    #   uv run python -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    #
    # 기본값을 두지 않습니다 — 키가 없으면 앱이 뜨지 않는 것이 맞습니다.
    # 개발용 기본값을 하나 박아 두면 그게 그대로 서버에 올라갑니다.
    #
    # SecretStr 이라 로그·예외 메시지에는 '**********' 로 찍힙니다.
    # 실제 값은 .get_secret_value() 로 꺼내며, 꺼내는 곳은 core/crypto.py 뿐입니다.
    #
    # 셋을 나눠 둔 이유는 잃었을 때의 결과가 전부 다르기 때문입니다 (D-012).
    jwe_key: SecretStr  # 토큰 암호화 (access token)
    aes_key: SecretStr  # 개인정보 컬럼 암복호화
    blind_index_key: SecretStr  # HMAC pepper. AES 키와 같은 값을 쓰면 안 됩니다.

    # ── 카카오 ────────────────────────────────────────────────────────
    # 앱이 보낸 id_token 의 `aud` 와 대조할 **앱 키 목록**입니다 —
    # "이 토큰이 우리 앱에게 발급된 것인가" (D-017).
    #
    # **하나가 아니라 목록인 이유**: 카카오의 `aud` 는 인가 요청에 쓴 앱 키
    # 그대로입니다. 같은 애플리케이션이라도 들어온 경로마다 값이 다릅니다.
    #
    #     Android / iOS SDK   네이티브 앱 키
    #     JavaScript SDK      JavaScript 키
    #     REST API            REST API 키   (cli/kakao_token.py 가 쓰는 흐름)
    #
    # 그래서 REST 키 하나만 대조하면 **앱 로그인이 전부 401 로 막힙니다.**
    #
    # **한 애플리케이션에서 나온 키만 넣으세요.** 카카오 회원번호(`sub`)는 앱 단위라,
    # 다른 애플리케이션의 키를 섞으면 같은 사람이 서로 다른 회원으로 갈라집니다.
    # 목록을 늘린다고 검사가 느슨해지는 것은 아닙니다 — 우리 앱'들'이 되는 것뿐입니다.
    # 어드민 키는 넣지 마세요. aud 로 오지 않고, 그 키 하나로 전 회원을 조작합니다.
    #
    # **비밀이 아닙니다.** 앱에도 들어가 있고 카카오에 요청할 때 그대로 나갑니다.
    # 그래도 기본값을 두지 않습니다. 기본값을 두면 검증이 조용히 엉뚱한 aud 를
    # 통과시키게 되는데, 그건 남의 앱 토큰으로 우리 서비스에 계정이 생긴다는 뜻입니다.
    #
    # 값은 JSON 배열입니다: DAENGS_KAKAO_APP_KEYS=["<네이티브>","<REST>"]
    #
    # 필수 필드로 두지 않고 빈 목록을 기본값으로 둔 뒤 아래 validator 에서 막습니다.
    # 필수로 두면 pydantic 이 "Field required" 로 먼저 끊어서, 옛 이름을 쓰고 있는
    # 사람에게 **무엇으로 바뀌었는지** 알려 줄 기회가 없어집니다.
    kakao_app_keys: list[str] = Field(default_factory=list)

    # `cli/kakao_token.py` 전용입니다. **로그인 검증에는 쓰이지 않습니다.**
    #
    # 그 CLI 는 REST 흐름이라 `client_id` 자리에 **REST API 키**를 넣어야 하는데,
    # 위 목록에서는 어느 것이 REST 키인지 알 수 없습니다 (넣은 순서에 기대면
    # 언젠가 조용히 어긋납니다). 그래서 한 줄 더 둡니다.
    #
    # 없어도 됩니다 — 그 CLI 를 쓸 때만 필요하고, 서버는 이 값 없이 잘 돕니다.
    # 하나만 받던 시절의 이름과 같아서, 이 값만 있고 위 목록이 비어 있으면
    # 아래 validator 가 "이름이 바뀌었다"고 알려 줍니다.
    kakao_rest_api_key: str | None = None

    @model_validator(mode="after")
    def _reject_legacy_database_url(self) -> "Settings":
        if self.legacy_database_url is not None:
            raise ValueError(
                "DAENGS_DATABASE_URL 은 더 이상 쓰지 않습니다 (D-013). backend/.env 에서 "
                "그 줄을 지우고 DAENGS_DB_HOST / DAENGS_DB_PASSWORD 를 넣으세요. "
                "backend/.env.example 에 예시가 있습니다."
            )
        return self

    @model_validator(mode="after")
    def _check_kakao_app_keys(self) -> "Settings":
        """앱 키 목록이 **비어 있지 않은지** 확인합니다. 이 검사가 핵심입니다.

        joserfc 의 `JWTClaimsRegistry` 는 `values` 가 빈 목록이면 aud 검사를
        **통째로 건너뜁니다** (`check_value` 의 `if not option_values: return`).
        즉 `DAENGS_KAKAO_APP_KEYS=[]` 는 "앱 키가 없다"가 아니라
        **"아무 카카오 앱의 토큰이나 통과"** 가 됩니다 — 남의 앱에서 받은 토큰으로
        우리 서비스에 계정이 생깁니다. 필수 필드로 두는 것만으로는 못 막습니다.

        빈 문자열도 같이 걸러 냅니다. `[""]` 는 목록이 비지 않았지만 어떤 aud 와도
        맞지 않아, 전원 로그인 불가를 조용히 만듭니다.
        """
        if self.kakao_rest_api_key and not self.kakao_app_keys:
            # 옛 이름만 남아 있는 .env 입니다. 위 필드는 이제 CLI 전용이라
            # 이것만으로는 로그인 검증이 서지 않습니다.
            raise ValueError(
                "DAENGS_KAKAO_REST_API_KEY 는 DAENGS_KAKAO_APP_KEYS 로 바뀌었습니다. "
                "카카오 id_token 의 aud 는 로그인에 쓴 앱 키라, 네이티브 SDK 로 들어온 "
                "토큰은 REST API 키와 맞지 않습니다. backend/.env 에서 그 줄을 "
                'DAENGS_KAKAO_APP_KEYS=["<네이티브 앱 키>","<REST API 키>"] 로 바꾸세요. '
                "backend/.env.example 에 예시가 있습니다."
            )

        if not self.kakao_app_keys or any(not k.strip() for k in self.kakao_app_keys):
            raise ValueError(
                "DAENGS_KAKAO_APP_KEYS 가 비어 있습니다. 비워 두면 aud 검증이 통째로 "
                "건너뛰어져 남의 카카오 앱 토큰으로도 우리 서비스에 계정이 생깁니다 "
                '(D-017). 예) DAENGS_KAKAO_APP_KEYS=["<네이티브 앱 키>","<REST API 키>"]'
            )
        return self

    @property
    def database_url(self) -> URL:
        """SQLAlchemy 접속 URL.

        문자열로 이어 붙이지 않고 URL.create 로 만듭니다. 비밀번호에 `@` `/` `#` 가
        들어가면 이어 붙인 URL 은 엉뚱하게 파싱되는데, 여기서는 알아서 이스케이프됩니다.

        로그에 찍을 일이 있으면 `.render_as_string(hide_password=True)` 를 쓰세요.
        """
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


def _load_settings(**overrides: object) -> Settings:
    """설정을 읽습니다. 실패하면 **값은 빼고** 무엇이 잘못됐는지만 알립니다.

    pydantic 의 ValidationError 메시지에는 입력값이 그대로 들어갑니다. 여기서 읽는
    입력에는 암호화 키와 DB 비밀번호가 있어서, 그대로 올리면 부팅 실패 로그에
    원문이 남습니다. 필드 이름과 사유만 남기고 값은 버립니다.

    `from None` 인 것도 같은 이유입니다 — 원래 예외를 달고 가면 그 메시지가
    트레이스백에 그대로 찍힙니다.
    """
    try:
        loaded = Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        problems = "\n".join(
            f"  {'.'.join(str(part) for part in error['loc']) or '(전체)'}: {error['msg']}"
            for error in exc.errors()
        )
        raise RuntimeError(
            "설정을 읽지 못했습니다. backend/.env 를 확인하세요 "
            f"(backend/.env.example 참고).\n{problems}"
        ) from None

    # **선택 기능의 설정 오류는 부팅을 막지 않고 여기서 크게 남깁니다** (`app_links.py`).
    # 사유에는 값이 없습니다. 콘솔 `/admin/status` 의 `app_links` 항목에도 같은 말이 뜹니다.
    if loaded.play_signing_config_error:
        logging.getLogger(__name__).error(loaded.play_signing_config_error)
    return loaded


settings = _load_settings()
