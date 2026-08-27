"""refine: (state | None, edits, utterance) → (new state, changes, question?)

순서: 초안 생성(반경만) → UI edits 적용 → utterance를 LLM이 툴로 → 적용 → 되돌림 지점 → diff.

**undo 의 단위는 턴이다.** 한 마디가 툴을 몇 개 부르든 되돌림 지점은 하나만 찍는다 —
"걸어서 15분 안에"는 set_mode(walk) + set_max_total_min(15) 두 개인데, 툴마다 찍으면 undo 한 번이
"도보는 유지, 시간 제한만 취소"라는 **사용자가 말한 적 없는 중간 상태**를 만든다. 스택 10칸도
4~5턴이면 찬다.
"""

from dataclasses import dataclass, field

from app.discovery.refine import tools
from app.discovery.refine.diff import changes as diff_changes
from app.discovery.refine.diff import changes_by_policy
from app.discovery.refine.nl import ToolCall, llm
from app.discovery.state import EditableState
from app.profile.contract import DogProfile


@dataclass
class RefineResult:
    state: EditableState
    changes: list[str]
    grouped: dict[str, list[str]] = field(default_factory=dict)   # 정책별 (target/journey/view)
    applied: list[ToolCall] = field(default_factory=list)
    question: str | None = None


def draft(lat: float, lng: float, radius_m: int) -> EditableState:
    """초안. **필터도 기본값도 없다.**

    노령·관절견에 `no_stairs` 를 깔던 것은 조사 판단 3 대로 없앴다 (#66) — 계단제외는
    보이는 것 없이 경로를 3배로 늘렸고(28분 → 84분), 그 트레이드오프를 말한 적이 없다.
    그게 프로필을 읽던 유일한 자리였으므로 `profile` 인자도 같이 뺐다 — 다음 사람이
    "여기 프로필 배선이 살아 있다" 고 읽지 않게.
    """
    s = EditableState(lat=lat, lng=lng)
    s.target.radius_m = radius_m
    return s


def profile_hint(p: DogProfile | None) -> str:
    if not p:
        return "없음"
    bits = [f"{p.name}", f"{p.age_years}세", p.size_class]
    if p.is_senior: bits.append("노령")
    if p.has_joint_issue: bits.append("관절")
    if p.is_brachy: bits.append("단두종")
    if p.has_car is False: bits.append("차 없음")
    return ", ".join(bits)


async def refine(state: EditableState | None, edits: list[ToolCall], utterance: str | None,
                 shown_ids: list[int], profile: DogProfile | None,
                 lat: float, lng: float, default_radius: int) -> RefineResult:
    if state is None:
        base = draft(lat, lng, default_radius)
    else:
        # origin은 매 요청의 현재 위치다. 클라이언트 state보다 새 요청 값을 우선한다.
        # history에는 넣지 않는다 — GPS 갱신은 사용자가 undo할 조건 편집이 아니다.
        base = state.model_copy(deep=True)
        base.lat, base.lng = lat, lng
    cur = base
    applied: list[ToolCall] = []
    question = None

    for e in edits:
        cur = tools.apply(cur, e.tool, e.args)
        applied.append(e)

    if utterance and utterance.strip():
        plan = await llm().plan(utterance, cur, shown_ids, profile_hint(profile))
        for c in plan:
            if c.tool == "ask":
                question = c.args.get("question")
                continue
            cur = tools.apply(cur, c.tool, c.args)
            applied.append(c)

    cur = _checkpoint_turn(base, cur, applied)

    grouped = changes_by_policy(base, cur)
    ch = diff_changes(base, cur)
    if state is None:
        ch = diff_changes(None, base) + ([] if ch == ["변경 없음"] else ch)
        grouped["target"] = changes_by_policy(None, base)["target"] + grouped["target"]
    return RefineResult(state=cur, changes=ch, grouped=grouped, applied=applied, question=question)


def _checkpoint_turn(base: EditableState, cur: EditableState,
                     applied: list[ToolCall]) -> EditableState:
    """이 턴의 되돌림 지점을 남길지 정한다.

    안 남기는 두 경우:
      undo 가 낀 턴  스택을 소비하는 턴이다. 지점을 찍으면 자기가 도로 팝한다
      상태가 그대로  "가까운 순으로"를 이미 거리순인데 또 말한 턴. 빈 칸이 스택을 먹으면 안 된다

    origin(GPS 갱신)은 이미 base 에 반영된 뒤라 지점에 새 좌표가 들어간다 — 위치는 undo 대상이
    아니라는 뜻이고, 그게 맞다.
    """
    if any(c.tool in tools.STACK_TOOLS for c in applied):
        return cur
    if cur.snapshot() == base.snapshot():
        return cur
    return tools.checkpoint(base, cur)
