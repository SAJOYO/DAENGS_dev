"""앱 사용자 AI 카드 생성 한도 (#537 · #543 · #572, D-076 · D-077 · D-084).

**제품 규칙입니다** (사용자 결정 2026-09-15, #572 에서 D-084 로 개정). 부르는 쪽(`services/ai_card.py`)은
세 예외만 압니다.

- 사용자별 **동시 1요청** — `AiCardBusyError` (409 `already_generating`). 한 요청의 행들이 `pick_group`
  하나를 공유합니다. 행 수는 엔진이 정합니다(#572 Task 8): Nano Banana 2 경로(지금 운영)는 한 장,
  `FLUX.2-klein-4B` GPU 경로는 `cardimage_pick_count` 장.
- **강아지마다 달마다 한 장**, 보호자마다 따로 — 같은 `dog_id`·`month` 의 `ready`/`generating` 카드가
  있으면 `AiCardMonthTakenError` (409 `month_taken`). 그 카드를 지우면 그 달은 다시 열립니다.
  `dog_id` 가 없으면 보지 않습니다.
- KST **하루 N회** (`DAENGS_CARDIMAGE_DAILY_LIMIT`, 기본 1) — `AiCardLimitError` (429 `limit_reached`).
  **세는 단위는 카드 장수가 아니라 요청(뽑기) 한 번입니다.** `ai_card_usage` 의 사용 기록
  (`unfulfilled_attempt = false`)으로 셉니다. 한 요청에서 **닮음이 `cardimage_judge_min` 이상인 카드가
  처음 `ready` 가 될 때** 딱 한 줄 남습니다. 검수 점수가 없는 카드(검수 장애)는 기준을 넘은 것으로
  봅니다 — 장애가 공짜 무한 생성이 되면 안 됩니다. 카드를 지워도 횟수는 돌아오지 않습니다.

**돈 나간 시도 상한 — KST 하루 `MAX_PAID_FAILURES_PER_DAY`(5) 요청** (하루 한도가 0 이어도 적용).
세는 것은 `ai_card_usage` 의 **시도 표시**(`unfulfilled_attempt = true`) 줄 수 **하나뿐**입니다.

- 표시는 요청의 첫 카드가 슬롯을 잡는 트랜잭션(`services/ai_card.py::_claim_slot`)에서, **유료 호출
  (엔진·검수)보다 먼저** 요청마다 한 줄(`card_id = pick_group`) 남습니다. 슬롯을 못 잡은 요청(대기 중에
  지워졌거나 정리됐다)은 엔진을 안 부르고 표시도 안 남깁니다.
- 기준 이상 카드가 나오면 `_finish_ready` 가 같은 트랜잭션에서 표시를 지우고 사용 기록을 남깁니다.
- 그래서 표시가 남는 요청은 **유료 호출까지 가서 좋은 카드를 못 얻은 요청 전부**입니다 — 닮음 미달
  (사진 각도가 나쁘면 몇 번을 뽑아도 안 구해져서(#557 E2 엎드린 옆모습 0장) 하루 한도를 안 쓰게
  했다), 호출 실패, 생성 중 삭제, 배포 재시작과 겹친 중단. 카드를 지워도 표시는 남습니다 — 카드
  행(`ai_cards`)으로 셌다면 생성 중에 지우는 것만으로 셈이 사라져 시작→삭제를 끝없이 되풀이할 수 있었습니다.

그래서 KST 하루에 유료 호출까지 가는 요청은 **좋은 뽑기 `DAENGS_CARDIMAGE_DAILY_LIMIT` 번 + 좋은 카드를
못 얻은 요청 최대 5번**이고, 요청 하나가 부르는 엔진은 최대 두 번입니다 — Nano Banana 2 경로는 한 장에
재시도 한 번, GPU 경로는 `cardimage_pick_count`(최대 2) 장에 재시도 없음(#572 Task 8). 한도
검사를 통과한 요청이 슬롯을 잡기 전에는 표시가 없지만, 그 요청이 살아 있는 동안은 동시 1요청이 다음
요청을 막고, 지워지면 엔진을 안 부르므로 이 셈을 넘지 않습니다. 전체 지출의 바닥은 카드 생성 키의
별도 GCP 프로젝트 지출 상한입니다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.repositories import ai_card as ai_card_repo
from daengs_backend.services import ai_card_engine

KST = ZoneInfo("Asia/Seoul")

# 유료 호출까지 가서 좋은 카드를 못 얻은 요청(시도 표시)의 KST 하루 상한. 설정값으로 빼지 않습니다.
# 이름은 옛 판(실패한 카드 행을 셌다)에서 왔다 — #572(D-084)부터는 시도 표시만 센다.
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
    재시도까지 하는 경우(2 × 2 × timeout)다. 그 재시도는 Nano Banana 2 경로(지금 운영)에서 **한 행
    안에서** 일어난다(#572 Task 8 — 그 경로는 seed 없이 한 장을 부르므로 `generate_card` 가 닮음 미달이면
    한 번 더 만든다). 슬롯은 행마다 한 번만 잡으므로 이 예산이 두 시도를 통째로 덮어야 한다. 그보다 짧으면 정상 진행 중인 작업을 실패로
    덮으므로 1분을 더 둔다. **세마포어를 기다리는 대기열 시간은 이 예산 밖이다** —
    `services/ai_card.py::_claim_slot` 이 슬롯을 잡고 돈이 나가는 호출(엔진) 직전에 행을
    다시 보아 `updated_at` 을 그 시각으로 찍으므로, 대기 중에 지워지거나 이미 정리된 행은
    애초에 엔진을 부르지 않는다.

    **GPU 경로(`cardgen_url`)가 켜져 있으면 `cardgen_timeout_s` 를 통째로 더한다** (#572, D-084).
    FLUX.2-klein-4B 는 콜드 스타트만 340.7~460초다(#557 E1: 옛 이미지로 다시 기동 403.1초 ·
    이미지 교체 뒤 첫 기동 374.9초 · E3: 기준 리비전으로 되돌려 재측정 340.7초 — 권장 FUSE 옵션
    둘 다 설정으로는 못 줄였고 오히려 735.6초로 느려지거나 기동 실패했다, #572 12달 실험 460초).
    `cardimage_timeout_ms` 는 그것을 모른다. 엔진 호출의 상한은 그때 `cardgen_timeout_s`(콜드 스타트
    포함, `ai_card_engine.default_engine`)이므로 그 값을 그대로 더한다 — 이 예산이 콜드 스타트보다
    짧으면 멀쩡히 도는 카드가 사라진 것으로 정리된다. **꺼져 있으면(지금 운영, Nano Banana 2) 예산은
    그대로다** — 늘리면 죽은 작업이 그만큼 오래 `generating` 으로 남는다. 켜짐의 판단은
    `default_engine`·장수와 같은 함수 `ai_card_engine.gpu_path_active` 로 한다(#572 Task 8).
    """
    budget = timedelta(milliseconds=4 * settings.cardimage_timeout_ms) + timedelta(seconds=60)
    if ai_card_engine.gpu_path_active():
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
    # `daily_limit == 0`(무제한)이어도 적용합니다 — 돈 나간 시도는 하루 한도와 따로 셉니다.
    # 카드 행(failed 등)은 보지 않습니다 — 지울 수 있어서 셈이 사라집니다. 지울 수 없는 표시만 셉니다.
    if await ai_card_repo.count_attempt_marks_since(session, app_user_id, day_start) >= MAX_PAID_FAILURES_PER_DAY:
        raise AiCardLimitError


async def daily_remaining(
    session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime, daily_limit: int
) -> int | None:
    """오늘 남은 횟수. 무제한(`daily_limit == 0`)이면 `None`. 앱이 「오늘 1번 남았어요」를 띄웁니다.

    돈 나간 시도 상한은 여기 반영하지 않습니다 — 그것은 안전장치라 앱에 숫자로 보이지 않습니다.
    """
    if not daily_limit:
        return None
    used = await ai_card_repo.count_usage_since(session, app_user_id, kst_day_start(now))
    return max(0, daily_limit - used)
