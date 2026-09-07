"""Runtime-only grounded generation extracted from freeze 22495d2.
GraphRAG/evaluation/OpenAI research dependencies are intentionally excluded.
Grounded generation uses the Gemini API.  Prompt: see PROMPT_VERSION.
"""
from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from langsmith import traceable

from daengs_training.guardrails import medical as medical_guardrail

PROMPT_VERSION = 'grounded-answer-ko-v3'

# v3 (2026-09-03): model-facing instructions moved to English (mentor convention,
# docs/orchestration/architecture.md §프롬프트·로케일 정책) and one semantic rule added —
# evidence must *directly* address the behavior/problem the user stated; adjacent
# evidence is not an answer.  Everything the user can see stays Korean: the question,
# the retrieved evidence, the canonical no-evidence sentence, the hedge prefix.

#: System-authored text for a gate REFUSE.  User-facing, so Korean.
REFUSAL_TEXT = '제공된 자료에는 이 질문에 답할 내용이 없습니다. 검색된 자료가 질문과 충분히 관련되어 있지 않아 답변을 생성하지 않았습니다.'

#: The one sentence the model must emit — verbatim, alone — when the evidence does not
#: directly cover what was asked.  service.model_reported_no_evidence() recognises it
#: (and paraphrases of the same shape) and turns it into UNCERTAIN.  Unchanged from v2.
NO_EVIDENCE_SENTENCE = '제공된 자료에는 이 질문에 대한 내용이 없습니다.'

#: v3 rule 2.  Kept as its own constant so a test can pin it: this is the rule that stops
#: "the user says the dog loves meat" from being answered as resource guarding.
DIRECT_EVIDENCE_RULE = (
    'The evidence must directly address the behavior or problem the user actually stated. '
    'Evidence that is merely related, similar, or adjacent to the question is NOT sufficient. '
    'Do not infer, assume, or substitute a behavior, motive, diagnosis, or problem the user did not state '
    '(for example, do not turn "the dog likes something a lot" into guarding, possessiveness, aggression, '
    'or a dominance/hierarchy problem).'
)

#: v3 rule 3.  The abstain path: the canonical Korean sentence, alone, and stop.
NO_EVIDENCE_RULE = (
    'If the evidence does not directly address the stated behavior or problem, reply with exactly this one '
    f'Korean sentence and nothing else: "{NO_EVIDENCE_SENTENCE}" '
    'Do not follow it with advice about a related or adjacent problem, and do not guess.'
)

#: Rules are numbered by build_prompt() in the order given.
PROMPT_RULES = (
    'Answer using only what is actually written in the <evidence> below.',
    DIRECT_EVIDENCE_RULE,
    NO_EVIDENCE_RULE,
    (
        'Do not fill gaps with general knowledge or common sense from outside the evidence. '
        'Saying the evidence does not cover it is better than producing a plausible sentence.'
    ),
    'Mark each piece of evidence you used with its number, like [1].',
    (
        'If the evidence gives concrete next steps or handling methods, present them together with the '
        'explanation of the cause. If it does not, do not invent them; stop at the explanation of the cause.'
    ),
    'Answer the user in Korean.',
)

#: The "[근거 약함] ..." prefix is shown to the user, so it stays Korean.
HEDGE_RULE = (
    'The evidence below was measured as weakly related to the question. Begin your answer with exactly '
    '"[근거 약함] 아래 답변은 관련성이 낮은 자료에 기반합니다" and do not use a definitive tone.'
)

PROFILE_NOTE = (
    'The <profile> below is situational information the user gave about their dog. Use it to tailor the '
    'advice to this dog, but every ground for the answer must still come from the <evidence>; the profile '
    'is not evidence. Even if the profile names a condition, do not mention it as a diagnosis without evidence.'
)

CONTEXT_ONLY_RULE = (
    'The <user_cases> section contains text written by dog owners in consultations. It is not expert advice '
    "and must not be cited as evidence; do not give it a number. Use it only to understand the user's "
    'situation, and take every ground for the answer from the <evidence>. A method a user says they tried '
    'is not thereby shown to work.'
)

DEFAULT_GEMINI_MODEL = 'gemini-3.1-flash-lite'
DEFAULT_GEMINI_TIMEOUT_MS = 30_000
GEMINI_MAX_OUTPUT_TOKENS = 2000
GEMINI_API_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta'

class GenerationError(RuntimeError):
    """Raised when inputs, settings or the answer client are unusable."""


class GenerationTimeoutError(GenerationError):
    """Raised when Gemini exceeds the configured provider deadline."""

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

        # `record` 는 usage 를 되돌려받는 out 파라미터라 호출 시점에는 비어 있습니다.
        # 트레이스 입력에 실으면 늘 `{}` 인 칸이 하나 생기므로 프롬프트만 남깁니다.
        # usage 는 상위 `training_rag` 런의 출력에 이미 실립니다 — 두 번 안 보냅니다.
        @traceable(
            run_type="llm",
            name="gemini_generate",
            process_inputs=lambda inputs: {"prompt": inputs.get("prompt")},
            process_outputs=lambda text: {"answer": text},
            metadata={"model": selected_model, "prompt_version": PROMPT_VERSION},
        )
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
            except httpx.TimeoutException as exc:
                raise GenerationTimeoutError(
                    f'Gemini call timed out for {selected_model}'
                ) from exc
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
    """Label of a <user_cases> item: a provenance marker, not a citation number."""
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
    header = f"[{position}] (document · {chunk['doc_id']} #{chunk['chunk_index']}"
    return header + (f' · {heading})' if heading else ')')

def format_profile_block(profile: dict[str, Any]) -> str:
    """The <profile> block build_prompt inserts ahead of <evidence> when a profile is given.

    The dict keys are the caller's contract and stay Korean; only the model-facing labels
    are English.  Values are shown as given.
    """
    conditions = profile['기존질환']
    conditions_text = ', '.join(conditions) if conditions else '없음'
    return '\n'.join([
        '<profile>', PROFILE_NOTE, '',
        f"Breed: {profile['견종']}", f"Age: {profile['나이']}", f"Weight: {profile['몸무게']}",
        f'Existing conditions: {conditions_text}', f"Notes: {profile['비고']}",
        '</profile>',
    ])

def build_prompt(question: str, chunks: Sequence[dict[str, Any]], band: str, profile: dict[str, Any] | None=None) -> str:
    """The generation prompt. Same wording for every band except the hedge rule.

    profile is None by default: omitting it (or passing None) reproduces the
    pre-profile prompt byte-for-byte — no profile-shaped gap in the middle of the
    text, no empty <profile></profile> block.

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
    if context_only:
        rules.append(CONTEXT_ONLY_RULE)
    sources = [f"{_chunk_header(position, chunk)}\n{chunk['text']}" for position, chunk in enumerate(citable, start=1)]
    lines = [
        'Answer the question using only the <evidence> below as grounds.',
        '',
        'Rules:',
        *(f'{number}. {rule}' for number, rule in enumerate(rules, start=1)),
        '',
    ]
    if profile is not None:
        lines += [format_profile_block(profile), '']
    lines += ['<evidence>', '', '\n\n'.join(sources), '', '</evidence>']
    if context_only:
        lines += ['', '<user_cases>', '', '\n\n'.join(f"({_context_label(chunk)})\n{chunk['text']}" for chunk in context_only), '', '</user_cases>']
    lines += ['', f'Question: {question}']
    return '\n'.join(lines)
