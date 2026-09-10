"""세 축 판정기 — **축마다 보는 것이 다르다** (#401).

────────────────────────────────────────────────────────────────────────────
왜 판정기를 셋으로 가르나
────────────────────────────────────────────────────────────────────────────
한 번 불러서 세 점수를 받는 편이 싸다. 그런데 그렇게 하면 **한 축의 입력이 다른 축의
판정을 물들인다.** 실제로 갈라 놓은 자리가 셋이다:

  모드 판정기에 프로필을 주면   근거 없는 개인화를 «그럴듯하다»고 합리화한다. 이 판정기가
                            물어야 하는 것은 "이 상황에 맞는 모드를 골랐나" 하나뿐인데,
                            프로필이 있으면 "답이 그럴듯한가"로 미끄러진다
  이어짐 판정기에 정답 모드를 주면  정확도 채점기가 된다 — 우리 라벨과 맞는지를 보게 되고,
                            그러면 라벨이 곧 채점 기준이라 판정기가 잴 것이 남지 않는다
  복구 판정기에 상태를 주면    "그래도 견종에 맞는 말은 했다"로 정정 실패를 덮는다.
                            이 축이 묻는 것은 **행동이 바뀌었나** 하나다

`expected_mode` 는 우리 정답지라 **어느 프롬프트에도 안 들어간다.** `user_input_needed` 는
다르다 — 그것은 라벨이 아니라 상황의 사실이고, "안 물었다"가 실패인지 아닌지는 그 사실
없이는 정할 수 없다. 그래서 그것 하나만 넘긴다 (`repair_applicable` · `note` 는 안 넘긴다).

────────────────────────────────────────────────────────────────────────────
오늘 두 축의 정답은 0 이다 — 그래도 정직하게 묻는다
────────────────────────────────────────────────────────────────────────────
`transcript.PRIOR_TURNS_REACH_INFERENCE` 가 False 다. 앞 턴이 추론에 안 닿으므로 오늘
런타임의 모든 행에서 `context_continuity` · `repair_success` 의 옳은 점수는 0 이다.
**그것을 프롬프트에 적지 않고 코드로 건너뛰지도 않는다.** 첫 랩이 이 두 판정기의
위양성 시험이 되는 것이 이 설계의 값이다 — 0 이 아닌 판정이 나오면 그것은 발견이 아니라
**판정기의 오류**이고, 그 사실은 물어봐야만 알 수 있다.

앞 턴은 **케이스 대본**에서 온다(드라이버가 실어 보낸 것이 아니라). 사용자가 실제로 겪은
대화가 그것이라, 런타임이 그것을 안 실어 보낸 것 자체가 이 카드가 재려는 실패다.

────────────────────────────────────────────────────────────────────────────
judge 위생 — `daengs_life/rag/stages/judge.py` · `training_quality/judge.py` 를 잇는다
────────────────────────────────────────────────────────────────────────────
  계열 분리     생성 Gemini → judge **OpenAI**. 같은 계열은 자기 계열의 문장을 후하게 본다
  모델 핀       `gpt-5.4-2026-03-05` (`settings.openai_judge_model`). 움직이는 별칭이면 두
              랩의 차이가 답변 때문인지 judge 때문인지 안 갈린다
  결정성        `temperature=0`. 채점자가 비결정적이면 랩 대조가 통째로 무의미해진다
  버전 고정     `PROMPT_VERSION` 과 모델명을 판정 파일 헤더에 적는다. 프롬프트를 고치면
              번호를 올리고 **앵커를 다시 통과해야 한다**
  판정 순서     `observations` → `rationale` → `score`. **칸 순서가 곧 생성 순서**라, 점수를
              먼저 두면 모델이 판정을 정해 놓고 이유를 붙인다
  앵커 게이트   앵커 통과 기록이 없으면 `run_score` 가 안 돈다. judge 를 믿을 근거가 앵커뿐이다
  앵커 비노출   앵커 케이스 본문을 프롬프트에 예시로 박지 않는다 — 그 케이스에서만 잘 맞는
              채점자가 된다 (`RAG-066` 의 *"값을 보인 낱말만 넣는다"* 와 같은 조심)

⚠ **이 점수는 지표가 아니다** (D-060 ⑦ · RAG-075). 하는 일은 채점이 아니라 사람이 볼
자리를 고르는 것이다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from daengs_evals.conversation_quality import ASSETS_DIR
from daengs_evals.conversation_quality.cases import ConversationCase
from daengs_evals.conversation_quality.drivers import NOT_REACHED
from daengs_evals.conversation_quality.rubric import AxisScores, applicability

VERSION = 1  # 판정 파일 스키마
PROMPT_VERSION = 1  # 프롬프트. **고치면 올리고 앵커를 다시 통과한다**

RUBRIC = "conversation_quality"

#: 부르는 순서다. `rubric.AxisScores` 의 칸 순서와 같게 둔다 — 두 곳이 갈리면 판정 파일을
#: 읽는 사람이 어느 쪽 순서로 읽어야 하는지 모른다.
AXES: tuple[str, ...] = ("response_mode_fit", "context_continuity", "repair_success")

#: 앵커 통과 기록의 파일 이름. **Task 7(`anchors.py`)이 이 규약을 그대로 써서 파일을 만든다** —
#: 만드는 쪽과 읽는 쪽이 갈리면 게이트가 조용히 열린 채로 남는다. 모델 · 앵커 세트 ·
#: 프롬프트 버전 셋 중 하나만 바뀌어도 다른 파일이 되는 것이 요점이다: 셋 중 무엇이 바뀌든
#: 판정기는 다른 물건이라 앵커를 다시 통과해야 한다.
ANCHOR_RECORD_TEMPLATE = "anchor_check_{anchor_set}_{prompt_version}_{model}.json"


def anchor_record_name(*, anchor_set: str, model: str, prompt_version: int = PROMPT_VERSION) -> str:
    """`anchor_check_<앵커세트>_<프롬프트버전>_<모델>.json`.

    모델 이름의 `/` 는 `_` 로 바꾼다 — provider 에 따라 `org/model` 꼴이 오는데 그대로
    쓰면 없는 하위 디렉터리를 가리킨다.
    """
    return ANCHOR_RECORD_TEMPLATE.format(
        anchor_set=anchor_set,
        prompt_version=prompt_version,
        model=model.replace("/", "_").replace("\\", "_"),
    )


def require_anchor_pass(
    anchor_dir: Path | None,
    *,
    anchor_set: str,
    model: str,
    prompt_version: int = PROMPT_VERSION,
) -> dict[str, Any]:
    """앵커 통과 기록이 없으면 **여기서 멈춘다**.

    judge 를 믿을 근거가 앵커뿐이라 게이트를 코드로 든다 (`training_quality` 의 같은 자리).
    기록이 있어도 `passed` 가 참이 아니면 통과가 아니다 — 돌려 보기만 하고 넘어가는 것을
    막는다.
    """
    directory = Path(anchor_dir) if anchor_dir is not None else ASSETS_DIR
    path = directory / anchor_record_name(
        anchor_set=anchor_set, model=model, prompt_version=prompt_version
    )
    if not path.exists():
        raise SystemExit(
            f"앵커 통과 기록이 없습니다: {path}\n"
            f"  먼저 앵커 검사를 돌리세요 — judge 를 믿을 근거는 앵커뿐입니다. "
            f"모델 · 앵커 세트 · 프롬프트 버전(v{prompt_version}) 중 하나라도 바뀌면 "
            "다시 통과해야 합니다."
        )
    record = json.loads(path.read_text("utf-8"))
    if not record.get("passed"):
        raise SystemExit(f"앵커가 통과하지 못한 기록입니다: {path}")
    return record


# ---------------------------------------------------------------------------
# 프롬프트 — 축마다 따로. 앵커 · 케이스 본문을 예시로 박지 않는다.
# ---------------------------------------------------------------------------
#
# ⚠ 정적 지시문이 **맨 앞**이고 대화가 뒤다 (`training_quality/judge.py` 와 같은 배치).
#   문항마다 달라지는 부분을 뒤로 몰아야 프롬프트 캐시의 접두사가 산다.

PROMPT_RESPONSE_MODE_FIT = """너는 반려견 상담 대화의 채점자다.

이 축이 묻는 것은 **정보 상황에 맞는 상호작용 모드를 골랐나** 하나뿐이다. 어시스턴트가
할 수 있는 것은 셋이다 — 지금 아는 것으로 답한다, 모자란 것을 되묻는다, 여기서 다룰 수
없다고 다른 곳으로 보낸다. 이 상황에서 그 셋 중 맞는 것을 골랐는지만 본다.

[입력이 더 필요했나]가 주어진다. **그 값이 «아니오»면 되묻지 않은 것을 감점하지 마라** —
물을 것이 없는데 되물으면 그것이 오히려 사용자를 붙잡는 실패다.

판정 기준:
- 되묻기가 성공하려면 **사용자가 바로 답할 수 있는 작고 구체적인 질문 하나**여야 한다.
  관찰할 항목을 늘어놓는 것은 질문이 아니다 — 사용자는 그중 무엇을 답해야 할지 모른다.
- 다른 곳으로 보내기가 성공하려면 **사용자가 다음에 할 수 있는 것**이 남아야 한다.
  다룰 수 없다는 말만 하고 끝나면 대화가 거기서 막힌다.
- 답하기가 성공하려면 그 답이 **지금 이 질문**을 향해야 한다. 물은 것과 주제만 겹치는
  일반론은 답한 것이 아니다.
- **내용이 사실인지는 묻지 않는다.** 틀린 말이어도 모드는 맞을 수 있고, 옳은 말이어도
  모드는 틀릴 수 있다. 사실 확인은 다른 자가 잰다.
- **말투 · 길이 · 공손함은 채점하지 마라.**
- 앞 턴도 사용자 프로필도 주지 않았다. **없는 것을 짐작해서 채점하지 마라** — 아래 발화와
  아래 답변만으로 판정한다.

점수:
  2  모드가 맞고, 사용자가 다음에 할 일이 답변 안에서 성립한다
  1  모드는 맞는데 절반만 됐다 — 되물었지만 답할 수 없는 큰 질문이거나, 답했지만 정작
     물은 대목을 얼버무렸거나, 다른 곳으로 보냈지만 갈 곳을 안 알려 줬다
  0  모드가 틀렸다 — 입력이 필요한데 안 묻고 일반론으로 닫았거나, 필요 없는데 되물어
     사용자를 붙잡았거나, 여기서 답할 수 있는 것을 통째로 돌려보냈다

먼저 [관찰]에 아래 글에서 **그대로 옮긴 짧은 인용**을 적어라 — 네가 판정의 근거로 삼은
대목이다. 그 다음 [근거], 마지막에 [점수]. **관찰에 없는 것을 근거로 삼지 마라.**

[사용자 발화]
{user_utterance}

[입력이 더 필요했나]
{input_was_needed}

[답변]
{answer}
"""

PROMPT_CONTEXT_CONTINUITY = """너는 반려견 상담 대화의 채점자다.

이 축이 묻는 것은 **답변이 앞에서 오간 것과 주어진 상태를 실제로 썼나** 하나뿐이다.

판정 기준:
- 앞 턴에 이미 나온 것을 **없던 일처럼** 다시 묻거나 그대로 되풀이하면 이어지지 않은 것이다.
- 상태를 «썼다»고 하려면 그 값이 **답을 바꿔야 한다.** 견종이나 나이를 한 번 부르고 나머지가
  누구에게나 할 수 있는 일반론이면 쓴 것이 아니다 — 이름표만 붙인 것이다.
- [주어진 상태]에 없는 개인 사정을 아는 척하면 감점한다. **지어낸 개인화는 안 쓴 것보다
  나쁘다** — 사용자가 그것을 사실로 읽는다.
- [주어진 상태]가 비었거나 «공급되지 않음»이면 **상태를 안 썼다고 감점하지 마라.** 그때는
  앞 턴만 보고 판정한다. 앞 턴이 없으면 상태만 보고 판정한다.
- **답이 사실인지, 답할 자리였는지 되물을 자리였는지는 묻지 않는다.** 다른 자가 잰다.
- **말투 · 길이 · 공손함은 채점하지 마라.**

점수:
  2  앞 턴과 주어진 상태가 답을 실제로 바꿨다
  1  절반만 — 한쪽만 반영했거나, 언급은 했는데 이름표에 그쳤다
  0  앞 턴도 상태도 없었던 것처럼 답했다

먼저 [관찰]에 아래 글에서 **그대로 옮긴 짧은 인용**을 적어라 — 앞 턴의 어느 대목과 답변의
어느 대목을 견줬는지 보이게. 그 다음 [근거], 마지막에 [점수]. **관찰에 없는 것을 근거로
삼지 마라.**

[앞 턴]
{prior_turns}

[지금 사용자 발화]
{current_utterance}

[주어진 상태]
{state_supplied}

[답변]
{answer}
"""

PROMPT_REPAIR_SUCCESS = """너는 반려견 상담 대화의 채점자다.

사용자가 앞 답변을 **바로잡거나 같은 요청을 되풀이하거나 항의했다.** 이 축이 묻는 것은
그 다음 턴에서 **행동이 바뀌었나** 하나뿐이다.

판정 기준:
- 채점하는 것은 **행동의 변화**다. 사과는 행동이 아니다 — «죄송합니다» 뒤에 같은 답이 다시
  나오면 0 이다.
- **표현만 바꿔 쓴 것도 변화가 아니다.** 문장이 달라도 실질이 앞 답변과 같으면 0 이다.
- 사용자가 바로잡은 **그 대목**을 집어서 다뤘는지 본다. 관계없는 유용한 말을 더한 것은 이
  축의 성공이 아니다.
- 사용자 프로필은 주지 않았다. **개인화 여부는 이 축이 묻지 않는다.**
- **답이 사실인지, 말투가 어떤지는 묻지 않는다.**

점수:
  2  바로잡은 것을 반영해 다른 행동을 했다 — 다르게 되묻거나, 오해를 고쳐 답했다
  1  일부만 — 여럿을 바로잡았는데 하나만 반영했거나, 방향만 틀고 앞 답을 그대로 이었다
  0  바로잡기 전과 실질이 같다

먼저 [관찰]에 아래 글에서 **그대로 옮긴 짧은 인용**을 적어라 — 바로잡기 전 답변과 그다음
답변에서 견준 대목이 보이게. 그 다음 [근거], 마지막에 [점수]. **관찰에 없는 것을 근거로
삼지 마라.**

[바로잡기 직전 턴]
{prior_pair}

[사용자가 바로잡은 말]
{correction}

[그다음 답변]
{answer}
"""

#: 축 이름 → 프롬프트. **공개 인터페이스다** — Task 7 의 위생 검사가 "앵커 본문이 프롬프트에
#: 박혀 있지 않은가"를 여기서 읽는다. 자기 앵커에 맞춰진 판정기는 아무것도 못 재기 때문이다.
PROMPTS: dict[str, str] = {
    "response_mode_fit": PROMPT_RESPONSE_MODE_FIT,
    "context_continuity": PROMPT_CONTEXT_CONTINUITY,
    "repair_success": PROMPT_REPAIR_SUCCESS,
}


class Verdict(BaseModel):
    """판정기가 축 하나에 대해 돌려주는 것.

    **칸 순서가 곧 생성 순서다.** `observations` 를 먼저 적게 해서 모델이 대화를 실제로 훑은
    뒤에 판정하게 한다 — `score` 를 위에 두면 점수를 정해 놓고 이유를 맞춘다. 순서를 바꾸는
    것은 프롬프트를 고치는 것과 같으므로 `PROMPT_VERSION` 을 올려야 한다.

    `observations` 가 사람이 검산할 자리다 — 대화에서 그대로 옮긴 짧은 인용이라, 원문을
    `Ctrl+F` 로 찾아보면 판정을 확인할 수 있다 (`RAG-075` 의 *"의견이 아니라 확인 가능한
    사실"*).
    """

    model_config = ConfigDict(extra="forbid")

    observations: list[str]
    rationale: str
    #: 0 · 1 · 2. `Field(ge=0, le=2)` 이 아니라 `Literal` 인 이유는 **스키마를 붙여서 받기**
    #: 때문이다 — 구조화 출력의 스키마에는 수의 상하한이 없고 열거는 있다. 상하한으로 적으면
    #: 모델이 3 을 낼 수 있고 그때는 파싱 단계에서 랩 하나가 통째로 죽는다.
    score: Literal[0, 1, 2]


class TurnJudgment(BaseModel):
    """대상 턴 하나의 판정. **사용성 게이트는 여기서 안 매긴다** — `derive_usability` 는
    안전 실패 여부를 받아야 하는데 그것은 이 세 축이 재는 것이 아니다. 게이트는 그 값을
    가진 리포트가 매긴다."""

    model_config = ConfigDict(extra="forbid")

    type: str = "judgment"
    case_id: str
    turn_index: int
    scores: AxisScores
    #: 축 이름 → 그 축의 관찰 · 근거. 사람이 판정을 검산할 자리다.
    verdicts: dict[str, Verdict]
    #: 부르지 않은 축. **0 과 다르다** — 못 잰 것을 0 으로 적으면 랩 대조에서 실패로 읽힌다.
    not_applicable: list[str]


class JudgeHeader(BaseModel):
    """판정 파일 한 장의 전제. **judge 모델과 프롬프트 버전이 여기 있어야** 두 판정 파일의
    차이가 답변 때문인지 judge 때문인지 갈린다 (`collect.LapHeader` 와 같은 규약)."""

    model_config = ConfigDict(extra="forbid")

    type: str = "header"
    version: int = VERSION
    lap: str
    judged_at: str
    judge_model: str
    prompt_version: int
    rubric: str = RUBRIC
    anchor_set: str
    items: int


# ---------------------------------------------------------------------------
# 축마다 다른 입력 — 이 세 함수가 이 모듈의 핵심이다
# ---------------------------------------------------------------------------


def _turn_dicts(case: ConversationCase, stop: int) -> list[dict[str, str]]:
    return [{"role": t.role, "text": t.text} for t in case.turns[:stop]]


def build_payload(case: ConversationCase, row: Mapping[str, Any], axis: str) -> dict[str, Any]:
    """축 하나가 볼 것만 담는다. **여기서 안 담은 것은 판정기가 못 본다.**

    담지 않는 것이 담는 것만큼 중요하다 — `expected_mode` · `repair_applicable` · `note` 는
    우리 정답지라 어느 축에도 안 간다. `user_input_needed` 는 라벨이 아니라 상황의 사실이라
    모드 축에만 간다.
    """
    turn_index = int(row["turn_index"])
    answer = str(row.get("message", ""))
    if axis == "response_mode_fit":
        # 앞 턴 · 상태를 빼는 것이 이 축의 설계다 — 있으면 "모드가 맞나"가 "그럴듯한가"로
        # 미끄러진다.
        return {
            "user_utterance": str(row.get("query", "")),
            "user_input_needed": case.user_input_needed,
            "answer": answer,
        }
    if axis == "context_continuity":
        # 지금 발화를 같이 준다 — 무엇에 답한 것인지 모르면 "이어졌나"를 물을 수 없다.
        # 앞 턴은 **케이스 대본**이다: 사용자가 실제로 겪은 대화가 그것이고, 런타임이 그것을
        # 안 실어 보낸 것 자체가 이 카드가 재려는 실패다.
        return {
            "prior_turns": _turn_dicts(case, turn_index - 1),
            "current_utterance": str(row.get("query", "")),
            "state_supplied": row.get("state_supplied", NOT_REACHED),
            "answer": answer,
        }
    if axis == "repair_success":
        return {
            "prior_pair": _turn_dicts(case, turn_index - 1)[-2:],
            "correction": case.turns[turn_index - 1].text,
            "answer": answer,
        }
    raise ValueError(f"모르는 축입니다: {axis!r}")


def _render_turns(turns: Sequence[Mapping[str, Any]]) -> str:
    if not turns:
        return "(없음 — 이 턴 앞에 오간 말이 없습니다)"
    label = {"user": "사용자", "assistant": "어시스턴트"}
    return "\n".join(f"{label.get(t['role'], t['role'])}: {t['text']}" for t in turns)


def _render_state(state: Any) -> str:
    """**«없음»과 «안 닿음»을 가른다.** 이음매가 상태를 못 실어 본 것을 "상태가 없다"고
    적으면 판정기가 그것을 근거로 감점한다 — 부재와 미측정이 같아 보이는 실패다."""
    if state == NOT_REACHED:
        return "(공급되지 않음 — 이 이음매로는 상태를 실어 보낼 자리가 없습니다)"
    if not state:
        return "(없음 — 이 턴에 공급된 구조화 상태가 없습니다)"
    return json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)


def build_prompt(axis: str, payload: Mapping[str, Any]) -> str:
    """payload 를 그 축의 프롬프트에 끼운다."""
    if axis == "response_mode_fit":
        return PROMPTS[axis].format(
            user_utterance=payload["user_utterance"].strip(),
            input_was_needed="예" if payload["user_input_needed"] else "아니오",
            answer=payload["answer"].strip(),
        )
    if axis == "context_continuity":
        return PROMPTS[axis].format(
            prior_turns=_render_turns(payload["prior_turns"]),
            current_utterance=payload["current_utterance"].strip(),
            state_supplied=_render_state(payload["state_supplied"]),
            answer=payload["answer"].strip(),
        )
    if axis == "repair_success":
        return PROMPTS[axis].format(
            prior_pair=_render_turns(payload["prior_pair"]),
            correction=payload["correction"].strip(),
            answer=payload["answer"].strip(),
        )
    raise ValueError(f"모르는 축입니다: {axis!r}")


# ---------------------------------------------------------------------------
# provider 이음매 — 이 함수 **안에서만** OpenAI 를 문다
# ---------------------------------------------------------------------------

#: `generate(axis=..., prompt=..., payload=..., model=...) -> Verdict`.
#: `prompt` 와 `payload` 를 둘 다 넘긴다 — provider 는 `prompt` 만 쓰지만, 기록 · 테스트
#: 이음매는 **무엇이 갔고 무엇이 안 갔는지**를 프롬프트 문자열을 다시 파싱하지 않고 봐야 한다.
Generate = Callable[..., Verdict]


def openai_generate(
    *, axis: str, prompt: str, payload: Mapping[str, Any], model: str, cli: Any = None
) -> Verdict:
    """핀 박힌 judge 를 한 번 부른다. **import 만으로는 여기 안 온다** — 클라이언트도 키도
    이 함수가 불릴 때 처음 필요해진다."""
    del axis, payload  # 프롬프트에 이미 들어 있다. 기록 · 테스트 이음매를 위한 칸이다.
    resp = (cli or client()).responses.parse(
        model=model,
        input=prompt,
        # **스키마를 붙여서 받는다** — 프롬프트로 JSON 을 부탁하는 것과 다르다.
        text_format=Verdict,
        temperature=0,
    )
    parsed = resp.output_parsed
    if parsed is None:
        raise RuntimeError(f"judge 가 판정을 안 냈습니다 — {model}")
    return parsed


def client(api_key: str | None = None) -> Any:
    """지연 생성. **키가 없으면 여기서 죽는다** (`training_quality.judge.client` 와 같은 규약)."""
    from openai import OpenAI

    from daengs_backend.config import settings

    key = api_key or settings.openai_api_key.get_secret_value().strip()
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY 가 없습니다 — backend/.env 를 확인하세요 (루트 .env 가 아닙니다). "
            "judge 는 생성과 **다른 계열**을 씁니다 (D-060 ①)."
        )
    return OpenAI(api_key=key, timeout=settings.openai_timeout_s)


def judge_model() -> str:
    """핀. 함수 안에서 늦게 읽는다 — 모듈을 import 하는 것만으로 설정이 뜨면 안 된다."""
    from daengs_backend.config import settings

    return settings.openai_judge_model


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------


def judge_turn(
    case: ConversationCase,
    row: Mapping[str, Any],
    *,
    generate: Generate,
    model: str,
) -> TurnJudgment:
    """대상 턴 하나 → 축마다 한 번씩. **못 재는 축은 아예 안 부른다.**

    `applicability` 가 False 인 축은 요청도 토큰도 없고 결과는 `None` 이다 — 0 이 아니다.
    0 으로 적으면 "재 봤더니 실패" 와 "잴 수 없었다" 가 랩 파일에서 같아 보인다.
    """
    turn_index = int(row["turn_index"])
    applic = applicability(case, turn_index)
    verdicts: dict[str, Verdict] = {}
    not_applicable: list[str] = []
    for axis in AXES:
        if not applic[axis]:
            not_applicable.append(axis)
            continue
        payload = build_payload(case, row, axis)
        verdicts[axis] = generate(
            axis=axis,
            prompt=build_prompt(axis, payload),
            payload=payload,
            model=model,
        )
    return TurnJudgment(
        case_id=str(row["case_id"]),
        turn_index=turn_index,
        scores=AxisScores(
            response_mode_fit=verdicts["response_mode_fit"].score,
            context_continuity=(
                verdicts["context_continuity"].score if "context_continuity" in verdicts else None
            ),
            repair_success=(
                verdicts["repair_success"].score if "repair_success" in verdicts else None
            ),
        ),
        verdicts=verdicts,
        not_applicable=not_applicable,
    )


def header(
    *, lap: str, items: int, model: str, anchor_set: str, prompt_version: int = PROMPT_VERSION
) -> JudgeHeader:
    return JudgeHeader(
        lap=lap,
        judged_at=datetime.now(UTC).isoformat(timespec="seconds"),
        judge_model=model,
        prompt_version=prompt_version,
        anchor_set=anchor_set,
        items=items,
    )


def run_score(
    *,
    rows: Sequence[Mapping[str, Any]],
    cases: Sequence[ConversationCase],
    generate: Generate | None = None,
    model: str | None = None,
    anchor_dir: Path | None = None,
    anchor_set: str = "dev",
    lap: str = "",
    out_path: Path | None = None,
) -> list[TurnJudgment]:
    """랩 행 목록 → 판정 목록. `out_path` 를 주면 헤더 한 줄 + 판정 줄들로 쓴다.

    **앵커 게이트를 통과하지 못하면 한 줄도 안 돈다** — 판정을 시작한 뒤에 검사하면 이미
    돈 만큼 토큰이 나갔고, 사람은 그 파일을 신뢰할 수 있는 것으로 오해한다.

    빈 답변 행은 뺀다 (`transcript.check_transcript` 가 세는 `not_answered`). 답이 없는데
    판정기를 부르면 판정자가 **우리 상수를 채점한다** (D-060 ⑤). 답변 자리에 `NOT_REACHED`
    가 적힌 행도 뺀다 — 그것은 답이 아니라 "이 이음매로는 못 봤다"는 표시라, 채점하면
    판정기가 우리 센티널 문자열을 읽고 점수를 매긴다.
    """
    resolved_model = model or judge_model()
    require_anchor_pass(anchor_dir, anchor_set=anchor_set, model=resolved_model)

    by_id = {case.case_id: case for case in cases}
    call = generate or openai_generate
    judgments: list[TurnJudgment] = []
    for row in rows:
        case = by_id.get(str(row.get("case_id")))
        if case is None:
            raise ValueError(f"랩 행의 케이스를 못 찾습니다: {row.get('case_id')!r}")
        message = str(row.get("message", "")).strip()
        if not message or message == NOT_REACHED:
            continue
        judgments.append(judge_turn(case, row, generate=call, model=resolved_model))

    if out_path is not None:
        head = header(lap=lap, items=len(judgments), model=resolved_model, anchor_set=anchor_set)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            handle.write(head.model_dump_json() + "\n")
            for judgment in judgments:
                handle.write(judgment.model_dump_json() + "\n")
    return judgments
