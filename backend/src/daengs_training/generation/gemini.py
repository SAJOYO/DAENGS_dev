"""Runtime-only grounded generation extracted from freeze 22495d2.
GraphRAG/evaluation/OpenAI research dependencies are intentionally excluded.
Prompt wording remains frozen; grounded generation uses the Gemini API.
"""
from __future__ import annotations
import os
import time

import httpx
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from daengs_training.guardrails import medical as medical_guardrail

PROMPT_VERSION = 'grounded-answer-ko-v2'

REFUSAL_TEXT = '제공된 자료에는 이 질문에 답할 내용이 없습니다. 검색된 자료가 질문과 충분히 관련되어 있지 않아 답변을 생성하지 않았습니다.'

PROMPT_RULES = ('1. 아래 <자료>에 실제로 적혀 있는 내용만으로 답하세요.', '2. 자료에 답이 없으면 "제공된 자료에는 이 질문에 대한 내용이 없습니다"라고 답하고, 추측하지 마세요.', '3. 자료 밖의 일반 지식이나 상식으로 빈칸을 채우지 마세요. 그럴듯한 문장을 만드는 것보다 없다고 말하는 것이 낫습니다.', '4. 답변에 쓴 자료를 [1]처럼 번호로 표시하세요.', '5. 자료에 다음에 취할 수 있는 구체적인 행동이나 대처법이 나와 있다면 원인 설명과 함께 안내하세요. 자료에 없으면 억지로 만들지 말고 원인 설명까지만 답하세요.')

HEDGE_RULE = '6. 아래 자료는 질문과의 관련성이 낮게 측정되었습니다. 답변 맨 앞에 "[근거 약함] 아래 답변은 관련성이 낮은 자료에 기반합니다"를 그대로 넣고, 단정적인 어조를 쓰지 마세요.'

PROFILE_NOTE = '아래 프로필은 질문자가 알려준 반려견의 상황 정보입니다. 답변할 때 이 정보를 반영해 이 반려견의 상황에 맞게 조언을 조정하세요. 다만 답변에 쓰는 근거는 반드시 <자료>에서만 가져와야 하고, 프로필은 그 근거로 쓸 수 없습니다. 프로필에 적힌 질환명이 있어도 그것을 근거 없이 진단처럼 언급하지 마세요.'

DEFAULT_GEMINI_MODEL = 'gemini-3.1-flash-lite'
DEFAULT_GEMINI_TIMEOUT_MS = 30_000
GEMINI_MAX_OUTPUT_TOKENS = 2000
GEMINI_API_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'

CONTEXT_ONLY_RULE = '6. <사용자사례>는 보호자가 상담에서 직접 쓴 글입니다. 전문가의 권고가 아니며 근거로 인용하면 안 됩니다. 번호를 붙여 참조하지 마세요. 질문자의 상황을 이해하는 데만 쓰고, 답변의 근거는 <자료>에서만 가져오세요. 사용자가 시도했다고 적은 방법이 효과적이라는 뜻은 아닙니다.'

class GenerationError(RuntimeError):
    """Raised when inputs, settings or the answer client are unusable."""

@dataclass(frozen=True)
class ClientInfo:
    name: str
    prompt_version: str = PROMPT_VERSION

class AnswerClient(Protocol):
    """How an answer is produced. Injected so tests can supply a deterministic fake.

    Mirrors the Encoder protocol in the evaluator: the expensive, non-deterministic
    dependency sits behind a two-method interface, and nothing else in the script
    knows whether a model, a person or a stub is on the other side.
    """

    @property
    def info(self) -> ClientInfo:
        ...

    def complete(self, prompt: str, record: dict[str, Any]) -> str | None:
        ...

MEDICAL_REFUSAL_TEMPLATE = medical_guardrail.SystemAuthoredText(
    "\uac71\uc815\uc774 \ub9ce\uc73c\uc2dc\uaca0\uc5b4\uc694. " + medical_guardrail.VET_REFERRAL_MESSAGE
)

def load_gemini_answer_client(
    api_key: str | None = None,
    model: str | None = None,
    timeout_ms: int | None = None,
    endpoint: str | None = None,
) -> AnswerClient:
    """Use Gemini only for grounded answer generation.

    Retrieval, evidence gating and medical guardrails remain outside the model.
    The API key is supplied through the process environment; it is never written
    to prompts, logs or response records.
    """
    key = (api_key or os.getenv('GEMINI_API_KEY', '')).strip()
    if not key:
        raise GenerationError('GEMINI_API_KEY is required for Training RAG generation')

    selected_model = (
        model
        or os.getenv('GEMINI_MODEL')
        or DEFAULT_GEMINI_MODEL
    ).strip()

    if timeout_ms is None:
        raw_timeout = os.getenv('GEMINI_TIMEOUT_MS', str(DEFAULT_GEMINI_TIMEOUT_MS))
        try:
            timeout_ms = int(raw_timeout)
        except ValueError as exc:
            raise GenerationError('GEMINI_TIMEOUT_MS must be an integer') from exc

    if timeout_ms <= 0:
        raise GenerationError('GEMINI_TIMEOUT_MS must be positive')

    base_url = (
        endpoint
        or os.getenv('GEMINI_API_BASE_URL')
        or GEMINI_API_BASE_URL
    ).rstrip('/')
    url = f'{base_url}/models/{selected_model}:generateContent'

    class GeminiAnswerClient:
        model_id = selected_model
        reasoning_effort = 'provider_default'

        @property
        def info(self) -> ClientInfo:
            return ClientInfo(name=f'gemini:{selected_model}')

        def complete(self, prompt: str, record: dict[str, Any]) -> str | None:
            payload = {
                'contents': [
                    {
                        'role': 'user',
                        'parts': [{'text': prompt}],
                    }
                ],
                'generationConfig': {
                    'temperature': 0,
                    'maxOutputTokens': GEMINI_MAX_OUTPUT_TOKENS,
                },
            }

            started = time.perf_counter_ns()
            try:
                response = httpx.post(
                    url,
                    headers={
                        'Content-Type': 'application/json',
                        'x-goog-api-key': key,
                    },
                    json=payload,
                    timeout=timeout_ms / 1000,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                raise GenerationError(
                    f'Gemini call failed for {selected_model}: {str(exc)[:400]}'
                ) from exc

            candidates = data.get('candidates') or []
            if not candidates:
                return None

            candidate = candidates[0] or {}
            content = candidate.get('content') or {}
            parts = content.get('parts') or []
            text = '\n'.join(
                str(part.get('text', ''))
                for part in parts
                if isinstance(part, dict) and part.get('text')
            ).strip()

            usage = data.get('usageMetadata') or {}
            prompt_tokens = int(usage.get('promptTokenCount') or 0)
            output_tokens = int(usage.get('candidatesTokenCount') or 0)

            record['usage'] = {
                'input_tokens': prompt_tokens,
                'output_tokens': output_tokens,
                'prompt_eval_count': prompt_tokens,
                'eval_count': output_tokens,
                'total_tokens': int(usage.get('totalTokenCount') or 0),
                'total_duration_ns': time.perf_counter_ns() - started,
                'done_reason': candidate.get('finishReason'),
            }
            return text or None

    return GeminiAnswerClient()

def _context_label(chunk: dict[str, Any]) -> str:
    """<사용자사례> 항목의 라벨. 인용 번호가 아니라 출처 표시다."""
    author = chunk.get('author_display') or '보호자'
    where = chunk.get('doc_id') or chunk.get('qa_id') or '상담'
    return f'{author} · {where}'

def _chunk_header(position: int, chunk: dict[str, Any]) -> str:
    """The "[N] (...)" citation header build_prompt puts above each chunk's text.

    Video and document chunks are shaped differently (see
    run_combined_retrieval_eval.py's load_video_chunks/load_document_chunks,
    which enforce this as a hard split: a document chunk is not allowed to carry
    "video_id" at all). "video_id" in chunk is therefore an exact, not a guessed,
    discriminator — never both true and false for the same real chunk record.
    """
    if 'video_id' in chunk:
        title = chunk.get('chapter_title', '')
        header = f"[{position}] ({chunk['video_id']} #{chunk['chunk_index']}"
        return header + (f' · {title})' if title else ')')
    heading = ' > '.join(chunk.get('heading_path', []))
    header = f"[{position}] (문서 · {chunk['doc_id']} #{chunk['chunk_index']}"
    return header + (f' · {heading})' if heading else ')')

def format_profile_block(profile: dict[str, Any]) -> str:
    """The <프로필> block build_prompt inserts ahead of <자료> when a profile is given."""
    conditions = profile['기존질환']
    conditions_text = ', '.join(conditions) if conditions else '없음'
    return '\n'.join(['<프로필>', PROFILE_NOTE, '', f"견종: {profile['견종']}", f"나이: {profile['나이']}", f"몸무게: {profile['몸무게']}", f'기존 질환: {conditions_text}', f"비고: {profile['비고']}", '</프로필>'])

def build_prompt(question: str, chunks: Sequence[dict[str, Any]], band: str, profile: dict[str, Any] | None=None) -> str:
    """The generation prompt. Same wording for every band except the hedge rule.

    profile is None by default: omitting it (or passing None) reproduces the
    pre-profile prompt byte-for-byte — no profile-shaped gap in the middle of the
    text, no empty <프로필></프로필> block.

    chunks may be video or document chunks (or a mix) — this script's own
    retrieval only ever passes video chunks (DEFAULT_CHUNK_DIR), but a caller
    that sources evidence elsewhere (e.g. run_combined_retrieval_eval.py's
    graph-augmented hybrid_merge, whose corpus includes documents) can pass
    those chunks straight through. See _chunk_header().
    """
    rules = list(PROMPT_RULES)
    if band == 'hedge':
        rules.append(HEDGE_RULE)
    citable = [c for c in chunks if c.get('citation_allowed', True)]
    context_only = [c for c in chunks if not c.get('citation_allowed', True)]
    sources = [f"{_chunk_header(position, chunk)}\n{chunk['text']}" for position, chunk in enumerate(citable, start=1)]
    lines = ['아래 <자료>만 근거로 질문에 답하세요.', '', '규칙:', *rules]
    if context_only:
        lines.append(CONTEXT_ONLY_RULE)
    lines.append('')
    if profile is not None:
        lines += [format_profile_block(profile), '']
    lines += ['<자료>', '', '\n\n'.join(sources), '', '</자료>']
    if context_only:
        lines += ['', '<사용자사례>', '', '\n\n'.join((f"({_context_label(chunk)})\n{chunk['text']}" for chunk in context_only)), '', '</사용자사례>']
    lines += ['', f'질문: {question}']
    return '\n'.join(lines)
