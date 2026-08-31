"""Medical-question guardrail v1: input-side refusal and output-side risk check.

Scope is deliberately narrow: this catches questions and answers that read as
asking for or giving a diagnosis, a prescription, or a medication/dosage. It does
not catch harmful *training method* advice (e.g. flooding) — that is a separate,
unbuilt defense (docs/agenda_0825.md, 안건 3). Scope is not something this module
should grow on its own; if a caller wants training-harm coverage, that is a new
lexicon and a new stage, not an extension of MEDICAL_TERMS.

Two independent checks, matched against different text:

  classify_input(question)   — question text against MEDICAL_TERMS (질환·증상
                                vocabulary dumped from the frozen graph by
                                scripts/build_medical_lexicon.py). Runs before
                                any retrieval, and before the existing score_gap
                                gate in scripts/run_combined_retrieval_eval.py.
                                On a MEDICAL verdict, the caller must return
                                VET_REFERRAL_MESSAGE immediately and skip
                                retrieval and generation entirely.

  classify_output(answer)    — generated answer text against MEDICAL_TERMS *and*
                                PRESCRIPTIVE_MARKERS. This exists because
                                classify_input only sees the question, and a
                                question that does not name a disease or symptom
                                can still pull disease/symptom evidence into a
                                hybrid-merged context (graph search walks 2 hops
                                from whatever the question *does* match) and come
                                back out in the generated text. Mentioning a
                                disease/symptom by name is not enough to act on —
                                Q16 in owner_fixtures.jsonl is a fixture that is
                                expected to be ANSWERed even though it names
                                "슬개골 탈구" and "관절 통증" outright, because it asks
                                what behavior a diagnosed dog shows, not what to
                                do about it. What must be caught is the answer
                                *prescribing*: a drug, a dose, a treatment. So a
                                BLOCK verdict requires a disease/symptom term
                                *and* a prescriptive marker in the same text;
                                a disease/symptom term alone downgrades to a
                                disclaimer.

Both checks do plain substring matching, the same style
scripts/run_combined_retrieval_eval.py already uses for graph entity seeding
(match_seeds) and guardrail/seed_lexicon.json already uses for its symptom
phrases. No stemming, no tokenization — matching what is already the codebase's
convention rather than introducing a second matching strategy this v1 has no
measurement to justify.

v2 addition — classify_input_v2(question, medical_terms, whitelist_terms):

v1's MEDICAL_TERMS came from frozen/frozen_stage2_0820.jsonl, i.e. entities the
training-video corpus itself talks about. That was the wrong source for this
job: a guardrail that exists to catch medical questions the corpus has no
business answering needs vocabulary for medicine *outside* the corpus, not a
dump of what the corpus already discusses. It also went wrong in the other
direction — several extracted "증상" entities (짖음, 불안, 분리불안, 하울링) are
ordinary training vocabulary that a symptom-labeling pass over training-video
transcripts was always going to produce, because a problem behavior and a
symptom description overlap heavily in how owners talk. See
reports/generated/medical_guardrail_v1v2_comparison.md for the measured cost of both
failure modes.

v2 (data/guardrail/medical_terms_v2.json) is a hand-authored list of general
veterinary vocabulary — procedures, products, emergency symptoms, clinical
actions — chosen without reference to what this corpus happens to contain.
data/guardrail/training_whitelist_v1.json is checked first and, on a match,
overrides any v2 dictionary hit: a question containing 짖음/분리불안/불안/하울링/
배변/산책/사회화 passes regardless of what else it contains. That is a known,
deliberate trade-off (see the whitelist file's known_tradeoff field), not an
oversight — a question that genuinely mixes a training topic with a real
emergency (e.g. "산책 중에 발작을 일으켜요") will pass under this rule.

v1 is kept, unmodified, alongside v2. It is not a superseded artifact to
delete — it is the recorded evidence for why sourcing a medical guardrail's
vocabulary from the corpus's own extraction was the wrong design.

apply_output_guardrail() and SystemAuthoredText — the self-block incident:

scripts/generate_answers.py's MEDICAL_REFUSAL_TEMPLATE ("가까운 동물병원에서
수의사의 진료를 받으시길 권합니다...") contains "병원" (a medical_terms_v2 entry)
and "처방" (a PRESCRIPTIVE_MARKERS entry) in the same hand-written sentence —
exactly the co-occurrence classify_output_v2 exists to catch. Run naively, the
guardrail written to keep unsafe *model* text out of a reply would have blocked
its own safe, hand-authored replacement text and shown OUTPUT_BLOCKED_MESSAGE
instead. First version of the fix (2026-08-20, same day) just skipped the
function call at that one call site — correct in effect, but the exemption
lived in generate_answers.py's control flow, not on the text itself. Anything
later routed down that same "no model call" branch would have silently
inherited the same bypass, model-generated or not, with nothing forcing a
re-check.

SystemAuthoredText fixes that: it marks the *value*, not the code path. A
caller that has hand-written, pre-vetted text wraps it before handing it to
apply_output_guardrail(); anything unwrapped — a plain str, which is what
every model client in this file returns — is real generated text and gets the
full classify_output_v2 check regardless of which branch produced it. A future
edit that starts putting model output where MEDICAL_REFUSAL_TEMPLATE used to go
gets checked automatically, because a plain str was never exempt.

v2 addition — classify_output_v2(answer, medical_terms_v2, whitelist_terms):

scripts/generate_answers.py wired classify_output into real generation on
2026-08-20 using v1's terms and immediately hit v1's own documented flaw:
"분리불안"/"불안" — ordinary training vocabulary, not a diagnosis — appeared in
two demo answers and both got OUTPUT_DISCLAIMER appended, unrelated to whether
the answer was medical. classify_output_v2 applies classify_input_v2's already-
accepted whitelist-first precedence to the output side instead: a whitelist hit
passes the text through untouched, same known trade-off as classify_input_v2
(a real emergency mentioned alongside a whitelist word passes uncaught — see
the whitelist file's known_tradeoff field). v1's classify_output is kept,
unmodified, for the same reason v1 itself is kept: recorded evidence, not a
deleted mistake.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

DEFAULT_LEXICON_PATH = Path("data/guardrail/medical_terms_v1.json")
DEFAULT_LEXICON_V2_PATH = Path("data/guardrail/medical_terms_v2.json")
DEFAULT_WHITELIST_PATH = Path("data/guardrail/training_whitelist_v1.json")

MIN_TERM_CHARS = 2

VET_REFERRAL_MESSAGE = (
    "이 질문은 반려견의 건강 상태에 대한 의학적 판단이 필요해 보입니다. "
    "저는 훈련 정보를 안내하는 어시스턴트이고 진단이나 처방을 할 수 없습니다. "
    "가까운 동물병원에서 수의사의 진료를 받으시길 권합니다."
)

OUTPUT_DISCLAIMER = (
    "\n\n※ 위 설명은 자료에 나온 일반 정보이며 진단이 아닙니다. "
    "증상이 있다면 수의사 상담을 권합니다."
)

OUTPUT_BLOCKED_MESSAGE = (
    "이 답변에는 특정 진단·처방·투약으로 읽힐 수 있는 내용이 포함되어 있어 표시하지 않습니다. "
    "약물, 용량, 치료법은 수의사만 결정할 수 있습니다. 가까운 동물병원에 문의해 주세요."
)

# Distinct from MEDICAL_TERMS on purpose (see module docstring): a fixed,
# hand-written list of markers for "this text is telling someone what to
# diagnose, prescribe, or administer" rather than "this text names a symptom".
# Not sourced from the frozen graph — the graph extraction does not label
# assertion type, only entity names — and not tuned against any eval set.
PRESCRIPTIVE_MARKERS = (
    "진단합니다", "진단됩니다", "진단입니다",
    "처방", "처방전", "처방식",
    "복용", "투여", "투약",
    "항생제", "스테로이드", "소염제", "진통제", "구충제",
    "연고를", "안약을", "점이제를",
    "mg", "밀리그램",
)


class GuardrailError(RuntimeError):
    """Raised when the medical-terms lexicon file is missing or malformed."""


@dataclass(frozen=True)
class InputVerdict:
    is_medical: bool
    matched_terms: tuple[str, ...] = field(default_factory=tuple)
    response: str | None = None
    # Set only by classify_input_v2, when a whitelist match forced this PASS
    # ahead of (or in spite of) a dictionary match. Empty for classify_input v1.
    whitelist_matched: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OutputVerdict:
    is_blocked: bool
    matched_disease_terms: tuple[str, ...] = field(default_factory=tuple)
    matched_prescriptive_markers: tuple[str, ...] = field(default_factory=tuple)
    text: str = ""
    # Set only by classify_output_v2, when a whitelist match passed the text
    # through ahead of (or in spite of) a dictionary match. Empty for classify_output v1.
    whitelist_matched: tuple[str, ...] = field(default_factory=tuple)
    # True only when apply_output_guardrail() bypassed matching entirely because
    # the input was a SystemAuthoredText, not model output. False (the default)
    # for every classify_output/classify_output_v2 call, including calls made
    # directly rather than through apply_output_guardrail().
    system_authored: bool = False


@dataclass(frozen=True)
class SystemAuthoredText:
    """Marks a string as hand-written by this codebase, not produced by a model.

    apply_output_guardrail() passes this through unmodified and unchecked — see
    this module's docstring ("apply_output_guardrail() and SystemAuthoredText —
    the self-block incident") for why a check built for uncontrolled model text
    can trip on a safe template's own wording (medical_terms_v2's "병원" +
    PRESCRIPTIVE_MARKERS' "처방", both present in MEDICAL_REFUSAL_TEMPLATE).
    Wrap only text a person actually wrote and reviewed — never a model's output,
    even after post-processing.
    """

    text: str


def _load_terms_file(path: Path, missing_hint: str) -> list[str]:
    """Shared loader for any {"terms": [...]} lexicon file, longest term first.

    Longest-first ordering only affects which matched term is reported first in
    a verdict when several substrings overlap (e.g. "관절 통증" containing
    "통증") — it does not change whether a verdict is reached, since that only
    requires at least one match.
    """
    if not path.is_file():
        raise GuardrailError(f"{path} not found. {missing_hint}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    terms = payload.get("terms")
    if not isinstance(terms, list) or not terms:
        raise GuardrailError(f"{path}: 'terms' must be a non-empty array")
    eligible = [t for t in terms if isinstance(t, str) and len(t) >= MIN_TERM_CHARS]
    return sorted(eligible, key=len, reverse=True)


def load_medical_terms(path: Path = DEFAULT_LEXICON_PATH) -> list[str]:
    """Load MEDICAL_TERMS (v1, corpus-extracted) from the versioned lexicon file."""
    return _load_terms_file(
        path, "Run `uv run python scripts/build_medical_lexicon.py` first."
    )


def load_medical_terms_v2(path: Path = DEFAULT_LEXICON_V2_PATH) -> list[str]:
    """Load the hand-authored, out-of-corpus veterinary vocabulary (v2)."""
    return _load_terms_file(path, "This file is hand-authored; it has no build script.")


def load_training_whitelist(path: Path = DEFAULT_WHITELIST_PATH) -> list[str]:
    """Load the training-domain terms that override a v2 dictionary match."""
    return _load_terms_file(path, "This file is hand-authored; it has no build script.")


def _find_matches(text: str, terms: Sequence[str]) -> tuple[str, ...]:
    return tuple(term for term in terms if term in text)


def classify_input(question: str, terms: Sequence[str]) -> InputVerdict:
    """MEDICAL if any dictionary term appears in the question, else pass-through.

    Runs before the score_gap gate and independently of it: a MEDICAL verdict
    means retrieval and generation are skipped entirely, not that the gate is
    asked to refuse. Non-MEDICAL is not a verdict this function makes about
    scope or answerability — those stay the gate's and, ultimately, a human
    reviewer's job (see Q19 in owner_fixtures.jsonl: refuse_reason SCOPE, not
    MEDICAL — out of this guardrail's remit by design).
    """
    matched = _find_matches(question, terms)
    if matched:
        return InputVerdict(is_medical=True, matched_terms=matched, response=VET_REFERRAL_MESSAGE)
    return InputVerdict(is_medical=False)


def classify_input_v2(
    question: str, medical_terms: Sequence[str], whitelist_terms: Sequence[str]
) -> InputVerdict:
    """Whitelist first, dictionary second — a whitelist hit always wins.

    This is the priority order the guardrail rebuild asked for: a question
    naming a training-domain topic (짖음, 분리불안, ...) passes even if it also
    happens to contain a medical_terms_v2 word. See the module docstring and
    data/guardrail/training_whitelist_v1.json's known_tradeoff for what that
    costs — it is a deliberate choice, not an omission.
    """
    whitelist_hits = _find_matches(question, whitelist_terms)
    if whitelist_hits:
        return InputVerdict(is_medical=False, whitelist_matched=whitelist_hits)
    matched = _find_matches(question, medical_terms)
    if matched:
        return InputVerdict(is_medical=True, matched_terms=matched, response=VET_REFERRAL_MESSAGE)
    return InputVerdict(is_medical=False)


def classify_output(
    answer: str,
    terms: Sequence[str],
    markers: Sequence[str] = PRESCRIPTIVE_MARKERS,
) -> OutputVerdict:
    """BLOCK only when a disease/symptom term and a prescriptive marker co-occur.

    A disease/symptom term alone (no prescriptive marker) is not blocked — it is
    returned with OUTPUT_DISCLAIMER appended, because informational mentions
    grounded in retrieved evidence are in scope (Q16) and a naive "any medical
    word -> block" rule would refuse them wrongly. This has not been measured
    against a corpus of real generated answers: this pipeline has no live
    generation step (data/eval/generation/*.jsonl records are dry-run/null),
    so this function is unit-tested against illustrative text only
    (tests/test_medical_guardrail.py), not scored for a false-positive rate.
    """
    disease_hits = _find_matches(answer, terms)
    marker_hits = _find_matches(answer, markers)
    if disease_hits and marker_hits:
        return OutputVerdict(
            is_blocked=True,
            matched_disease_terms=disease_hits,
            matched_prescriptive_markers=marker_hits,
            text=OUTPUT_BLOCKED_MESSAGE,
        )
    if disease_hits:
        return OutputVerdict(
            is_blocked=False,
            matched_disease_terms=disease_hits,
            text=answer + OUTPUT_DISCLAIMER,
        )
    return OutputVerdict(is_blocked=False, text=answer)


def apply_output_guardrail(
    answer: "str | SystemAuthoredText",
    medical_terms: Sequence[str],
    whitelist_terms: Sequence[str],
    markers: Sequence[str] = PRESCRIPTIVE_MARKERS,
) -> OutputVerdict:
    """The call site every caller should use instead of classify_output_v2 directly.

    A SystemAuthoredText passes through untouched — see this module's docstring
    for the incident this exists to prevent. Anything else (a plain str, which
    is what every model client returns) gets the full classify_output_v2 check,
    no matter which code path produced it. This is what makes the exemption safe
    against a future edit: it is a property of the value handed in, not of the
    branch that called this function.
    """
    if isinstance(answer, SystemAuthoredText):
        return OutputVerdict(is_blocked=False, text=answer.text, system_authored=True)
    return classify_output_v2(answer, medical_terms, whitelist_terms, markers)


# 화이트리스트가 처방마커 때문에 무효화된 사례를 모은다. 규칙 변경으로 판정이
# 뒤집히는 자리이므로, 과차단이 실사용에서 나타나는지 관찰할 창구가 필요하다.
# 텍스트 자체는 담지 않는다 — 상담 원문이 로그로 새면 안 된다. 어떤 용어가
# 부딪혔는지만 남긴다.
WHITELIST_REVOKED_LOG: list[dict[str, tuple[str, ...]]] = []


def _log_whitelist_revoked(
    whitelist_hits: tuple[str, ...],
    disease_hits: tuple[str, ...],
    marker_hits: tuple[str, ...],
) -> None:
    WHITELIST_REVOKED_LOG.append({
        "whitelist_matched": whitelist_hits,
        "matched_disease_terms": disease_hits,
        "matched_prescriptive_markers": marker_hits,
    })


def classify_output_v2(
    answer: str,
    medical_terms: Sequence[str],
    whitelist_terms: Sequence[str],
    markers: Sequence[str] = PRESCRIPTIVE_MARKERS,
) -> OutputVerdict:
    """Whitelist first, v2 dictionary second — but a prescriptive marker revokes it.

    A whitelist hit (분리불안/불안/하울링/짖음/배변/산책/사회화, ...) passes the
    text through unmodified — no disclaimer, no block — which is classify_input_v2's
    already-accepted trade-off (data/guardrail/training_whitelist_v1.json's
    known_tradeoff) applied symmetrically here.

    **The whitelist no longer wins when a prescriptive marker is present** (added
    2026-08-25). Q&A sources put an owner's question and a trainer's answer in one
    retrieval, and when both reach the model the answer can carry training
    vocabulary from one and drug vocabulary from the other. Measured on synthetic
    Q&A pairs, a whitelist term from the expert answer neutralised 처방/연고를-class
    markers that came from the owner's question in 3 of 5 cases — every one of
    which would otherwise have been blocked. Training advice has no reason to say
    처방/투약/mg, so the marker is a usable signal for "this stopped being a
    training answer".

    Known limit: this does not fix the root cause — text from different sources
    merged into one string, where one source's vocabulary vouches for another's.
    It uses the marker list as a proxy. A dangerous combination that misses all
    19 markers bypasses this the same way. If that recurs, the answer is a
    provenance-aware check, not a longer marker list. See
    docs/design_qa_authority_retrieval.md.
    """
    whitelist_hits = _find_matches(answer, whitelist_terms)
    marker_hits = _find_matches(answer, markers)
    if whitelist_hits and not marker_hits:
        return OutputVerdict(is_blocked=False, text=answer, whitelist_matched=whitelist_hits)
    disease_hits = _find_matches(answer, medical_terms)
    if disease_hits and marker_hits:
        if whitelist_hits:
            # 화이트리스트가 있었는데도 차단된 경우 — 규칙 변경으로 판정이
            # 뒤집힌 자리다. 과차단이 실사용에서 나타나는지 보려면 이 사례가
            # 눈에 보여야 한다. 판정을 바꾸지는 않고 기록만 한다.
            _log_whitelist_revoked(whitelist_hits, disease_hits, marker_hits)
        return OutputVerdict(
            is_blocked=True,
            matched_disease_terms=disease_hits,
            matched_prescriptive_markers=marker_hits,
            text=OUTPUT_BLOCKED_MESSAGE,
            whitelist_matched=whitelist_hits,
        )
    if disease_hits:
        return OutputVerdict(
            is_blocked=False,
            matched_disease_terms=disease_hits,
            text=answer + OUTPUT_DISCLAIMER,
            whitelist_matched=whitelist_hits,
        )
    return OutputVerdict(is_blocked=False, text=answer, whitelist_matched=whitelist_hits)
