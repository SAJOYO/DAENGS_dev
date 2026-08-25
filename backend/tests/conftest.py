"""테스트용 암호화 키를 환경 변수에 넣습니다.

config.Settings 는 키가 없으면 뜨지 않으므로, daengs_backend 를 import 하기
**전에** 채워야 합니다. conftest.py 는 테스트 모듈보다 먼저 로드되므로 여기가
그 자리입니다.

setdefault 가 아니라 그냥 덮어씁니다. 환경 변수가 backend/.env 보다 우선이라,
이렇게 해야 개발 PC 에 있는 진짜 키로 테스트가 돌지 않습니다.
"""

import base64
import os


def _key(filler: int) -> str:
    """32바이트 고정 키. 테스트용이라 난수일 필요가 없습니다."""
    return base64.urlsafe_b64encode(bytes([filler]) * 32).decode()


# AES 와 blind index 는 서로 다른 값이어야 합니다 — 코드가 둘을 섞어 쓰면
# 테스트가 그냥 통과해 버리는 일이 없도록.
os.environ["DAENGS_AES_KEY"] = _key(1)
os.environ["DAENGS_BLIND_INDEX_KEY"] = _key(2)
os.environ["DAENGS_JWE_KEY"] = _key(3)
