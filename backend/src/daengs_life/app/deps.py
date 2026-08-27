"""의존성 주입 (RAG-027).

**이 파일이 있는 이유는 둘이다.**

1. `Cache` 는 Redis 커넥션 풀을 들고 있다 — 요청마다 만들면 풀이 요청 수만큼 생긴다.
   프로세스에 하나만 두고 돌려 쓴다.
2. **테스트가 여기를 갈아끼운다.** 검문소 D 를 API 레벨에서 다시 돌리려면 죽은 provider 를
   가진 캐시를 넣을 수 있어야 하고, 그 자리가 FastAPI 의 `dependency_overrides` 다.
   전역 싱글턴을 모듈 상수로 두면 그게 불가능해진다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterator

from daengs_life.realtime.cache import Cache
from daengs_life.realtime.config import KST


@lru_cache(maxsize=1)
def get_cache() -> Cache:
    """프로세스당 하나. `Cache()` 가 Redis 연결을 시도하고, 실패하면 메모리로 떨어진다 (④-c)."""
    return Cache()


def get_now() -> datetime:
    """지금. **주입하는 이유는 테스트다** — 컨트롤러가 `datetime.now()` 를 직접 부르면
    API 테스트가 그날의 실제 시각(과 픽스처의 예보 구간)에 묶여, 내일 이유 없이 깨진다.
    판정이 시각의 함수라는 ③-a 가 여기서도 그대로 값어치를 한다.
    """
    return datetime.now(KST)


# ---------------------------------------------------------------- 파트① `/ask` (RAG-028 ①⑤)
@dataclass(frozen=True)
class Encoder:
    """질의를 벡터로 바꾸는 것 한 벌. `key` 를 함께 들고 다니는 이유는 **문서 쪽과 같은 모델이어야
    하기 때문**이다 — 다른 모델로 질의를 인코딩하면 두 벡터가 다른 공간에 있어 코사인이 무의미해진다."""

    key: str
    st: Any


@lru_cache(maxsize=1)
def get_encoder() -> Encoder:
    """프로세스당 하나. **CPU 에 올린다** (RAG-028 ①).

    호출마다 올렸다 내리면 요청당 5~7초가 붙는다. GPU 를 안 쓰는 것은 성능을 포기한 게 아니라
    **배치와의 VRAM 경합을 없앤 것**이다 — 질의 하나는 CPU 로 124ms 인데 같은 요청 안에서 Gemini 가
    초 단위를 쓰므로 그 74ms 차이는 묻히고, 서버가 2.3GB 를 물고 있으면 2랩에서 새 소스를 임베딩할 때
    6GB 중 3.7GB 만 남는다. device 를 바꿔도 검색 결과가 안 바뀌는 것은 실측으로 확인했다.

    **`ml` 그룹(torch)이 없으면 여기서 죽는다.** 앱은 그래도 떠야 하므로 lifespan 이 이 예외를
    삼키고, `/ask` 만 503 이 된다 — `Cache` 가 Redis 없이도 앱을 띄우는 것과 같은 태도다.
    """
    from daengs_life.rag.core import config
    from daengs_life.rag.stages import embed

    key = config.settings.embedding_model_key
    return Encoder(key=key, st=embed.load_model(embed.MODELS[key], device="cpu"))


def get_conn() -> Iterator[Any]:
    """DB 커넥션 — **요청당 하나** (RAG-028 ⑤).

    `Cache` 처럼 싱글턴으로 두지 않는 이유: psycopg 커넥션은 스레드 안전하지 않은데 RAG-028 ④가
    컨트롤러를 `def` 로 두어 **스레드풀에서** 돌린다. 하나를 공유하면 동시 요청이 같은 커넥션을 밟는다.
    그래도 `deps` 를 거치는 것은 성능이 아니라 **테스트가 여기를 갈아끼우기 위해서**다(이 파일의 존재 이유 2번).
    """
    from daengs_life.rag.stages import load

    conn = load.connect()
    try:
        yield conn
    finally:
        conn.close()


__all__ = ["Encoder", "get_cache", "get_conn", "get_encoder", "get_now"]
