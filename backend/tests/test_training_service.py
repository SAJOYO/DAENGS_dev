"""HTTP contract tests for the PGVector-backed local RAG API."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from daengs_training import service as rag_service
from daengs_training.generation import gemini as generation
from daengs_training.retrieval.pgvector import RuntimeRetriever
from daengs_training.service import RAGService, TrainingTimeoutError, load_serving_document_ids

#: Synthetic evidence.  Not corpus text — written for these tests only.
WALK_HITS = [
    {
        "chunk_id": "chunk-1",
        "document_id": "doc-training",
        "chunk_index": 3,
        "text": "산책 훈련은 짧고 차분하게 시작합니다.",
        "metadata": {"heading_path": ["산책", "시작"]},
        "score": 0.91,
    }
]

#: Food / resource-guarding advice.  It never says that a dog merely liking meat
#: is the same problem — that is the inference the service must not accept.
GUARDING_HITS = [
    {
        "chunk_id": "chunk-guard",
        "document_id": "doc-training",
        "chunk_index": 5,
        "text": "먹이를 빼앗으려 하면 으르렁거리며 지키는 행동은 먹이를 줄 때 '기다려'를 먼저 가르쳐 줄입니다.",
        "metadata": {"heading_path": ["FAQ", "먹이 지키기"]},
        "score": 0.83,
    }
]

BITING_HITS = [
    {
        "chunk_id": "chunk-bite",
        "document_id": "doc-training",
        "chunk_index": 7,
        "text": "손을 물면 놀이를 바로 멈추고, 물어도 되는 장난감으로 바꿔 줍니다.",
        "metadata": {"heading_path": ["FAQ", "무는 행동"]},
        "score": 0.87,
    }
]


class FakeRetriever:
    model_name = "intfloat/multilingual-e5-base"

    def __init__(
        self, decision: str = "PASS", reason: str = "fixture", hits: list[dict] | None = None
    ) -> None:
        self.decision = decision
        self.reason = reason
        self.hits = WALK_HITS if hits is None else hits
        self.search_calls = 0

    def search(self, question: str, top_k: int) -> list[dict]:
        self.search_calls += 1
        return list(self.hits)

    def gate(self, question: str, results: list[dict]) -> dict:
        return {"decision": self.decision, "reason": self.reason, "top_score": 0.91}


class FakeClient:
    model_id = "gemini-3.1-flash-lite"
    reasoning_effort = "disabled"
    info = generation.ClientInfo(name="gemini:gemini-3.1-flash-lite")

    def __init__(self, answer: str = "[1] 산책은 짧고 차분하게 시작해 보세요.") -> None:
        self.calls = 0
        self.answer = answer

    def complete(self, prompt: str, record: dict) -> str:
        self.calls += 1
        record["usage"] = {"input_tokens": 10, "output_tokens": 12}
        return self.answer


class TimeoutClient(FakeClient):
    def complete(self, prompt: str, record: dict) -> str:
        raise generation.GenerationTimeoutError("provider deadline")


def client_for(
    decision: str = "PASS", *, medical_terms: list[str] | None = None,
    answer: str = "[1] 산책은 짧고 차분하게 시작해 보세요.",
    reason: str = "fixture",
    hits: list[dict] | None = None,
) -> tuple[RAGService, FakeRetriever, FakeClient]:
    retriever = FakeRetriever(decision, reason, hits)
    model = FakeClient(answer)
    service = RAGService(
        retriever=retriever,
        client=model,
        medical_terms=medical_terms or [],
        whitelist_terms=[],
        serving_document_ids=("fixture-doc",),
    )
    return service, retriever, model


class RAGApiTests(unittest.TestCase):
    def test_gemini_timeout_keeps_a_typed_provider_signal(self):
        client = generation.load_gemini_answer_client(api_key="test-key")
        with patch.object(
            generation.httpx,
            "post",
            side_effect=httpx.ReadTimeout("deadline"),
        ), self.assertRaises(generation.GenerationTimeoutError):
            client.complete("prompt", {})

    def test_training_domain_preserves_generation_timeout(self):
        retriever = FakeRetriever()
        service = RAGService(
            retriever=retriever,
            client=TimeoutClient(),
            medical_terms=[],
            whitelist_terms=[],
            serving_document_ids=("fixture-doc",),
        )
        with self.assertRaises(TrainingTimeoutError):
            service.answer("산책 훈련은 어떻게 시작하나요?")

    def test_serving_corpus_is_a_nonempty_unique_reviewed_allow_list(self):
        document_ids = load_serving_document_ids()
        self.assertEqual(14, len(document_ids))
        self.assertEqual(len(document_ids), len(set(document_ids)))
        self.assertTrue(all(doc_id.startswith("nias_companion-") for doc_id in document_ids))

    def test_runtime_filter_excludes_non_evidence_artifacts(self):
        self.assertFalse(RuntimeRetriever.is_retrieval_eligible("[1](#) [2](#)"))
        self.assertFalse(RuntimeRetriever.is_retrieval_eligible(
            "수집된 HTML에서 본문 텍스트를 추출하지 못했습니다."
        ))
        self.assertFalse(RuntimeRetriever.is_retrieval_eligible("schema_version: 1\ndoc_id: x"))
        self.assertFalse(RuntimeRetriever.is_retrieval_eligible("A" * 200))
        self.assertTrue(RuntimeRetriever.is_retrieval_eligible(
            "배변 패드는 잠자리에서 떨어진 곳에 둡니다."
        ))

    def test_chat_generates_only_after_a_pass_and_returns_evidence_cards(self):
        service, retriever, model = client_for()
        body = service.answer("산책 훈련은 어떻게 시작하나요?").model_dump()
        self.assertEqual("ANSWER", body["decision"])
        self.assertTrue(body["generated"])
        self.assertEqual("gemini-3.1-flash-lite", body["model"])
        self.assertEqual("chunk-1", body["evidence"][0]["chunk_id"])
        self.assertEqual(1, retriever.search_calls)
        self.assertEqual(1, model.calls)

    def test_uncertain_retrieval_does_not_call_the_model(self):
        service, _, model = client_for("UNCERTAIN")
        response = service.answer("근거 없는 질문")
        self.assertEqual("UNCERTAIN", response.decision)
        self.assertFalse(response.generated)
        self.assertEqual(0, model.calls)

    def test_model_no_evidence_fallback_is_not_reported_as_an_answer(self):
        service, _, model = client_for(
            answer="제공된 자료에는 이 질문에 대한 내용이 없습니다."
        )
        response = service.answer("범위 밖 질문")
        self.assertEqual("UNCERTAIN", response.decision)
        self.assertFalse(response.generated)
        self.assertEqual(1, model.calls)

    # --- Evidence directness: the answer may only address what the user stated -------------
    #
    # Reproduced 2026-09-03: "고기를 너무 좋아해서 문제야. 대책은?" retrieved adjacent FAQ
    # chunks (food/possession guarding, attachment, hierarchy) that PASS the floor gate,
    # and the model opened with "자료에는 직접 내용이 없지만" and then gave cited advice for a
    # problem the user never described.  These cases pin the boundary from both sides.

    def test_vague_problem_with_only_adjacent_evidence_is_uncertain(self):
        """CASE A — the reproduced defect, with the model's actual answer shape."""
        adjacent_answer = (
            "제공된 자료에는 강아지가 고기를 너무 좋아하는 문제에 대한 직접적인 내용은 없습니다. "
            "다만, 먹이에 대한 집착과 관련해서는 먹이를 빼앗으려 할 때 으르렁거리며 지키는 행동을 "
            "'기다려' 교육으로 줄일 수 있고, 반려인이 서열 상위를 차지해야 합니다 [1]."
        )
        service, _, model = client_for(answer=adjacent_answer, hits=GUARDING_HITS)
        body = service.answer("고기를 너무 좋아해서 문제야. 대책은?").model_dump()
        self.assertEqual("UNCERTAIN", body["decision"])
        self.assertEqual("model_reported_insufficient_evidence", body["reason"])
        self.assertFalse(body["generated"])
        self.assertEqual(rag_service.INSUFFICIENT_EVIDENCE_TEXT, body["answer"])
        for adjacent_problem in ("지키", "집착", "으르렁", "서열", "[1]"):
            self.assertNotIn(adjacent_problem, body["answer"])
        self.assertEqual(1, model.calls)

    def test_vague_problem_with_clause_final_negation_is_uncertain(self):
        """CASE A, the other opener the model uses: "...없지만, ..." in one sentence."""
        pivot_answer = (
            "제공된 자료에는 고기를 좋아하는 것 자체에 대한 내용은 없지만, 먹이를 지키는 행동은 "
            "'기다려' 교육으로 줄일 수 있습니다 [1]."
        )
        service, _, _ = client_for(answer=pivot_answer, hits=GUARDING_HITS)
        response = service.answer("고기를 너무 좋아해서 문제야. 대책은?")
        self.assertEqual("UNCERTAIN", response.decision)
        self.assertFalse(response.generated)

    def test_explicit_resource_guarding_stays_answerable(self):
        """CASE B — the same evidence answers a question that actually states guarding."""
        service, _, _ = client_for(
            answer="먹이를 줄 때 '기다려'를 먼저 가르치면 지키는 행동이 줄어듭니다 [1].",
            hits=GUARDING_HITS,
        )
        body = service.answer("고기를 뺏으려고 하면 으르렁거리면서 지켜요. 어떻게 해야 해?").model_dump()
        self.assertEqual("ANSWER", body["decision"])
        self.assertTrue(body["generated"])
        self.assertEqual("chunk-guard", body["evidence"][0]["chunk_id"])

    def test_ordinary_training_question_stays_answerable(self):
        """CASE C — a directly supported behavior question is unaffected."""
        service, _, _ = client_for(
            answer="손을 물면 놀이를 바로 멈추고 장난감으로 바꿔 주세요 [1].",
            hits=BITING_HITS,
        )
        response = service.answer("강아지가 자꾸 손을 물어요. 어떻게 훈련해야 해?")
        self.assertEqual("ANSWER", response.decision)
        self.assertTrue(response.generated)

    def test_grounded_answer_mentioning_a_gap_later_is_still_an_answer(self):
        """A negation deep inside a grounded answer is not an opening disclaimer."""
        answer = (
            "손을 물면 놀이를 바로 멈추고 물어도 되는 장난감으로 바꿔 줍니다 [1]. 반복하면 무는 것과 "
            "물지 말아야 하는 것을 구분하게 됩니다 [1]. 이 방법을 며칠 동안 꾸준히 적용해 보세요. "
            "장난감을 바꿔 주는 순서는 놀이를 멈춘 직후가 좋습니다 [1]. 다만 몇 주가 걸리는지에 "
            "대한 정보는 제공된 자료에는 없습니다."
        )
        self.assertFalse(rag_service.model_reported_no_evidence(answer))
        service, _, _ = client_for(answer=answer, hits=BITING_HITS)
        self.assertEqual("ANSWER", service.answer("강아지가 자꾸 손을 물어요.").decision)

    def test_canonical_no_evidence_sentence_is_recognized_verbatim(self):
        """CASE D — the sentence the v3 prompt asks for maps to the existing path."""
        self.assertTrue(rag_service.model_reported_no_evidence(generation.NO_EVIDENCE_SENTENCE))

    def test_medical_input_is_refused_before_retrieval_or_generation(self):
        service, retriever, model = client_for(medical_terms=["약용 샴푸"])
        response = service.answer("약용 샴푸를 추천해 주세요")
        self.assertEqual("MEDICAL_REFUSAL", response.decision)
        self.assertFalse(response.generated)
        self.assertEqual(0, retriever.search_calls)
        self.assertEqual(0, model.calls)

    def test_safety_refusal_does_not_blame_the_corpus(self):
        """A boundary refusal and an empty retrieval are different facts.

        generation.REFUSAL_TEXT says the supplied material has nothing on the
        question.  For a boundary refusal that sentence is false — the corpus
        may well cover the topic — and it was the only text every gate REFUSE
        sent, so the reader was given the wrong reason for the refusal.
        """
        service, _, model = client_for("REFUSE", reason="safety_boundary_training_harm")
        body = service.answer("체벌해도 되나요?").model_dump()
        self.assertEqual("REFUSE", body["decision"])
        self.assertEqual(rag_service.SAFETY_BOUNDARY_TEXT, body["answer"])
        self.assertNotIn("제공된 자료에는", body["answer"])
        self.assertEqual("safety_boundary_training_harm", body["reason"])
        self.assertEqual(0, model.calls)

    def test_empty_retrieval_still_says_the_corpus_had_nothing(self):
        """The other half of the split: no_results keeps the original wording."""
        service, _, model = client_for("REFUSE", reason="no_results")
        body = service.answer("코퍼스 밖 주제").model_dump()
        self.assertEqual("REFUSE", body["decision"])
        self.assertEqual(generation.REFUSAL_TEXT, body["answer"])
        self.assertEqual(0, model.calls)

    def test_blank_question_is_rejected_by_the_service(self):
        service, _, _ = client_for()
        with self.assertRaises(ValueError):
            service.answer("   ")

    def test_runtime_identifies_generation_model_without_calling_it(self):
        service, _, model = client_for()
        self.assertEqual("gemini-3.1-flash-lite", service.model_name)
        self.assertEqual(0, model.calls)
