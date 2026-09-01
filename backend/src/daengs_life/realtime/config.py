"""realtime 의 설정·경로.

**키는 자기 Settings 로 읽는다** — crawler 가 자기 것을 갖는 이유와 같다 (RAG-015). 공유 Settings 를
만들면 realtime 이 `LAW_OC`·`DATABASE_URL` 까지 알게 되어 패키지 경계가 이름 수준에서 흐려진다.

**`crawler` 에서 가져오는 것은 경로 탐색 하나뿐이다** (RT-001 ①-2). "data/ 는 어디인가"의 답이
둘이 되면 컨테이너에서 서로 다른 곳을 본다 (RAG-014). 넓히면 `Fetcher`·`store`·`seed_sources` 가
따라 들어오고 그 순간 "실시간은 저장하지 않는다"(RAG-012)가 흐려진다 —
`tests/test_import_direction_packages.py` 가 그 경계를 말이 아니라 기계로 막는다.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from pydantic_settings import BaseSettings, SettingsConfigDict

from daengs_life.crawler.core.config import DATA_DIR, KST, require_data_dir  # noqa: F401

# 밑줄 이름을 그대로 가져오는 이유 — env 파일의 **병합 순서**(루트 .env → backend/.env, 뒤가 우선)는
# 답이 둘이 되면 안 되는 값이다 (RAG-014·RAG-015). 여기서 다시 조립하면 crawler 쪽 순서가 바뀔 때
# realtime 만 조용히 달라진다. 공개 이름이 필요해지면 crawler 쪽에서 개명한다.
from daengs_life.crawler.core.config import _ENV_FILES

# 측정소 목록처럼 월 1회 갱신하는 **정적 메타**만 여기 둔다 (docs/life/realtime-apis.md §1).
# 실황·예보는 절대 오지 않는다 — 그것이 이 패키지가 crawler 를 안 닮는 지점이다.
REFERENCE_DIR: Path | None = DATA_DIR / "reference" if DATA_DIR else None


def normalize_key(value: str) -> str:
    """`%` 가 있으면 URL 디코딩 1회, 없으면 그대로.

    data.go.kr 포털이 **같은 키를 Encoding/Decoding 두 벌**로 보여준다. httpx 가 `params` 를
    자동 인코딩하므로 맞는 것은 Decoding 형태이고, Encoding 키를 그대로 주면 `%2F` → `%252F`
    이중 인코딩으로 실패한다 (docs/life/realtime-apis.md §6.1 함정 1 — 실측).

    **판별이 확정적이라 오탐이 없다.** 키는 base64(`A-Za-z0-9+/=`) 아니면 hex 이고 두 알파벳
    어디에도 `%` 가 없다. 두 벌로 발급하는 것이 실측된 곳은 data.go.kr 하나지만, 알파벳 근거가
    셋에 똑같이 성립하므로 셋 다 같은 함수를 통과시킨다 — 나중에 apihub 가 같은 짓을 해도 안 깨진다.
    """
    return unquote(value) if "%" in value else value


class Settings(BaseSettings):
    """realtime 이 쓰는 값 전부. 이름은 docs/life/data-sources.md §9 발급 체크리스트와 같다."""

    # --- API 키 (RT-001 ① 전송 계약 3종과 1:1) ---
    # 미발급이면 빈 문자열이다. 없다고 import 시점에 터뜨리지 않는다 — 키 하나가 비어도
    # 나머지 전송은 돌아야 하고, 그 위에서 ⑤ 저하 정책이 UNKNOWN 을 만든다.
    data_go_kr_key: str = ""     # 기상청 단기예보·특보 · 에어코리아 2종
    kakao_rest_key: str = ""     # Local — "○○동" 표기 (없어도 관통된다, RT-001 ①)
    kma_hub_key: str = ""        # apihub typ01(AWS) · typ02(생활기상지수)

    # --- 호출 예절·예산 ---
    request_timeout_sec: float = 5.0     # ⑤-b 전체 예산 8초 안에서 개별 호출 상한
    request_budget_sec: float = 8.0      # ⑤-b 재시도까지 포함한 한 요청의 총 예산

    # 월 1회짜리 정적 메타(측정소 목록 17회 · AWS 지점표)는 **요청 예산 밖이다.**
    # ⑤-b 의 8초는 "한 판정에 쓰는 시간"인데, 여기에 콜드 캐시의 목록 수집까지 넣으면
    # 첫 요청이 예산을 다 쓰고 판정을 못 낸다. 정상 경로에서는 Beat 가 미리 데우므로
    # 이 예산은 콜드 스타트에서만 쓰인다 (④-d).
    static_budget_sec: float = 30.0

    # --- 캐시·오케스트레이션 (RAG-001 · RT-001 ④) ---
    # 비어 있으면 캐시가 프로세스 메모리로 떨어진다. **연결 실패도 마찬가지다** — ④-c 가
    # "Redis 를 쓰되 없어도 돈다"를 확정했고, 그 판단이 `cache.open_store()` 에 있다.
    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

# 키는 **읽는 즉시 정규화**한다. transport 3 모듈이 각자 하면 세 곳이 따로 늙고,
# 한 곳이 빠지면 그 전송만 이중 인코딩으로 조용히 실패한다.
DATA_GO_KR_KEY = normalize_key(settings.data_go_kr_key.strip())
KAKAO_REST_KEY = normalize_key(settings.kakao_rest_key.strip())
KMA_HUB_KEY = normalize_key(settings.kma_hub_key.strip())

REQUEST_TIMEOUT_SEC = settings.request_timeout_sec
REQUEST_BUDGET_SEC = settings.request_budget_sec
STATIC_BUDGET_SEC = settings.static_budget_sec
REDIS_URL = settings.redis_url.strip()

# 이름 → 값. CLI 출력과 provider 의 "키 없음" 안내가 같은 목록을 본다.
KEYS: dict[str, str] = {
    "DATA_GO_KR_KEY": DATA_GO_KR_KEY,
    "KAKAO_REST_KEY": KAKAO_REST_KEY,
    "KMA_HUB_KEY": KMA_HUB_KEY,
}
