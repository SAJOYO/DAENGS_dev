"""LLM-as-a-judge — **답이 물은 것에 답했나** (RAG-007 · `D15`).

────────────────────────────────────────────────────────────────────────────
왜 이 축인가 — 지금 지표 둘이 못 보는 자리
────────────────────────────────────────────────────────────────────────────
`score.py` 가 세는 것은 둘뿐이다:

    cited      답변에 **조 번호가 나왔나**
    grounded   답변이 `[N]` 으로 **골든셋 `must` 를 지목했나**

**답이 맞는지는 아무도 안 잰다.** 실물이 `RAG-072` 의 `I4` 다 — *"보험금을 안 주는 사유가
뭔가요"* 에 생성이 *"부담보 특별약관이 부가된 경우 지급하지 않습니다"* 라고 답했다.
**틀린 말이 아닌데 물은 것의 답이 아니다.** 두 지표 어느 쪽도 그 구분을 못 한다 —
조 번호도 나왔고 근거도 지목했기 때문이다.

────────────────────────────────────────────────────────────────────────────
축을 **하나만** 세운다
────────────────────────────────────────────────────────────────────────────
`RAG-007` 의 루브릭은 넷이다 (Faithfulness · 인용 정확도 3단 · 답변 정확성 · Abstention).
여기 있는 것은 **하나**이고, 나머지 셋을 안 세운 이유가 각각 있다:

  Faithfulness      청크 **본문**이 필요하다. 덤프에는 `chunk_id`·`citation`·`tier` 만 있고
  인용 정확도        본문이 없다 (`generate.DumpRow`) — DB 를 다시 봐야 하고, 그러면
                    `score-laps` 가 지켜 온 *"코퍼스 없이 돈다"* 는 약속이 깨진다.
                    축 하나를 위해 그 약속을 깨지 않는다
  답변 정확성        `RAG-007` 이 *"참조 답안 대비"* 로 적었는데 골든셋에는 `must`/`nice`
                    청크 라벨만 있고 **모범 답안이 없다.** 만드는 것은 별도 라벨 작업이다
  Abstention        `A6`(기대 채점, RAG-055)이 이미 확정된 자로 재고 있다. 겹쳐 세지 않는다

이 축은 `question` 과 `text` **둘만** 본다. 그래서 이 모듈도 랩 파일만 있으면 돈다.

────────────────────────────────────────────────────────────────────────────
judge 위생 — `RAG-007` 의 셋, 그리고 사람이 더한 하나
────────────────────────────────────────────────────────────────────────────
  계열 분리     생성 Gemini(`gemini-3.1-flash-lite`) → judge **OpenAI**.
              `RAG-007` 이 요구한 것은 *"급 분리"*(flash 생성 → pro judge)까지였는데,
              **그것은 같은 계열의 위아래**라 self-preference 가 남는다 — 같은 훈련 계보의
              모델은 자기 계열이 쓴 문장을 후하게 본다. 특히 이 축은 판정이 문체·구성에
              민감해 편향이 붙기 쉬운 자리다. **2026-09-07 에 사람이 계열까지 가르기로 정했다**
  버전 고정     `PROMPT_VERSION` 과 judge 모델명을 판정 파일에 같이 적는다. 프롬프트를 고치면
              번호를 올리고 **다시 캘리브레이션한다** (RAG-007 의 요구 그대로)
  판정 순서     `rationale` → `verdict`. 스키마의 **칸 순서가 곧 생성 순서**라, 판정을 먼저
              두면 모델이 답을 정해 놓고 이유를 붙인다
  결정성        `temperature=0`. 생성은 비결정적이어도 되지만 **채점자가 비결정적이면**
              같은 답변이 랩마다 다른 점수를 받아 랩 대조가 통째로 무의미해진다

⚠ **이 판정은 아직 지표가 아니다.** `RAG-007` 이 *"사람 라벨 30개와 일치율로 캘리브레이션
**후** 신뢰"* 라고 못 박았고, 그 라벨이 아직 없다. 그래서 `score-laps` 에 열을 안 더한다 —
이 모듈이 지금 하는 일은 **사람이 라벨링할 문항을 고르는 것**이고, 그 자리가 `disagreements()`다.

⚠ **키가 없으면 이 모듈만 안 돈다.** `openai_api_key` 를 `gemini_api_key` 와 갈라 둔 이유가
그것이다 — 채점 도구 하나 때문에 `/life/ask` 가 뜨지 않으면 안 된다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..core import config
from . import score

VERSION = 1          # 판정 파일 스키마
PROMPT_VERSION = 1   # 프롬프트. **고치면 올리고 재캘리브레이션한다** (RAG-007)

RUBRIC = "answers_question"

# 프롬프트. **답이 사실인지가 아니라 물은 것에 답했는지를 묻는다** — 사실 확인은 컨텍스트가
# 있어야 하고(Faithfulness), 그것은 이 축이 아니다. `I4` 는 사실이면서 답이 아니었다.
#
# ⚠ 예시를 하나만 든다. `I4` 를 그대로 쓰지 않고 **모양만** 쓴 이유는, 골든셋 문항을 프롬프트에
# 박으면 그 문항에서만 잘 맞는 채점자가 되기 때문이다 (`RAG-066` 의 *"값을 보인 낱말만 넣는다"*
# 와 같은 결의 조심이다).
PROMPT = """너는 반려동물 법령·약관 질의응답의 채점자다.

아래 [질문]과 [답변]을 읽고 **답변이 질문이 물은 것에 답하고 있는지**만 판정해라.

판정 기준:
- 먼저 질문이 실제로 무엇을 묻는지 한 문장으로 적어라. 그리고 답변이 **그것을** 말하는지 본다.
- 답변이 사실인지, 근거가 옳은지는 **묻지 않는다.** 사실이면서 물은 것의 답이 아닐 수 있다.
- 질문이 여럿을 물으면 **전부** 답해야 참이다.
- "자료에 없다"며 물러선 답변은 **답하지 않은 것**이다 (거짓).
- 관련은 있지만 질문의 초점을 비껴간 답변은 **거짓**이다.
  예) 어떤 경우에 해당하는지의 **목록**을 물었는데 그중 하나만 말하고 마치면 거짓이다.
- 조 번호나 출처 표기가 없다는 이유로 거짓을 주지 마라. 그것은 다른 자가 잰다.

[질문]
{question}

[답변]
{answer}
"""


class Verdict(BaseModel):
    """judge 가 돌려주는 판정.

    **칸 순서가 곧 생성 순서다** — `asked`·`rationale` 이 먼저 와야 모델이 이유를 쓰고 나서
    판정한다 (RAG-007 의 *"rationale→verdict 순서"*). 순서를 바꾸는 것은 프롬프트를 고치는
    것과 같으므로 `PROMPT_VERSION` 을 올려야 한다.
    """

    model_config = ConfigDict(extra="forbid")

    asked: str              # 질문이 실제로 물은 것 — 사람이 판정을 검산할 때 여기부터 본다
    rationale: str          # 왜 그렇게 봤나
    answers_question: bool


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "judgment"
    id: str
    answers_question: bool
    asked: str
    rationale: str


class JudgeHeader(BaseModel):
    """판정 파일 한 장의 전제. **judge 모델과 프롬프트 버전이 여기 있어야** 두 판정 파일의
    차이가 답변 때문인지 judge 때문인지 갈린다 (`generate.DumpHeader` 와 같은 규약)."""

    model_config = ConfigDict(extra="forbid")

    type: str = "header"
    version: int = VERSION
    lap: str                       # 어느 랩을 봤나
    judged_at: str
    judge_model: str
    prompt_version: int
    rubric: str = RUBRIC
    items: int


def client(api_key: str | None = None):
    """지연 생성. **키가 없으면 여기서 죽는다** — `generate._client` 와 같은 규약이다.

    타임아웃을 여기서 건다. 안 걸면 SDK 기본값에 맡기게 되는데, 33문항을 도는 배치라
    하나가 물리면 사람이 멈춘 줄 모르고 기다린다.
    """
    from openai import OpenAI

    key = api_key or config.settings.openai_api_key
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY 가 없다 — backend/.env 를 확인할 것. "
            "judge 는 생성과 **다른 계열**을 쓴다 (RAG-007 · D15)"
        )
    return OpenAI(api_key=key, timeout=config.settings.openai_timeout_s)


def judge_one(question: str, answer: str, *, cli=None, model: str | None = None) -> Verdict:
    """문항 하나 → 판정 하나. **순수하다** — 랩도 골든셋도 DB 도 안 본다.

    `generate.answer` 가 검색 결과를 받아 순수한 것과 같은 이유다: 테스트가 문자열 둘로
    프롬프트 조립과 파싱을 붙잡을 수 있어야 한다.
    """
    name = model or config.settings.openai_judge_model
    cli = cli or client()
    resp = cli.responses.parse(
        model=name,
        input=PROMPT.format(question=question, answer=answer),
        # **스키마를 붙여서 받는다** — 프롬프트로 JSON 을 부탁하는 것과 다르다
        # (`generate.answer` 의 같은 자리 주석 참고).
        text_format=Verdict,
        temperature=0,
    )
    parsed = resp.output_parsed
    if parsed is None:
        raise RuntimeError(f"judge 가 판정을 안 냈다 — {name}")
    return parsed


def judge_rows(rows: list[dict[str, Any]], *, cli=None, model: str | None = None,
               ckinds: dict[str, str] | None = None, on_item=None) -> list[Judgment]:
    """랩 하나(문항 목록) → 판정 목록.

    **경계 문항은 뺀다** (`score.marks` 와 같은 자). `expect: abstain`·`refuse` 는 물러서는
    것이 정답이라 *"물은 것에 답했나"* 를 물으면 **옳게 물러선 답이 거짓으로 찍힌다.**
    그 축은 `A6`(기대 채점)이 이미 자기 자로 재고 있다 — 겹쳐 세면 같은 문항이 두 표에서
    반대를 말한다 (`RAG-062` 가 `cited` 에서 경계 문항을 걷어낸 것과 같은 자리다).
    """
    out: list[Judgment] = []
    for row in rows:
        qid = str(row.get("id", ""))
        if not qid or (ckinds is not None and ckinds.get(qid) == score.NO_MUST):
            continue
        v = judge_one(row.get("question", ""), row.get("text", ""), cli=cli, model=model)
        j = Judgment(id=qid, answers_question=v.answers_question,
                     asked=v.asked.strip(), rationale=v.rationale.strip())
        out.append(j)
        if on_item:
            on_item(j)
    return out


def header(lap: str, items: list[Judgment], model: str | None = None) -> JudgeHeader:
    return JudgeHeader(
        lap=lap,
        judged_at=datetime.now(config.KST).isoformat(timespec="seconds"),
        judge_model=model or config.settings.openai_judge_model,
        prompt_version=PROMPT_VERSION,
        items=len(items),
    )


def disagreements(marks: dict[str, tuple[bool, bool]],
                  judgments: list[Judgment]) -> list[dict[str, Any]]:
    """`grounded` 와 judge 가 **엇갈린 문항**. 이 카드의 산출물이다.

    **캘리브레이션 대상을 여기서 고른다** — `RAG-007` 이 요구한 사람 라벨을 무작위로 고르면
    대부분 둘이 일치하는 쉬운 문항이라 judge 를 못 검증한다. 엇갈린 자리가 judge 가 무엇을
    다르게 보는지 드러나는 유일한 곳이다.

    두 방향이 각각 다른 것을 뜻한다:

      grounded ✓ · judge ✗   근거는 옳게 지목했는데 **답이 초점을 비껴갔다.** `I4` 의 자리다
      grounded ✗ · judge ✓   답은 됐는데 `must` 를 안 지목했다. **`D4` 가 여는 물음**이다 —
                            조 번호가 없는 소스는 인용할 것이 애초에 없다 (RAG-070 ③)
    """
    by_id = {j.id: j for j in judgments}
    out = []
    for qid in sorted(by_id.keys() & marks.keys()):
        _, grounded = marks[qid]
        j = by_id[qid]
        if grounded == j.answers_question:
            continue
        out.append({"id": qid, "grounded": grounded, "answers": j.answers_question,
                    "asked": j.asked, "rationale": j.rationale})
    return out


def agreement(reference: list[Judgment], candidate: list[Judgment]) -> dict[str, Any]:
    """두 판정 목록의 **일치율**. `RAG-007` 이 요구한 캘리브레이션이 이 수다.

    **사람 라벨도 판정 파일로 적는다** (`judge_model: human`). 파일 모양을 같게 둔 이유가
    이것이다 — 사람과 judge 를 견주는 코드와 judge 둘을 견주는 코드가 갈리면, 프롬프트를
    고칠 때마다 둘 중 하나가 낡는다.

    ⚠ **양쪽에 다 있는 문항만 본다** (`disagreements` · `score.flips` 와 같은 규약).
    사람 라벨은 보통 일부만 있으므로 **분모는 그 일부**다 — 그것이 정직한 분모다.

    `RAG-007` 은 *"변경 시 재캘리브레이션"* 이라 못 박았다. 프롬프트나 모델을 바꾸면
    이 함수를 다시 부르는 것이 그 요구의 전부다.
    """
    ref = {j.id: j for j in reference}
    cand = {j.id: j for j in candidate}
    shared = sorted(ref.keys() & cand.keys())
    mismatch = [{"id": qid,
                 "reference": ref[qid].answers_question,
                 "candidate": cand[qid].answers_question,
                 "reference_why": ref[qid].rationale,
                 "candidate_why": cand[qid].rationale}
                for qid in shared
                if ref[qid].answers_question != cand[qid].answers_question]
    return {"n": len(shared), "agreed": len(shared) - len(mismatch), "mismatch": mismatch}


def summarise(judgments: list[Judgment], marks: dict[str, tuple[bool, bool]]) -> dict[str, int]:
    """판정 요약 — 네 칸(`grounded` × `answers_question`)과 합계.

    **네 칸을 다 돌려준다.** 합계 하나로 줄이면 `D6`(RAG-071)이 *"총계 한 줄로 카드의 성패를
    말하면 안 된다"* 로 잡은 자리를 이 표가 다시 연다.
    """
    by_id = {j.id: j for j in judgments}
    cells = {(True, True): 0, (True, False): 0, (False, True): 0, (False, False): 0}
    for qid in by_id.keys() & marks.keys():
        cells[(marks[qid][1], by_id[qid].answers_question)] += 1
    return {
        "n": len(judgments),
        "answers": sum(j.answers_question for j in judgments),
        "both": cells[(True, True)],
        "grounded_only": cells[(True, False)],
        "answers_only": cells[(False, True)],
        "neither": cells[(False, False)],
    }
