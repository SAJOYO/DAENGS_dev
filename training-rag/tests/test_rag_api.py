"""HTTP contract tests for the PGVector-backed local RAG API."""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from scripts import runtime_generation as generation
from scripts import rag_api
from scripts.pgvector_runtime import RuntimeRetriever
from scripts.rag_api import RAGService, create_app, load_serving_document_ids


class FakeRetriever:
    model_name = "intfloat/multilingual-e5-base"

    def __init__(self, decision: str = "PASS", reason: str = "fixture") -> None:
        self.decision = decision
        self.reason = reason
        self.search_calls = 0

    def search(self, question: str, top_k: int) -> list[dict]:
        self.search_calls += 1
        return [
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-training",
                "chunk_index": 3,
                "text": "산책 훈련은 짧고 차분하게 시작합니다.",
                "metadata": {"heading_path": ["산책", "시작"]},
                "score": 0.91,
            }
        ]

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


def client_for(
    decision: str = "PASS", *, medical_terms: list[str] | None = None,
    answer: str = "[1] 산책은 짧고 차분하게 시작해 보세요.",
    reason: str = "fixture",
) -> tuple[TestClient, FakeRetriever, FakeClient]:
    retriever = FakeRetriever(decision, reason)
    model = FakeClient(answer)
    service = RAGService(
        retriever=retriever,
        client=model,
        medical_terms=medical_terms or [],
        whitelist_terms=[],
        serving_document_ids=("fixture-doc",),
    )
    return TestClient(create_app(service)), retriever, model


class RAGApiTests(unittest.TestCase):
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
        client, retriever, model = client_for()
        with client:
            response = client.post("/chat", json={"question": "산책 훈련은 어떻게 시작하나요?"})

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("ANSWER", body["decision"])
        self.assertTrue(body["generated"])
        self.assertEqual("gemini-3.1-flash-lite", body["model"])
        self.assertEqual("chunk-1", body["evidence"][0]["chunk_id"])
        self.assertEqual(1, retriever.search_calls)
        self.assertEqual(1, model.calls)

    def test_uncertain_retrieval_does_not_call_the_model(self):
        client, _, model = client_for("UNCERTAIN")
        with client:
            response = client.post("/chat", json={"question": "근거 없는 질문"})

        self.assertEqual(200, response.status_code)
        self.assertEqual("UNCERTAIN", response.json()["decision"])
        self.assertFalse(response.json()["generated"])
        self.assertEqual(0, model.calls)

    def test_model_no_evidence_fallback_is_not_reported_as_an_answer(self):
        client, _, model = client_for(
            answer="제공된 자료에는 이 질문에 대한 내용이 없습니다."
        )
        with client:
            response = client.post("/chat", json={"question": "범위 밖 질문"})

        self.assertEqual(200, response.status_code)
        self.assertEqual("UNCERTAIN", response.json()["decision"])
        self.assertFalse(response.json()["generated"])
        self.assertEqual(1, model.calls)

    def test_medical_input_is_refused_before_retrieval_or_generation(self):
        client, retriever, model = client_for(medical_terms=["약용 샴푸"])
        with client:
            response = client.post("/chat", json={"question": "약용 샴푸를 추천해 주세요"})

        self.assertEqual(200, response.status_code)
        self.assertEqual("MEDICAL_REFUSAL", response.json()["decision"])
        self.assertFalse(response.json()["generated"])
        self.assertEqual(0, retriever.search_calls)
        self.assertEqual(0, model.calls)

    def test_safety_refusal_does_not_blame_the_corpus(self):
        """A boundary refusal and an empty retrieval are different facts.

        generation.REFUSAL_TEXT says the supplied material has nothing on the
        question.  For a boundary refusal that sentence is false — the corpus
        may well cover the topic — and it was the only text every gate REFUSE
        sent, so the reader was given the wrong reason for the refusal.
        """
        client, _, model = client_for("REFUSE", reason="safety_boundary_training_harm")
        with client:
            response = client.post("/chat", json={"question": "체벌해도 되나요?"})

        body = response.json()
        self.assertEqual("REFUSE", body["decision"])
        self.assertEqual(rag_api.SAFETY_BOUNDARY_TEXT, body["answer"])
        self.assertNotIn("제공된 자료에는", body["answer"])
        self.assertEqual("safety_boundary_training_harm", body["reason"])
        self.assertEqual(0, model.calls)

    def test_empty_retrieval_still_says_the_corpus_had_nothing(self):
        """The other half of the split: no_results keeps the original wording."""
        client, _, model = client_for("REFUSE", reason="no_results")
        with client:
            response = client.post("/chat", json={"question": "코퍼스 밖 주제"})

        body = response.json()
        self.assertEqual("REFUSE", body["decision"])
        self.assertEqual(generation.REFUSAL_TEXT, body["answer"])
        self.assertEqual(0, model.calls)

    def test_blank_question_is_rejected_at_the_http_boundary(self):
        client, _, _ = client_for()
        with client:
            response = client.post("/chat", json={"question": "   "})

        self.assertEqual(422, response.status_code)

    def test_healthz_identifies_the_active_models_without_a_model_call(self):
        client, _, model = client_for()
        with client:
            response = client.get("/healthz")

        self.assertEqual(200, response.status_code)
        self.assertEqual("gemini-3.1-flash-lite", response.json()["generation_model"])
        self.assertEqual(0, model.calls)
