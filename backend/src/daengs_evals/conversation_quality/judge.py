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

⚠ 다만 **오늘 케이스 세트에서 그 비트는 정답지와 완전히 공선이다** — `need=True` 인 5건이
전부 `ASK`, `need=False` 인 8건이 전부 ANSWER·REDIRECT 다. 라벨을 안 준다는 약속이 이 세트
위에서는 절반만 지켜지는 셈이라, **이 세트만으로는 모드 축이 답변을 재는지 이 비트를
되읽는지 갈리지 않는다.** 칸을 빼는 것은 답이 아니다(스펙이 금지한 감점이 되살아난다) —
가르는 일은 앵커가 한다. 자세한 것은 `build_payload` 의 같은 자리 주석.

────────────────────────────────────────────────────────────────────────────
`StatelessDriver` 로 모은 행에서 두 축의 정답은 0 이다 — 그래도 정직하게 묻는다
────────────────────────────────────────────────────────────────────────────
`StatelessDriver`(`drivers.py`) 는 이전 턴을 담아 보내지 않는다 — 그 드라이버로 모은
행에서는 `context_continuity` · `repair_success` 의 옳은 점수가 늘 0 이다. `#416` 의
`SessionDriver` 는 이전 턴을 실제로 실어 보내므로 그 드라이버로 모은 행에는 이 못박음이
적용되지 않는다(`transcript.PRIOR_TURNS_REACH_INFERENCE`, `report.FLOORED_AXES` 의
`before.driver` 분기 참고). **여기서는 그래도(어느 드라이버로 모았든) 0 을 프롬프트에
적지 않고 코드로 건너뛰지도 않는다.** `StatelessDriver` 랩이 이 두 판정기의 위양성
시험이 되는 것이 이 설계의 값이다 — 0 이 아닌 판정이 나오면 그것은 발견이 아니라
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
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from daengs_evals.conversation_quality import ASSETS_DIR
from daengs_evals.conversation_quality.cases import ConversationCase
from daengs_evals.conversation_quality.drivers import NOT_REACHED
from daengs_evals.conversation_quality.rubric import AxisScores, StateAudit, applicability

VERSION = 1  # 판정 파일 스키마
PROMPT_VERSION = 3  # 프롬프트. **고치면 올리고 앵커를 다시 통과한다**
#
# v3 (2026-09-10): `context_continuity` 의 띠가 **옳은 행동을 틀린 행동보다 낮게** 매기고
# 있었다. `cq_state_irrelevant_insert_01` — 이 질문과 상관없는 보더콜리 프로필이 주어진 턴 —
# 에서 **안 쓰는 것이 옳은 답**인데 v2 의 띠로는 "있는 것을 하나도 안 썼다" 라 0 이고, 정작
# 이 케이스가 잡으려는 실패(이름만 껴 넣기)는 "언급은 했는데 이름표에 그쳤다" 라 1 이었다.
# 모호함이 아니라 **뒤집힘**이고, 뒤집힌 축 하나가 첫 랩 전체를 오독하게 만든다.
# 고친 것 둘: 점수를 «주어진 것» 이 아니라 «주어진 것 중 **답에 영향을 주는 것**» 위에서
# 매기게 했고(상관없는 상태를 안 쓴 것은 감점이 아니며 억지로 끼워 넣는 쪽이 0 이다),
# **이름표만 붙인 언급을 0 으로 못 박았다** — v2 는 그것이 항목으로는 "쓴 것이 아니다"(0),
# 띠 문구로는 "절반"(1) 이라 두 갈래로 읽혔다. 갈리는 자리를 0 으로 정한다: `rubric.StateAudit`
# 이 *"일반론에 견종 이름을 껴 넣은 것은 상태를 쓴 것이 아니다"* 로 이미 그렇게 정해 뒀다.
#
# v2 (2026-09-10): `context_continuity` 를 고쳤다. v1 의 점수 띠가 *"앞 턴**과** 상태"* 라는
# 연언(連言)으로 적혀 있었는데, 면제 조항은 상태가 없을 때만 봐 줬다. 그래서 상태만 있고 앞
# 턴이 없는 행(`target_turns=[1]` 인 케이스들 — `cq_state_relevant_present_01` ·
# `cq_state_irrelevant_insert_01`)에서 **완벽한 답의 천장이 1 인지 2 인지 안 갈렸다.**
# 관찰 지시("앞 턴의 어느 대목과 견줬는지")도 그 행에서는 지킬 수가 없었다.
# 고친 것 셋: 면제 조항을 상태 부재 · 앞 턴 부재 **둘로 갈랐고**, 점수를 *"주어진 것 중 있는
# 것"* 위에서 매기게 했고, 지어낸 개인화에 **0 이라는 번호를 붙였다**(v1 은 "감점한다"로만
# 적혀 번호가 없었다). 더해서 상태 감사 세 칸을 판정기가 직접 적게 했다 — 그 셋을 머릿속에서
# 굴린 뒤 서수 하나로 뭉개는 것이 `rubric.StateAudit` 이 막으려는 바로 그 실패다.

RUBRIC = "conversation_quality"

#: 부르는 순서다. `rubric.AxisScores` 의 칸 순서와 같게 둔다 — 두 곳이 갈리면 판정 파일을
#: 읽는 사람이 어느 쪽 순서로 읽어야 하는지 모른다.
AXES: tuple[str, ...] = ("response_mode_fit", "context_continuity", "repair_success")

#: 앵커 통과 기록의 파일 이름. **`anchors.py` 가 이 규약을 그대로 써서 파일을 만든다** —
#: 만드는 쪽과 읽는 쪽이 갈리면 게이트가 조용히 열린 채로 남는다. 모델 · 앵커 세트 ·
#: 프롬프트 버전 셋 중 하나만 바뀌어도 다른 파일이 되는 것이 요점이다: 셋 중 무엇이 바뀌든
#: 판정기는 다른 물건이라 앵커를 다시 통과해야 한다.
ANCHOR_RECORD_TEMPLATE = "anchor_check_{anchor_set}_{prompt_version}_{model}.json"

#: 기록 **안**에 반드시 있어야 하는 칸. 파일 이름은 모델 · 세트 이름 · 프롬프트 버전으로
#: 갈리는데, **정작 앵커 자체가 바뀐 것은 이름에 안 나타난다** — 앵커를 고치거나 늘려도
#: 파일 이름이 그대로라 낡은 통과 기록이 게이트를 조용히 열어 준다. 게이트의 명분이
#: *"judge 를 믿을 근거는 앵커뿐"* 인데 그 앵커가 무엇이었는지 기록이 말하지 못하면 명분이
#: 성립하지 않는다. 그래서 앵커 파일의 해시를 기록에 적게 하고, `run_score` 가 받은 기대
#: 해시와 **대조한다**.
#:
#: 해시는 `cases.file_sha256` 으로 낸다 — 바이트가 아니라 **LF 로 정규화한 텍스트**를
#: 해시한다. 이 저장소는 `core.autocrlf=true` 라 개발 PC(Windows)는 CRLF, CI(ubuntu)는 LF 로
#: 체크아웃되므로 바이트 해시는 둘이 영영 안 맞는다.
ANCHOR_RECORD_REQUIRED_FIELDS = ("passed", "anchors_sha256")


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
    anchors_sha256: str,
    prompt_version: int = PROMPT_VERSION,
) -> dict[str, Any]:
    """앵커 통과 기록이 없으면 **여기서 멈춘다**.

    judge 를 믿을 근거가 앵커뿐이라 게이트를 코드로 든다 (`training_quality` 의 같은 자리).
    막는 자리가 넷이다:

      기록이 없다              아직 한 번도 안 돌렸다
      `passed` 가 참이 아니다   돌려만 보고 넘어가는 것 — 실패한 기록도 파일은 남는다
      `anchors_sha256` 이 없다  무엇을 통과한 것인지 기록이 말하지 못한다. 옛 모양의 기록이다
      해시가 다르다            앵커를 고쳤거나 늘렸다. **파일 이름은 그대로라 이것 말고는 못 잡는다**

    **`anchors_sha256` 은 기본값이 없다.** 넘길 수 있게만 해 두면 안 넘기는 길이 곧 기본
    경로가 되고, 그러면 마지막 자리가 열린 채로 남는다 — 기록은 있고 `passed` 는 참이고
    해시는 아무도 안 보는 문자열인 상태. 부르는 쪽이 `cases.file_sha256` 으로 지금 앵커
    파일의 해시를 내서 넘긴다. 이 모듈이 앵커 파일을 직접 읽지 않는 이유는 그러면
    `anchors.py` 를 import 하게 되기 때문이지, 검사가 선택이어서가 아니다.

    깨진 JSON 도 여기서 `SystemExit` 으로 돌린다 — `JSONDecodeError` 트레이스백을 보면
    사람이 "앵커를 다시 돌려라" 로 읽지 못한다.
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
    try:
        record = json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"앵커 기록을 읽을 수 없습니다: {path}\n  {exc}") from exc
    if not record.get("passed"):
        raise SystemExit(f"앵커가 통과하지 못한 기록입니다: {path}")
    recorded = record.get("anchors_sha256")
    if not recorded:
        raise SystemExit(
            f"앵커 기록에 `anchors_sha256` 이 없습니다: {path}\n"
            "  무엇을 통과한 기록인지 알 수 없으면 게이트가 아닙니다. 앵커 검사를 다시 도세요."
        )
    if recorded != anchors_sha256:
        raise SystemExit(
            f"앵커가 통과 기록 뒤에 바뀌었습니다: {path}\n"
            f"  기록 {recorded}\n  지금 {anchors_sha256}\n"
            "  파일 이름은 그대로라 이 대조 말고는 못 잡습니다. 앵커 검사를 다시 도세요."
        )
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

이 축이 묻는 것은 **답변이 주어진 것을 실제로 썼나** 하나뿐이다. 주어지는 것은 둘이다 —
[앞 턴]과 [주어진 상태]. **둘 중 하나만 있을 수 있다.** 그때는 있는 것만으로 판정한다.

판정 기준:
- **먼저 주어진 것 중 «이 질문의 답에 영향을 주는 것»을 가려라.** 이 축은 주어진 것을 다
  쓰라는 축이 아니라 **답이 달라져야 할 것을 반영했나**를 보는 축이다.
- **답과 상관없는 상태를 안 쓴 것은 감점이 아니다.** 견종 · 나이가 이 질문의 답을 바꾸지
  않는다면 그것을 안 부르고 답한 것이 옳다. **상관없는 프로필을 굳이 끼워 넣는 쪽이 실패다** —
  사용자는 그것을 «내 사정을 반영한 답»으로 읽는데 실제로는 아무것도 반영되지 않았다.
- 앞 턴에 이미 나온 것을 **없던 일처럼** 다시 묻거나 그대로 되풀이하면 이어지지 않은 것이다.
- 상태를 «썼다»고 하려면 그 값이 **답을 바꿔야 한다.** 견종이나 나이를 한 번 부르고 나머지가
  누구에게나 할 수 있는 일반론이면 **쓴 것이 아니다** — 이름표만 붙인 것이고, 그것은 절반이
  아니라 **안 쓴 것으로 센다.**
- [주어진 상태]가 «없음»이거나 «공급되지 않음»이면 **상태를 안 썼다고 감점하지 마라.**
  그때는 [앞 턴]만 보고 판정하고, 상태에 관한 두 칸은 거짓으로 적어라.
- [앞 턴]이 «없음»이면 **앞 턴을 안 이었다고 감점하지 마라.** 그때는 [주어진 상태]만 보고
  판정한다. 이 경우 [지금 사용자 발화]에 답이 맞물리는지가 아니라 **상태를 썼는지**만 본다.
- [주어진 상태]에 없는 개인 사정을 아는 척하면 **그것만으로 0 이다.** 지어낸 개인화는 안 쓴
  것보다 나쁘다 — 사용자가 그것을 사실로 읽는다. 다른 대목을 잘 이었어도 0 으로 내려간다.
- **답이 사실인지, 답할 자리였는지 되물을 자리였는지는 묻지 않는다.** 다른 자가 잰다.
- **말투 · 길이 · 공손함은 채점하지 마라.**

점수는 **주어진 것 중 답에 영향을 주는 것**만 놓고 매긴다:
  2  영향을 주는 것을 전부 실제로 썼다. **주어진 것이 이 답에 아무 영향도 주지 않는 경우,
     그것을 안 쓰고 답한 것도 2 다** — 안 쓰는 것이 옳은 판단이었다
  1  영향을 주는 것이 둘인데 하나만 실제로 썼다 (앞 턴은 이었는데 상태는 안 썼다, 또는 그 반대)
  0  영향을 주는 것이 있는데 하나도 안 썼다, **이름표만 붙였다**, 상관없는 상태를 억지로
     끼워 넣었다, 또는 없는 개인 사정을 지어냈다

먼저 [관찰]에 아래 글에서 **그대로 옮긴 짧은 인용**을 적어라 — 주어진 것(있는 쪽)의 어느
대목과 답변의 어느 대목을 견줬는지 보이게. 그 다음 [근거], 그 다음 아래 세 칸, 마지막에
[점수]. **관찰에 없는 것을 근거로 삼지 마라.**

세 칸은 점수가 아니라 **사실 기록**이다. 점수를 정하고 거꾸로 맞추지 말고, 본 대로 적어라:
- `relevant_state_used` — [주어진 상태]의 값이 답변에 실제로 반영됐나. 상태가 없으면 거짓
- `state_used_correctly` — 반영된 그 값을 **맞게** 썼나 (나이를 잘못 읽는 따위가 없나).
  반영이 없었으면 거짓
- `unsupported_or_superficial_personalization` — 상태에 없는 것을 지어냈거나, 이름표만
  붙였나

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

#: 축 이름 → 프롬프트. **공개 인터페이스다** — `anchors.py` 의 위생 검사(`test_no_anchor_text_
#: appears_in_any_axis_prompt`)가 "앵커 본문이 프롬프트에 박혀 있지 않은가"를 여기서 읽는다.
#: 자기 앵커에 맞춰진 판정기는 아무것도 못 재기 때문이다.
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


class ContinuityVerdict(BaseModel):
    """`context_continuity` 축만 이 모양으로 받는다 — **상태 감사 세 칸이 점수보다 먼저다.**

    `rubric.StateAudit` 이 있는 이유가 *"이진 `used_state` 를 품질 점수로 쓰지 않는다"* 인데,
    판정기가 그 셋을 머릿속에서 굴린 뒤 서수 하나로 뭉개 버리면 그 칸들이 영영 안 남는다.
    그래서 **판정기에게 따로 적게 한다.** 점수 뒤에 두면 점수를 정해 놓고 칸을 맞추므로 앞에
    둔다 (`Verdict` 의 칸 순서와 같은 이유).

    `relevant_state_available` 은 여기 없다 — 그것은 payload 로 이미 아는 사실이라 판정기에게
    물을 것이 아니다. `judge_turn` 이 그 자리를 채운다.
    """

    model_config = ConfigDict(extra="forbid")

    observations: list[str]
    rationale: str
    relevant_state_used: bool
    state_used_correctly: bool
    unsupported_or_superficial_personalization: bool
    score: Literal[0, 1, 2]


#: 축 이름 → 그 축이 돌려줄 스키마. `openai_generate` 가 `text_format` 으로 붙인다.
VERDICT_MODELS: dict[str, type[BaseModel]] = {
    "response_mode_fit": Verdict,
    "context_continuity": ContinuityVerdict,
    "repair_success": Verdict,
}


class TurnJudgment(BaseModel):
    """대상 턴 하나의 판정. **사용성 게이트는 여기서 안 매긴다** — `derive_usability` 는
    안전 실패 여부를 받아야 하는데 그것은 이 세 축이 재는 것이 아니다. 게이트는 그 값을
    가진 리포트가 매긴다."""

    model_config = ConfigDict(extra="forbid")

    type: str = "judgment"
    case_id: str
    turn_index: int
    scores: AxisScores
    #: 상태 사용의 **사실 기록**. 점수가 아니다 (`rubric.StateAudit`). `context_continuity` 를
    #: 안 잰 턴에서는 `None` — 감사 자체를 안 한 것과 "안 썼다" 는 다르다.
    state_audit: StateAudit | None = None
    #: 축 이름 → 그 축의 관찰 · 근거. 사람이 판정을 검산할 자리다.
    verdicts: dict[str, ContinuityVerdict | Verdict]
    #: 부르지 않은 축. **0 과 다르다** — 못 잰 것을 0 으로 적으면 랩 대조에서 실패로 읽힌다.
    not_applicable: list[str]


class SkipRecord(BaseModel):
    """판정기 콜 하나가 죽어서 그 행을 건너뛴 자리의 기록.

    빈 답변 · `NOT_REACHED` 는 지금도 줄을 안 남긴다 — 그 둘의 이유는 랩 파일의
    `message` 를 보면 바로 갈리니 판정 파일에 따로 적을 것이 없다(헤더의 `skipped` 수에만
    잡힌다). 에러는 다르다 — **무엇이 실패했는지가 랩 파일 어디에도 없어서**, 사람이
    나중에 "이 39콜 중 하나가 5xx 였구나"를 알아볼 유일한 자리가 이 줄이다.

    담지 않는 것이 담는 것만큼 중요하다: 트레이스백도, 판정기에 갔던 프롬프트도, 랩의
    답변도 안 담는다. 이 파일은 사람이 공유해서 볼 수도 있는 산출물이라 예외 메시지에
    프롬프트·답변이 실려 있을 가능성 자체를 코드로 막는다 — 남기는 것은 예외 **타입 이름**뿐이다.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = "skip"
    reason: Literal["error"] = "error"
    case_id: str
    turn_index: int
    error_type: str


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
    #: 통과 기록이 말한 앵커 해시. **이름만으로는 어떤 앵커를 통과했는지 모른다** — 판정
    #: 파일이 스스로 그것을 들고 있어야 나중에 두 랩의 전제가 같았는지 대조할 수 있다.
    anchors_sha256: str
    items: int
    #: 판정하지 않고 건너뛴 랩 행 수(빈 답변 · `NOT_REACHED`). **판정 파일이 자기를 설명해야
    #: 한다** — 리포트가 "판정 못 한 비율" 을 내려고 랩 파일과 차집합을 뜨게 두지 않는다.
    skipped: int = 0


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
        #
        # ⚠ **`user_input_needed` 는 오늘 케이스 세트에서 정답지와 공선(collinear)이다.**
        #   `cases_v1.jsonl` 16건(D-072 가 13 → 16, (False, ANSWER) 3건 추가)의 분포는
        #   `{(True, ASK): 5, (False, ANSWER): 9, (False, REDIRECT): 2}` 다 —
        #   `need=True` ⟺ `expected_mode=ASK` 가 **5/5**,
        #   `need=False` ⟺ ANSWER·REDIRECT 가 **11/11** 로 **완전히 겹친다.** (이 수는 파일에서
        #   직접 센 것이다. 세트를 늘리면 여기부터 다시 세라 — 겹침이 깨지는 것이 목표다.)
        #   프롬프트가 이 비트를 0 점 규칙으로
        #   양방향에 쓰므로, 이 세트만으로는 판정기가 답변을 재는지 이 비트를 되읽는지 갈리지
        #   않는다. 그렇다고 빼면 스펙이 금지한 "입력이 필요 없었는데 안 물었다고 감점" 이
        #   되살아나므로 **칸은 유지하고**, 가르는 일은 앵커가 한다 (`anchors.py`:
        #   `need=True` 인데 ASK 가 답이 아닌 앵커와 그 역을 넣는다).
        #
        # ⚠ 이 비트는 **케이스 단위**라 모든 대상 턴에 같은 값이 간다 —
        #   `cq_observed_wellness_repair_01` 은 턴 1 에도 턴 7 에도 «예»가 붙는다. 턴마다
        #   갈라야 할 값으로 보이더라도 **케이스 스키마를 이 때문에 바꾸지 않는다.**
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
) -> Verdict | ContinuityVerdict:
    """핀 박힌 judge 를 한 번 부른다. **import 만으로는 여기 안 온다** — 클라이언트도 키도
    이 함수가 불릴 때 처음 필요해진다."""
    del payload  # 프롬프트에 이미 들어 있다. 기록 · 테스트 이음매를 위한 칸이다.
    resp = (cli or client()).responses.parse(
        model=model,
        input=prompt,
        # **스키마를 붙여서 받는다** — 프롬프트로 JSON 을 부탁하는 것과 다르다.
        # 축마다 스키마가 다르다: `context_continuity` 만 상태 감사 세 칸을 더 받는다.
        text_format=VERDICT_MODELS[axis],
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


def _state_audit(payload: Mapping[str, Any], verdict: ContinuityVerdict) -> StateAudit:
    """네 칸 중 하나는 payload 가 알고 셋은 판정기가 답한다.

    **판정기의 답을 손보지 않는다.** 상태가 없는데 «썼다»고 하면 그 모순이 그대로 남아야
    사람이 그 판정을 골라낼 수 있다 — 여기서 `available and used` 로 뭉개면 판정기의 오답이
    우리 코드에 가려진다.
    """
    # ⚠ payload 가 아는 것은 **상태가 실렸나**까지다. 그것이 이 질문에 «relevant» 한지는
    #   판정기가 세 칸에서 말한다 — 상관없는 프로필이 실린 턴에서 이 칸이 참인 것은 모순이
    #   아니라 "실리기는 했다" 는 뜻이다.
    state = payload.get("state_supplied")
    return StateAudit(
        relevant_state_available=bool(state) and state != NOT_REACHED,
        relevant_state_used=verdict.relevant_state_used,
        state_used_correctly=verdict.state_used_correctly,
        unsupported_or_superficial_personalization=(
            verdict.unsupported_or_superficial_personalization
        ),
    )


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
    verdicts: dict[str, Verdict | ContinuityVerdict] = {}
    not_applicable: list[str] = []
    state_audit: StateAudit | None = None
    for axis in AXES:
        if not applic[axis]:
            not_applicable.append(axis)
            continue
        payload = build_payload(case, row, axis)
        verdict = generate(
            axis=axis,
            prompt=build_prompt(axis, payload),
            payload=payload,
            model=model,
        )
        verdicts[axis] = verdict
        if axis == "context_continuity" and isinstance(verdict, ContinuityVerdict):
            state_audit = _state_audit(payload, verdict)
    return TurnJudgment(
        case_id=str(row["case_id"]),
        turn_index=turn_index,
        state_audit=state_audit,
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
    *,
    lap: str,
    items: int,
    model: str,
    anchor_set: str,
    anchors_sha256: str,
    skipped: int = 0,
    prompt_version: int = PROMPT_VERSION,
) -> JudgeHeader:
    return JudgeHeader(
        lap=lap,
        judged_at=datetime.now(UTC).isoformat(timespec="seconds"),
        judge_model=model,
        prompt_version=prompt_version,
        anchor_set=anchor_set,
        anchors_sha256=anchors_sha256,
        items=items,
        skipped=skipped,
    )


#: 이어 돌릴 때 어긋나면 안 되는 네 자리. `judge_model` · `anchor_set` · `anchors_sha256` 은
#: `run_score` 의 인자로, `prompt_version` 은 모듈 상수로 온다 — 넷이 같은 튜플에 있는 이유는
#: 넷 다 "이 판정 파일이 어떤 전제로 만들어졌나"를 말하는 핀이기 때문이다(`report._SHARED_
#: HEADER_PINS` 와 같은 자리, 다만 거기는 랩 헤더와 판정 헤더를 맞추고 여기는 판정 헤더
#: 자기 자신의 과거·현재를 맞춘다).
_RESUME_PINNED_FIELDS: tuple[str, ...] = (
    "judge_model",
    "prompt_version",
    "anchor_set",
    "anchors_sha256",
)


def _load_existing_output(path: Path) -> tuple[dict[str, Any], list[str]]:
    """이어 돌릴 판정 파일을 헤더 dict + 본문 줄(원문 그대로)로 가른다.

    본문을 다시 파싱해서 객체로 만들지 않는다 — 판정 줄은 그대로 새 파일에 옮겨 적을
    뿐이라 원문 문자열이면 충분하고, 깨진 JSON 한 줄 때문에 이어 돌리기 전체가 죽을
    이유도 없다(그런 줄은 `_carry_forward_judgments` 가 무시한다).
    """
    lines = [line for line in path.read_text("utf-8").splitlines() if line.strip()]
    if not lines:
        raise SystemExit(f"이어 돌릴 판정 파일이 비어 있습니다: {path}")
    try:
        head = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise SystemExit(f"이어 돌릴 판정 파일의 헤더를 읽을 수 없습니다: {path}\n  {exc}") from exc
    return head, lines[1:]


def _check_resume_pins(
    existing_header: Mapping[str, Any], *, resolved_model: str, anchor_set: str, anchors_sha256: str
) -> None:
    """옮겨진 핀 위에서 이어 돌리면 한 파일에 서로 다른 전제로 판정된 행이 섞인다 —
    비교 게이트(`report._check_shared_header_pins`)가 막으려는 바로 그 실패를 여기서
    미리 막는다. 넷 중 **어느 것이 움직였는지 이름으로** 말해야 사람이 "새로 돌려라"
    말고 "이게 왜 달라졌지"를 볼 수 있다."""
    now = {
        "judge_model": resolved_model,
        "prompt_version": PROMPT_VERSION,
        "anchor_set": anchor_set,
        "anchors_sha256": anchors_sha256,
    }
    for field in _RESUME_PINNED_FIELDS:
        before = existing_header.get(field)
        after = now[field]
        if before != after:
            raise SystemExit(
                f"이어 돌릴 수 없습니다 — `{field}` 이 바뀌었습니다: "
                f"기존={before!r} 지금={after!r}\n"
                "  옮겨진 핀 위에서 이어 돌리면 한 파일에 서로 다른 전제로 판정된 행이"
                " 섞입니다. 새 판정 파일로 처음부터 돌리세요."
            )


def _carry_forward_judgments(
    existing_lines: Sequence[str],
) -> tuple[list[str], set[tuple[str, int]]]:
    """이전 파일의 본문 줄 중 **성공한 판정만** 그대로 옮겨 적을 목록으로 고른다.

    에러로 건너뛴 줄(`SkipRecord`, `type == "skip"`)은 옮기지 않는다 — «이어 돌리기»의
    요점이 실패했던 콜을 다시 태워 보는 것이라, 옛 실패 기록을 그대로 두면 이번에 성공해도
    파일에 실패와 성공이 둘 다 남는다. 판정으로도 스킵으로도 안 읽히는 줄(수동으로 헤더
    말고 다른 걸 넣었다거나)도 조용히 버린다 — 이어 돌리기가 죽을 이유는 아니다.
    """
    kept: list[str] = []
    done: set[tuple[str, int]] = set()
    for raw in existing_lines:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if data.get("type") != "judgment":
            continue
        try:
            key = (str(data["case_id"]), int(data["turn_index"]))
        except (KeyError, TypeError, ValueError):
            continue
        kept.append(raw)
        done.add(key)
    return kept, done


def _rewrite_header_line(path: Path, new_header: JudgeHeader) -> None:
    """헤더 한 줄만 최종값으로 바꿔 쓴다. **본문 줄은 손대지 않는다.**

    `skipped`(그리고 최종 `items`)는 루프가 다 돌아야 아는 값인데, 헤더는 루프가 돌기
    **전에** 나가 있어야 한다 — 그래야 죽어도 부분 파일이 읽힌다(요구사항 1). 그래서
    자리표시 헤더로 시작해서 끝에 이 함수로 한 번만 고쳐 쓴다. 임시 파일에 쓰고
    `os.replace` 로 바꿔치기하는 이유는 이 마지막 한 걸음에서마저 죽더라도 **원본 파일은
    반쪽으로 남지 않게** 하기 위해서다(반쪽으로 남는 것은 임시 파일 쪽이고, 그것은 아무도
    안 읽는다).
    """
    lines = path.read_text("utf-8").splitlines()
    lines[0] = new_header.model_dump_json()
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def run_score(
    *,
    rows: Sequence[Mapping[str, Any]],
    cases: Sequence[ConversationCase],
    anchors_sha256: str,
    generate: Generate | None = None,
    model: str | None = None,
    anchor_dir: Path | None = None,
    anchor_set: str = "dev",
    lap: str = "",
    out_path: Path | None = None,
    resume: bool = False,
) -> list[TurnJudgment]:
    """랩 행 목록 → 판정 목록. `out_path` 를 주면 헤더 한 줄 + 판정 줄들로 **한 줄씩** 쓴다.

    **한 랩은 대략 39 콜이다.** 예전에는 다 돈 뒤 한 번에 썼다 — 그러면 어디선가 죽었을 때
    이미 낸 값까지 전부 잃는다(이미 돈 만큼은 이미 돈 만큼의 돈이다). 그래서 지금은 판정이
    하나 나올 때마다 그 줄을 바로 쓰고 `flush` 한다.

    **앵커 게이트를 통과하지 못하면 한 줄도 안 돈다** — 판정을 시작한 뒤에 검사하면 이미
    돈 만큼 토큰이 나갔고, 사람은 그 파일을 신뢰할 수 있는 것으로 오해한다.

    **`anchors_sha256` 은 기본값이 없다** — 부르려면 지금 앵커 파일의 해시를
    (`cases.file_sha256` 으로) 내서 넘겨야 한다. 기본값을 주면 안 넘기는 길이 기본 경로가
    되어, 앵커를 고쳐도 파일 이름이 그대로인 그 구멍이 그대로 남는다. 검사를 관례가 아니라
    **서명**으로 든다.

    빈 답변 행은 여기서 직접 걸러 뺀다(`transcript.check_transcript` 를 부르지 않는다 —
    그 함수는 판정 전 필터가 아니라 랩 하나가 다 만들어진 뒤에 리포트가 보여줄 사실을 계산하는
    자리다). 답이 없는데 판정기를 부르면 판정자가 **우리 상수를 채점한다** (D-060 ⑤). 답변
    자리에 `NOT_REACHED` 가 적힌 행도 뺀다 — 그것은 답이 아니라 "이 이음매로는 못 봤다"는
    표시라, 채점하면 판정기가 우리 센티널 문자열을 읽고 점수를 매긴다. **뺀 수는 헤더에
    적는다.**

    **판정기 콜 하나가 죽어도 랩 전체를 잃지 않는다.** `judge_turn` 이 던지는 예외를 행
    단위로 잡아 그 행을 스킵 처리하고 계속 돈다 — 축 하나만 실패해도 그 행은 통째로
    스킵한다(부분 판정을 완전한 판정처럼 파일에 남기지 않는다). `KeyboardInterrupt` ·
    `SystemExit` 같은 `BaseException` 은 안 잡는다 — 그건 사람이 진짜로 멈추라고 한 것이고,
    죽더라도 이미 flush 된 줄까지는 파일에 남는다.

    **`resume=True` 면 `out_path` 가 있어야 한다.** 그 파일이 있으면 헤더의 네 핀
    (judge_model · prompt_version · anchor_set · anchors_sha256)이 지금 돌리려는 조건과
    같은지 먼저 본다 — 다르면 SystemExit. 같으면 이미 성공한 판정 행은 다시 안 부르고
    (`_carry_forward_judgments`), 나머지(에러로 죽었던 행 포함)만 새로 돈다.
    """
    if resume and out_path is None:
        raise ValueError("`resume=True` 는 `out_path` 가 있어야 이어 돌릴 파일을 압니다")

    resolved_model = model or judge_model()
    record = require_anchor_pass(
        anchor_dir, anchor_set=anchor_set, model=resolved_model, anchors_sha256=anchors_sha256
    )
    resolved_anchors_sha256 = str(record["anchors_sha256"])

    kept_lines: list[str] = []
    done_keys: set[tuple[str, int]] = set()
    if resume and out_path is not None and out_path.exists():
        existing_header, existing_lines = _load_existing_output(out_path)
        _check_resume_pins(
            existing_header,
            resolved_model=resolved_model,
            anchor_set=anchor_set,
            anchors_sha256=resolved_anchors_sha256,
        )
        kept_lines, done_keys = _carry_forward_judgments(existing_lines)

    by_id = {case.case_id: case for case in cases}
    call = generate or openai_generate
    judgments: list[TurnJudgment] = []
    skipped = 0

    handle = None
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        handle = out_path.open("w", encoding="utf-8")
        # 자리표시 헤더 — `skipped`·`items` 는 루프가 끝나야 안다. 끝에서 다시 쓴다
        # (`_rewrite_header_line`). 죽으면 이 값이 최종이 아니지만, 파일은 여전히 읽힌다.
        placeholder = header(
            lap=lap,
            items=len(kept_lines),
            model=resolved_model,
            anchor_set=anchor_set,
            anchors_sha256=resolved_anchors_sha256,
            skipped=0,
        )
        handle.write(placeholder.model_dump_json() + "\n")
        for raw in kept_lines:
            handle.write(raw + "\n")
        handle.flush()

    try:
        for row in rows:
            case_id = str(row.get("case_id"))
            turn_index = int(row.get("turn_index", -1))
            case = by_id.get(case_id)
            if case is None:
                raise ValueError(f"랩 행의 케이스를 못 찾습니다: {row.get('case_id')!r}")
            if (case_id, turn_index) in done_keys:
                continue  # 이어 돌리기 — 이 행은 이전 실행에서 이미 성공했다
            message = str(row.get("message", "")).strip()
            if not message or message == NOT_REACHED:
                skipped += 1
                continue
            try:
                judgment = judge_turn(case, row, generate=call, model=resolved_model)
            except Exception as exc:  # noqa: BLE001 — 콜 하나의 실패로 39콜 전부를 잃지 않는다
                skipped += 1
                if handle is not None:
                    skip = SkipRecord(
                        case_id=case_id, turn_index=turn_index, error_type=type(exc).__name__
                    )
                    handle.write(skip.model_dump_json() + "\n")
                    handle.flush()
                continue
            judgments.append(judgment)
            if handle is not None:
                handle.write(judgment.model_dump_json() + "\n")
                handle.flush()
    finally:
        if handle is not None:
            handle.close()

    if out_path is not None:
        final = header(
            lap=lap,
            items=len(kept_lines) + len(judgments),
            model=resolved_model,
            anchor_set=anchor_set,
            anchors_sha256=resolved_anchors_sha256,
            skipped=skipped,
        )
        _rewrite_header_line(out_path, final)

    if kept_lines:
        judgments = [TurnJudgment.model_validate(json.loads(raw)) for raw in kept_lines] + judgments
    return judgments
