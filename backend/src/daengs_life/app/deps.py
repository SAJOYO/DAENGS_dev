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
#
# **락은 그대로 두고, 기다리는 방식만 바꿨다** (#37). 예열이 도는 중이라면 `/ask` 는 락에서
# 기다리지 않고 즉시 503 이다 — `hf-cache` 가 빈 첫 배포에서는 로드에 가중치 1.2GB 다운로드가
# 얹혀 nginx 의 `proxy_read_timeout`(60초)을 넘고, 그러면 사용자는 60초를 물고서 **우리가 내지
# 않은** HTML 504 를 받는다 (2026-08-27 실측: 1차 504 at 60.06s · 2차 200 at 22.5s · 3차 1.82s).
# 락을 없애는 것이 아니라 대기를 없애는 것이다 — 두 벌을 막는 일은 여전히 이 락이 한다.
_ENCODER_LOCK = threading.Lock()

# **락을 들고 있는 것이 예열 스레드인가.** `_ENCODER_LOCK.locked()` 로는 그것을 알 수 없어서 둔다.
# 구분이 필요한 이유는 예열이 꺼진 개발 PC(`DAENGS_WARM_UP_ENCODER=false`)다 — 거기서는 아무도
# 예열하지 않으므로 **첫 요청이 로드를 무는 것이 설계**이고, 락을 들고 있는 것은 같은 처지의 다른
# 요청이라 기다리는 편이 맞다. 둘을 "락이 잡혀 있다" 하나로 다루면 그 PC 에서 두 번째 요청이
# 엉뚱하게 503 을 받고, 예열이 꺼져 있는 한 그게 계속된다.
_WARM_UP_IN_PROGRESS = threading.Event()

# 503 에 실어 보내는 재시도 힌트(초). 예열은 캐시가 차 있으면 5~7초, 콜드면 분 단위라 정확한
# 값이 있을 수 없다 — 짧게 주고 프론트가 몇 번 더 묻게 하는 편이 길게 주고 세워 두는 것보다 낫다.
_RETRY_AFTER = "10"


@lru_cache(maxsize=1)
def _encoder() -> Encoder:
    """실제 로드. **밖에서 부르지 않는다** — 락을 안 거치면 위 주석이 무의미해진다."""
    from daengs_life.rag.core import config
    from daengs_life.rag.stages import embed

    key = config.settings.embedding_model_key
    return Encoder(key=key, st=embed.load_model(embed.MODELS[key], device="cpu"))


def _load_encoder(*, wait: bool) -> Encoder:
    """락을 잡고 올린다. `wait=False` 면 락이 잡혀 있을 때 **기다리지 않고** 503 이다.

    실패를 캐시하지 않는다 — `lru_cache` 는 예외를 담지 않으므로 다음 요청이 다시 시도한다.
    `ml` 이 없으면 ImportError 라 재시도가 싸고, 가중치 다운로드가 끊긴 경우에는 재시도가 옳다.
    """
    if not _ENCODER_LOCK.acquire(blocking=wait):
        raise HTTPException(
            status_code=503,
            detail="임베딩 모델을 올리는 중이다 — 잠시 뒤 다시 시도해라",
            headers={"Retry-After": _RETRY_AFTER},
        )
    try:
        return _encoder()
    except Exception as e:                   # noqa: BLE001 — 무엇이 됐든 요청 잘못이 아니다
        raise HTTPException(
            status_code=503,
            detail=f"임베딩 모델을 올리지 못했다 — {type(e).__name__}: {e}",
        ) from e
    finally:
        _ENCODER_LOCK.release()


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

    **예열이 도는 중이면 기다리지 않고 503 이다** (#37). 기다린 대가가 나쁘기 때문이다 — 콜드
    캐시에서는 60초를 물고서 nginx 의 HTML 504 를 받아, 상태 코드도 본문도 우리 손을 떠난다.
    503 + `Retry-After` 면 프론트(`lib/life-rag.ts`)가 *"모델을 올리는 중입니다"* 를 띄우고
    다시 물을 수 있다. `ml` 이 없을 때 `/ask` 만 503 이 되는 규칙과 같은 자리다.

    **예열이 꺼져 있으면 기다린다.** 그때는 아무도 예열하지 않아 첫 요청이 로드를 무는 것이
    설계다 (`_WARM_UP_IN_PROGRESS` 의 주석).
    """
    return _load_encoder(wait=not _WARM_UP_IN_PROGRESS.is_set())


def release_encoder() -> None:
    """상주 모델을 놓는다. 두 앱의 lifespan 이 종료 때 부른다.

    `get_encoder.cache_clear()` 가 아닌 이유는 캐시가 `_encoder` 쪽에 있기 때문이다.
    이름을 하나 두는 편이 `_encoder` 를 밖에서 부르게 하는 것보다 낫다.
    """
    _encoder.cache_clear()


def encoder_loaded() -> bool:
    """지금 모델이 올라와 있나. **올리지 않는다** — 물어보기만 한다 (#180).

    `get_encoder()` 로는 이걸 물을 수 없다. 안 올라와 있으면 **올려 버리기** 때문이다.
    상태 화면이 "모델이 올라왔나"를 묻자고 1.2GB 를 끌어오면 안 된다 —
    `daengs_screening.service.healthz()` 가 `_agent.cache_info()` 로 같은 답을 내는 것과
    같은 이유이고, 그쪽 주석("헬스체크가 350MB 를 끌어오면 안 된다")이 여기에도 그대로다.

    `_encoder` 를 밖에서 부르게 하지 않으려고 이름을 하나 둔다 — `release_encoder` 와 같다.

    **예열의 성패를 부르는 쪽이 알 방법이 이것뿐이다.** `warm_up_encoder()` 는 성공해도
    실패해도 `None` 을 돌려준다(lifespan 이 부르므로 예외를 안 던진다). `daengs_backend`
    쪽에서는 `main.py` 가 예열 뒤 이것을 물어 `app.state` 에 적는다 (#180 · D-035 접점).
    """
    return _encoder.cache_info().currsize > 0


def warm_up_encoder() -> None:
    """모델을 미리 올린다. **예외를 던지지 않는다** — 부르는 쪽이 lifespan 이라서다.

    두 앱이 같은 함수를 부른다 (`daengs_backend.main` · `daengs_life.app.main`). 예열을 각자의
    lifespan 에 적지 않고 여기 모으는 이유는, `daengs_backend` 가 `daengs_life.app` **너머**를
    직접 알게 하지 않기 위해서다 (D-018 · RAG-014). 등록 한 줄과 예열 한 줄이 접점의 전부여야
    2단계(별도 프로세스로 분리)가 싼 채로 남는다.

    **도는 동안 표시를 세운다** — 그것을 보고 `/ask` 가 기다리지 않고 503 을 낸다 (#37).
    `get_encoder` 를 거치지 않고 `_load_encoder(wait=True)` 를 직접 부르는 이유가 그 표시다.
    거쳐 부르면 자기가 세운 표시를 자기가 보게 되어, 다른 요청이 먼저 락을 잡은 순간 **예열이**
    503 으로 물러난다. 예열은 언제나 기다리는 쪽이다.
    """
    _WARM_UP_IN_PROGRESS.set()
    try:
        encoder = _load_encoder(wait=True)
    except HTTPException as e:
        # `_load_encoder` 가 이미 사람이 읽을 문장으로 옮겨 놨다. 예외를 통째로 찍으면
        # "503: 임베딩 모델을 올리지 못했다 …" 처럼 같은 말이 두 번 나온다.
        logger.warning("임베딩 모델을 못 올렸다 — /ask 만 503 이 된다: %s", e.detail)
        return
    except Exception as e:                       # noqa: BLE001 — 예열 실패로 앱을 못 세우면 안 된다
        logger.warning("임베딩 모델을 못 올렸다 — /ask 만 503 이 된다: %s", e)
        return
    finally:
        # 여기서 내려야 한다 — 모델이 캐시에 들어간 뒤이므로 다음 `/ask` 는 락도 안 잡는다.
        # 실패했을 때도 내린다. 그래야 다음 요청이 스스로 다시 시도한다 (그때는 기다리는 쪽이다).
        _WARM_UP_IN_PROGRESS.clear()

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

    ⚠ **비교 대상은 키가 아니라 `repo` 다.** `load.py` 가 `metadata.embedding_model` 에 넣는 것은
    정식 식별자(`Qwen/Qwen3-Embedding-0.6B`)이지 파일명용 키(`qwen3-embedding-0.6b`)가 아니다
    (RAG-008). 키로 비교하면 **정상인데도 매번 불일치 경고가 나고**, 그러면 경고가 무시되기
    시작해서 진짜 불일치까지 묻힌다 — 경고를 다는 목적 자체가 사라진다.
    """
    from daengs_life.rag.stages import embed, load

    model = embed.MODELS.get(key)
    serving = model.repo if model else key

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

    others = [f"{m}({n}건)" for m, n in rows if m != serving]
    if others:
        logger.warning(
            "⚠ 임베딩 모델 불일치 — 서빙은 %s(%s) 인데 코퍼스는 %s 다. "
            "검색이 **에러 없이** 무의미한 순위를 낸다 — 같은 모델로 다시 적재하거나 "
            "EMBEDDING_MODEL_KEY 를 코퍼스에 맞출 것",
            serving, key, ", ".join(others),
        )
    else:
        logger.info("코퍼스 임베딩 모델 일치: %s (%d건)", serving, sum(n for _, n in rows))


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
