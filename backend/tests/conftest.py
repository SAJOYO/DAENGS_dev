"""테스트용 암호화 키를 환경 변수에 넣습니다.

config.Settings 는 키가 없으면 뜨지 않으므로, daengs_backend 를 import 하기
**전에** 채워야 합니다. conftest.py 는 테스트 모듈보다 먼저 로드되므로 여기가
그 자리입니다.

setdefault 가 아니라 그냥 덮어씁니다. 환경 변수가 backend/.env 보다 우선이라,
이렇게 해야 개발 PC 에 있는 진짜 키로 테스트가 돌지 않습니다.
"""

import base64
import os

import pytest


def _key(filler: int) -> str:
    """32바이트 고정 키. 테스트용이라 난수일 필요가 없습니다."""
    return base64.urlsafe_b64encode(bytes([filler]) * 32).decode()


# AES 와 blind index 는 서로 다른 값이어야 합니다 — 코드가 둘을 섞어 쓰면
# 테스트가 그냥 통과해 버리는 일이 없도록.
# DB 는 붙지 않습니다. 설정이 뜨는 데 필요한 값만 채웁니다
# (db_host · db_password 는 기본값이 없습니다).
# backend/.env 에 옛 DAENGS_DATABASE_URL 이 남아 있으면 여기서 걸러지지 않고
# 설정 로딩이 실패합니다 — 그건 의도한 동작입니다 (D-013). .env 를 고치세요.
os.environ["DAENGS_DB_HOST"] = "localhost"
os.environ["DAENGS_DB_PASSWORD"] = "test-password"

# 카카오 앱 키 허용 목록. id_token 의 aud 와 대조하는 값이라, 테스트에서는 이 중
# 하나로 서명된 가짜 토큰을 만듭니다 (tests/test_kakao.py).
#
# **둘을 넣습니다.** 하나만 넣으면 "목록 중 아무거나 맞으면 통과"가 실제로 되는지
# 확인할 수 없고, value 하나로 되돌려 놔도 테스트가 그냥 통과합니다.
# 실제 운영에서도 앱(네이티브 키)과 CLI(REST 키)가 함께 들어옵니다.
os.environ["DAENGS_KAKAO_APP_KEYS"] = '["test-native-app-key","test-rest-api-key"]'

os.environ["DAENGS_AES_KEY"] = _key(1)
os.environ["DAENGS_BLIND_INDEX_KEY"] = _key(2)
os.environ["DAENGS_JWE_KEY"] = _key(3)

# `/life/ask` 의 임베딩 모델을 기동 때 올리지 않습니다.
#
# `with TestClient(app)` 로 lifespan 을 여는 테스트가 여럿인데(test_walk_auth · test_ask_auth ·
# test_main_stays_light), 켜 두면 `ml` 그룹이 깔린 PC 에서 그때마다 1.2GB 를 올리고
# 대조하느라 **실서버 DB 에까지 붙습니다.** 테스트는 DB 에 안 붙는다는 이 파일의 약속(위)이
# 그대로 깨지는 자리입니다.
#
# 예열 자체의 동작은 `test_ask_warm_up.py` 가 함수를 직접 불러서 봅니다 — lifespan 을 통해
# 보려 하면 스레드와 취소가 끼어 느리고 불안정해집니다.
os.environ["DAENGS_WARM_UP_ENCODER"] = "false"


@pytest.fixture(autouse=True)
def _no_crawl_runs_writes(request, monkeypatch):
    """**기본값은 "운영 DB 에 안 쓴다"** 다 (RAG-047 · RAG-045 ③ 의 재발 방지).

    `crawl_due` 에 실행 이력을 붙이는 순간, 그 태스크를 부르던 **기존 테스트 전부가** 팀에
    하나뿐인 DB 에 행을 남기게 됐다 — `test_tasks_crawl.py` 가 실제로 `stale`·`fresh`·`law-x`
    같은 가짜 소스로 8행을 남겼고, 그 행들이 관리자 화면에 가짜 실행으로 뜬다.

    RAG-045 ③ 에서 `documents` 로 똑같은 일이 있었고 그때 배운 것은 **"쓰지 않는 쪽이 기본이어야
    한다"** 는 것이다. 테스트를 하나씩 고치면 다음에 `crawl_due` 를 부르는 테스트가 새로 생기는
    날 다시 뚫린다. 그래서 여기서 통째로 막고, 진짜로 DB 가 필요한 테스트만 `db_or_skip` 을
    받아서 **명시적으로 열어** 쓴다 (`test_crawl_runs.py`).

    막는 방법이 예외인 것은 의도다 — `crawl_runs.start()` 는 실패를 삼키고 `None` 을 돌려주므로
    (그 모듈의 계약), 기록만 조용히 빠지고 크롤 태스크의 동작은 그대로다.
    """
    if "db_or_skip" in request.fixturenames:
        return                                  # 명시적으로 DB 를 받은 테스트는 건드리지 않는다

    from daengs_life.tasks import crawl, crawl_runs

    def _blocked():
        raise RuntimeError("테스트는 crawl_runs 에 쓰지 않는다 — 필요하면 db_or_skip 을 받을 것")

    monkeypatch.setattr(crawl_runs, "_connect", _blocked)

    # **브로커도 막는다.** DB 만 막았더니 `crawl_due` 가 fan-out 으로 바뀐 뒤
    # `crawl_source.apply_async` 가 **실서버 Redis 로 나갔다** — 2026-08-30 에 테스트 메시지
    # 3건이 운영 `crawl` 큐에 쌓였고, 워커가 안 떠 있어서 아무도 몰랐다. 떠 있었다면 없는
    # 소스로 3번 시도하고 재시도까지 돌았을 것이다.
    #
    # RAG-047 ⑤ 와 같은 병이 **공유 인프라 한 겹 옆에서** 다시 난 것이다. `documents` →
    # `crawl_runs` → 브로커. 그래서 여기서는 "테스트가 팀 공용 자원에 내보내지 않는다"를
    # 통째로 막는다. 발사를 봐야 하는 테스트는 `apply_async` 를 자기가 monkeypatch 한다
    # (`test_tasks_crawl.py` 의 `dispatched` fixture).
    def _no_dispatch(*_a, **_k):
        raise RuntimeError("테스트는 실서버 브로커로 발사하지 않는다 — apply_async 를 직접 대체할 것")

    monkeypatch.setattr(crawl.crawl_source, "apply_async", _no_dispatch)


@pytest.fixture(autouse=True)
def recorded_metrics(request, monkeypatch):
    """요청 지표를 **DB 대신 리스트에 모은다** (#297). 위 fixture 와 같은 규칙이다.

    `/assistant/query` 가 이제 요청마다 `request_metrics` 에 한 행을 남긴다. 그 공장은
    `SessionLocal` 이라 **테스트에서도 팀에 하나뿐인 DB 를 문다** — 막지 않으면
    `crawl_runs`(RAG-047)와 `documents`(RAG-045 ③)에서 두 번 겪은 일이 세 번째로 난다.
    이번에는 표가 아직 없어서 쓰기가 실패하고 조용히 넘어가지만, 마이그레이션을 개발 DB 에
    적용하는 순간 **테스트를 돌릴 때마다 가짜 지표가 쌓인다.**

    막는 방법이 다른 것은 의도다. 저쪽은 예외를 던져 "쓰려 하면 터지게" 하는데, 여기서는
    **모으기만** 한다 — 지표 쓰기는 실패를 삼키는 것이 계약이라(services/request_metrics.py)
    예외를 던져도 테스트가 아무것도 못 본다. 대신 모아 두면 **무엇을 남기려 했는지**를
    테스트가 그대로 볼 수 있다:

        def test_무엇을_남기나(client, recorded_metrics):
            client.post("/assistant/query", json=...)
            (row,) = recorded_metrics
            assert row["status"] == "ANSWERED"

    진짜 쓰기 경로(`_write` 안쪽)를 보는 테스트는 이 fixture 를 안 받고 리포지토리를
    직접 부른다 (`test_request_metrics.py`).
    """
    from daengs_backend.services import request_metrics

    rows: list[dict] = []

    async def _collect(_factory, **fields):
        rows.append(fields)

    monkeypatch.setattr(request_metrics, "_write", _collect)
    return rows


# ---------------------------------------------------------------- 수집에서 뺄 파일
#
# **`uv run pytest` 를 인자 없이 돌릴 수 있게 하는 자리입니다** (#224).
#
# 아래 둘은 pytest 테스트가 아니라 **직접 돌리는 검사 스크립트**입니다. 모듈 최상단에서
# `sys.exit()` 을 부르는데, 이름이 `test_` 라 pytest 가 import 하고 그 `SystemExit` 이
# **INTERNALERROR 로 수집 전체를 끊습니다.** 한 파일 때문에 스위트가 통째로 안 도는 것이라
# 다른 실패와 성격이 다릅니다 (decisions-rag.md RAG-057 ⑨ "테스트 위생 둘").
#
# ⚠️ **파일을 고치거나 이름을 바꾸지 않습니다.** 둘 다 상류 저장소
# (gayeoniee/deeplearning_test)에서 `tools/sync_screening.py` 가 가져오는 사본이라,
# 여기서 고치면 다음 동기화가 되돌리거나 조용히 갈라집니다. 그래서 **우리 쪽에서 수집만**
# 막습니다. 원본 파일명이 바뀌면 `sync_screening.py` 의 `FILES` 와 이 목록을 같이 고치세요.
#
# 스크립트로서의 용도는 그대로입니다:
#     uv run python tests/test_screening_message.py
#     uv run python tests/test_screening_agent.py     # `--group screening` 필요 (PIL)
collect_ignore = [
    "test_screening_message.py",
    "test_screening_agent.py",
]
