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
os.environ["DAENGS_TRAINING_RAG_BASE_URL"] = "http://training-rag.test"

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

# `/ask` 의 임베딩 모델을 기동 때 올리지 않습니다.
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

    from daengs_life.tasks import crawl_runs

    def _blocked():
        raise RuntimeError("테스트는 crawl_runs 에 쓰지 않는다 — 필요하면 db_or_skip 을 받을 것")

    monkeypatch.setattr(crawl_runs, "_connect", _blocked)
