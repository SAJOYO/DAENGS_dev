"""`POST /hospital/search` 의 순위 계약. 부품이 아니라 **요청 하나가 통과하는 길**을 본다.

**왜 필요한가**: `find_places` 도 `_sort` 도 각자 멀쩡했는데, 그 사이에서 부스트 재료가
순위를 state 밖의 값으로 흔들었다. 함수별 테스트 97개가 전부 통과했다. 계약은 함수가
아니라 요청 단위로 써야 잡힌다. 그때의 재료(커뮤니티 근거)는 결정 #63 으로 없앴지만,
부스트가 요청 경로 어딘가에서 섞일 여지는 남아 있으므로 이 계약은 그대로 지킨다.

계약: **state 가 같으면 결과 순서도 같다.** 무상태 서버(클라가 state 를 되돌려준다)의
전제이고, 이게 깨지면 사용자는 조건을 안 건드렸는데 목록이 재배열되는 걸 본다.

단언은 **심은 행만 추려서** 한다. TEST_ORIGIN 반경 안에 공공데이터 실행이 들어와 있어
(울릉도) 전체 목록으로 비교하면 적재 상태에 따라 테스트가 흔들린다. 상대 순서만 보면
남의 행이 사이에 끼어도 계약은 그대로 검증된다.
"""

from app.discovery.state import EditableState
from app.features.hospital.api import HospitalSearchIn, hospital_search
from tests.conftest import TEST_ORIGIN, place_row, seeded_places

# 같은 500m 밴드 안의 두 곳. 밴드가 같아야 boost 가 순서를 뒤집을 수 있어서 —
# 밴드가 다르면 거리가 이기므로 이 회귀를 아예 못 본다.
NEAR, FAR = "가까운동물병원", "조금먼동물병원"
ROWS = [
    place_row("r1", NEAR, east_m=200),
    place_row("r2", FAR, east_m=400),
]

# 규칙에 하나도 안 걸리는 발화 → FakeLLM 이 ask 만 낸다 → **state 는 그대로**.
# 그런데 예전엔 이것만으로 부스트 재료 조회가 켜져서 순위가 바뀌었다.
NO_OP_UTTERANCE = "음 글쎄요"


def _state() -> EditableState:
    return EditableState(lat=TEST_ORIGIN[0], lng=TEST_ORIGIN[1])


async def _search(db, state: EditableState | None = None, **kw):
    transport = kw.pop("transport", "none")
    return await hospital_search(
        HospitalSearchIn(state=state or _state(), transport=transport, **kw), db,
    )


def _ours(out, names: tuple[str, ...]) -> list[str]:
    """심은 행만 원래 순서대로. 반경 안 실데이터는 계약과 무관하다."""
    return [r.name for r in out.results if r.name in names]


async def test_utterance_presence_does_not_reorder_results():
    """같은 state, 발화만 있고 없고 → 순서가 같아야 한다.

    발화는 state 를 바꾸지 않는다(ask). 그러니 순서가 달라지면 그건 state 밖의
    일회성 값이 순위에 들어갔다는 뜻이다.
    """
    async with seeded_places(ROWS) as db:
        quiet = _ours(await _search(db), (NEAR, FAR))
        spoken = _ours(await _search(db, utterance=NO_OP_UTTERANCE), (NEAR, FAR))
    assert quiet == spoken == [NEAR, FAR]


async def test_prefer_tags_still_boost_within_band():
    """부스트 기계 자체는 살아 있다 — 사용자가 요청한 선호만 (결정 #20).

    과목 축은 #64 로 없앴다. 남은 선호 어휘는 영업 형태(야간·24시·응급)다.
    """
    night = "24시동물병원"
    rows = [place_row("r1", NEAR, east_m=200),
            place_row("r2", night, east_m=400, tags=["24h"])]
    async with seeded_places(rows) as db:
        st = _state()
        st.target.night_service = True
        out = await _search(db, state=st)
    assert _ours(out, (NEAR, night)) == [night, NEAR]


async def test_hospital_request_uses_resolved_journey_and_view_plans():
    """state를 API가 직접 주워 쓰지 않고 resolver 결정이 응답까지 관통한다."""
    async with seeded_places(ROWS) as db:
        state = _state()
        state.urgency = "urgent"
        state.journey.preferred_mode = "walk"
        out = await _search(db, state=state, transport="estimate")

    assert out.show_call_cta is True
    assert out.call_reasons == ["user"]
    assert out.results
    assert all(r.transport.mode_priority[0] == "car" for r in out.results if r.transport)
    overridden = {entry.overrode for entry in out.resolution if entry.overrode}
    assert {"journey.preferred_mode", "sort"} <= overridden
