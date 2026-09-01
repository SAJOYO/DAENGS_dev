"""저장된 대화 하나를 구조화 요약으로 접는다. 새 상담을 하지 않는다.

**이미 있는 Gemini 구조화 출력 경로를 그대로 재사용한다** —
`orchestration/semantic.py` 의 `google-genai` + `response_json_schema` 패턴,
설정은 `config.settings.gemini_*`, 스키마 실패는 O-14 대로 **1회만** 재시도한 뒤
실패로 끝낸다. 공급자를 새로 들이지 않는 이유는 그럴 기술적 근거가 없어서다:
Training·Life 생성과 의미 라우터가 이미 Gemini 이고(D-030 사실 갱신 · D-041),
구조화 JSON 출력도 라우터에서 이미 검증됐다.

**이 모듈은 요약기이지 상담기가 아니다.** RAG 검색을 새로 하지 않고, 대화에 없는
사실을 만들지 않으며, 원문의 주의·한계·출처를 떨어뜨리지 않는다. 그 세 가지가
프롬프트의 전부다 — 요약이 경고를 지우면 보관함에 남는 문장이 원문보다 위험해진다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from daengs_backend.config import settings

PROMPT_VERSION = "chat-summary-ko-v1"

#: 의미 라우터와 같은 모델을 쓴다 (`orchestration/semantic.py ROUTER_MODEL_ID`).
#: 요약은 라우팅보다 출력이 길지만 판단의 종류는 같은 급이라, 모델을 따로 고를
#: 근거가 아직 없다. 바꾸려면 벤치마크가 먼저다 (D-031 의 선정 규칙).
SUMMARY_MODEL_ID = "gemini-3.1-flash-lite"

#: 요약이 커져도 저장 칸을 넘지 않게 하는 선. DB 의 VARCHAR(120) 과 맞춘다.
_TITLE_MAX = 120
_LIST_MAX = 10


class ChatSummaryDraft(BaseModel):
    """모델이 내도 되는 것의 전부. 그 밖의 키는 거부한다."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=_TITLE_MAX)
    question_summary: str = Field(min_length=1, max_length=2_000)
    answer_summary: str = Field(min_length=1, max_length=4_000)
    key_points: list[str] = Field(default_factory=list, max_length=_LIST_MAX)
    #: **비어 있어도 된다.** 원문에 주의가 없었으면 지어내지 않는 것이 맞다.
    cautions: list[str] = Field(default_factory=list, max_length=_LIST_MAX)
    source_citations: list[str] = Field(default_factory=list, max_length=_LIST_MAX)


class ChatSummaryError(Exception):
    """공급자 실패 또는 스키마 실패. 라우터가 502 로 바꾼다.

    **부분 저장을 하지 않는다** — 요약이 못 나왔는데 빈 껍데기가 보관함에 남으면
    사용자는 저장에 성공한 것으로 본다.
    """


_POLICY = """You are the DAENGS conversation summarizer, not an assistant.

Summarize ONLY the conversation supplied below. Return exactly one JSON object conforming to the
supplied ChatSummaryDraft schema, in Korean (ko-KR). Return no Markdown and no prose outside the
JSON object.

Hard rules:
- Use only what the conversation already says. Do not add facts, advice, diagnosis, regulations,
  fees, deadlines, or recommendations that are not in it. You have no search tool and no knowledge
  source for this task.
- Do not answer the user's question, continue the conversation, or offer a new consultation.
- Preserve every warning, limitation, uncertainty, and refusal the assistant expressed. Put them in
  `cautions` verbatim in meaning. Never drop a caution to make the summary shorter.
- Copy source citations exactly as they appear in the conversation into `source_citations`. Do not
  invent, complete, guess, or reformat a URL or document name.
- If the assistant abstained, refused, or failed, say so plainly in `answer_summary` instead of
  presenting an answer that was never given.
- `title` is a short Korean noun phrase naming the topic, at most 120 characters.
- Leave a list empty when the conversation has nothing for it."""


def build_summary_prompt(*, transcript: str) -> str:
    """대화 원문에서 프롬프트를 만든다. **빈 대화는 부르지 않는다.**"""
    if not transcript.strip():
        raise ValueError("transcript must not be blank")
    schema = json.dumps(
        ChatSummaryDraft.model_json_schema(), ensure_ascii=False, sort_keys=True
    )
    return (
        f"PROMPT_VERSION: {PROMPT_VERSION}\n\n"
        f"{_POLICY}\n\n"
        f"CHAT_SUMMARY_JSON_SCHEMA:\n{schema}\n\n"
        f"OUTPUT_LOCALE: ko-KR\n"
        f"CONVERSATION:\n{transcript}\n"
    )


def render_transcript(messages: list[tuple[str, str]]) -> str:
    """`(role, content)` 목록을 모델이 읽을 한 덩어리로.

    **이 세션의 메시지만 들어옵니다.** 부르는 쪽(`services/chat.py`)이 세션 하나로
    좁혀서 넘기고, 여기서는 다른 대화를 끌어올 방법 자체가 없습니다.
    """
    lines: list[str] = []
    for role, content in messages:
        speaker = "USER" if role == "user" else "ASSISTANT"
        lines.append(f"[{speaker}] {content}")
    return "\n".join(lines)


def validate_summary_draft(raw: object) -> ChatSummaryDraft | None:
    """공급자 출력을 스키마로 검증한다. **잘못된 원출력은 노출하지 않는다.**

    `orchestration/semantic.py validate_semantic_decision` 과 같은 모양이다 —
    한쪽만 고치지 말 것.
    """
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, ChatSummaryDraft):
        return parsed
    try:
        return ChatSummaryDraft.model_validate(parsed)
    except (ValidationError, ValueError, TypeError):
        return None


@lru_cache(maxsize=1)
def _gemini_client() -> Any:
    # google-genai 는 함수 안에서 import 한다 — 이 모듈을 불러오는 것만으로
    # 공급자 스택이 딸려 오지 않게 (semantic.py 와 같은 규칙).
    from google import genai
    from google.genai import types

    api_key = settings.gemini_api_key.get_secret_value().strip()
    if not api_key:
        raise ChatSummaryError("GEMINI_API_KEY is required for chat summaries")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=settings.gemini_timeout_ms),
    )


async def _generate_with_gemini(prompt: str) -> object:
    def _call() -> object:
        from google.genai import types

        response = _gemini_client().models.generate_content(
            model=SUMMARY_MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                candidate_count=1,
                max_output_tokens=2_048,
                response_mime_type="application/json",
                response_json_schema=ChatSummaryDraft.model_json_schema(),
            ),
        )
        parsed = getattr(response, "parsed", None)
        return parsed if parsed is not None else getattr(response, "text", None)

    return await asyncio.to_thread(_call)


class GeminiChatSummarizer:
    """스키마 강제 요약. O-14 와 같은 **1회 한정** 재시도.

    테스트는 `generate` 에 가짜를 주입합니다 — 실제 API 키가 필요 없습니다
    (`GeminiSemanticRouter` 와 같은 방식).
    """

    def __init__(self, generate: Callable[[str], Awaitable[object]] | None = None) -> None:
        self._generate = generate or _generate_with_gemini

    async def summarize(self, *, transcript: str) -> ChatSummaryDraft:
        prompt = build_summary_prompt(transcript=transcript)
        for _attempt in range(2):  # 스키마 실패에만 한 번 더. 재시도 프레임워크를 만들지 않는다.
            try:
                raw = await self._generate(prompt)
            except ChatSummaryError:
                raise
            except Exception as exc:  # 공급자 실패는 요약 실패다
                raise ChatSummaryError("chat summary provider call failed") from exc
            draft = validate_summary_draft(raw)
            if draft is not None:
                return draft
        raise ChatSummaryError("chat summary output failed schema validation twice")


__all__ = [
    "PROMPT_VERSION",
    "SUMMARY_MODEL_ID",
    "ChatSummaryDraft",
    "ChatSummaryError",
    "GeminiChatSummarizer",
    "build_summary_prompt",
    "render_transcript",
    "validate_summary_draft",
]
