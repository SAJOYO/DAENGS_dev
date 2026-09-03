"""Contract of the Training generation prompt (grounded-answer-ko-v3).

These pin what a future prompt edit must not silently drop: the direct-evidence
rule that keeps an underspecified question from being answered as an adjacent
behavior, the Korean output contract, and the canonical no-evidence sentence the
service recognises.  No model is called.
"""

from __future__ import annotations

from daengs_training import service as rag_service
from daengs_training.generation import gemini as generation

QUESTION = "고기를 너무 좋아해서 문제야. 대책은?"
EVIDENCE = [
    {
        "doc_id": "doc-synthetic",
        "chunk_index": 2,
        "heading_path": ["FAQ", "먹이 지키기"],
        "text": "먹이를 빼앗으려 하면 으르렁거리며 지키는 행동은 '기다려' 교육으로 줄입니다.",
        "citation_allowed": True,
    }
]


def prompt(**kwargs) -> str:
    return generation.build_prompt(QUESTION, EVIDENCE, "answer", **kwargs)


def test_prompt_version_is_v3_and_clients_report_it() -> None:
    assert generation.PROMPT_VERSION == "grounded-answer-ko-v3"
    assert generation.ClientInfo(name="x").prompt_version == "grounded-answer-ko-v3"


def test_direct_evidence_rule_is_in_the_prompt() -> None:
    """The rule this card exists for.  Remove it and the meat → guarding expansion returns."""
    text = prompt()
    assert generation.DIRECT_EVIDENCE_RULE in text
    assert "directly address the behavior or problem the user actually stated" in text
    assert "adjacent" in text and "NOT sufficient" in text
    assert "Do not infer, assume, or substitute" in text


def test_no_evidence_rule_names_the_canonical_korean_sentence_and_stops() -> None:
    """Abstaining means the canonical sentence alone — no "다만 ..." advice after it."""
    text = prompt()
    assert generation.NO_EVIDENCE_SENTENCE == "제공된 자료에는 이 질문에 대한 내용이 없습니다."
    assert f'"{generation.NO_EVIDENCE_SENTENCE}"' in text
    assert "nothing else" in text
    assert "Do not follow it with advice about a related or adjacent problem" in text
    # The sentence the prompt asks for is the one the service turns into UNCERTAIN.
    assert rag_service.model_reported_no_evidence(generation.NO_EVIDENCE_SENTENCE)


def test_model_facing_instructions_are_english_and_output_is_korean() -> None:
    text = prompt()
    assert "Answer the user in Korean." in text
    assert text.startswith("Answer the question using only the <evidence> below as grounds.")
    assert "Rules:" in text and "<evidence>" in text and "</evidence>" in text
    assert text.endswith(f"Question: {QUESTION}")
    # v2's Korean instruction scaffolding is gone.
    for korean_instruction in ("규칙:", "<자료>", "</자료>", "질문:", "아래 <자료>만 근거로"):
        assert korean_instruction not in text
    for rule in generation.PROMPT_RULES:
        assert rule in text


def test_evidence_and_question_are_not_translated() -> None:
    text = prompt()
    assert EVIDENCE[0]["text"] in text
    assert QUESTION in text
    assert "[1] (document · doc-synthetic #2 · FAQ > 먹이 지키기)" in text


def test_rules_are_numbered_in_order_without_duplicates() -> None:
    numbered = [line for line in prompt().splitlines() if line[:1].isdigit()]
    assert [line.split(".", 1)[0] for line in numbered] == [str(i) for i in range(1, 8)]
    assert numbered[1].startswith("2. " + generation.DIRECT_EVIDENCE_RULE[:40])
    assert numbered[-1] == "7. Answer the user in Korean."


def test_hedge_and_user_case_rules_keep_korean_user_facing_text() -> None:
    hedged = generation.build_prompt(QUESTION, EVIDENCE, "hedge")
    assert "8. " + generation.HEDGE_RULE in hedged
    assert "[근거 약함] 아래 답변은 관련성이 낮은 자료에 기반합니다" in hedged

    with_case = generation.build_prompt(
        QUESTION,
        [
            *EVIDENCE,
            {"doc_id": "case-1", "text": "우리 개도 고기를 좋아해요", "citation_allowed": False},
        ],
        "answer",
    )
    assert "8. " + generation.CONTEXT_ONLY_RULE in with_case
    assert "<user_cases>" in with_case and "우리 개도 고기를 좋아해요" in with_case
    assert "<사용자사례>" not in with_case


def test_profile_block_keeps_caller_keys_and_values() -> None:
    text = prompt(
        profile={"견종": "푸들", "나이": "3살", "몸무게": "5kg", "기존질환": [], "비고": "없음"}
    )
    assert "<profile>" in text and generation.PROFILE_NOTE in text
    assert "Breed: 푸들" in text and "Existing conditions: 없음" in text
    assert "<프로필>" not in text


def test_refusal_text_is_untouched_system_korean() -> None:
    assert generation.REFUSAL_TEXT.startswith("제공된 자료에는 이 질문에 답할 내용이 없습니다.")
