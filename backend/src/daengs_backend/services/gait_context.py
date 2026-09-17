"""보행 기록 두 건 → 해설이 받아도 되는 사실 한 벌 (D-080).

`services/screening_context.py` 와 같은 층이고 같은 규칙입니다 — **소유권을 확인해 읽고,
좁혀서 넘기고, 계약을 실제로 통과시켜 좁힘을 강제**합니다. 나중에 누가 관절 수치나 이름을
얹으려 하면 `GaitCompareContext` 의 `extra="forbid"` 에서 걸립니다.

**다른 점 하나 — 여기는 읽는 게 아니라 계산합니다.** 피부는 판정이 행에 저장돼 있어 읽기만
하면 되지만, 보행 **비교는 저장되지 않습니다**(비교 테이블이 없습니다). 그래서 이 파일은
두 행의 소유를 확인한 뒤 `services/gait.compare_detailed` 로 비교를 그 자리에서 냅니다.
무거운 것은 없습니다 — numpy 만 쓰고 모델·영상·저장소를 하나도 안 만집니다 (D-058).

**못 하면 조용히 비우지 않고 이유를 돌려줍니다.** `screening_context` 는 못 채우면 `{}` 이고
그게 맞았습니다(기록이 없으면 이 기능이 생기기 전과 같이 답하면 됨). 여기는 다릅니다 —
사용자가 **비교 결과 화면에서 "이 변화 물어보기" 를 눌러** 들어왔습니다. 그 요청을 조용히
일반 답이나 "영상을 올려 주세요" HANDOFF 로 넘기면 사용자가 방금 한 행동을 부정하는 답이
나갑니다. 그래서 이유 범주(`GaitUnavailableReason`)를 실어 보내고, 어댑터가 모델을 태우지
않고 고정 문구로 닫습니다.

⚠️ **여기서 "좋아졌다 / 나빠졌다" 를 계산하지 않습니다.** 방향을 말하지 않는 것은 비교
판정 자체의 규칙이고(`daengs_gait.compare.direction_note` — 표본이 작을 때 관절별 비율이
크게 흩어집니다), 이 층은 그 위에 아무것도 더하지 않습니다. 내는 것은 "어느 쪽 다리에서
여러 관절이 함께 달라 보였나 · 얼마나 잴 수 있었나" 까지입니다.

**기록은 구성원(대표 ∪ 돌보미) 기준입니다** — `gait_repo.get_accessible_pair` 가 쿼리
조건으로 묶으므로 "없음" 과 "남의 것" 이 여기서 이미 같은 답입니다.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models.gait_record import GaitRecord
from daengs_backend.orchestration.contracts import GaitCompareContext
from daengs_backend.services import gait as gait_service

__all__ = ["CONTEXT_KEY", "UNAVAILABLE_KEY", "resolve_compare"]

#: `context` 에 싣는 두 키. planner 가 화이트리스트로 읽습니다.
CONTEXT_KEY = "gait_compare"
UNAVAILABLE_KEY = "gait_compare_unavailable"

#: 서버 `side_summary` 의 다리 키 → 계약의 영어 이름. `daengs_gait.compare_v4._side_of` 가
#: 내는 값이고, 좌/우로 안 갈리는 관절 이름은 `전체` 한 덩어리로 옵니다 — 그 모양은 다리별
#: 판정에 못 쓰므로 `legacy_pair` 로 닫습니다.
_SIDES = {"왼쪽": "left", "오른쪽": "right"}

#: 앱 `CHANGED_JOINTS_FOR_LEG` 와 **같은 수**입니다. 서버 `SIDE_MIN_DIFF` 도 같은 2 이고,
#: 7단계가 그 둘을 서버 하나로 모았습니다 — 여기서는 "잰 관절이 몇 개는 돼야 비슷하다고
#: 말할 수 있나" 쪽으로만 씁니다 (`flagged` 는 서버가 이미 계산해 옵니다).
_MIN_MEASURED_PER_SIDE = 2

#: `quality_tier` 가 이것이 아니면 "짧은 영상" 입니다. 비교 함수가 참고용 안내를 붙이는
#: 집합과 **같은 기준**입니다 — 여기서 느슨하게 잡으면 저쪽이 참고용이라고 본 비교에
#: 해설이 "충분" 이라고 도장을 찍습니다.
_GOOD_TIER = "good"

#: 앱이 그리는 한쪽 다리의 관절 수 (`GaitJoint` 는 다리마다 고관절·무릎·뒷발 셋).
#: "여섯 판정 지점 전부" 를 세는 기준이고, 관절 목록이 바뀌면 여기도 같이 바뀌어야 한다 —
#: `tests/test_orchestration_gait_agent.py` 가 그 수를 못 박는다.
_JOINTS_PER_SIDE = 3

#: 전문가 의견 한 줄이 붙으려면 **잰 지점이 이만큼은 있어야** 한다 (#582).
#:
#: 두 개만 재고 둘 다 달라졌을 때 "전부 달라졌다" 라고 부르면 근거가 너무 얇다. 넷은 여섯 중
#: 셋을 넘는 첫 수이고, **한쪽 다리(3)만으로는 못 켜진다**는 뜻이기도 하다 — 한쪽 이야기는
#: `change_kind == "one_side"` 가 이미 한다.
_ADVISORY_MIN_MEASURED = 4

_MAX_DAYS = 3_650


def _unavailable(reason: str) -> dict[str, Any]:
    return {UNAVAILABLE_KEY: {"reason": reason}}


async def resolve_compare(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    recent_record_id: uuid.UUID | str,
    past_record_id: uuid.UUID | str,
    *,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """`context` 에 합칠 조각 하나. **둘 중 하나는 반드시 찹니다.**

    성공하면 `{"gait_compare": {...}}`, 못 하면 `{"gait_compare_unavailable": {"reason": …}}`.
    빈 dict 는 안 냅니다 — 부르는 쪽(`routers/assistant._with_gait_context`)이 이미 "앱이
    비교 참조를 보냈다" 를 확인하고 부르므로, 여기서 조용히 비우면 그 신호가 사라집니다.

    순서가 곧 이유의 우선순위입니다: 모양이 틀린 id · 같은 기록 → 읽기 전에 닫고, 그다음
    소유·개체·모델(비교 함수의 가드) → 품질 → 다리별 판정 가능 여부.
    """
    try:
        recent_id = uuid.UUID(str(recent_record_id))
        past_id = uuid.UUID(str(past_record_id))
    except (ValueError, AttributeError, TypeError):
        # 모양이 틀린 id 는 **없는 기록과 같은 답**입니다 (`screening_context._accessible`).
        return _unavailable("not_found")
    if recent_id == past_id:
        return _unavailable("same_record")

    try:
        result, past, recent = await gait_service.compare_detailed(
            session, app_user_id, recent_id, past_id
        )
    except gait_service.NotFoundError:
        return _unavailable("not_found")
    except gait_service.CompareError as exc:
        # 사람이 읽는 문구가 아니라 **기계용 코드**로 가릅니다 — 문구를 다듬어도 분류가
        # 안 흔들리게 하려는 것이고, 그 코드는 `CompareError` 가 들고 옵니다.
        return _unavailable(getattr(exc, "code", "model_mismatch"))

    if result.get("status") != "ok":
        # 한쪽 영상의 보행 장면이 부족해 비교 함수가 스스로 멈춘 자리입니다.
        return _unavailable("quality")

    sides = _sides_of(result)
    if sides is None:
        # 좌/우로 안 갈리는 관절 이름(옛 기록)이거나 `side_summary` 자체가 없는 비교입니다.
        return _unavailable("legacy_pair")

    context = _narrowed(result, sides, past=past, recent=recent, now=now)
    return {CONTEXT_KEY: context}


def _sides_of(result: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """`side_summary` 를 **다리별 판정에 쓸 수 있을 때만** 돌려줍니다.

    쓸 수 없는 모양이 둘 있습니다. 옛 기록끼리의 비교에는 `side_summary` 가 **아예 없고**
    (`compare.compare_loaded_records` 는 그 값을 안 만듭니다), 관절 이름이 좌/우로 안 갈리면
    `전체` 한 덩어리로 옵니다. 둘 다 "왼쪽이 달라졌다" 를 말할 근거가 없는 자리라, 억지로
    끼우지 않고 못 한다고 말합니다.
    """
    summary = result.get("side_summary")
    if not isinstance(summary, dict) or not summary:
        return None
    if set(summary) - set(_SIDES):
        return None
    return {_SIDES[name]: value for name, value in summary.items() if isinstance(value, dict)}


def _narrowed(
    result: dict[str, Any],
    sides: dict[str, dict[str, Any]],
    *,
    past: GaitRecord,
    recent: GaitRecord,
    now: datetime.datetime | None,
) -> dict[str, Any]:
    """비교 결과 + 두 행 → `GaitCompareContext` 한 벌. **계약을 실제로 통과시킵니다.**

    `dict` 를 손으로 짜서 넘기지 않는 이유는 `screening_context._narrowed` 와 같습니다 —
    좁힘을 강제하는 것이 이 통과 자체입니다.
    """
    left, right = _counts(sides.get("left")), _counts(sides.get("right"))
    flagged = [side for side in ("left", "right") if bool((sides.get(side) or {}).get("flagged"))]
    reliability = _reliability(past=past, recent=recent)
    version_mismatch = result.get("version_warning") is not None
    return GaitCompareContext(
        change_kind=_change_kind(flagged, left, right),
        flagged_sides=flagged,
        left_measured=left[0],
        left_joints=left[1],
        right_measured=right[0],
        right_joints=right[1],
        days_between=_days_between(past, recent, now=now),
        reliability=reliability,
        version_mismatch=version_mismatch,
        expert_advisory=_expert_advisory(
            sides, left, right, reliability=reliability, version_mismatch=version_mismatch
        ),
    ).model_dump()


def _expert_advisory(
    sides: dict[str, dict[str, Any]],
    left: tuple[int, int],
    right: tuple[int, int],
    *,
    reliability: str,
    version_mismatch: bool,
) -> bool:
    """**잰 지점이 전부 달라졌고, 그렇게 볼 근거도 충분한가** (D-080, 조건은 #582 로 완화).

    셋을 다 요구한다:

    1. **잰 지점이 `_ADVISORY_MIN_MEASURED` 개 이상이고, 그 전부가 달라졌다.**
    2. 두 영상 다 보행 장면이 충분했다 (`reliability == "ok"`).
    3. 분석 버전이 같다. 버전이 다르면 같은 영상도 이동범위가 달라 보이므로
       (`compare` 의 경고와 같은 사실), 그 비교로는 "전부 달라졌다" 를 근거로 못 삼는다.

    ## 왜 "여섯 중 여섯" 에서 "잰 것 중 전부" 로 바꿨나 (#582)

    실기기에서 **잰 다섯 지점이 전부 달라졌는데 이 줄이 안 붙었다.** 여섯 번째를 못 쟀기
    때문이다. 옛 조건이 못 잰 지점을 **변화 없음과 똑같이** 취급한 탓인데, 못 잰 것은
    "안 달라졌다" 가 아니라 **"모른다"** 다. 그 구분은 D-063 7단계에서 `n_unmeasured` 를
    만들며 이미 세웠는데 이 조건만 그것을 안 쓰고 있었다.

    ⚠️ **하한이 왜 있나.** 두 개만 재고 둘 다 달라졌을 때 "전부" 라고 부르면 근거가 너무
    얇다. 넷은 여섯 중 셋을 넘는 첫 수이고, **한쪽 다리(3)만으로는 못 켜진다**는 뜻이기도
    하다 — 한쪽 이야기는 `change_kind == "one_side"` 가 이미 하고 있다.

    ⚠️ **색이나 "빨강 몇 개" 로 세지 않는다.** 앱의 3색은 심각도가 아니라 **달라진 축의
    수**다(주황=한 축, 빨강=두 축, `GaitJointChange` 주석: "나쁘다는 뜻이 아니라 두 방향
    모두 달라졌다는 표시"). 빨강을 개수로 세면 그것이 사실상 심각도 점수가 되고 사용자는
    빨강이 많을수록 나쁘다고 읽는다. **"잰 것이 전부 달라졌다" 는 정도가 아니라 범위**라서
    그 선을 넘지 않고, 서버는 이미 잰 수와 달라진 수를 갖고 있어 계약을 넓힐 필요도 없다.

    **켜져도 정도(severity)를 말하는 것이 아니다.** 이 서비스는 진단이 아니고(D-058),
    켜진다고 행동이 바뀌지도 않는다 — 고정 문장 한 줄이 덧붙을 뿐이고 `vet_visit` 는
    v1 에 없다. 느슨하게 할수록 그 한 줄이 흔해지고, 흔해지면 사용자가 그것을
    "나빠졌다는 신호" 로 읽기 시작한다 — 하한을 두는 이유가 그것이다.
    """
    if reliability != "ok" or version_mismatch:
        return False
    measured = left[0] + right[0]
    if measured < _ADVISORY_MIN_MEASURED:
        return False
    changed = sum(_as_int((sides.get(side) or {}).get("n_diff")) for side in ("left", "right"))
    return changed == measured


def _counts(side: dict[str, Any] | None) -> tuple[int, int]:
    """(실제로 잰 수, 비교 대상이 된 관절 수).

    ⚠️ **`n_joints` 를 "화면에 보이는 줄 수" 로 읽으면 틀립니다.** 서버는 두 기록 중 어느
    쪽에든 있는 관절만 세고, 양쪽 다 없는 관절은 아예 안 셉니다 — 앱은 고정 6관절을 늘
    그립니다. 그래서 판정에는 `n_joints` 가 아니라 **잰 수**만 씁니다 (7단계 위험 2).
    """
    if not side:
        return (0, 0)
    joints = _as_int(side.get("n_joints"))
    unmeasured = _as_int(side.get("n_unmeasured"))
    return (max(0, joints - unmeasured), joints)


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _change_kind(flagged: list[str], left: tuple[int, int], right: tuple[int, int]) -> str:
    """앱 `verdictOf` 와 **같은 규칙**입니다 (D-063 7단계).

    변화가 기준을 채운 다리 수로 먼저 가르고, 아니면 "비슷하다고 말할 근거가 있나" 를
    봅니다 — **못 잰 것을 변화 없음 쪽으로 세지 않습니다.** 이 갈래(`not_enough`)가 무너지면
    못 잰 것이 "뚜렷한 차이 없음" 으로 흘러들어 없는 안심을 줍니다.
    """
    if len(flagged) >= 2:
        return "both_sides"
    if flagged:
        return "one_side"
    if left[0] >= _MIN_MEASURED_PER_SIDE and right[0] >= _MIN_MEASURED_PER_SIDE:
        return "no_change"
    return "not_enough"


def _reliability(*, past: GaitRecord, recent: GaitRecord) -> str:
    """어느 쪽 영상이 짧았나. 기준은 비교 함수가 참고용 안내를 붙이는 집합과 같습니다."""
    recent_short = recent.quality_tier != _GOOD_TIER
    past_short = past.quality_tier != _GOOD_TIER
    if recent_short and past_short:
        return "both_short"
    if recent_short:
        return "recent_short"
    if past_short:
        return "past_short"
    return "ok"


def _days_between(past: GaitRecord, recent: GaitRecord, *, now: datetime.datetime | None) -> int:
    """두 기록의 간격(일). 촬영일이 없으면 만든 날로 떨어집니다 — 순서를 정할 때
    `services/gait._order_by_age` 가 쓰는 것과 같은 규칙입니다.

    음수는 내지 않습니다. 순서는 이미 서버가 날짜로 정했지만, 같은 날 기록이나 시계
    어긋남에서 0 이 나오는 편이 음수 간격을 지어내는 것보다 맞습니다.
    """
    del now  # 두 기록 사이의 간격이라 현재 시각이 필요 없습니다 — 시그니처만 맞춥니다.
    days = (_date_of(recent) - _date_of(past)).days
    return max(0, min(days, _MAX_DAYS))


def _date_of(record: GaitRecord) -> datetime.date:
    return record.captured_at or record.created_at.date()
