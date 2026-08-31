"""설정·경로·공통 상수.

값은 `pydantic-settings` 로 읽는다 (RAG-015). 우선순위는 **실제 환경변수 > backend/.env > 루트 .env**.
두 env 파일은 병합된다 — 루트에만 있는 값도 그대로 올라오고, 양쪽에 있으면 backend 쪽이 이긴다.
배포에서는 오케스트레이터가 넣은 환경변수가 항상 이기므로 파일이 하나도 없어도 동작한다.

`crawler` 는 자기 Settings 를 가진다. app 이 생기면 app 이 같은 `backend/.env` 를 자기 Settings 로
읽는다 — 공유 Settings 를 만들면 크롤러가 `DATABASE_URL`·`GEMINI_API_KEY` 까지 알게 되어
`app → crawler` 한 방향(RAG-009)이 흐려진다.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

KST = ZoneInfo("Asia/Seoul")

# backend/ — 이 패키지가 속한 uv 프로젝트의 루트. 레포를 통째로 두든 backend/ 만 이미지에
# 넣든 **항상 존재하는** 유일한 기준점이라 .env 를 찾는 출발점으로 쓴다.
# 이관 전에는 세 단계 위였다. src 레이아웃으로 들어오면서 `src/daengs_life/` 두 겹이
# 끼어 다섯 단계가 됐다 (D-018) — backend/src/daengs_life/crawler/core/config.py.
PACKAGE_ROOT = Path(__file__).resolve().parents[4]


def _find_repo_root(start: Path) -> Path | None:
    """data/manifests/seed_sources.yaml 이 보이는 곳까지 위로 올라간다. 없으면 None.

    없다고 예외를 내면 안 된다 — 백엔드 이미지에는 data/ 가 없고, app 이 crawler 를 import 하므로
    (RAG-009) import 시점에 터지면 컨테이너가 아예 뜨지 않는다. 데이터가 정말 필요한 시점에
    require_data_dir() 이 안내와 함께 실패한다.
    """
    for p in [start, *start.parents]:
        if (p / "data" / "manifests" / "seed_sources.yaml").exists():
            return p
    return None


# DAENGS_REPO_ROOT 만 Settings 가 아니라 os.environ 에서 직접 읽는다 — 어느 .env 를 읽을지
# 정하려면 이 값이 먼저 필요해 Settings 안에 둘 수 없다(닭-달걀). 컨테이너 레벨 노브라 성격도 맞는다.
_repo_root_env = os.environ.get("DAENGS_REPO_ROOT")
REPO_ROOT: Path | None = Path(_repo_root_env) if _repo_root_env else _find_repo_root(PACKAGE_ROOT)

# 뒤에 오는 파일이 우선. 존재하지 않는 경로는 pydantic-settings 가 알아서 건너뛴다.
_ENV_FILES = tuple(p for p in ((REPO_ROOT / ".env") if REPO_ROOT else None,
                               PACKAGE_ROOT / ".env") if p is not None)


class Settings(BaseSettings):
    """crawler 가 쓰는 값 전부. 이름은 docs/life/data-sources.md §9 발급 체크리스트와 같다."""

    # --- 크롤 예절 (docs/life/data-sources.md §12) ---
    user_agent: str = "daengs-life-crawler/0.1 (+mailto:choiyc05@gmail.com)"
    request_delay_sec: float = 1.5          # 같은 호스트 연속 요청 간격
    request_timeout_sec: float = 30
    max_retries: int = 3

    # --- API 키 ---
    # 미발급이면 빈 문자열이고, 그 소스는 discover() 에서 발급처를 안내하며 멈춘다.
    law_oc: str = ""
    data_go_kr_key: str = ""
    kakao_rest_key: str = ""
    seoul_open_data_key: str = ""
    kma_hub_key: str = ""

    # --- 경로 override ---
    # 레포 안에서 실행하면 비워 둔다. 컨테이너처럼 레포 밖에서 돌릴 때만 지정.
    daengs_data_dir: Path | None = None

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",                     # 같은 .env 안의 POSTGRES_* 등은 무시
    )


settings = Settings()

USER_AGENT = settings.user_agent
REQUEST_DELAY_SEC = settings.request_delay_sec
REQUEST_TIMEOUT_SEC = settings.request_timeout_sec
MAX_RETRIES = settings.max_retries

DATA_DIR: Path | None = settings.daengs_data_dir or (REPO_ROOT / "data" if REPO_ROOT else None)
RAW_DIR = DATA_DIR / "raw" if DATA_DIR else None
MANIFEST_DIR = DATA_DIR / "manifests" if DATA_DIR else None
SEED_FILE = MANIFEST_DIR / "seed_sources.yaml" if MANIFEST_DIR else None
CRAWL_LOG = MANIFEST_DIR / "crawl_log.jsonl" if MANIFEST_DIR else None


def require_data_dir() -> Path:
    """data/ 가 필요한 작업의 진입점에서 부른다. 없으면 무엇을 해야 하는지 알려주며 실패한다."""
    if DATA_DIR is None:
        raise RuntimeError(
            "데이터 경로를 찾지 못했다 (data/manifests/seed_sources.yaml 기준으로 탐색).\n"
            "  레포 밖에서 실행 중이면 DAENGS_DATA_DIR 로 data/ 위치를 알려줄 것.\n"
            "  예) 컨테이너: DAENGS_DATA_DIR=/data + 볼륨 마운트\n"
            f"  탐색 시작점: {PACKAGE_ROOT}"
        )
    return DATA_DIR


def normalize_key(value: str) -> str:
    """`%` 가 있으면 URL 디코딩 1회, 없으면 그대로.

    data.go.kr 이 **같은 키를 Encoding/Decoding 두 벌**로 보여준다. httpx 가 `params` 를 자동
    인코딩하므로 맞는 것은 Decoding 형태이고, Encoding 키를 그대로 주면 `%2F` → `%252F` 이중
    인코딩으로 **HTTP 403 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`** 가 난다 — 키가 죽은 것처럼
    보이는 메시지라 원인을 엉뚱한 데서 찾게 된다 (2026-08-27 실측).

    **판별이 확정적이라 오탐이 없다.** 키는 base64(`A-Za-z0-9+/=`) 아니면 hex 이고 두 알파벳
    어디에도 `%` 가 없다. 그래서 키 전부를 같은 함수에 통과시킨다 — `LAW_OC`(이메일 ID)처럼
    `%` 가 없는 값은 그대로 나온다.

    `realtime/config.py` 에 같은 함수가 하나 더 있고 **일부러 합치지 않았다.** crawler 는
    realtime 을 import 하지 않는다(RAG-009 의 한 방향 규칙). 열 줄을 아끼자고 그 방향을 뚫으면
    D-021 이 싸다고 본 접점 크기가 커진다.
    """
    return unquote(value) if "%" in value else value


# 마스킹 대상 — Settings 의 키 필드에서 만든다. 필드를 추가하면 여기에도 이름을 넣을 것.
_SECRET_FIELDS = ["law_oc", "data_go_kr_key", "kakao_rest_key", "seoul_open_data_key", "kma_hub_key"]
# **읽는 즉시 정규화한다.** 소스마다 하면 한 곳이 빠졌을 때 그 소스만 조용히 실패한다.
SECRETS = {name.upper(): normalize_key((getattr(settings, name) or "").strip())
           for name in _SECRET_FIELDS}

LAW_OC = SECRETS["LAW_OC"]
DATA_GO_KR_KEY = SECRETS["DATA_GO_KR_KEY"]
SEOUL_OPEN_DATA_KEY = SECRETS["SEOUL_OPEN_DATA_KEY"]


# 키가 담기는 쿼리 파라미터 이름. 값 대신 이름으로 지우는 게 정확하다 —
# LAW_OC 는 이메일 앞부분이라 짧을 수 있고, 짧은 값을 문자열 치환하면 URL 의 멀쩡한 부분까지 망가진다.
_RE_SECRET_PARAM = re.compile(
    r"([?&](?:OC|serviceKey|authKey|apiKey|appKey|KEY)=)[^&#]*", re.IGNORECASE)


def redact(url: str | None) -> str | None:
    """URL 에 박힌 API 키를 *** 로 가린다.

    API 소스는 키를 쿼리스트링에 넣는데, 그 URL 이 `.meta.json` 의 source_url 로 들어가고
    meta 는 git 에 커밋된다(RAG-008). 저장·로그·화면 출력 직전에 반드시 통과시킨다.

    파라미터 이름으로 한 번, 발급받은 값 자체로 한 번 — 두 겹으로 지운다.
    이름을 모르는 새 API 가 와도 값 매칭이 받아내고, 값이 짧아 값 매칭을 못 쓰면 이름이 받아낸다.
    """
    if not url:
        return url
    url = _RE_SECRET_PARAM.sub(r"\1***", url)
    for value in SECRETS.values():
        if len(value) >= 8:                   # 짧은 값은 URL 의 정상 문자열을 오탐하므로 이름 규칙에 맡긴다
            url = url.replace(value, "***")
    return url
