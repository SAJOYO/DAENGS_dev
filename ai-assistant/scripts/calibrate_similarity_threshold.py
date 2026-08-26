"""similarity_threshold 재보정용 스윕(sweep) 스크립트.

rag.py의 grounding 판정 로직(dense_results[0].score >= similarity_threshold)을 그대로
재현해, 후보 임계값마다 "정답 있는 질의(EVAL_SET)가 여전히 grounded로 잡히는 비율"과
"무관한 질의(NEGATIVE_QUERIES)가 잘못 grounded로 잡히는 비율(오탐률)"을 함께 측정합니다.

evaluate_search_quality.py의 EVAL_SET/NEGATIVE_QUERIES를 그대로 재사용합니다(같은 기준으로
비교해야 의미가 있으므로 별도로 새로 만들지 않음).

2026-08-19 실행 결과, threshold=0.5에서 오탐률 83.3%(5/6)가 나왔고, 오탐 케이스들의 top-1
유사도가 0.524~0.575로 좁게 몰려있는 게 확인되어 이 스크립트를 만들었습니다 — 감으로 값을
올리지 않고, 실제 정답/오답 질의의 점수 분포를 보고 결정하기 위함입니다.

사용법: PYTHONPATH=src python scripts/calibrate_similarity_threshold.py
"""

import sys

from app.services.embedding import embed_text
from app.repository_documents_test import search_similar_test

from evaluate_search_quality import EVAL_SET, NEGATIVE_QUERIES

CANDIDATE_THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]


def _top1_score(query: str) -> float | None:
    embedding = embed_text(query)
    results = search_similar_test(embedding, top_k=1)
    return results[0].score if results else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"정답 질의 {len(EVAL_SET)}건 + 오답(무관) 질의 {len(NEGATIVE_QUERIES)}건의 top-1 유사도 점수를 수집합니다...\n")

    positive_scores: list[float] = []
    for i, eq in enumerate(EVAL_SET, start=1):
        score = _top1_score(eq.query)
        if score is not None:
            positive_scores.append(score)
        print(f"[정답 {i}/{len(EVAL_SET)}] score={score:.3f} :: {eq.query}")

    negative_scores: list[float] = []
    for i, q in enumerate(NEGATIVE_QUERIES, start=1):
        score = _top1_score(q)
        if score is not None:
            negative_scores.append(score)
        print(f"[오답 {i}/{len(NEGATIVE_QUERIES)}] score={score:.3f} :: {q}")

    print(f"\n정답 질의 점수 분포: min={min(positive_scores):.3f}  max={max(positive_scores):.3f}  "
          f"mean={sum(positive_scores)/len(positive_scores):.3f}")
    print(f"오답 질의 점수 분포: min={min(negative_scores):.3f}  max={max(negative_scores):.3f}  "
          f"mean={sum(negative_scores)/len(negative_scores):.3f}")

    print(f"\n{'threshold':>10}{'정답 grounded율':>18}{'오탐률(FP)':>14}")
    print("-" * 42)
    best_threshold = None
    best_fp_rate = 1.0
    best_positive_rate = 0.0
    for t in CANDIDATE_THRESHOLDS:
        positive_rate = sum(1 for s in positive_scores if s >= t) / len(positive_scores)
        fp_rate = sum(1 for s in negative_scores if s >= t) / len(negative_scores)
        print(f"{t:>10.2f}{positive_rate:>18.1%}{fp_rate:>14.1%}")
        # 오탐률이 더 낮은 후보를 우선 채택하고, 오탐률이 같다면 정답 grounded율이 더 높은 쪽을 채택.
        if fp_rate < best_fp_rate or (fp_rate == best_fp_rate and positive_rate > best_positive_rate):
            best_threshold = t
            best_fp_rate = fp_rate
            best_positive_rate = positive_rate

    print(f"\n추천 threshold: {best_threshold} (오탐률 {best_fp_rate:.1%}, 정답 grounded율 {best_positive_rate:.1%})")
    print("이 값을 src/app/config.py의 similarity_threshold 기본값과 .env.example의 SIMILARITY_THRESHOLD에 반영하세요.")


if __name__ == "__main__":
    main()
