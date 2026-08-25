"""로그인 실패 횟수 제한. `(login_id, IP)` 쌍으로 5회 실패 시 10분 잠급니다.

Argon2id 가 느려서 어느 정도는 막히지만, 자동화된 스크립트가 흔한 비밀번호 목록을
밤새 돌리면 결국 뚫립니다. 관리자 계정이라 하나 뚫리면 개인정보 복호화까지 열립니다.

**계정 단위로 세지 않는 이유**: 계정 하나를 팀이 공유하고 있어서, 계정으로 세면
한 명이 5번 틀릴 때 팀 전체가 잠깁니다. 아이디만 아는 사람은 누구나 일부러 잠글 수도
있습니다. IP 를 섞으면 잠기는 것은 그 IP 하나뿐입니다.

**IP 를 믿을 수 있는 근거**는 backend 가 포트를 열지 않는다는 것입니다 (D-005).
nginx 를 통해서만 들어오므로 X-Real-IP 를 아무나 위조할 수 없습니다.
routers 가 그 헤더를 읽어 넘겨 주고, 여기서는 문자열 하나로만 봅니다.

**상태는 프로세스 메모리입니다.** DB 에 두면 admin_users 에 컬럼이 늘고 서버에서
ALTER TABLE 을 손으로 돌려야 합니다. backend 는 단일 프로세스라 지금은 이걸로 됩니다.
**재시작하면 카운터가 풀리는 것이 한계**이고, 개발 중에는 리로드마다 풀립니다.
워커를 늘리거나 잠금이 재시작을 견뎌야 할 때 다시 봅니다.
"""

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

__all__ = [
    "FAILURE_LIMIT",
    "LOCKOUT",
    "LockedOutError",
    "check_not_locked",
    "record_failure",
    "record_success",
    "reset_all",
]

# 연속 실패 몇 번에 잠글지. 사람이 오타로 다섯 번 틀리기는 쉽지 않고,
# 스크립트에게는 다섯 번이 아무것도 아닙니다.
FAILURE_LIMIT = 5

# 잠기는 시간이자 실패 기록이 잊히는 시간입니다.
# 사흘에 걸쳐 네 번 틀린 사람이 다섯 번째에 잠기면 안 되므로, 이 시간이 지나면
# 카운터를 0부터 다시 셉니다.
LOCKOUT = timedelta(minutes=10)

# 이 수를 넘으면 지나간 기록을 쓸어냅니다.
# 아이디나 IP 를 바꿔 가며 두드리면 키가 계속 늘어나 메모리를 먹습니다.
_SWEEP_THRESHOLD = 1024


class LockedOutError(Exception):
    """잠긴 상태입니다. 라우터는 401 이 아니라 **429** 로 돌려줍니다.

    401 로 주면 비밀번호가 맞는 사람이 계속 401 만 보면서 영문을 모릅니다.
    다만 메시지에 계정 존재 여부는 넣지 마세요.
    """

    def __init__(self, retry_after: timedelta) -> None:
        self.retry_after = retry_after
        super().__init__("로그인 시도가 너무 많습니다.")


@dataclass
class _Attempt:
    count: int = 0
    #: 처음 실패한 시각. 여기서 LOCKOUT 이 지나면 카운터를 다시 셉니다.
    first_failed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    locked_until: datetime | None = None

    def is_stale(self, now: datetime) -> bool:
        """더 들고 있을 이유가 없는 기록인지."""
        if self.locked_until is not None:
            return now >= self.locked_until
        return now - self.first_failed_at >= LOCKOUT


# 키는 (login_id, ip) 입니다.
_attempts: dict[tuple[str, str], _Attempt] = {}

# uvicorn 은 요청을 스레드풀로도 돌립니다. dict 자체는 GIL 로 안전하지만
# "읽고 → 판단하고 → 쓰는" 사이가 갈라지면 카운터가 샙니다.
_lock = threading.Lock()


def _sweep(now: datetime) -> None:
    """지나간 기록을 버립니다. 호출하는 쪽이 _lock 을 잡고 있어야 합니다."""
    for key in [k for k, v in _attempts.items() if v.is_stale(now)]:
        del _attempts[key]


def check_not_locked(login_id: str, ip: str) -> None:
    """잠겨 있으면 LockedOutError 를 냅니다.

    **계정이 있는지 확인하기 전에 부르세요.** 없는 아이디로 두드릴 때도 똑같이
    잠겨야 합니다 — 안 그러면 잠기는지 여부로 계정 존재를 알아낼 수 있습니다.
    """
    now = datetime.now(UTC)
    with _lock:
        attempt = _attempts.get((login_id, ip))
        if attempt is None or attempt.locked_until is None:
            return
        if now >= attempt.locked_until:
            # 잠금이 풀렸습니다. 기록을 지우고 0부터 다시 셉니다.
            del _attempts[(login_id, ip)]
            return
        raise LockedOutError(attempt.locked_until - now)


def record_failure(login_id: str, ip: str) -> None:
    """실패를 한 번 셉니다. FAILURE_LIMIT 에 닿으면 잠급니다.

    **비밀번호가 틀렸을 때뿐 아니라 아이디가 없을 때도 부르세요.** 한쪽만 세면
    응답 속도나 잠금 여부로 계정 존재가 드러납니다.
    """
    now = datetime.now(UTC)
    with _lock:
        if len(_attempts) >= _SWEEP_THRESHOLD:
            _sweep(now)

        attempt = _attempts.get((login_id, ip))
        if attempt is None or now - attempt.first_failed_at >= LOCKOUT:
            # 처음이거나, 마지막 실패로부터 충분히 지나서 잊힌 경우입니다.
            attempt = _Attempt(count=0, first_failed_at=now)
            _attempts[(login_id, ip)] = attempt

        attempt.count += 1
        if attempt.count >= FAILURE_LIMIT:
            attempt.locked_until = now + LOCKOUT


def record_success(login_id: str, ip: str) -> None:
    """로그인에 성공했으니 기록을 지웁니다.

    **잠긴 상태에서는 여기까지 올 수 없습니다** — check_not_locked 가 먼저 막습니다.
    그래서 잠금이 성공으로 풀리지는 않습니다.
    """
    with _lock:
        _attempts.pop((login_id, ip), None)


def reset_all() -> None:
    """전부 지웁니다. **테스트 전용입니다.**

    운영 코드에서 부르면 잠금이 통째로 풀립니다.
    """
    with _lock:
        _attempts.clear()
