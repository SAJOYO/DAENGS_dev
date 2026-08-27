"""의존성 주입 (RAG-027).

**이 파일이 있는 이유는 둘이다.**

1. `Cache` 는 Redis 커넥션 풀을 들고 있다 — 요청마다 만들면 풀이 요청 수만큼 생긴다.
   프로세스에 하나만 두고 돌려 쓴다.
2. **테스트가 여기를 갈아끼운다.** 검문소 D 를 API 레벨에서 다시 돌리려면 죽은 provider 를
   가진 캐시를 넣을 수 있어야 하고, 그 자리가 FastAPI 의 `dependency_overrides` 다.
   전역 싱글턴을 모듈 상수로 두면 그게 불가능해진다.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterator

from fastapi import HTTPException

from daengs_life.realtime.cache import Cache
from daengs_life.realtime.config import KST

logger = logging.getLogger(__name__)


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


# 모델 로드를 **직렬화한다.** `lru_cache` 는 캐시 자체는 스레드 안전하지만 *동시 미스*는 막지
# 않는다 — 예열이 도는 중에 `/ask` 가 들어오면 두 스레드가 각각 1.2GB 를 따로 올린다. 상주 RAM 이
# 이 카드의 유일한 상시 비용이라(D-021) 그 순간의 2배가 서버를 스왑으로 민다.
# 이것이 *"예열은 백그라운드로 돌리되 `/ask` 만 기다리게 한다"* 의 실제 장치다.
_ENCODER_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _encoder() -> Encoder:
    """실제 로드. **밖에서 부르지 않는다** — 락을 안 거치면 위 주석이 무의미해진다."""
    from daengs_life.rag.core import config
    from daengs_life.rag.stages import embed

    key = config.settings.embedding_model_key
    return Encoder(key=key, st=embed.load_model(embed.MODELS[key], device="cpu"))


def get_encoder() -> Encoder:
    """프로세스당 하나. **CPU 에 올린다** (RAG-028 ①).

    호출마다 올렸다 내리면 요청당 5~7초가 붙는다. GPU 를 안 쓰는 것은 성능을 포기한 게 아니라
    **배치와의 VRAM 경합을 없앤 것**이다 — 질의 하나는 CPU 로 124ms 인데 같은 요청 안에서 Gemini 가
    초 단위를 쓰므로 그 74ms 차이는 묻히고, 서버가 2.3GB 를 물고 있으면 2랩에서 새 소스를 임베딩할 때
    6GB 중 3.7GB 만 남는다. device 를 바꿔도 검색 결과가 안 바뀌는 것은 실측으로 확인했다.

    **`ml` 그룹(torch)이 없으면 503 이다** (D-021). 앱은 그래도 떠야 하므로 lifespan 이 예외를
    삼키고 `/ask` 만 503 이 된다 — `Cache` 가 Redis 없이도 앱을 띄우는 것과 같은 태도다.
    500 이 아닌 이유는 **요청이 틀린 게 아니라 환경이 덜 갖춰진 것**이라서고, `services/ask.py` 가
    `GEMINI_API_KEY` 없음을 503 으로 보내는 것과 같은 규칙이다.

    실패를 캐시하지 않는다 — `lru_cache` 는 예외를 담지 않으므로 다음 요청이 다시 시도한다.
    `ml` 이 없으면 ImportError 라 재시도가 싸고, 가중치 다운로드가 끊긴 경우에는 재시도가 옳다.
    """
    with _ENCODER_LOCK:
        try:
            return _encoder()
        except Exception as e:                   # noqa: BLE001 — 무엇이 됐든 요청 잘못이 아니다
            raise HTTPException(
                status_code=503,
                detail=f"임베딩 모델을 올리지 못했다 — {type(e).__name__}: {e}",
            ) from e


def release_encoder() -> None:
    """상주 모델을 놓는다. 두 앱의 lifespan 이 종료 때 부른다.

    `get_encoder.cache_clear()` 가 아닌 이유는 캐시가 `_encoder` 쪽에 있기 때문이다.
    이름을 하나 두는 편이 `_encoder` 를 밖에서 부르게 하는 것보다 낫다.
    """
    _encoder.cache_clear()


def warm_up_encoder() -> None:
    """모델을 미리 올린다. **예외를 던지지 않는다** — 부르는 쪽이 lifespan 이라서다.

    두 앱이 같은 함수를 부른다 (`daengs_backend.main` · `daengs_life.app.main`). 예열을 각자의
    lifespan 에 적지 않고 여기 모으는 이유는, `daengs_backend` 가 `daengs_life.app` **너머**를
    직접 알게 하지 않기 위해서다 (D-018 · RAG-014). 등록 한 줄과 예열 한 줄이 접점의 전부여야
    2단계(별도 프로세스로 분리)가 싼 채로 남는다.
    """
    try:
        encoder = get_encoder()
    except HTTPException as e:
        # `get_encoder` 가 이미 사람이 읽을 문장으로 옮겨 놨다. 예외를 통째로 찍으면
        # "503: 임베딩 모델을 올리지 못했다 …" 처럼 같은 말이 두 번 나온다.
        logger.warning("임베딩 모델을 못 올렸다 — /ask 만 503 이 된다: %s", e.detail)
        return
    except Exception as e:                       # noqa: BLE001 — 예열 실패로 앱을 못 세우면 안 된다
        logger.warning("임베딩 모델을 못 올렸다 — /ask 만 503 이 된다: %s", e)
        return

    logger.info("임베딩 모델 상주: %s", encoder.key)
    warn_if_corpus_uses_another_model(encoder.key)


def warn_if_corpus_uses_another_model(key: str) -> None:
    """서빙 키와 **코퍼스에 실제로 들어 있는** 임베딩 모델을 대조한다.

    **여기가 틀려도 에러가 안 나는 자리다.** 문서 벡터와 질의 벡터가 다른 모델이면 두 벡터가
    다른 공간에 있어 코사인이 무의미해지는데, 세 모델의 차원이 전부 1024 라 예외가 **하나도**
    안 난다 — 그럴듯한 순위가 그냥 나온다. #34 가 `qwen3` 로 적재하고 이 카드가 기본값을 바꾸므로
    둘이 어긋날 창이 실제로 열린다. 그래서 기동 때 한 번 소리를 낸다.

    **막지는 않는다.** `load.existing_models()` 가 적재 전에 보여 주기만 하는 것과 같은 태도다
    (RAG-025 ①) — 교체는 정상 경로이고, 조용한 것만이 문제다.
    """
    from daengs_life.rag.stages import load

    try:
        conn = load.connect()
    except Exception as e:                       # noqa: BLE001 — 대조 실패로 앱을 못 세우면 안 된다
        logger.warning("코퍼스의 임베딩 모델을 확인하지 못했다 (DB 연결): %s", e)
        return
    try:
        rows = load.existing_models(conn)
    except Exception as e:                       # noqa: BLE001 — 위와 같다
        logger.warning("코퍼스의 임베딩 모델을 확인하지 못했다 (조회): %s", e)
        return
    finally:
        conn.close()

    if not rows:
        # 이 카드를 #34 보다 먼저 머지하면 여기로 온다. `/ask` 는 404 만 내지만 **조용히
        # 틀리는 것은 아니다** — 그래서 배포를 막을 이유가 아니라 알려 줄 이유다.
        logger.warning("코퍼스가 비어 있다 — /ask 는 404 `근거를 찾지 못했다` 만 낸다")
        return

    others = [f"{m}({n}건)" for m, n in rows if m != key]
    if others:
        logger.warning(
            "⚠ 임베딩 모델 불일치 — 서빙은 %s 인데 코퍼스는 %s 다. "
            "검색이 **에러 없이** 무의미한 순위를 낸다 — 같은 모델로 다시 적재하거나 "
            "embedding_model_key 를 코퍼스에 맞출 것",
            key, ", ".join(others),
        )
    else:
        logger.info("코퍼스 임베딩 모델 일치: %s (%d건)", key, sum(n for _, n in rows))


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


__all__ = ["Encoder", "get_cache", "get_conn", "get_encoder", "get_now",
           "release_encoder", "warm_up_encoder", "warn_if_corpus_uses_another_model"]
