"""앱 사용자 AI 카드 생성 한도 (#537 · #543 · #572, D-076 · D-077 · D-084).

**제품 규칙입니다** (사용자 결정 2026-09-15, #572 에서 D-084 로 개정). 부르는 쪽(`services/ai_card.py`)은
세 예외만 압니다.

- 사용자별 **동시 1요청** — `AiCardBusyError` (409 `already_generating`). 한 요청은 카드 여러 장
  (`cardimage_pick_count`)이고 그 행들이 `pick_group` 하나를 공유합니다.
- **강아지마다 달마다 한 장**, 보호자마다 따로 — 같은 `dog_id`·`month` 의 `ready`/`generating` 카드가
  있으면 `AiCardMonthTakenError` (409 `month_taken`). 그 카드를 지우면 그 달은 다시 열립니다.
  `dog_id` 가 없으면 보지 않습니다.
- KST **하루 N회** (`DAENGS_CARDIMAGE_DAILY_LIMIT`, 기본 1) — `AiCardLimitError` (429 `limit_reached`).
  **세는 단위는 카드 장수가 아니라 요청(뽑기) 한 번입니다.** `ai_card_usage` 의 사용 기록
  (`below_judge_min = false`)으로 셉니다. 한 요청에서 **닮음이 `cardimage_judge_min` 이상인 카드가
  처음 `ready` 가 될 때** 딱 한 줄 남습니다. 검수 점수가 없는 카드(검수 장애)는 기준을 넘은 것으로
  봅니다 — 장애가 공짜 무한 생성이 되면 안 됩니다. 카드를 지워도 횟수는 돌아오지 않고, 실패한
  카드는 기록이 없어 세지 않습니다.

**돈 나간 헛시도 상한 — 하루 `MAX_PAID_FAILURES_PER_DAY` 번** (하루 한도가 0 이어도 적용).
하루 한도가 세지 않는데 돈은 나간 것 두 가지를 **합쳐서** 셉니다.

- **실패한 유료 호출** — `failed` 이고 `error_code` 가 `PAID_FAILURE_CODES` 인 **카드 행 수**. #572 부터
  취소된 형제 카드는 엔진을 안 부르고(`_claim_slot`) 이 코드를 받지 않으므로, 행 수가 곧 **실패한
  유료 호출 수**입니다. 한 요청에서 두 장이 실패하면 돈이 두 번 나갔으니 둘로 셉니다.
- **닮음 미달 요청** — 카드는 `ready` 가 됐지만 한 장도 기준에 못 미친 요청. 사진 각도가 나쁘면 몇
  번을 뽑아도 안 구해지므로(#557 E2 엎드린 옆모습 0장) 하루 한도를 안 쓰게 했는데, 그 카드는
  `failed` 가 아니라 위 셈에 안 걸립니다 — 막지 않으면 GPU·검수를 끝없이 부를 수 있습니다. 그래서
  `ai_card_usage` 에 **요청마다 한 줄** 미달 표시(`below_judge_min = true`)를 남기고 그것을 셉니다.
  카드 행으로 세지 않는 이유는 같은 강아지·같은 달을 다시 뽑으려면 그 카드를 지워야 해서입니다.

그래서 KST 하루에 사용자가 받는 것은 **좋은 뽑기 N번 + 헛시도(미달 요청·실패한 유료 호출) 합쳐 최대
5번**입니다. 전체 지출의 바닥은 카드 생성 키의 별도 GCP 프로젝트 지출 상한입니다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.repositories import ai_card as ai_card_repo

KST = ZoneInfo("Asia/Seoul")

# 모델 호출까지 가서(돈이 나간 뒤) 실패한 코드와 헛시도 하루 상한. 설정값으로 빼지 않습니다.
# `interrupted`·`internal`·`unavailable` 은 세지 않습니다. 상한에는 닮음 미달 요청도 함께 듭니다.
PAID_FAILURE_CODES = frozenset({"upstream", "no_image", "storage"})
MAX_PAID_FAILURES_PER_DAY = 5


class AiCardBusyError(Exception):
    """이미 만들고 있는 카드가 있습니다. 라우터가 409 `already_generating` 으로 바꿉니다."""


class AiCardMonthTakenError(Exception):
    """이 강아지의 이 달 카드가 이미 있습니다. 라우터가 409 `month_taken` 으로 바꿉니다."""


class AiCardLimitError(Exception):
    """오늘 한도를 다 썼습니다. 라우터가 429 `limit_reached` 로 바꿉니다."""


def stale_after() -> timedelta:
    """`generating` 을 사라진 작업으로 볼 기준. **그 요청(`pick_group`)이 마지막으로 움직인 시각부터** 잰다
    (`repositories/ai_card.py::expire_generating`).

    한 건의 최악은 슬롯을 잡은 뒤 엔진·검수가 각각 `cardimage_timeout_ms` 를 다 쓰고
    재시도까지 하는 경우(2 × 2 × timeout)다. 그보다 짧으면 정상 진행 중인 작업을 실패로
    덮으므로 1분을 더 둔다. **세마포어를 기다리는 대기열 시간은 이 예산 밖이다** —
    `services/ai_card.py::_claim_slot` 이 슬롯을 잡고 돈이 나가는 호출(엔진) 직전에 행을
    다시 보아 `updated_at` 을 그 시각으로 찍으므로, 대기 중에 지워지거나 이미 정리된 행은
    애초에 엔진을 부르지 않는다.

    **GPU 경로(`cardgen_url`)가 켜져 있으면 `cardgen_timeout_s` 를 통째로 더한다** (#572, D-084).
    FLUX.2-klein-4B 는 콜드 스타트만 6~7분이고(#557 E3 — 설정으로 못 줄였다, #572 12달 실험 460초)
    `cardimage_timeout_ms` 는 그것을 모른다. 엔진 호출의 상한은 그때 `cardgen_timeout_s`(콜드 스타트
    포함, `ai_card_engine.default_engine`)이므로 그 값을 그대로 더한다 — 이 예산이 콜드 스타트보다
    짧으면 멀쩡히 도는 카드가 사라진 것으로 정리된다. **꺼져 있으면(지금 운영, Nano Banana 2) 예산은
    그대로다** — 늘리면 죽은 작업이 그만큼 오래 `generating` 으로 남는다. 켜짐의 판단은
    `default_engine` 과 같게 `strip()` 한 값으로 한다.
    """
    budget = timedelta(milliseconds=4 * settings.cardimage_timeout_ms) + timedelta(seconds=60)
    if settings.cardgen_url.strip():
        budget += timedelta(seconds=settings.cardgen_timeout_s)
    return budget


def kst_day_start(now: datetime) -> datetime:
    return now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)


async def check_quota(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    *,
    now: datetime,
    daily_limit: int,
    dog_id: uuid.UUID | None,
    month: int,
) -> None:
    """돈이 나가기 전에 부릅니다. `dog_id`·`month` 는 **키워드 필수**입니다 — 빠뜨려서 달별 검사가
    조용히 꺼지면 안 됩니다."""
    await ai_card_repo.expire_generating(
        session, app_user_id, stale_before=now - stale_after(), now=now
    )
    if await ai_card_repo.has_generating(session, app_user_id):
        raise AiCardBusyError
    # 하루 한도보다 먼저 — 내일 다시 해도 안 되는 이유이기 때문입니다.
    if dog_id is not None and await ai_card_repo.has_month_card(session, app_user_id, dog_id, month):
        raise AiCardMonthTakenError
    day_start = kst_day_start(now)
    if daily_limit and await ai_card_repo.count_usage_since(session, app_user_id, day_start) >= daily_limit:
        raise AiCardLimitError
    # `daily_limit == 0`(무제한)이어도 적용합니다 — 헛시도는 하루 한도와 따로 셉니다.
    paid_failures = await ai_card_repo.count_failed_since(session, app_user_id, day_start, PAID_FAILURE_CODES)
    below_min_requests = await ai_card_repo.count_below_judge_min_since(session, app_user_id, day_start)
    if paid_failures + below_min_requests >= MAX_PAID_FAILURES_PER_DAY:
        raise AiCardLimitError


async def daily_remaining(
    session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime, daily_limit: int
) -> int | None:
    """오늘 남은 횟수. 무제한(`daily_limit == 0`)이면 `None`. 앱이 「오늘 1번 남았어요」를 띄웁니다.

    헛시도 상한은 여기 반영하지 않습니다 — 그것은 안전장치라 앱에 숫자로 보이지 않습니다.
    """
    if not daily_limit:
        return None
    used = await ai_card_repo.count_usage_since(session, app_user_id, kst_day_start(now))
    return max(0, daily_limit - used)
