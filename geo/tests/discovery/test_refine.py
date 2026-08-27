import inspect
from datetime import UTC, datetime

import pytest

from app.discovery.refine import tools
from app.discovery.refine.diff import changes
from app.discovery.refine.engine import draft, refine
from app.discovery.refine.nl import FakeLLM, ToolCall
from app.discovery.state import EditableState
from app.profile.source import PERSONAS

S = EditableState(lat=37.4979, lng=127.0276)


def test_tools_are_pure_and_leave_history_alone():
    """툴은 편집만 한다. 되돌림 지점은 턴 경계(refine)가 찍는다."""
    s2 = tools.narrow(S)
    assert S.target.radius_m == 2000 and s2.target.radius_m == 1000
    assert s2.history == [], "툴이 스택을 건드렸다 — 되돌림 지점은 턴당 하나여야 한다"


def test_checkpoint_restores_the_whole_turn():
    s2 = tools.checkpoint(S, tools.narrow(S))
    assert len(s2.history) == 1
    s3 = tools.undo(s2)
    assert s3.target.radius_m == 2000 and s3.history == []


def test_undo_on_empty_is_noop():
    assert tools.undo(S) is S


# ------------------------------------------------------------------ undo 의 단위 = 턴
async def _turn(state, utterance=None, edits=(), profile=None):
    r = await refine(state, [ToolCall(t, a) for t, a in edits], utterance, [],
                     profile, 37.4979, 127.0276, 2000)
    return r.state


async def test_one_utterance_is_one_undo_step():
    """"걸어서 15분 안에" 는 툴 2개(set_mode + set_max_total_min)다. 되돌림은 한 번이어야 한다.

    툴마다 찍으면 undo 1회가 "도보는 유지, 시간 제한만 취소" — 사용자가 말한 적 없는 상태다.
    예전 예제였던 "계단은 빼줘" 는 #66 으로 축이 사라졌다.
    """
    before = await _turn(None)
    after = await _turn(before, "걸어서 15분 안에")
    assert after.journey.preferred_mode == "walk" and after.journey.max_total_min == 15
    assert len(after.history) == 1, "한 마디가 스택 두 칸을 먹었다"

    back = tools.undo(after)
    assert back.journey.max_total_min is None and back.journey.preferred_mode is None


async def test_stack_depth_counts_turns_not_tools():
    s = await _turn(None)
    for utterance in ("걸어서 15분 안에", "밤에 갈 수 있는 곳", "너무 멀어"):
        s = await _turn(s, utterance)
    assert len(s.history) == 3


async def test_undo_turn_does_not_leave_its_own_step():
    """undo 가 자기 되돌림 지점을 찍으면 그걸 도로 팝해서 아무 일도 안 일어난다."""
    s = await _turn(await _turn(None), "너무 멀어")
    narrowed = s.target.radius_m
    undone = await _turn(s, "아까로 되돌려")
    assert undone.target.radius_m == 2000 != narrowed
    assert undone.history == []


async def test_turn_that_changes_nothing_keeps_the_stack():
    """이미 거리순인데 "가까운 순으로" — 빈 칸이 10칸짜리 스택을 먹으면 안 된다."""
    s = await _turn(await _turn(None), "너무 멀어")
    same = await _turn(s, "가까운 순으로")
    assert same.sort == "distance" and len(same.history) == len(s.history)


async def test_reset_is_undoable():
    s = await _turn(await _turn(None), "걸어서 15분 안에")
    cleared = await _turn(s, "다 풀어줘")
    assert cleared.journey.max_total_min is None
    assert tools.undo(cleared).journey.max_total_min == 15


async def test_origin_refresh_is_not_undone():
    """GPS 갱신은 조건 편집이 아니다 — 되돌림 지점에 새 좌표가 들어가 있어야 한다."""
    s = await _turn(None)
    moved = await refine(s, [], "너무 멀어", [], None, 37.5100, 127.0400, 2000)
    assert tools.undo(moved.state).lat == 37.5100


def test_walk_scoped_tool_never_sets_the_mode():
    """도보 하위 설정은 **수단(preferred_mode)을 절대 안 세운다.**

    #66 이후 walk scope 에 남은 툴은 `set_walk_max_min` 하나다. 계층 규칙은 그대로다 —
    도보를 함의하는 말이면 자연어 층이 set_mode 를 따로 낸다.
    """
    s = tools.set_walk_max_min(S, 10)
    assert s.journey.walk.max_walk_min == 10
    assert s.journey.preferred_mode is None, "하위 설정이 상위(수단)를 몰래 세웠다"


def test_walk_settings_survive_switching_to_car():
    """차량으로 바꿔도 도보 설정은 남는다 — 도보 대안에 계속 쓰이고, 돌아오면 살아 있어야 한다."""
    s = tools.set_walk_max_min(S, 10)
    s = tools.set_mode(s, "car")
    assert s.journey.preferred_mode == "car"
    assert s.journey.walk.max_walk_min == 10


def test_total_and_walk_time_limits_are_separate():
    """'차로 10분'과 '노견 도보 10분'은 다른 값이다."""
    s = tools.set_max_total_min(S, 30)
    s = tools.set_walk_max_min(s, 10)
    assert s.journey.max_total_min == 30 and s.journey.walk.max_walk_min == 10
    assert tools.set_max_total_min(s, 45).journey.walk.max_walk_min == 10


def test_time_limit_does_not_imply_a_mode():
    """'15분 안에'는 수단을 함의하지 않는다 — 차로 갈 수도 있다."""
    assert tools.set_max_total_min(S, 15).journey.preferred_mode is None


def test_set_mode_switches_sort_to_duration():
    assert tools.set_mode(S, "car").sort == "duration"


def test_diff_lists_every_change():
    s = tools.set_night_service(tools.narrow(S))
    s = tools.exclude(s, [3, 4])
    c = changes(S, s)
    assert "반경 2km → 1km" in c and "야간 표방 우선" in c and "2곳 제외" in c


def test_diff_draft():
    assert changes(None, S)[0].startswith("초안")


@pytest.mark.parametrize("utt,tool,args", [
    ("너무 멀어", "narrow", {}),
    ("좀 더 넓게 봐줘", "widen", {}),
    ("500m 안에서만", "set_radius", {"m": 500}),
    ("지금 열린 데", "set_open_now", {"on": True}),
    ("밤에 갈 수 있는 곳", "set_night_service", {"on": True}),
    ("급해요 지금 당장", "set_urgency", {"level": "urgent"}),
    ("15분 안에 갈 수 있는 데", "set_max_total_min", {"minutes": 15}),
    ("차로 갈게", "set_mode", {"mode": "car"}),
    ("아까대로", "undo", {}),
    ("24시 하는 데", "require", {"tags": ["24h"]}),
])
async def test_fake_llm_rules(utt, tool, args):
    plan = await FakeLLM().plan(utt, S, [], "")
    names = {c.tool: c for c in plan}
    assert tool in names, plan
    for k, v in args.items():
        assert names[tool].args[k] == v


async def test_fake_llm_ordinal_exclude_uses_shown_ids():
    plan = await FakeLLM().plan("두번째 거는 빼줘", S, [11, 22, 33], "")
    assert ToolCall("exclude", {"ids": [22]}) in plan


async def test_fake_llm_asks_when_clueless():
    plan = await FakeLLM().plan("음...", S, [], "")
    assert plan[0].tool == "ask"


def test_draft_sets_no_condition_from_the_profile():
    """초안은 반경만 세운다. 프로필에서 유래한 기본값은 #66 으로 없앴다.

    노령·관절견에 `no_stairs` 를 깔던 것이 유일한 프로필 유래 기본값이었고, 그건 보이는 것
    없이 경로를 3배로 늘렸다 (28분 → 84분).
    """
    s = draft(37.5, 127.0, 2000)
    assert s.target.radius_m == 2000
    assert s.target.open_now is False and s.target.require_tags == []
    assert s.journey == EditableState(lat=37.5, lng=127.0).journey, "초안이 이동 설정을 세웠다"

    # 프로필은 아예 인자가 아니다 — 어느 개로 시작하든 초안이 갈릴 여지가 없다
    assert "profile" not in inspect.signature(draft).parameters


async def test_refine_edits_then_utterance():
    r = await refine(None, [ToolCall("set_radius", {"m": 800})], "야간에 하는 데",
                     [], PERSONAS["dubu"], 37.5, 127.0, 2000)
    assert r.state.target.radius_m == 800 and r.state.target.night_service is True
    assert r.question is None
    assert any("야간" in c for c in r.changes)


async def test_refine_question_leaves_state():
    r = await refine(S, [], "글쎄", [], None, S.lat, S.lng, 2000)
    assert r.question and r.state.snapshot() == S.snapshot()


# --- 정책 경계 (state.py) --------------------------------------------------
def test_every_tool_belongs_to_exactly_one_policy():
    from app.discovery.refine.tools import (
        CONTEXT_TOOLS,
        JOURNEY_TOOLS,
        TARGET_TOOLS,
        TOOLS,
        VIEW_TOOLS,
    )
    groups = [set(CONTEXT_TOOLS), set(TARGET_TOOLS), set(JOURNEY_TOOLS), set(VIEW_TOOLS)]
    assert set().union(*groups) == set(TOOLS)                  # 빠진 툴 없음
    for i, a in enumerate(groups):                             # 겹치는 툴 없음
        for b in groups[i + 1:]:
            assert not (a & b)


def test_context_tools_touch_neither_target_nor_journey():
    """상황은 **입력**이지 정책이 아니다.

    '급하다'가 target 을 직접 건드리면 journey 는 그걸 영영 못 본다 — `emergency` 가
    정확히 그 상태였다. 사영은 resolver 한 곳에서만 일어나야 하고, 툴은 사실만 적는다.
    """
    from app.discovery.refine.tools import CONTEXT_TOOLS
    args = {"set_urgency": {"level": "urgent"},
            "set_time_intent": {"kind": "service_at", "at": datetime(2026, 8, 20, 15, 0, tzinfo=UTC)}}
    for name, fn in CONTEXT_TOOLS.items():
        out = fn(S, **args[name])
        assert out.target == S.target, f"{name} 이 target 을 건드렸다"
        assert out.journey == S.journey, f"{name} 이 journey 를 건드렸다"


def test_reset_clears_filters_but_keeps_the_situation():
    """필터 초기화가 사실까지 지우면 안 된다 — 지금 급한 건 필터를 푼다고 안 달라진다."""
    s = tools.set_urgency(tools.set_night_service(S), "urgent")
    s = tools.set_time_intent(s, "depart_at", datetime(2026, 8, 20, 21, 0, tzinfo=UTC))
    r = tools.reset(s)
    assert r.target.night_service is False
    assert r.urgency == "urgent" and r.time_intent is not None


def test_tool_specs_cover_all_tools_with_policy():
    from app.discovery.refine.tools import TOOL_SPECS, TOOLS, policy_of
    spec_names = {t["name"] for t in TOOL_SPECS} - {"ask"}
    assert spec_names == set(TOOLS)
    assert all(t["policy"] == policy_of(t["name"]) for t in TOOL_SPECS if t["name"] != "ask")


def test_journey_tools_never_touch_target():
    from app.discovery.refine.tools import JOURNEY_TOOLS
    args = {"set_mode": {"mode": "car"}, "set_max_total_min": {"minutes": 10},
            "set_walk_max_min": {"minutes": 10}}
    assert set(args) == set(JOURNEY_TOOLS), "툴이 늘거나 줄었는데 이 표가 안 따라왔다"
    for name, fn in JOURNEY_TOOLS.items():
        out = fn(S, **args[name])
        assert out.target == S.target, f"{name} 이 target 을 건드렸다"


def test_target_tools_never_touch_journey():
    from app.discovery.refine.tools import TARGET_TOOLS
    args = {"set_origin": {"lat": 37.6, "lng": 127.1}, "set_radius": {"m": 500}, "widen": {},
            "narrow": {}, "set_open_now": {}, "set_night_service": {},
            "set_emergency_service": {},
            "note_symptoms": {"terms": ["눈이 뿌옇"]}, "clear_symptoms": {},
            "require": {"tags": ["24h"]}, "unrequire": {"tags": ["24h"]},
            "exclude": {"ids": [1]}, "pin": {"ids": [2]}}
    for name, fn in TARGET_TOOLS.items():
        out = fn(S, **args[name])
        assert out.journey == S.journey, f"{name} 이 journey 를 건드렸다"


def test_diff_groups_by_policy():
    from app.discovery.refine.diff import changes_by_policy
    s = tools.set_walk_max_min(tools.narrow(S), 10)
    g = changes_by_policy(S, s)
    assert any("반경" in c for c in g["target"])
    assert any("도보" in c for c in g["journey"])
    assert all("도보" not in c for c in g["target"]), "이동 설정이 target 으로 샜다"


def test_max_total_min_is_advice_only_unless_hard():
    s = tools.set_max_total_min(S, 10)
    assert s.journey.max_total_min == 10 and s.journey.hard_limit is False
    assert tools.set_max_total_min(S, 10, hard=True).journey.hard_limit is True


def test_every_tool_has_a_scope_and_walk_tools_are_marked():
    """scope 는 policy 와 직교하는 축. 도보 전용 툴만 walk 여야 한다."""
    assert {n for n in tools.TOOLS if tools.scope_of(n) == "walk"} == set(tools.WALK_TOOLS)
    for spec in tools.TOOL_SPECS:
        assert spec.get("scope") in ("any", "walk"), spec["name"]
        if spec["name"] in tools.TOOLS:
            assert spec["scope"] == tools.scope_of(spec["name"]), spec["name"]


def test_nl_emits_mode_explicitly_instead_of_letting_a_tool_set_it():
    """수단은 **자연어 층이 따로 낸다.** 툴이 몰래 세우면 applied/diff 에 안 보여서
    사용자가 왜 도보가 됐는지 모른다.

    예전 예제('계단 없는 길로' → set_mode + set_walk_avoid)는 #66 으로 축이 사라졌다.
    """
    import asyncio

    from app.discovery.refine.nl import FakeLLM
    plan = asyncio.run(FakeLLM().plan("걸어서 15분 안에", S, [], ""))
    assert [c.tool for c in plan] == ["set_mode", "set_max_total_min"]


def test_nl_merges_accumulating_tools_instead_of_dropping():
    """같은 툴이 두 번 나오면 인자를 합친다. 마지막 것만 남기면 조건이 조용히 사라진다.

    "24시"와 "큰 병원"은 각각 다른 규칙인데 둘 다 `require` 를 낸다. 마지막 것만 남기면
    사용자가 말한 조건 하나가 조용히 사라진다.
    """
    import asyncio

    from app.discovery.refine.nl import FakeLLM
    plan = asyncio.run(FakeLLM().plan("24시 하는 큰 병원", S, [], ""))
    tags = next(c.args["tags"] for c in plan if c.tool == "require")
    assert set(tags) == {"24h", "center"}, tags


def test_symptoms_stay_verbatim_and_translate_to_nothing():
    """증상은 진단이 아니다. "숨을 헐떡여요" → 심장은 관할 밖이고, 과목 축 자체가 없다 (#64).

    말은 `note_symptoms` 로 원문 그대로만 남고, 어떤 필터·부스트도 만들지 않는다.
    """
    import asyncio

    from app.discovery.refine.nl import FakeLLM
    for utt in ("숨을 헐떡여요", "뒷다리를 절뚝거려요", "눈이 뿌옇게 됐어요", "자꾸 긁어요"):
        plan = asyncio.run(FakeLLM().plan(utt, S, [], ""))
        assert [c.tool for c in plan] == ["note_symptoms"], f"{utt} -> {plan}"
