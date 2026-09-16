"""Walk ownership/state errors; no storage, I/O or sealing imports."""


class WalkNotFoundError(Exception):
    """내 산책이 아니거나 없습니다.

    **남의 것일 때도 이 예외입니다** — 403 으로 나누면 "그 id 는 존재한다"를
    알려 주는 셈입니다 (`services/pet.py` 와 같은 판단).
    """


class WalkStateConflictError(RuntimeError):
    """현재 입력 봉인 상태와 요청이 충돌한다."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
