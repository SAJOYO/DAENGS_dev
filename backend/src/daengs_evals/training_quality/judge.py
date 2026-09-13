"""LLM-as-a-judge — **답이 검색된 청크에 붙어 있나** (D-060 · RAG-007).

────────────────────────────────────────────────────────────────────────────
왜 이 축인가 — 앞의 두 판정기가 못 보는 자리
────────────────────────────────────────────────────────────────────────────
판정 하네스가 이미 둘이고, **둘 다 판정자에게 근거를 안 보여준다.**

    daengs_evals.answer_quality (#277)          오케스트레이터 답변률·품질   질문 + 답변만
    daengs_life/.../judge.py (#305)       Life RAG answers_question    질문 + 답변만

각각 이유가 있다 — #277 은 라우팅 정보를 주지 않으려고, #305 는 덤프에 청크 본문이 없어
*"코퍼스 없이 돈다"* 는 약속을 지키려고. **훈련 RAG 는 반대다.** 재려는 것이 *"답이 검색된
네 청크에 붙어 있는가"* 라, 청크를 안 보여주면 *"근거 있어 보이는가"* 라는 다른 질문이 된다.

그 차이가 실물로 드러난 자리가 있다. #277 의 판정 프롬프트 v1 은 앵커 `specific_law_fee` 를
놓쳤다 — flash-lite 도 pro 도 *"동물보호법 제47조"* 라는 **법령명을 출처로 봤다.** 근거를
안 보여주면 판정자는 "출처처럼 생긴 문자열" 을 근거로 센다.

────────────────────────────────────────────────────────────────────────────
축을 **하나만** 세운다
────────────────────────────────────────────────────────────────────────────
`RAG-007` 의 루브릭은 넷이고 여기 있는 것은 하나다. 나머지 셋을 안 세운 이유가 각각 있다:

  answers_question   **#305 가 이미 세운 자다.** 같은 축을 두 벌 만들면 같은 답변이 두 표에서
                     반대를 말할 수 있고, 그때 어느 쪽이 맞는지 가릴 자가 없다
  인용 정확도         `[N]` 이 그 주장을 담은 청크를 가리키는지. **이 축과 갈라야 한다** —
                     근거는 있는데 번호를 잘못 단 답과 근거가 아예 없는 답은 고치는 방법이
                     다르다. 이 축이 캘리브레이션을 통과한 뒤에 별도로 세운다
  Abstention         `service.py` 의 게이트가 코드로 정한다. 모델이 만든 문장이 아니라 우리
                     상수라, 루브릭으로 재면 판정자가 우리 상수를 채점한다 (D-060 ⑤)

────────────────────────────────────────────────────────────────────────────
judge 위생 — #305 를 그대로 잇는다 (D-060 ①)
────────────────────────────────────────────────────────────────────────────
  계열 분리     생성 Gemini(`gemini-3.1-flash-lite`) → judge **OpenAI**. 같은 계열의 위아래는
              self-preference 가 남는다 (RAG-007 의 "급 분리" 를 #305 가 계열까지 올렸다)
  버전 고정     `PROMPT_VERSION` 과 judge 모델명을 판정 파일에 같이 적는다. 프롬프트를 고치면
              번호를 올리고 **다시 캘리브레이션한다**
  판정 순서     `supported` → `unsupported` → `rationale` → `grounded`. **칸 순서가 곧 생성
              순서**라, 판정을 먼저 두면 모델이 답을 정해 놓고 이유를 붙인다
  결정성        `temperature=0`. 채점자가 비결정적이면 같은 답변이 랩마다 다른 점수를 받아
              랩 대조가 통째로 무의미해진다
  모델 핀       `gpt-5.4-2026-03-05` — 움직이는 별칭이면 두 판정 파일의 차이가 답변 때문인지
              judge 때문인지 안 갈린다 (`settings.openai_judge_model`)

⚠ **이 판정은 지표가 아니고 앞으로도 아니다.** `RAG-007` 이 *"사람 라벨 30개와 일치율로
캘리브레이션 **후** 신뢰"* 라고 못 박았는데, 2026-09-07 에 **사람 라벨을 진행하지 않기로**
정했다 (D-060 ⑦). 조건이 생기지 않으므로 `summarise` 의 비율을 어디에도 올리지 않는다.

그래도 이 모듈은 값이 있다 — 하는 일이 채점이 아니라 **사람이 볼 자리를 고르는 것**이고,
그 자리가 `disagreements()`다. 교차검증(`agreement`)을 붙일 수는 있지만 그것은 **판정자 간
일치율**이지 캘리브레이션이 아니다 — `docs/training/judge_codex_handoff.md`.

⚠ **`RAG-075` 를 읽고 시작한다.** #305 는 *"judge 가 `I4` 를 통과시켰다"* 를 실패로 적었는데,
사람이 라벨링해 보니 **사람도 「답함」이었다** — 틀린 것은 judge 가 아니라 사람의 읽기였고,
judge 는 오히려 **엄한 쪽으로** 틀렸다. 그 카드가 뽑은 뿌리가 *"재는 자 없이 판단했다"* 다.
그래서 이 모듈의 앵커는 **의견이 아니라 확인 가능한 사실**로만 짓는다 (`anchors.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

VERSION = 1  # 판정 파일 스키마
PROMPT_VERSION = 2  # 프롬프트. **고치면 올리고 재캘리브레이션한다** (RAG-007)
#
# v2 (2026-09-07): 첫 실제 랩(`lap1`)이 오탐 하나를 드러냈다. `t18` — *"장난감을 항상 옆에 두는
# 게 좋은가요?"* 에 답변이 *"아니요, 좋지 않습니다"* 로 시작했는데 judge 가 **그 한 마디를
# 근거 없는 주장으로** 잡았다. 자료 2 는 *"늘 반려견 옆에 장난감이 항상 있다는 것은 호기심
# 유발을 시키는데 큰 도움이 되지 않습니다"* 라고 그대로 말한다 — 문자열로 확인되는 오탐이다.
#
# 고친 것은 둘이다: 예/아니오 입장 표명은 별개 주장이 아니라는 것, 연결어와 요약 방식은
# 채점 대상이 아니라는 것. **`t18` 하나에 맞춘 것이 아니라** yes/no 질문 전반에 걸리는
# 구멍이라 일반 규칙으로 적었다 (`RAG-066` 의 *"값을 보인 낱말만 넣는다"* 와 같은 조심).

RUBRIC = "grounded"

#: 청크를 몇 자까지 프롬프트에 싣나. 훈련 청크는 보통 이 안에 들어가고, 넘치는 것은 잘라서
#: 넣되 **자른 사실을 판정자에게 말해 준다**(`_render_chunks`). 말 안 하고 자르면 판정자가
#: "자료에 없다" 와 "잘려서 안 보인다" 를 구분할 수 없다.
MAX_CHUNK_CHARS = 2_000

# 프롬프트. **답이 좋은지가 아니라 자료에 붙어 있는지를 묻는다.**
#
# ⚠ 정적 지시문이 **맨 앞**이고 질문·답변·자료가 뒤다. 프롬프트 캐시의 접두사를 길게 잡으려는
#   것이고(입력이 출력의 2배 이상이다), 문항마다 달라지는 부분을 뒤로 몰아야 그 캐시가 산다.
#
# ⚠ 앵커 문항을 프롬프트에 예시로 박지 않는다 — 그 문항에서만 잘 맞는 채점자가 된다
#   (#305 가 I4 를 모양만 쓴 것과 같은 조심).
PROMPT = """너는 반려견 훈련 질의응답의 채점자다.

[자료]에 있는 것만이 답변이 쓸 수 있는 근거다. 답변의 모든 주장이 [자료]로 뒷받침되는지만
판정해라.

판정 기준:
- 답변을 주장 단위로 쪼개라. 각 주장이 [자료]의 어느 대목에서 나왔는지 찾아라.
- **[자료]에 없는 구체적 내용이 하나라도 있으면 거짓이다.** 구체적 내용이란 방법·수치·기간·
  도구·금지 사항처럼 사람이 따라 할 수 있는 것을 말한다.
- **세상에서 사실인지는 묻지 않는다.** 옳은 말이어도 [자료]에 없으면 거짓이다. 너의 사전
  지식으로 빈자리를 메우지 마라.
- 자료를 다른 말로 바꿔 쓴 것은 참이다. 표현이 달라도 같은 내용이면 뒷받침된 것이다.
- 자료 여럿에 걸쳐 있어도 참이다. 한 주장이 [자료 1]과 [자료 2]에 나뉘어 있어도 된다.
- 일반적인 맺음말("수의사와 상의하세요", "천천히 하세요")은 구체적 내용이 아니므로 넘어간다.
- **예/아니오 같은 입장 표명은 별개 주장이 아니다.** "아니요, 좋지 않습니다"처럼 질문에
  답하는 한 마디는, 그 판단의 **근거가 자료에 있으면** 뒷받침된 것으로 본다. 자료가
  "도움이 되지 않는다"고 말하는데 답변이 "좋지 않습니다"라고 한 것을 따로 떼어 근거 없다고
  하지 마라. 이어지는 문장들이 그 입장의 근거다.
- **글을 잇는 표현은 주장이 아니다.** "따라서", "이로 인해", "다음과 같습니다" 같은 연결어나
  자료를 요약·재배열한 방식은 채점하지 마라. 채점하는 것은 **자료에 없는 내용**이지 자료를
  옮긴 방식이 아니다.
- `[N]` 인용 번호가 맞는지는 **묻지 않는다.** 그것은 다른 자가 잰다 — 번호가 틀려도 내용이
  자료에 있으면 이 축에서는 참이다.

먼저 뒷받침되는 주장(supported)과 안 되는 주장(unsupported)을 각각 적어라.

그 다음 판정은 **네가 적은 목록에서 기계적으로 나온다**:

    unsupported 가 비어 있다  →  grounded = true
    unsupported 에 하나라도 있다  →  grounded = false

**예외는 없다.** 빠진 내용이 사소해 보여도, 답변이 전체적으로 훌륭해도, 나머지 주장이 전부
자료에 있어도 마찬가지다. unsupported 에 항목을 적어 놓고 grounded 를 참으로 두는 것은
**틀린 판정이다.** 사소하다고 판단했으면 애초에 unsupported 에 적지 마라 — 적을지 말지에서
판단하고, 적은 뒤에는 판단하지 마라.

[질문]
{question}

[자료]
{chunks}

[답변]
{answer}
"""


class Verdict(BaseModel):
    """judge 가 돌려주는 판정.

    **칸 순서가 곧 생성 순서다.** `supported`·`unsupported` 를 먼저 적게 해서 모델이 주장을
    실제로 훑은 뒤에 판정하게 한다 — `grounded` 를 위에 두면 판정을 정해 놓고 목록을 맞춘다.
    순서를 바꾸는 것은 프롬프트를 고치는 것과 같으므로 `PROMPT_VERSION` 을 올려야 한다.

    `unsupported` 가 이 축의 산출물이다 — 사람이 판정을 검산할 때 여기부터 본다. 비어 있는데
    `grounded=False` 면 그 판정은 못 믿는다 (`disagreements` 가 그것을 골라낸다).
    """

    model_config = ConfigDict(extra="forbid")

    supported: list[str]  # 자료로 뒷받침되는 주장
    unsupported: list[str]  # 자료에 없는 주장 — 이 축의 산출물
    rationale: str
    grounded: bool


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "judgment"
    id: str
    grounded: bool
    supported: list[str]
    unsupported: list[str]
    rationale: str


class JudgeHeader(BaseModel):
    """판정 파일 한 장의 전제. **judge 모델과 프롬프트 버전이 여기 있어야** 두 판정 파일의
    차이가 답변 때문인지 judge 때문인지 갈린다 (#305 의 `JudgeHeader` 와 같은 규약)."""

    model_config = ConfigDict(extra="forbid")

    type: str = "header"
    version: int = VERSION
    lap: str
    judged_at: str
    judge_model: str
    prompt_version: int
    rubric: str = RUBRIC
    items: int


def client(api_key: str | None = None) -> Any:
    """지연 생성. **키가 없으면 여기서 죽는다** (#305 의 `client()` 와 같은 규약).

    타임아웃을 여기서 건다. 안 걸면 SDK 기본값에 맡기게 되는데, 배치라 하나가 물리면 사람이
    멈춘 줄 모르고 기다린다. 단위는 **초**다 (`GEMINI_TIMEOUT_MS` 와 다르다).
    """
    from openai import OpenAI

    from daengs_backend.config import settings

    key = api_key or settings.openai_api_key.get_secret_value().strip()
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY 가 없습니다 — backend/.env 를 확인하세요 (루트 .env 가 아닙니다). "
            "judge 는 생성과 **다른 계열**을 씁니다 (RAG-007 · D15 · D-060 ①)."
        )
    return OpenAI(api_key=key, timeout=settings.openai_timeout_s)


def _render_chunks(chunks: list[dict[str, Any]]) -> str:
    """청크를 판정 프롬프트의 [자료] 로. **자른 것은 잘랐다고 말한다.**

    말 안 하고 자르면 판정자가 *"자료에 없다"* 와 *"잘려서 안 보인다"* 를 구분할 수 없고,
    그 둘은 사람이 고치는 방법이 다르다.
    """
    out = []
    for i, chunk in enumerate(chunks, start=1):
        text = str(chunk.get("text", ""))
        clipped = text[:MAX_CHUNK_CHARS]
        tail = "\n… (이 자료는 여기서 잘렸습니다)" if len(text) > MAX_CHUNK_CHARS else ""
        head = " › ".join(str(p) for p in (chunk.get("heading_path") or []))
        label = f"[자료 {i}] {chunk.get('document_id', '?')}" + (f" — {head}" if head else "")
        out.append(f"{label}\n{clipped}{tail}")
    return "\n\n".join(out)


def build_prompt(question: str, answer: str, chunks: list[dict[str, Any]]) -> str:
    return PROMPT.format(
        question=question.strip(),
        chunks=_render_chunks(chunks),
        answer=answer.strip(),
    )


def judge_one(
    question: str,
    answer: str,
    chunks: list[dict[str, Any]],
    *,
    cli: Any = None,
    model: str | None = None,
) -> Verdict:
    """문항 하나 → 판정 하나. **순수하다** — 덤프도 DB 도 안 본다.

    #305 의 `judge_one` 과 같은 이유다: 테스트가 문자열과 리스트만으로 프롬프트 조립과
    파싱을 붙잡을 수 있어야 한다.
    """
    from daengs_backend.config import settings

    name = model or settings.openai_judge_model
    cli = cli or client()
    resp = cli.responses.parse(
        model=name,
        input=build_prompt(question, answer, chunks),
        # **스키마를 붙여서 받는다** — 프롬프트로 JSON 을 부탁하는 것과 다르다.
        text_format=Verdict,
        temperature=0,
    )
    parsed = resp.output_parsed
    if parsed is None:
        raise RuntimeError(f"judge 가 판정을 안 냈습니다 — {name}")
    return parsed


def judge_rows(
    rows: list[dict[str, Any]],
    *,
    cli: Any = None,
    model: str | None = None,
    on_item: Any = None,
) -> list[Judgment]:
    """덤프 한 장 → 판정 목록.

    **`decision != "ANSWER"` 인 행은 뺀다** (D-060 ⑤). `REFUSE`·`MEDICAL_REFUSAL`·`UNCERTAIN`
    의 문구는 모델이 만든 것이 아니라 `service.py` 의 상수라, 채점하면 판정자가 우리 상수를
    채점한다. 그 경로가 옳게 발동했는가는 게이트의 자(`retrieval-gate/`)가 이미 본다.

    **청크가 없는 행도 뺀다.** 이 축은 근거를 보고 재는 것이라, 근거 없이 부르면 판정자가
    자기 사전 지식으로 채점한다 — 그것이 정확히 이 모듈이 피하려는 실패다.
    """
    out: list[Judgment] = []
    for row in rows:
        qid = str(row.get("id", ""))
        if not qid or row.get("decision") != "ANSWER":
            continue
        chunks = row.get("chunks") or []
        if not chunks:
            continue
        verdict = judge_one(
            row.get("question", ""), row.get("answer", ""), chunks, cli=cli, model=model
        )
        judgment = Judgment(
            id=qid,
            grounded=verdict.grounded,
            supported=[s.strip() for s in verdict.supported],
            unsupported=[s.strip() for s in verdict.unsupported],
            rationale=verdict.rationale.strip(),
        )
        out.append(judgment)
        if on_item:
            on_item(judgment)
    return out


def header(lap: str, items: list[Judgment], model: str | None = None) -> JudgeHeader:
    from daengs_backend.config import settings

    return JudgeHeader(
        lap=lap,
        judged_at=datetime.now(UTC).isoformat(timespec="seconds"),
        judge_model=model or settings.openai_judge_model,
        prompt_version=PROMPT_VERSION,
        items=len(items),
    )


def agreement(reference: list[Judgment], candidate: list[Judgment]) -> dict[str, Any]:
    """두 판정 목록의 **일치율**. `RAG-007` 이 요구한 캘리브레이션이 이 수다.

    **다른 판정자도 같은 판정 파일로 적는다.** 파일 모양을 같게 둔 이유가 이것이다 —
    사람↔judge 를 견주는 코드와 judge↔judge 를 견주는 코드가 갈리면 프롬프트를 고칠 때마다
    둘 중 하나가 낡는다 (#305 · `RAG-075` ①).

    ⚠ **여기서 나오는 수를 캘리브레이션으로 읽지 마라** (D-060 ⑦). 사람 라벨은 진행하지
    않기로 했고, 남는 상대는 다른 LLM 이다. **LLM 둘이 일치하는 것은 둘이 같은 맹점을
    공유하는 것일 수도 있다.** 특히 상대가 GPT 계열이면 judge 와 같은 계열이라 D-060 ① 이
    일부러 갈라 둔 self-preference 분리가 무너져 이 수가 부풀려진다. `judge_model` 에
    실제 모델명을 적게 한 이유가 그것이다 — `human` 으로 위장하면 아무도 구분하지 못한다.

    ⚠ **양쪽에 다 있는 문항만 본다.** 사람 라벨은 보통 일부만 있으므로 **분모는 그 일부**다 —
    안 본 문항을 일치로 세면 라벨을 안 단 만큼 점수가 올라간다. 그것이 정직한 분모다.
    """
    ref = {j.id: j for j in reference}
    cand = {j.id: j for j in candidate}
    shared = sorted(ref.keys() & cand.keys())
    mismatch = [
        {
            "id": qid,
            "reference": ref[qid].grounded,
            "candidate": cand[qid].grounded,
            "reference_why": ref[qid].rationale,
            "candidate_unsupported": cand[qid].unsupported,
        }
        for qid in shared
        if ref[qid].grounded != cand[qid].grounded
    ]
    return {"n": len(shared), "agreed": len(shared) - len(mismatch), "mismatch": mismatch}


def disagreements(
    judgments: list[Judgment], rows: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """**사람이 라벨링할 문항을 고른다.** 이 모듈의 지금 산출물이다.

    무작위로 고르면 대부분 쉬운 문항이라 judge 를 못 검증한다 (#305 의 `disagreements` 와 같은
    판단). 이 축에서 캐낼 자리는 판정자가 **스스로 흔들린 곳**이다:

      grounded=False 인데 `unsupported` 가 비었다   판정과 근거가 어긋난다. 못 믿을 판정이다
      grounded=True  인데 `unsupported` 가 있다     같은 자리의 반대 방향
      `unsupported` 가 하나뿐이다                   경계선 문항 — 사람과 갈릴 확률이 높다

    셋 다 **판정 내부의 모순이나 경계**라, 정답을 몰라도 고를 수 있다. 사람이 답을 아는 문항을
    골라 오는 것이 아니라 **judge 가 자신 없어 하는 문항**을 고르는 것이 캘리브레이션이다.
    """
    by_id = {row.get("id"): row for row in (rows or [])}
    out = []
    for judgment in judgments:
        if not judgment.grounded and not judgment.unsupported:
            reason = "판정은 거짓인데 근거로 든 주장이 없다"
        elif judgment.grounded and judgment.unsupported:
            reason = "판정은 참인데 뒷받침 안 되는 주장을 적었다"
        elif not judgment.grounded and len(judgment.unsupported) == 1:
            reason = "뒷받침 안 되는 주장이 하나뿐 — 경계선"
        else:
            continue
        row = by_id.get(judgment.id) or {}
        out.append(
            {
                "id": judgment.id,
                "why_review": reason,
                "grounded": judgment.grounded,
                "unsupported": judgment.unsupported,
                "rationale": judgment.rationale,
                "question": row.get("question", ""),
            }
        )
    return out


def summarise(judgments: list[Judgment]) -> dict[str, Any]:
    """판정 요약. **합계 하나로 줄이지 않는다** — `D6`(RAG-071)이 *"총계 한 줄로 카드의 성패를
    말하면 안 된다"* 로 잡은 자리다."""
    total = len(judgments)
    grounded = sum(1 for j in judgments if j.grounded)
    unsupported_counts = [len(j.unsupported) for j in judgments if not j.grounded]
    return {
        "n": total,
        "grounded": grounded,
        "not_grounded": total - grounded,
        "review_needed": len(disagreements(judgments)),
        "unsupported_claims_total": sum(unsupported_counts),
        "unsupported_claims_max": max(unsupported_counts, default=0),
    }
