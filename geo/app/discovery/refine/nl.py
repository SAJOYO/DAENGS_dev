"""발화 → 툴 호출. LLM이 끼는 유일한 지점.

FakeLLM: 규칙 매칭. 스켈레톤 검증용이지만 실제로도 1차 필터로 쓸 만하다 (LLM 호출 전 싼 경로).
OpenAILLM: function calling. 키 있을 때.
둘 다 반환은 ToolCall 목록뿐 — 병원 정보 생성 금지.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import settings
from app.discovery.refine.tools import TOOL_SPECS
from app.discovery.state import EditableState
from app.usage.gate import UsageGate
from app.usage.models import LanguageParseIntent
from app.usage.registry import usage_gate


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


class LLM(Protocol):
    async def plan(self, utterance: str, state: EditableState, shown_ids: list[int],
                   profile_hint: str) -> list[ToolCall]: ...


# ---------------------------------------------------------------- Fake (규칙)
_ORD = {"첫": 0, "첫번째": 0, "1번": 0, "두": 1, "두번째": 1, "2번": 1, "세": 2, "세번째": 2, "3번": 2,
        "네": 3, "네번째": 3, "4번": 3, "다섯": 4, "5번": 4}

_RULES: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"(너무\s*멀|가까운\s*(데|곳)|좁혀|좀\s*더\s*가까)"), lambda m, s, ids: [ToolCall("narrow")]),
    (re.compile(r"(더\s*넓|멀어도|범위\s*늘|넓혀|없\s*으면\s*멀리)"), lambda m, s, ids: [ToolCall("widen")]),
    (re.compile(r"(\d+)\s*(m|미터)\b"), lambda m, s, ids: [ToolCall("set_radius", {"m": int(m.group(1))})]),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(km|킬로)"), lambda m, s, ids: [ToolCall("set_radius", {"m": int(float(m.group(1)) * 1000)})]),
    (re.compile(r"(지금|당장|바로).*(열|하는|영업|진료)|영업\s*중|문\s*연"), lambda m, s, ids: [ToolCall("set_open_now", {"on": True})]),
    (re.compile(r"야간|밤에|새벽|늦게"), lambda m, s, ids: [ToolCall("set_night_service", {"on": True})]),
    # "응급 병원"은 병원 능력 요구, "급해"는 이번 상황이다. 긴급 상황이 검색·경로·보기에
    # 미치는 영향은 resolver가 한 번에 사영하므로 자연어 층은 사실만 적는다.
    (re.compile(r"응급\s*(병원|진료|센터|실)"),
     lambda m, s, ids: [ToolCall("set_emergency_service", {"on": True})]),
    (re.compile(r"응급|급해|급하|위급"),
     lambda m, s, ids: [ToolCall("set_urgency", {"level": "urgent"})]),
    (re.compile(r"24\s*시"), lambda m, s, ids: [ToolCall("require", {"tags": ["24h"]})]),
    (re.compile(r"큰\s*병원|의료센터|2차|종합"), lambda m, s, ids: [ToolCall("require", {"tags": ["center"]})]),
    # **증상에서 과목을 추론하지 않는다.** "숨을 헐떡여요" → 심장은 진단이고 관할 밖이다
    # (docs/overview.md). 과목 축 자체가 #64 로 없어졌으므로 추론할 대상도 없다.
    # 증상은 **말 그대로** state.symptoms 에 남는다 (note_symptoms) — 지금은 기록까지다.
    (re.compile(r"(절뚝|다리를?\s*(절|저는)|눈이?\s*뿌옇|숨을?\s*헐떡|헐떡거|기침|귀를?\s*긁|피부를?\s*긁|자꾸\s*긁|토했|토를|설사)"),
     lambda m, s, ids: [ToolCall("note_symptoms", {"terms": [m.group(1)]})]),
    (re.compile(r"걸어|도보|산책\s*겸|걷"), lambda m, s, ids: [ToolCall("set_mode", {"mode": "walk"})]),
    (re.compile(r"차로|차\s*타|운전|택시"), lambda m, s, ids: [ToolCall("set_mode", {"mode": "car"})]),
    (re.compile(r"버스|지하철|대중교통"), lambda m, s, ids: [ToolCall("set_mode", {"mode": "transit"})]),
    (re.compile(r"(\d+)\s*분\s*(안|이내|내로|넘지)"), lambda m, s, ids: [ToolCall("set_max_total_min", {"minutes": int(m.group(1))})]),
    (re.compile(r"영업\s*중\s*(먼저|우선)|열린\s*데\s*(먼저|우선)"), lambda m, s, ids: [ToolCall("set_sort", {"sort": "open_first"})]),
    (re.compile(r"가까운\s*순|거리\s*순"), lambda m, s, ids: [ToolCall("set_sort", {"sort": "distance"})]),
]

# 같은 툴이 여러 번 나오면 인자를 합쳐야 하는 것들 (툴 -> 리스트 인자 이름)
ACCUMULATING = {"note_symptoms": "terms", "require": "tags", "unrequire": "tags"}

_EXCL = re.compile(r"(첫번째|두번째|세번째|네번째|다섯번째|[1-5]번|첫|두|세|네|다섯)\s*(번째)?\s*(거|곳|병원|데)?\s*(는|은|말고|빼|제외)")
_HERE = re.compile(r"(여기|거기|이\s*병원|이\s*데)\s*(는|은)?\s*(말고|빼|제외|싫)")


class FakeLLM:
    async def plan(self, utterance: str, state: EditableState, shown_ids: list[int],
                   profile_hint: str) -> list[ToolCall]:
        u = utterance.strip()
        calls: list[ToolCall] = []
        # undo/reset은 단독 — 다른 규칙과 섞이면 의미가 깨진다
        if re.search(r"(초기화|처음부터|다\s*풀어)", u):
            return [ToolCall("reset")]
        if re.search(r"(아까|이전|되돌|취소|다시\s*(전|이전|원래))", u):
            return [ToolCall("undo")]
        m = _EXCL.search(u)
        if m and shown_ids:
            key = m.group(1).replace("번째", "")
            idx = _ORD.get(key, _ORD.get(m.group(1)))
            if idx is not None and idx < len(shown_ids):
                calls.append(ToolCall("exclude", {"ids": [shown_ids[idx]]}))
        elif _HERE.search(u) and shown_ids:
            calls.append(ToolCall("exclude", {"ids": [shown_ids[0]]}))
        for rx, fn in _RULES:
            mm = rx.search(u)
            if mm:
                calls.extend(fn(mm, state, shown_ids))
        # 중복 툴 병합. **누적형은 합친다** — 마지막 것만 남기면 "눈이 뿌옇고 다리도 절뚝"에서
        # ortho가 사라지고, "계단도 지하도도 빼줘"에서 하나가 사라진다.
        merged: dict[str, ToolCall] = {}
        for c in calls:
            key = ACCUMULATING.get(c.tool)
            if key and c.tool in merged:
                prev = merged[c.tool].args.get(key) or []
                merged[c.tool].args[key] = sorted(set(prev) | set(c.args.get(key) or []))
            else:
                merged[c.tool] = ToolCall(c.tool, dict(c.args))
        result = list(merged.values())
        if not result:
            result = [ToolCall("ask", {"question": "어떤 조건을 바꿀까요? 예: 더 가까운 곳, 야간 진료, 계단 없는 길, 정형 잘 보는 곳"})]
        return result


# ---------------------------------------------------------------- OpenAI
class OpenAILLM:
    def __init__(self, api_key: str, model: str):
        self._key, self._model = api_key, model

    async def plan(self, utterance: str, state: EditableState, shown_ids: list[int],
                   profile_hint: str) -> list[ToolCall]:
        import httpx
        tools = [{
            "type": "function",
            "function": {"name": t["name"], "description": t["desc"],
                         "parameters": {"type": "object", "properties": {
                             k: {"type": _jt(v)} for k, v in t["args"].items()}}},
        } for t in TOOL_SPECS]
        sys = ("너는 동물병원 검색 조건 편집기다. 사용자의 말을 툴 호출로만 바꿔라. 병원을 추천하거나 정보를 만들지 마라. "
               "의도가 불명확하면 ask 툴을 써라. 화면 순번(첫번째, 두번째…)은 shown_ids 순서로 id를 골라라. 각 툴에는 scope가 있다. scope=walk 툴은 도보로 갈 때만 의미가 있다. '걸어서 15분 안에' 처럼 도보를 함의하는 말이면 set_mode(walk)를 따로 호출해라 — 안 부르면 수단이 안 바뀐다. 'N분 안에'는 전체 이동시간(set_max_total_min)이지 도보 시간이 아니다.\n"
               f"현재 상태: {json.dumps(state.snapshot(), ensure_ascii=False, default=str)}\n"
               f"shown_ids: {shown_ids}\n프로필 힌트: {profile_hint}")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post("https://api.openai.com/v1/chat/completions",
                             headers={"Authorization": f"Bearer {self._key}"},
                             json={"model": self._model, "messages": [
                                 {"role": "system", "content": sys},
                                 {"role": "user", "content": utterance}],
                                 "tools": tools, "tool_choice": "required"})
            r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        return [ToolCall(tc["function"]["name"], json.loads(tc["function"]["arguments"] or "{}"))
                for tc in msg.get("tool_calls", [])]


class MeteredLLM:
    """실제 LLM 호출만 Usage Gate 뒤에 둔다. 거부를 FakeLLM으로 조용히 바꾸지 않는다."""

    def __init__(self, inner: LLM, gate: UsageGate):
        self._inner = inner
        self._gate = gate

    async def plan(self, utterance: str, state: EditableState, shown_ids: list[int],
                   profile_hint: str) -> list[ToolCall]:
        intent = LanguageParseIntent(input_chars=len(utterance))
        permit = await self._gate.check(intent)
        await self._gate.consume(intent, permit)
        return await self._inner.plan(utterance, state, shown_ids, profile_hint)


def _jt(t: str) -> str:
    t = t.rstrip("?")
    return {"int": "integer", "float": "number", "bool": "boolean", "str": "string"}.get(t, "array" if t.startswith("list") else "string")


def llm() -> LLM:
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return MeteredLLM(
            OpenAILLM(settings.openai_api_key, settings.openai_model),
            usage_gate(),
        )
    return FakeLLM()
