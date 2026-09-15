"""도감 카드 AI 생성 — 사진 한 장 + 달 + 이름 → 카드 PNG (#496 · #537, docs/cardimage/).

**순수 생성 로직이다.** DB·웹·`daengs_backend` 를 import 하지 않고 설정은 전부 인자로 받는다
(`tests/test_cardimage_boundary.py` 가 지킨다, D-076). 설정을 읽어 엔진을 만들고 이것을 부르는
자리는 backend 의 `services/ai_card_engine.py` 하나다 — 나중에 이 패키지만 별도 서비스로 떼면
그 한 곳이 HTTP 호출로 바뀐다.

흐름은 `generate.py`. 엔진(Nano Banana 2)과 검수(flash-lite)는 Protocol 뒤에 있다 — 테스트는 가짜로.
"""

from daengs_cardimage.generate import (  # noqa: F401
    CardImageUnavailable,
    GeneratedCard,
    generate_card,
)
