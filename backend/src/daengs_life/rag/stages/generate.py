"""9단계 생성 — 검색 결과 위에 Gemini 로 답을 만든다 (RAG-028).

**조립 순서가 사는 곳이 여기다** (RAG-028 ③). `/life/ask` 와 `rag generate` 가 같은 순서
(`인코딩 → search → 프롬프트 → Gemini`)를 밟는데, 그것을 라우터와 CLI 에 각각 적으면 RAG-003 이
8·9단계 사이에 끼는 날 고칠 곳이 둘이 된다 — `pipeline.py` 가 이미 그 자리를 예고해 놨다.
RAG-027 은 이 조립을 `app/services/ask.py` 에 두라고 하지만, `rag` 는 `app` 을 import 할 수 없어
(RAG-014) **`rag generate --questions`(검문소④)가 그 코드를 못 쓴다.** 그러면 검문소가 서빙과
다른 코드를 검사하게 되고, 그것이 RAG-026 ②가 8단계에서 막은 상태 그대로다.

함수가 둘인 이유 —
- `answer(question, hits)` 는 **순수**하다. 검색 결과를 받으므로 DB 도 모델도 없이 테스트된다
- `ask(...)` 가 `search` 와 그것을 잇는다. **순서는 여기 한 번만 적힌다**

**프롬프트는 일부러 순진하다.** 검문소③이 이미 알려준 것은 "검증질문 7개 중 5개는 top-5 안에
정답 조항이 없다"이고, 1랩이 **새로** 알려주는 것은 그 상태에서 LLM 이 지어내는지 물러서는지다.
근거 부족 시 거부를 프롬프트로 부탁할지 구조로 막을지는 **한 랩 돌려본 뒤** 정한다(RAG-029).
지금 방어 장치를 넣으면 관찰하려던 것을 지운다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..core import config
from . import embed, goldenset, load, search
from .search import Hit

VERSION = 3      # 2 = 생성이 낸 boundary·covered 가 행에 있다 (RAG-055)
                 # 3 = 반려견 프로필(`dog`)이 행에 있다 (RAG-056 · 로드맵 B4)

# 질문이 걸린 경계 (RAG-055). `medical`·`emergency` 는 로드맵 §2 가 "이 개의 몸에 대한 판단"
# 으로 묶은 것이고 RAG-008 ③ 이 "문서가 아니라 판단"이라고 가른 자리다.
Boundary = Literal["none", "medical", "emergency"]

# 답변에 조항 번호를 요구한다 — KPI("출처 링크 + 조항 번호 인용") 자체이고, 요구하지 않으면
# 검문소④가 셀 것이 없어진다.
#
# **판단 둘을 답변과 같은 호출에서 받는다** (RAG-055). 카드 #177 이 방식 (a)/(b) 를 열어 뒀고
# lap15 가 (a) 를 가리켰다 — 문장을 읽는 신호로는 **조용한 재해석**을 못 잡기 때문이다. B2
# ("우주선에 강아지 태우는 규정")에서 모델은 물러섰다는 말도, 고쳐 읽었다는 말도 없이 항공
# 규정을 확신 있게 답했다. 밖에서 답변 텍스트를 아무리 읽어도 그 일은 안 보이고, **모델
# 자신에게 묻는 것**만 남는다.
#
# 별도 분류 호출(b)을 안 고른 이유는 값이 아니라 정합성이다 — 같은 컨텍스트를 본 같은 호출이
# `covered` 를 말해야 "이 근거로 답했다"와 "이 근거로는 부족하다"가 같은 판단에서 나온다.
# 호출이 둘이면 분류기가 본 것과 답변이 선 것이 갈릴 수 있고, 그 어긋남은 로그에 안 보인다.
#
# ⚠️ **키워드 하드 규칙이 아니다** (#80 routing §1 이 금하는 것). `boundary` 는 모델이 질문을
# 읽고 정하는 값이고, 여기 있는 것은 그 기준의 서술이다.
#
# **거절 문장을 여기서 만드는 것도 결정이다** (RAG-055). 어댑터는 Life 가 준 문장을 그대로
# `refusal.message` 로 옮긴다(불변식 3 무손실). 그러면 그 문장을 누가 쓰느냐만 남는데, 서빙이
# 고정 문구를 끼우면 **Life 가 무엇을 봤는지가 사라진다.** lap17 의 B4·B5 가 그 필요를 보여
# 줬다 — 경계로는 옳게 갈랐는데 문장이 *"참고자료에는 …이 포함되어 있지 않습니다"* 여서,
# 거절이 아니라 기권처럼 읽혔다. 사용자에게 필요한 것은 자료 이야기가 아니라 "지금 병원에".
#
# ⚠️ **답변을 줄이지 말라는 문단이 장식이 아니다.** 처음 판(lap16)은 판단 둘의 설명이 앞에 오고
# 답변 지시가 한 줄이었는데, 답변 평균 길이가 508자에서 198자로 반토막 나면서 `cited` 가
# 17/28 → 11/28 로 떨어졌다. 조항을 여럿 들어야 하는 보험·운송 문항(I1·I2·I3·I5·T3)이 통째로
# 인용을 잃었다 — **틀린 답이 된 게 아니라 요약이 됐다.** 구조화 출력을 붙일 때 답변 쪽 요구를
# 같이 세워 두지 않으면 모델이 스키마를 채우는 일에 무게를 옮긴다.
PROMPT = """당신은 한국의 반려동물 관련 제도를 안내하는 도우미입니다.

아래 [참고자료]를 근거로 [질문]에 답하세요.
답변에는 근거가 된 **법령명과 조항 번호**를 함께 밝히고, 사용한 자료의 번호를 [1] 처럼 표시하세요.

**답변(`answer`)은 줄이지 마세요.** 질문에 걸리는 조항이 여럿이면 **전부** 들고, 금액·기한·
조건 같은 구체적인 값을 자료에 있는 그대로 적으세요. 요약하지 말고 물은 것에 끝까지 답하세요.

답변과 함께 판단 둘을 내세요.

**boundary** — 이 질문이 어디에 속하는가.
- `emergency`: 지금 이 동물에게 벌어진 일에 대한 대처를 묻는다. 이물질을 삼켰다, 다쳤다,
  쓰러졌다처럼 시간이 걸린 상황.
- `medical`: 이 동물의 몸에 대한 판단을 묻는다. 증상의 원인, 진단, 약의 종류나 용량.
- `none`: 그 밖의 전부. 제도·비용·절차·이동·보험처럼 문서로 답할 수 있는 것.
**"먹여도 되나요"는 `none` 입니다** — 급여해도 되는지는 자료에 적힌 사실이지 이 동물의 몸에
대한 판단이 아닙니다. 같은 음식이라도 **"먹었어요"** 는 벌어진 일이므로 `emergency` 입니다.
`emergency` 와 `medical` 둘 다에 해당하면 `emergency` 입니다.

`boundary` 가 `medical` 이나 `emergency` 면, 답변은 **자료에 무엇이 있고 없는지를 따지지 말고**
사용자가 지금 할 일부터 말하세요 — `emergency` 는 지체 없이 동물병원에 갈 것, `medical` 은
수의사의 진료로 판단할 일이라는 것. 이 답변은 사용자에게 **거절 문구로 그대로 보입니다.**

**covered** — [참고자료]가 [질문]이 물은 것에 실제로 답하는가.
질문의 낱말을 다른 뜻으로 바꿔 읽어야 자료가 맞아떨어진다면 `false` 입니다.
비슷한 주제일 뿐 물은 값이 자료에 없어도 `false` 입니다.
자료로 답할 수 있으면 `true` 이고, 이때 답변을 줄일 이유는 없습니다.

[참고자료]
{context}

[질문] {question}
"""

# 반려견 프로필 블록 (로드맵 B4). **`PROMPT` 뒤에 붙인다** — 프로필이 없을 때 프롬프트가
# 지금과 한 글자도 달라지지 않아야 lap18 과 lap19 를 같은 축에서 비교할 수 있다. `{dog}` 슬롯을
# 본문에 파 두면 빈 문자열이어도 줄바꿈이 남아 그 비교가 깨진다.
#
# **맹견 판정을 코드가 하지 않는다.** 시행규칙의 5종 목록은 코퍼스에 있고(law_animal_protection),
# `breed` 는 앱이 정하는 어휘라 여기서 문자열 집합을 들고 있으면 앱에 견종이 하나 늘 때마다
# 조용히 어긋난다. 잡종("그 잡종의 개")까지 코드로 가르려 들면 더 그렇다.
#
# ⚠️ **프로필로 조항을 만들지 말라는 문단이 이 블록의 전부다.** 견종·나이는 [참고자료]가 이미
# 견종이나 나이로 답을 가를 때 그 갈래를 고르는 데만 쓴다. 이것이 없으면 A3a 가 세운 경계가
# 무너지는 방식이 특히 나쁘다 — 근거 없이 지어낸 답이 **이 아이에게 맞춘 답처럼** 보인다.
DOG_BLOCK = """
[반려견] {facts}

[참고자료]가 견종이나 나이에 따라 답을 가르는 경우에만 위 정보를 쓰세요. 해당하는 갈래를
고르고, 왜 그 갈래인지를 한 줄로 밝히세요.
[참고자료]에 없는 기준을 이 정보로 만들어 내지 마세요 — 견종이나 나이만으로는 알 수 없는
것을 물었다면 그렇게 답하세요.
"""

_ITEM = "[{n}] {citation} — {title}{section}\n{content}"

# 답변에서 조항 번호를 뽑는 정규식. `제15조` · `제15조의2` 를 잡는다.
# **항·호까지 잡지 않는 이유** — 검색 단위(청크)가 조 단위라 항까지 대조하면 실재하는데도
# 없다고 세게 된다. 검문소④는 "지어냈는가"를 보는 것이지 인용의 정밀도를 보는 것이 아니다.
ARTICLE_RE = re.compile(r"제\d+조(?:의\d+)?")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Answer:
    """답변 하나 + **그 답을 만든 근거 전부**.

    `hits` 를 들고 다니는 것이 RAG-028 ②다 — 1랩의 `/life/ask` 는 제품이 아니라 **검문소④가 읽는 관찰
    도구**이고, 무엇을 컨텍스트로 줬는지 함께 말하지 않으면 "인용한 조항이 실재했나"를 셀 수 없다.
    """
    question: str
    text: str
    hits: list[Hit]
    model: str                          # 실제로 답을 만든 Gemini 모델
    embedding_model: str                # 검색에 쓴 임베딩 모델 — 둘 다 있어야 랩 비교가 성립한다
    cited: list[str] = field(default_factory=list)        # 답변에 등장한 조항 번호 (등장 순)
    ungrounded: list[str] = field(default_factory=list)   # 그중 컨텍스트에 없는 것 = 검문소④
    # 생성이 함께 낸 판단 둘 (RAG-055). **기본값이 "답한다" 쪽인 것은 의도다** — 구조화 출력이
    # 실패하거나 옛 덤프를 읽을 때 조용히 거절·기권으로 바뀌면 그것이 더 나쁘다
    boundary: Boundary = "none"
    covered: bool = True
    # 이 답을 만들 때 쓴 반려견 프로필 (RAG-056). **덤프에 남아야 한다** — 안 남기면 같은
    # 문항의 두 랩이 왜 다른 답을 냈는지 아무도 못 가른다. 없으면 프로필 없이 물은 것이다
    dog: "DogProfile | None" = None

    @property
    def grounded(self) -> list[str]:
        return [a for a in self.cited if a not in self.ungrounded]


# ---------------------------------------------------------------- 프롬프트
def build_context(hits: list[Hit]) -> str:
    """참고자료 블록. **`content` 를 자르지 않는다** (RAG-028 ②).

    검문소③ B 가 찾은 상황 때문이다 — easylaw 해설 청크는 `citation` 이 해설 주소인데 **본문 안에
    「동물보호법」 제2조 같은 조항 번호가 박혀 있다.** 자르면 모델이 그걸 보고 인용했는지, 지어냈는지
    구분할 수 없게 된다.
    """
    return "\n\n".join(
        _ITEM.format(n=h.rank, citation=h.citation or h.chunk_id,
                     title=h.document_title,
                     section=f" · {h.section}" if h.section else "",
                     content=h.content)
        for h in hits
    )


@dataclass(frozen=True)
class DogProfile:
    """Life 가 받는 반려견 사실 둘 (로드맵 B4).

    어댑터가 원시값으로 넘기고 여기서 모양을 갖는다 — `daengs_backend` 의 `DogContext` 를
    import 하면 `rag` 가 `app` 은커녕 오케스트레이션까지 의존하게 된다 (RAG-014).
    """

    breed: str | None = None
    age_months: int | None = None

    @property
    def has_facts(self) -> bool:
        return bool(self.breed) or self.age_months is not None

    def describe(self) -> str:
        """`[반려견]` 줄. **개월을 그대로 쓰지 않는다** — "38개월"보다 "3년 2개월"이 조문의
        연령 조건(만 나이)과 맞대 보기 쉽고, 모델이 단위를 헷갈릴 자리가 준다.
        """
        parts = []
        if self.breed:
            parts.append(f"견종: {self.breed}")
        if self.age_months is not None:
            years, months = divmod(self.age_months, 12)
            age = f"{years}년 {months}개월" if years else f"{months}개월"
            parts.append(f"나이: {age} (만 {years}세)")
        return " · ".join(parts)


def build_prompt(question: str, hits: list[Hit], *, dog: DogProfile | None = None) -> str:
    prompt = PROMPT.format(context=build_context(hits), question=question)
    if dog is None or not dog.has_facts:
        return prompt
    return prompt + DOG_BLOCK.format(facts=dog.describe())


class Verdict(BaseModel):
    """생성이 한 호출에서 내는 것 전부 (RAG-055). Gemini 구조화 출력의 스키마 그대로다.

    ⚠️ **`_Base` 를 안 쓴다.** `extra="forbid"` 가 JSON 스키마에 `additionalProperties` 로 나가는데
    Gemini 가 그 낱말을 모른다 — `400 INVALID_ARGUMENT ... Unknown name "additional_properties"`.
    이 파일의 다른 모델과 다른 이유가 그것이고, 여기서는 잃는 것도 없다: 이 스키마는 우리가
    받는 쪽이지 우리가 쓰는 쪽이 아니라, 모델이 칸을 더 붙여도 무시하면 그만이다.

    **`answer` 가 여기 들어와도 답변의 모양은 안 바뀐다** — 조항 번호와 `[N]` 표기를 그대로
    요구하므로 `cited_articles` 와 `score.referenced_indices` 가 보던 것이 그대로 있다.
    그것이 이 카드가 랩 비교를 유지하는 방법이다.
    """
    answer: str
    boundary: Boundary
    covered: bool


def parse_verdict(raw: str) -> Verdict | None:
    """구조화 출력 → `Verdict`. **못 읽으면 `None` 이고, 부르는 쪽이 물러선다.**

    조용히 기본값을 만들지 않는 이유는 그 기본값이 "답한다" 쪽이어서다 — 파싱이 깨진 것을
    "경계 아님 · 근거 충분"으로 읽으면 거절해야 할 질문이 통과한다. 부르는 쪽이 답변 텍스트는
    살리되 판단 둘은 **기본값**으로 두고, 그 사실이 로그에 남는다.
    """
    try:
        return Verdict.model_validate_json(raw)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- 검문소④
def cited_articles(text: str) -> list[str]:
    """답변에 등장한 조항 번호 (중복 제거, 등장 순)."""
    seen: dict[str, None] = {}
    for m in ARTICLE_RE.findall(text):
        seen.setdefault(m, None)
    return list(seen)


def ungrounded_articles(text: str, hits: list[Hit]) -> list[str]:
    """답변이 든 조항 번호 중 **컨텍스트에 없는 것**. 검문소④가 세는 수 그대로다.

    ⚠️ **느슨한 검사다.** `제2조` 가 컨텍스트의 *다른 법령* 제2조로 맞아 버릴 수 있어 실제보다
    적게 나온다. 그래도 이 방향의 오차가 옳다 — 1 이상이면 그것은 **확실히** 지어낸 것이고,
    "0이면 프롬프트로 충분하다"는 판정은 RAG-029 에서 더 엄한 검사로 다시 본다.
    법령명까지 짝지어 대조하는 것이 KPI 에 맞지만, 그 정밀도는 관찰이 아니라 **방어 장치**의
    일이고 방어 장치는 1랩을 보고 나서 정한다.
    """
    haystack = "\n".join(f"{h.citation}\n{h.content}" for h in hits)
    return [a for a in cited_articles(text) if a not in haystack]


# ---------------------------------------------------------------- 생성
def _client(api_key: str | None = None):
    """지연 생성. 키가 없으면 여기서 죽는다 — 검색까지 다 해 놓고 마지막에 죽지 않게.

    **타임아웃을 여기서 건다.** 안 걸면 무제한이라, 상류가 물리면 `/life/ask` 를 돌리는 스레드풀
    워커를 그대로 잡고 있는다 — FastAPI 가 `def` 컨트롤러를 스레드풀에서 돌리므로(RAG-028 ④)
    그 워커는 다른 요청도 못 받는다.

    ⚠ `HttpOptions.timeout` 은 **밀리초**다. 설정 이름이 `gemini_timeout_ms` 인 이유이고,
    여기서 단위를 바꾸지 않는다 — 변환을 한 번 끼우면 두 곳이 서로를 믿어야 한다.
    """
    from google import genai
    from google.genai import types

    key = api_key or config.settings.gemini_api_key
    if not key:
        raise RuntimeError("GEMINI_API_KEY 가 없다 — backend/.env 를 확인할 것")
    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(timeout=config.settings.gemini_timeout_ms),
    )


def answer(question: str, hits: list[Hit], *, client=None, model: str | None = None,
           embedding_model: str | None = None, dog: DogProfile | None = None) -> Answer:
    """**순수하다** — 검색 결과를 받는다. DB 도 임베딩 모델도 안 만진다.

    나눠 둔 이유는 `search()` 가 `conn` 을 받게 한 것과 같다: 테스트가 손으로 만든 `Hit` 몇 개로
    프롬프트 조립과 검문소④ 산식을 붙잡을 수 있어야 하고, 그러려면 이 함수가 Gemini 말고는
    아무것도 필요로 하면 안 된다.
    """
    from google.genai import types

    name = model or config.settings.gemini_model
    cli = client or _client()
    resp = cli.models.generate_content(
        model=name, contents=build_prompt(question, hits, dog=dog),
        # **스키마를 붙여서 받는다** (RAG-055). 프롬프트로 JSON 을 부탁하는 것과 다르다 —
        # 부탁은 모델이 산문으로 새면 그만이고, 그 새는 날이 하필 거절해야 할 질문일 수 있다
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           response_schema=Verdict),
    )
    verdict = parse_verdict((resp.text or "").strip())
    if verdict is None:
        # 판단은 못 받았지만 **답변까지 버리지는 않는다.** 기본값은 "답한다" 쪽이고,
        # 그 선택이 위험한 자리는 서빙(`services/ask.py`)이 아니라 여기가 아니다
        text = (resp.text or "").strip()
        return Answer(
            question=question, text=text, hits=hits, model=name,
            embedding_model=embedding_model or config.settings.embedding_model_key,
            cited=cited_articles(text), ungrounded=ungrounded_articles(text, hits),
            dog=dog if dog and dog.has_facts else None,
        )

    text = verdict.answer.strip()
    return Answer(
        question=question, text=text, hits=hits, model=name,
        embedding_model=embedding_model or config.settings.embedding_model_key,
        cited=cited_articles(text), ungrounded=ungrounded_articles(text, hits),
        boundary=verdict.boundary, covered=verdict.covered,
        dog=dog if dog and dog.has_facts else None,
    )


def ask(question: str, *, k: int = search.DEFAULT_K, include_supplementary: bool = True,
        category: str | None = None, model_key: str | None = None,
        st=None, conn=None, client=None, model: str | None = None,
        dog: DogProfile | None = None) -> Answer:
    """질문 하나 → 답 하나. **9단계의 순서가 이 세 줄이다.**

    `st`(임베딩 모델)·`conn`(DB)·`client`(Gemini) 셋 다 **받으면 만들지도 닫지도 않는다** — ①의
    규약이고 `search(conn=None)` 의 `own` 패턴 그대로다. CLI 는 7문항을 돌며 모델을 한 번만
    올리고, 서버는 lifespan 이 올린 것을 넘긴다.

    `include_supplementary` 기본이 `True` 인 것은 **엔진의 기본값**이지 서빙의 기본값이 아니다.
    서빙 정책은 `app/services/ask.py` 가 갖는다 — RAG-026 ①이 *"검사 도구와 서빙이 같은 기본값을
    쓸 이유가 없다"* 며 비워 둔 자리이고, 값은 1랩을 돌고 정한다.
    """
    key = model_key or config.settings.embedding_model_key
    # `st` 가 있으면 올라와 있는 모델을 그대로 쓰고, 없으면 `encode` 가 올렸다 내린다.
    # **분기가 사라졌다** (RAG-066 ③) — 예전에는 `st` 가 있을 때 `make_query(q, encode_query(…))`
    # 를 직접 불러 입구가 둘이었고, 그 자리에 어휘 확장을 걸면 한쪽만 걸린다.
    query = search.encode(question, model_key=key, st=st)

    hits = search.search(query, k=k, include_supplementary=include_supplementary,
                         category=category, conn=conn)
    # **검색 질의에는 프로필이 안 들어간다** (로드맵 B4 = "프로필 → 프롬프트"). 견종을 질의에
    # 섞으면 검색이 달라져 lap 비교 축이 흔들리고, 그것은 지역 필터(A2)와 같은 종류의 카드다.
    # 그래서 top-k 에 맹견 조항이 안 오면 프로필이 있어도 답이 안 갈린다 — `## 남은 것`(#202).
    return answer(question, hits, client=client, model=model, embedding_model=key, dog=dog)


# ---------------------------------------------------------------- 덤프 (RAG-028 ⑥)
class DumpHeader(_Base):
    """랩 하나의 전제. **코퍼스 스냅샷이 여기 있어야 2랩 비교가 성립한다** (RAG-024 ④와 같은 규약)."""
    type: str = "header"
    version: int = VERSION
    lap: str
    generated_at: str
    gemini_model: str
    embedding_model: str
    documents: int                    # 코퍼스 스냅샷 — 이 숫자가 2랩에서 달라지는 것이 실험 그 자체다
    k: int
    questions: int


class DumpHit(_Base):
    rank: int
    score: float
    chunk_id: str
    logical: str                      # 수집 날짜를 뺀 주소 (RAG-028 ⑥ⓑ)
    citation: str
    tier: str                         # must / nice / -


class DumpDog(_Base):
    """랩 행에 남는 프로필. `DogProfile` 과 같은 칸이지만 **읽는 쪽 타입이 따로 있다** —
    `DogProfile` 은 서빙이 받는 입력이고 이쪽은 저장 포맷이라, 한쪽이 늘 때 다른 쪽이
    덩달아 늘지 않게 갈라 둔다 (`DumpHit` 과 `Hit` 을 가른 것과 같다).
    """

    breed: str | None = None
    age_months: int | None = None


class DumpRow(_Base):
    """문항 하나. **비교 축 셋이 전부 여기 있다** (RAG-028 ⑥ⓐ) — `hit_ids`·`cited`·`ungrounded`.

    `text` 도 남기지만 **비교 축이 아니다.** LLM 이 비결정적이라 답변 문장을 대조하면 랩 사이의
    차이가 코퍼스 때문인지 샘플링 때문인지 안 갈린다. 눈으로 읽으려고 남길 뿐이다.
    """
    type: str = "answer"
    id: str
    question: str
    text: str
    hits: list[DumpHit]
    cited: list[str]
    ungrounded: list[str]
    # 생성이 낸 판단 둘 (RAG-055). **덤프에 남겨야 `score-laps` 가 잰다** — 이것이 없으면
    # `expect: refuse` 는 영영 못 재고, `covered` 후보도 랩을 다시 떠야만 비교된다.
    # 옛 랩(`lap1`~`lap15`)에는 이 칸이 없다. 읽는 쪽이 `.get()` 으로 넘어간다
    boundary: Boundary = "none"
    covered: bool = True
    # 이 문항을 어떤 프로필로 물었나 (RAG-056). 옛 랩(`lap1`~`lap18`)에는 이 칸이 없고,
    # 없는 것과 프로필 없이 물은 것은 같은 뜻이라 기본값이 `None` 인 것이 맞다
    dog: DumpDog | None = None


def dump_rows(items: list[tuple[str, Answer, set[str], set[str]]]) -> list[DumpRow]:
    return [
        DumpRow(
            id=qid, question=a.question, text=a.text,
            hits=[DumpHit(rank=h.rank, score=round(h.score, 6), chunk_id=h.chunk_id,
                          logical=goldenset.logical(h.chunk_id), citation=h.citation,
                          tier=search.tier_of(h.chunk_id, must, nice))
                  for h in a.hits],
            cited=a.cited, ungrounded=a.ungrounded,
            boundary=a.boundary, covered=a.covered,
            dog=DumpDog(breed=a.dog.breed, age_months=a.dog.age_months) if a.dog else None,
        )
        for qid, a, must, nice in items
    ]


def dump_header(lap: str, items: list[tuple[str, Answer, set[str], set[str]]], k: int,
                conn=None) -> DumpHeader:
    first = items[0][1]
    own = conn is None
    conn = conn or load.connect()
    try:
        n = load.count(conn)
    finally:
        if own:
            conn.close()
    return DumpHeader(
        lap=lap, generated_at=datetime.now(config.KST).isoformat(timespec="seconds"),
        gemini_model=first.model, embedding_model=first.embedding_model,
        documents=n, k=k, questions=len(items),
    )
