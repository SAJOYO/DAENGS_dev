"""Runtime PGVector retriever with a conservative answer gate."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass

from langsmith import traceable

from daengs_training import telemetry
from daengs_training.resources import RUNTIME_ROOT

#: Minimum characters for any term in SAFETY_BOUNDARY_TERMS.
#:
#: This is the same rule data/guardrail/medical_terms_v2.json applies to its own
#: entries (medical_guardrail.MIN_TERM_CHARS), and it exists here for the reason
#: that file's risk_notes recorded on 2026-08-20: a one-syllable term matches as
#: a substring of ordinary words.  That lexicon dropped bare "약" for the phrase
#: "약을 먹여" after "예약 / 요약 / 계약 / 생략 / 절약" were shown to match it.
#:
#: The lesson never reached this gate, which kept its own copy of "약" until
#: 2026-08-28.  The cost was measured, not hypothetical: "배변 훈련은 약 몇 주
#: 정도 걸리나요?", "산책 줄을 약하게 잡아야 하나요?" and "간식을 약간만 주면서
#: 훈련해도 되나요?" were all refused, and the first two had already been passed
#: by the whitelist in classify_input_v2 one stage earlier.
MIN_BOUNDARY_TERM_CHARS = 2

#: The refuse_boundary category of data/eval/queries/training_api_eval_v1.jsonl,
#: as vocabulary.  Two concepts, taken from the frozen set's own reviewer_note
#: fields: "체벌 요청 거절" (oq0033) and "사람 약 거절" (oq0034).
#:
#: **Medical vocabulary does not belong here.**  That judgement is owned by
#: daengs_training.guardrails.medical.classify_input_v2, which checks the training
#: whitelist first, and _medical_verdict() below delegates to it rather than
#: keeping a second opinion.  Two lexicons for one question is what let this
#: gate overrule a whitelist PASS.
#:
#: These two entries are not medical vocabulary in that sense: the frozen set
#: labels them refuse_boundary and expects REFUSE, while a classify_input_v2
#: hit produces MEDICAL_REFUSAL.  They ask for a harmful *action*, which is this
#: gate's own remit — the same split daengs_training.guardrails.medical's docstring
#: describes ("if a caller wants training-harm coverage, that is a new lexicon
#: and a new stage, not an extension of MEDICAL_TERMS").
#:
#: Recall is deliberately narrow, and narrow in the way medical_terms_v2's
#: risk_notes describes for "약을 먹여": "사람 약을" catches "사람 약을 임의로
#: 먹여도" but not "사람 약 먹여도", and "임의로 먹여" catches the adverb form
#: only.  Widening it means adding more phrases here, never shortening one.
SAFETY_BOUNDARY_TERMS = (
    # 체벌·물리적 처벌 요청. "때리" covers 때리면 / 때리는 / 사람을 때리.
    "체벌", "때려", "때리",
    # 임의 투약 요청. Removed from this gate: bare "약", "복용량", "처방" —
    # the last two are medical_terms_v2 entries and are reached through
    # _medical_verdict() instead, so the two lexicons cannot drift apart.
    "사람 약을", "임의로 먹여",
)

_BOUNDARY_PATTERN = re.compile("|".join(re.escape(t) for t in SAFETY_BOUNDARY_TERMS))

#: Floor for catastrophically broken retrieval — **not a relevance check.**
#:
#: Measured 2026-08-28 on the frozen answerable set (19 rows,
#: reports/retrieval_gate_signal_0828.md): observed top_score runs 0.8385~0.8832,
#: so this branch fires 0 times.  It stays because a run whose scores collapse
#: below it is broken in a way worth catching — the obvious one being a serving
#: embedding label that does not match the indexed vectors, which produces
#: garbage silently because the dimensions still line up.
#:
#: It cannot be turned into a relevance check by raising it.  hit and miss
#: overlap completely on top_score, on margin_topk, on top1-top2, on document
#: diversity and on score spread; the best in-sample threshold buys 2 rows out
#: of 19 over "always PASS" and the best-looking one points the wrong way.  E5
#: encodes query and passage separately, and across 65 same-domain chunks the
#: gold/non-gold difference is 0.003~0.032 — under the model's resolution.
#: A usable gate needs a different score source, not a different number here.
RETRIEVAL_FLOOR_SCORE = 0.70

_MEDICAL_LEXICONS: tuple | None = None


def _trace_documents(hits: list[dict]) -> dict:
    """검색 결과를 retriever 런의 모양으로.

    **본문 전문을 그대로 싣습니다.** "왜 이 답이 나왔나" 는 결국 "무엇을 읽고 답했나"
    이고, 청크를 식별자로만 남기면 트레이스를 열 때마다 DB 를 다시 봐야 합니다.
    그 비용이 민원 하나당 한 번씩 붙으면 아무도 트레이스를 안 봅니다.

    `page_content` · `metadata` 이름을 쓰는 것은 트레이스 뷰어가 그 모양을 문서
    카드로 렌더하기 때문입니다. 다른 이름이면 그냥 JSON 덩어리로 보입니다.

    **맨 앞의 가드가 핵심입니다.** langsmith 는 트레이싱이 꺼져 있어도 이 함수를
    부릅니다 — `_handle_container_end` 가 `outputs_processor` 를 먼저 돌리고 그 다음에
    `_container_end` 가 빠져나갑니다 (0.11.2 실측: 꺼진 상태에서 3회 호출 중 3회 실행).
    가드가 없으면 **트레이싱을 안 켠 서버가 매 요청마다 청크 4개 전문을 복사합니다.**
    """
    from langsmith import utils as ls_utils

    if not ls_utils.tracing_is_enabled():
        return {}
    # 검색이 예외로 끝나면 langsmith 는 여기를 `None` 으로 부른다 (0.11.2 실측 —
    # DB 접속 실패 때 "NoneType is not iterable" 로그가 그것). 실패한 런의 출력은 빈 것이
    # 맞고, 예외 자체는 langsmith 가 런의 error 로 따로 남긴다.
    if hits is None:
        return {}
    return {
        "documents": [
            {
                "page_content": hit.get("text", ""),
                "metadata": {
                    "chunk_id": hit.get("chunk_id"),
                    "document_id": hit.get("document_id"),
                    "chunk_index": hit.get("chunk_index"),
                    "score": hit.get("score"),
                    **(hit.get("metadata") or {} if isinstance(hit.get("metadata"), dict) else {}),
                },
            }
            for hit in hits
        ]
    }


def _medical_verdict(question: str):
    """Ask medical_guardrail.classify_input_v2 — the one owner of that judgement.

    Returns None when the hand-authored lexicon files cannot be read, which
    leaves the medical stage out rather than failing a retrieval call.  The
    serving path does not depend on this: daengs_training.service loads the same two
    lexicons at construction time and refuses medical questions before any
    search happens, so a None here cannot open a hole there.

    Paths are resolved from this file, not the process CWD, because gate() is
    called from the CLI below and from experiment scripts that run from
    elsewhere.
    """
    global _MEDICAL_LEXICONS
    if _MEDICAL_LEXICONS is None:
        try:
            from daengs_training.guardrails import medical as medical_guardrail
            _MEDICAL_LEXICONS = (
                medical_guardrail,
                medical_guardrail.load_medical_terms_v2(RUNTIME_ROOT / "data/guardrail/medical_terms_v2.json"),
                medical_guardrail.load_training_whitelist(RUNTIME_ROOT / "data/guardrail/training_whitelist_v1.json"),
            )
        except Exception:  # noqa: BLE001 - missing/malformed lexicon or import path
            _MEDICAL_LEXICONS = ()
    if not _MEDICAL_LEXICONS:
        return None
    module, medical_terms, whitelist_terms = _MEDICAL_LEXICONS
    return module.classify_input_v2(question, medical_terms, whitelist_terms)

@dataclass
class RuntimeRetriever:
    dsn: str = "postgresql://postgres:postgres@localhost:5432/vectordb"
    model_name: str = "intfloat/multilingual-e5-base"
    embedding_label: str | None = None
    document_ids: tuple[str, ...] | None = None
    def __post_init__(self):
        import psycopg
        from sentence_transformers import SentenceTransformer
        self.label=self.embedding_label or self.model_name
        self.model=SentenceTransformer(self.model_name)
        self.psycopg=psycopg
    @staticmethod
    def is_retrieval_eligible(text: str) -> bool:
        """Reject extraction/navigation artifacts without deleting their raw rows.

        The collection DB intentionally keeps broad raw data.  Those artifacts
        must still never become RAG evidence: empty extractor placeholders,
        pagination links, embedded base64 images, and serialized document
        headers have no claim an answer model may ground itself in.
        """
        clean = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", text).replace("\u200b", "").strip()
        if not clean:
            return False
        if clean.startswith("schema_version:"):
            return False
        if "본문 텍스트를 추출하지 못했습니다" in clean or "원문 추출 결과 없음" in clean:
            return False
        if re.search(r"[A-Za-z0-9+/=]{128,}", clean):
            return False
        # A board's pagination strip survives the link strip as its current page
        # number in bold ("**2**").  Measured 2026-09-03 on the serving corpus:
        # three such chunks were eligible, and for short casual queries
        # ("자꾸 깨물어요", "손 물어요", "옷을 물어뜯어요") they took one to three
        # of the four evidence slots — a digit satisfied the old check.  Evidence
        # is prose, and prose has letters: Korean or Latin.  Numbers, markup and
        # navigation residue alone are not evidence.
        return bool(re.search(r"[가-힣A-Za-z]", clean))

    @traceable(run_type="retriever", name="pgvector_search", process_outputs=_trace_documents)
    def search(self, question: str, top_k: int=5):
        # Timing only (daengs_training.telemetry).  The encode call, the connection and
        # the query are exactly as before; connect_ms/query_ms fall out of the existing
        # `with connect()` boundary without restructuring retrieval.
        trace=telemetry.current_trace()
        with trace.stage(telemetry.EVENT_EMBEDDING):
            vec=self.model.encode("query: "+question,normalize_embeddings=True)
        vector="["+",".join(str(float(x)) for x in vec)+"]"
        with trace.stage(telemetry.EVENT_PGVECTOR) as pgvector_stage:
            connect_started=time.perf_counter()
            with self.psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
                pgvector_stage["connect_ms"]=int((time.perf_counter()-connect_started)*1_000)
                query_started=time.perf_counter()
                # A large candidate pool is intentional: noisy raw collection rows
                # can otherwise occupy all of a top-5 before the quality filter sees
                # any real prose.  This is local PGVector; generation dominates the
                # request latency by orders of magnitude.
                candidate_limit=max(500, top_k * 100)
                where="embedding_model=%s"
                params=[vector,self.label]
                if self.document_ids is not None:
                    # The serving allow-list is small enough that an exact scan is
                    # faster and more reliable than an HNSW filtered scan.  HNSW's
                    # finite candidate set can otherwise return no filtered rows.
                    cur.execute("set local enable_indexscan = off")
                    where += " and document_id = any(%s)"
                    params.append(list(self.document_ids))
                params.extend([vector,candidate_limit])
                cur.execute(f"select chunk_id,document_id,chunk_index,text,metadata,1-(embedding <=> %s::vector) from training_rag_chunks where {where} order by embedding <=> %s::vector limit %s",params)
                rows=[]
                seen=set()
                for r in cur.fetchall():
                    if r[0] in seen or not self.is_retrieval_eligible(r[3]):
                        continue
                    seen.add(r[0])
                    rows.append({"chunk_id":r[0],"document_id":r[1],"chunk_index":r[2],"text":r[3],"metadata":r[4],"score":float(r[5])})
                    if len(rows)>=top_k:
                        break
                pgvector_stage["query_ms"]=int((time.perf_counter()-query_started)*1_000)
                return rows
    def gate(self, question: str, results: list[dict]):
        """Harm boundary, then the medical verdict, then retrieval quality.

        **The whitelist outranks the medical dictionary and nothing else.**
        That is precisely the precedence data/guardrail/training_whitelist_v1.json
        claims for itself — "medical_terms_v2.json보다 매칭 우선순위가 높다" — and
        classify_input_v2 is where it is applied.  This gate honours that by
        asking classify_input_v2 for the medical verdict instead of keeping a
        second copy of its vocabulary, which is what used to overrule it: the
        gate carried its own "약" and refused questions the whitelist had just
        passed on 배변 / 산책.

        SAFETY_BOUNDARY_TERMS is checked *before* that, and the whitelist does
        not reach it.  Measured on 2026-08-28: with the whitelist first,
        "산책 중에 말을 안 들으면 때려도 되나요?" and five like it passed on
        배변 / 산책 / 짖음 / 분리불안 / 사회화 / 하울링 alone.  Those words appear in
        most training questions, so whitelist-first switched the physical-harm
        boundary off for the common case.  The whitelist file's known_tradeoff
        accepts that risk for *medical* vocabulary only; extending it to
        physical harm was never reviewed, and is not assumed here.

        Neither order costs the overblocking fix: the four pass controls in
        tests/test_pgvector_runtime.py carry no boundary term, so they reach
        retrieval under both.
        """
        boundary=_BOUNDARY_PATTERN.search(question)
        if boundary: return {"decision":"REFUSE","reason":"safety_boundary_training_harm","matched":boundary.group(0)}
        medical=_medical_verdict(question)
        if medical is not None:
            if medical.whitelist_matched:
                # 의료 사전보다 화이트리스트가 이긴다 — classify_input_v2 의 규칙 그대로다.
                return self._retrieval_decision(results, whitelist=list(medical.whitelist_matched))
            if medical.is_medical:
                return {"decision":"REFUSE","reason":"safety_boundary_medical","matched":list(medical.matched_terms)}
        return self._retrieval_decision(results)

    @staticmethod
    def _retrieval_decision(results: list[dict], whitelist: list[str] | None = None):
        extra={"whitelist_matched":whitelist} if whitelist else {}
        if not results: return {"decision":"REFUSE","reason":"no_results",**extra}
        if results[0]["score"] < RETRIEVAL_FLOOR_SCORE: return {"decision":"UNCERTAIN","reason":"low_top_score","top_score":results[0]["score"],**extra}
        # 진단용으로 계속 싣는다. **판정에는 쓰지 않는다** — hit/miss 를 못 가른다는 것이
        # 측정됐다(reports/retrieval_gate_signal_0828.md). "retrieval_confident" 는
        # 관련성 판정이 아니라 "바닥 위"라는 뜻이다.
        margin=results[0]["score"]-(results[-1]["score"] if len(results)>1 else 0)
        return {"decision":"PASS","reason":"retrieval_confident","top_score":results[0]["score"],"margin_topk":margin,**extra}

def main():
    if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    ap=argparse.ArgumentParser(); ap.add_argument("question"); ap.add_argument("--top-k",type=int,default=5); args=ap.parse_args(); r=RuntimeRetriever(); hits=r.search(args.question,args.top_k); print(json.dumps({"question":args.question,"gate":r.gate(args.question,hits),"results":[{**x,"text":x["text"][:500]} for x in hits]},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
