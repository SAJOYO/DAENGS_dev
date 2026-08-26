# evaluate_law_qa_hit_rate.py = law_qa_eval_set.json(scripts/generate_law_qa_eval_set.py로 생성,
# 질의마다 정답 청크의 documents_test.id가 정확히 붙어있는 데이터셋)을 가지고, 실제 검색
# 파이프라인(dense/keyword/hybrid/final=rerank)이 그 정답 id를 얼마나 잘 찾아내는지 측정.
#
# evaluate_search_quality.py의 EVAL_SET 채점은 "정답 키워드가 content에 포함되는지" 부분
# 일치 방식이라 느슨합니다. 이 스크립트는 문서 id를 정확히 대조하므로(id 연동) 훨씬 엄격한
# Hit Rate@1 / Hit Rate@k / MRR을 얻을 수 있습니다.
#
# 사용법: PYTHONPATH=src python scripts/evaluate_law_qa_hit_rate.py
# 사전 조건: docker compose up -d db, documents_test에 법령 청크가 적재되어 있어야 함,
#           Ollama 실행 중(bge-m3, qwen2.5:7b-instruct).

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import get_settings
from app.repository_documents_test import SearchTestResult, search_keyword_test, search_similar_test
from app.services.embedding import embed_text
from app.services.hybrid_search import reciprocal_rank_fusion
from app.services.reranker import rerank

EVAL_SET_PATH = (
    Path(__file__).resolve().parent.parent / "aidlc-docs" / "construction" / "build-and-test" / "law_qa_eval_set.json"
)
RESULT_PATH = (
    Path(__file__).resolve().parent.parent
    / "aidlc-docs"
    / "construction"
    / "build-and-test"
    / "law_qa_hit_rate_result.json"
)


@dataclass
class StageMetrics:
    hit_rate_at_1: float
    hit_rate_at_k: float
    mrr: float


def _rank_of_match(results: list[SearchTestResult], target_id: str) -> int | None:
    """target_id가 results 안에서 몇 번째(1부터)에 있는지. 없으면 None."""
    for rank, r in enumerate(results, start=1):
        if str(r.id) == target_id:
            return rank
    return None


def _aggregate(ranks: list[int | None], top_k: int) -> StageMetrics:
    n = len(ranks)
    hit_at_1 = sum(1 for r in ranks if r == 1) / n
    hit_at_k = sum(1 for r in ranks if r is not None and r <= top_k) / n
    mrr = sum((1.0 / r) if r is not None else 0.0 for r in ranks) / n
    return StageMetrics(hit_rate_at_1=hit_at_1, hit_rate_at_k=hit_at_k, mrr=mrr)


def main() -> None:
    settings = get_settings()
    with open(EVAL_SET_PATH, encoding="utf-8") as f:
        eval_set = json.load(f)
    print(f"평가 질의: {len(eval_set)}건 (top_k={settings.top_k})")

    dense_ranks, keyword_ranks, hybrid_ranks, final_ranks = [], [], [], []
    per_query_results = []

    for i, entry in enumerate(eval_set, start=1):
        query = entry["question"]
        target_id = entry["id"]

        query_embedding = embed_text(query)
        dense_results = search_similar_test(query_embedding, top_k=settings.retrieval_pool_size)
        keyword_results = search_keyword_test(query, top_k=settings.retrieval_pool_size)
        fused = reciprocal_rank_fusion(dense_results, keyword_results)[: settings.rerank_pool_size]
        final_results = rerank(query, fused, top_k=settings.top_k)

        dense_rank = _rank_of_match(dense_results, target_id)
        keyword_rank = _rank_of_match(keyword_results, target_id)
        hybrid_rank = _rank_of_match(fused, target_id)
        final_rank = _rank_of_match(final_results, target_id)

        dense_ranks.append(dense_rank)
        keyword_ranks.append(keyword_rank)
        hybrid_ranks.append(hybrid_rank)
        final_ranks.append(final_rank)

        per_query_results.append(
            {
                "section": entry["section"],
                "question": query,
                "dense_rank": dense_rank,
                "keyword_rank": keyword_rank,
                "hybrid_rank": hybrid_rank,
                "final_rank": final_rank,
            }
        )
        print(
            f"[{i}/{len(eval_set)}] {entry['section']} | dense={dense_rank} keyword={keyword_rank} "
            f"hybrid={hybrid_rank} final={final_rank}"
        )

    top_k = settings.top_k
    summary = {
        "n_queries": len(eval_set),
        "top_k": top_k,
        "dense": asdict(_aggregate(dense_ranks, settings.retrieval_pool_size)),
        "keyword": asdict(_aggregate(keyword_ranks, settings.retrieval_pool_size)),
        "hybrid": asdict(_aggregate(hybrid_ranks, settings.rerank_pool_size)),
        "final": asdict(_aggregate(final_ranks, top_k)),
    }

    print("\n=== 요약 (id 정확 일치 기준) ===")
    for stage in ["dense", "keyword", "hybrid", "final"]:
        m = summary[stage]
        print(
            f"{stage:8s} Hit@1={m['hit_rate_at_1']:.2%}  Hit@k={m['hit_rate_at_k']:.2%}  MRR={m['mrr']:.3f}"
        )

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "per_query": per_query_results}, f, ensure_ascii=False, indent=2)
    print(f"\n저장 완료: {RESULT_PATH}")


if __name__ == "__main__":
    main()
