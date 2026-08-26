"""검색 품질(Hit Rate, MRR) + LLM-as-a-judge 답변 품질 평가 스크립트.

documents_test(현재 /chat이 실제로 사용하는 테이블) 기준으로 평가합니다. 이전 버전은
옛 documents 테이블(app.repository)을 대상으로 했는데, /chat이 documents_test로
전환된 뒤로는 실제로 쓰이지 않는 테이블을 평가하고 있었습니다 — 이번에 바로잡았습니다.

이 스크립트가 하는 일 두 가지:

1. 검색(retrieval) 품질 — Hit Rate@1, Hit Rate@top_k, MRR
   각 파이프라인 단계(dense / keyword / hybrid / final=hybrid+rerank)별로 측정합니다.
   정답 판정: SearchTestResult에는 문서 ID가 없고 청크가 여러 개 겹쳐 있어(특히 dailyvet
   대량 청킹분), 문자열 완전 일치 대신 질의별 "주제 키워드"가 content에 포함되어 있으면
   관련 문서로 판정합니다.

2. 답변(generation) 품질 — LLM-as-a-judge
   각 질의에 대해 실제 answer_query()(rag.py, /chat과 완전히 동일한 파이프라인)를 그대로
   호출해서 최종 답변을 받고, 로컬 LLM(qwen2.5:7b-instruct)에게 "질문/참고문서/답변"을
   보여주고 0~10점으로 채점하게 합니다. guardrail.py가 정규식으로 못 잡는 미묘한 진단성
   표현이나, 참고 문서와 안 맞는 내용(hallucination)을 잡아내려는 목적입니다
   (guardrail.py 주석에도 "정규식만으로는 한계가 있다"고 이미 적혀 있음 — 이 채점이
   그 한계를 보완하는 가벼운 안전망입니다).

운영 로깅: 실행마다 아래 두 파일을 자동으로 남긴다(수작업 집계 불필요).
- search-quality-run.log   : 실행 시각, 질의별 처리/오류 로그, 실행 시간 요약
- search-quality-history.csv : 실행마다 한 줄씩 누적되는 측정 수치 이력
둘 다 aidlc-docs/construction/build-and-test/ 아래에 생성된다.

개별 질의 처리 중 오류(DB/Ollama 연결 끊김 등)가 나도 전체 실행이 죽지 않도록 질의
단위로 예외를 잡아 기록하고 다음 질의로 진행한다.

주의: 질의당 답변 생성까지 전부 돌리므로(리랭커 LLM 호출 + 생성 + 재시도 + 채점) 질의
수가 많으면 꽤 오래 걸립니다(로컬 LLM 기준 질의당 수십 초 단위). 백그라운드 실행을 권장.

사용법: PYTHONPATH=src python scripts/evaluate_search_quality.py
사전 조건: `docker compose up -d db`로 pgvector가 떠 있고 documents_test에 문서가
적재되어 있으며, Ollama가 실행 중이어야 함(bge-m3, qwen2.5:7b-instruct).
"""

import csv
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import ollama

from app.config import get_settings
from app.repository_documents_test import SearchTestResult, search_keyword_test, search_similar_test
from app.services.embedding import embed_text
from app.services.hybrid_search import reciprocal_rank_fusion
from app.services.rag import answer_query
from app.services.reranker import rerank

BUILD_AND_TEST_DIR = Path(__file__).resolve().parent.parent / "aidlc-docs" / "construction" / "build-and-test"
HISTORY_CSV = BUILD_AND_TEST_DIR / "search-quality-history.csv"
LOG_FILE = BUILD_AND_TEST_DIR / "search-quality-run.log"

HISTORY_FIELDS = [
    "timestamp",
    "duration_seconds",
    "n_positive_total",
    "n_positive_processed",
    "n_positive_errors",
    "n_negative_total",
    "n_negative_processed",
    "n_negative_errors",
    "has_errors",
    "top_k",
    "similarity_threshold",
    "dense_hit_rate@1",
    "dense_hit_rate@k",
    "dense_mrr",
    "keyword_hit_rate@1",
    "keyword_hit_rate@k",
    "keyword_mrr",
    "hybrid_hit_rate@1",
    "hybrid_hit_rate@k",
    "hybrid_mrr",
    "final_hit_rate@1",
    "final_hit_rate@k",
    "final_mrr",
    "false_positive_rate",
    "false_positive_count",
    # --- LLM-as-a-judge 답변 품질 (신규) ---
    "judge_n",
    "judge_errors",
    "judge_mean_score",
    "judge_min_score",
    "judge_below_6_count",
]


def _setup_logger() -> logging.Logger:
    """콘솔 + search-quality-run.log 파일 양쪽에 실행 로그를 남기는 로거를 구성한다."""
    logger = logging.getLogger("search_quality_eval")
    logger.setLevel(logging.INFO)
    if logger.handlers:  # 재실행 시 핸들러 중복 등록 방지
        return logger

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    BUILD_AND_TEST_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


logger = _setup_logger()


@dataclass
class EvalQuery:
    query: str
    relevant_keywords: list[str]  # 하나라도 content에 포함되면 관련 문서로 판정


EVAL_SET: list[EvalQuery] = [
    # food-safety (한국어)
    EvalQuery("자일리톨이 들어간 껌을 강아지가 먹었어요, 위험한가요?", ["자일리톨"]),
    EvalQuery("포도나 건포도를 강아지가 먹으면 어떻게 되나요?", ["포도", "건포도"]),
    EvalQuery("초콜릿을 강아지가 먹으면 어떤 증상이 나타나나요?", ["초콜릿"]),
    # puppy-care (한국어)
    EvalQuery("강아지 배변 훈련은 언제부터 어떻게 시작해야 하나요?", ["배변"]),
    EvalQuery("강아지 백신은 왜 여러 번 나눠서 맞아야 하나요?", ["백신"]),
    EvalQuery("강아지 구충은 얼마나 자주 해야 하나요?", ["구충"]),
    # routine-care (한국어)
    EvalQuery("수컷 강아지 중성화 수술은 언제 하는게 좋나요?", ["중성화", "거세"]),
    EvalQuery("강아지 심장사상충 예방은 어떻게 하나요?", ["심장사상충"]),
    EvalQuery("노령견은 건강검진을 얼마나 자주 받아야 하나요?", ["노령견"]),
    # nutrition-guideline (한국어)
    EvalQuery("강아지에게 생고기 식단을 줘도 되나요?", ["생고기"]),
    EvalQuery("강아지 체형 점수는 어떻게 평가하나요?", ["체형 점수", "Body Condition"]),
    EvalQuery("강아지 하루 필요 칼로리는 어떻게 계산하나요?", ["칼로리"]),
    # home-care (원래 영어 질의였으나, 지금은 schemas.py가 비한국어 질문을 422로 거부하므로
    # 실제로 발생할 수 없는 케이스였음 — 동일한 사실을 다루는 한국어 질의로 교체)
    EvalQuery("강아지는 운동을 얼마나 시켜야 하나요?", ["운동"]),
    EvalQuery("강아지 사료는 하루에 얼마나 자주 줘야 하나요?", ["급여", "먹이"]),
    EvalQuery("강아지 사료를 고를 때 뭘 확인해야 하나요?", ["AAFCO"]),
    # --- 헷갈리는/어려운 질의 (food-safety 내 인접 주제 구분, 카테고리 간 겹침 등) ---
    EvalQuery("마카다미아너트나 아몬드를 강아지가 먹으면 위험한가요?", ["마카다미아", "아몬드"]),
    EvalQuery("양파랑 초콜릿 중에 어떤 게 강아지한테 더 위험한가요?", ["양파", "초콜릿"]),
    EvalQuery("강아지 예방접종은 언제, 어떤 걸 맞아야 하나요?", ["백신"]),  # puppy-care/routine-care 둘 다 정답 가능
    EvalQuery("날달걀이나 날고기를 강아지에게 줘도 되나요?", ["날고기", "날달걀"]),
    EvalQuery("커피나 카페인이 든 음료를 강아지가 마셔도 되나요?", ["카페인", "커피"]),
    EvalQuery("이스트가 들어간 빵 반죽을 강아지가 먹으면 어떻게 되나요?", ["이스트"]),
    # --- dailyvet 대량 청킹분(현재 데이터의 94%) 반영 신규 질의 ---
    EvalQuery("강아지 비만은 어떻게 관리해야 하나요?", ["비만"]),
    EvalQuery("강아지 알레르기 피부염은 어떻게 관리하나요?", ["알레르기", "아토피"]),
    EvalQuery("노령견 건강 관리는 어떻게 해야 하나요?", ["노령견"]),
    EvalQuery("심장사상충 예방에 검사가 왜 필요한가요?", ["심장사상충"]),
    EvalQuery("반려동물 영양제는 먹여도 안전한가요?", ["영양제"]),
    EvalQuery("반려동물완전사료가 뭔가요?", ["완전사료"]),
]

# 오탐(false positive) 검증용: DB의 어떤 문서와도 관련 없는 질의.
# rag.py의 실제 grounding 판정(dense_results[0].score >= similarity_threshold)을
# 그대로 재현해, "관련 문서가 없는데도 근거 있다고 착각하는 비율"을 측정한다.
# LLM-judge 단계에서는 "근거 없이도 적절히 모른다고 답했는지"를 채점하는 데도 재사용한다.
NEGATIVE_QUERIES: list[str] = [
    "강아지 이름을 영어로 짓고 싶은데 추천해줄만한 거 있어?",
    "강아지 미용실은 어디가 좋을까요?",
    "반려견 보험은 어떻게 가입하나요?",
    "강아지랑 해외여행 갈 때 여권 발급 절차가 궁금해요",
    "오늘 서울 날씨가 어때요?",
    "강아지 훈련소 비용은 보통 얼마인가요?",
]

# --- LLM-as-a-judge ---

_JUDGE_SYSTEM_PROMPT = (
    "당신은 반려견 생활관리 AI 비서의 답변 품질을 채점하는 평가자입니다. "
    "질문, 참고 문서(있다면), 실제 답변을 보고 아래 기준을 종합해 0~10점 정수로 채점하세요:\n"
    "1. 질문에 실제로 답했는가 (동문서답이 아닌가)\n"
    "2. 참고 문서가 주어졌다면, 답변이 그 내용에 실제로 근거하는가 (문서에 없는 내용을 지어내지 않았는가)\n"
    "3. 참고 문서가 없다면, 확신 없는 내용을 단정적으로 지어내지 않고 적절히 모른다고 답했는가\n"
    "4. 의학적 진단명이나 확정적 처방(예: '~병입니다', '병원에 가세요')을 사용하지 않았는가\n"
    "5. 답변 전체가 자연스러운 한국어로 작성되었는가\n"
    "숫자 하나만 출력하고 다른 설명은 절대 덧붙이지 마세요."
)

_NUMBER_PATTERN = re.compile(r"\d+(\.\d+)?")


def _judge_answer(query: str, context_snippets: list[str], answer: str) -> float:
    """reranker.py의 _score_relevance()와 동일한 패턴: LLM에게 0~10점 채점을 요청."""
    settings = get_settings()
    client = ollama.Client(host=settings.ollama_host)

    if context_snippets:
        context_text = "\n".join(f"- {s}" for s in context_snippets)
        user_prompt = f"질문: {query}\n\n참고 문서:\n{context_text}\n\n답변: {answer}"
    else:
        user_prompt = f"질문: {query}\n\n참고 문서: 없음 (일반 지식으로 답변하거나 모른다고 답해야 하는 경우)\n\n답변: {answer}"

    response = client.chat(
        model=settings.generation_model,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        options={"temperature": 0},
    )
    text = response["message"]["content"]
    match = _NUMBER_PATTERN.search(text)
    if not match:
        return -1.0  # 파싱 실패 (reranker.py와 동일한 관례: 집계에서 제외하고 오류로 카운트)
    return max(0.0, min(10.0, float(match.group())))


def is_relevant(content: str, keywords: list[str]) -> bool:
    return any(kw in content for kw in keywords)


def reciprocal_rank(ranked_contents: list[str], keywords: list[str]) -> float:
    for rank, content in enumerate(ranked_contents, start=1):
        if is_relevant(content, keywords):
            return 1.0 / rank
    return 0.0


def rank_of(ranked_contents: list[str], keywords: list[str]) -> int | None:
    for rank, content in enumerate(ranked_contents, start=1):
        if is_relevant(content, keywords):
            return rank
    return None


def hit_rate_at_k(ranks: list[int | None], k: int) -> float:
    if not ranks:
        return 0.0
    hits = sum(1 for r in ranks if r is not None and r <= k)
    return hits / len(ranks)


def mrr(reciprocal_ranks: list[float]) -> float:
    if not reciprocal_ranks:
        return 0.0
    return sum(reciprocal_ranks) / len(reciprocal_ranks)


def run_stage(name: str, per_query_contents: list[list[str]], queries: list[EvalQuery], top_k: int) -> dict:
    """per_query_contents와 queries는 성공적으로 처리된 질의만 같은 순서로 담고 있어야 한다."""
    ranks = [rank_of(contents, eq.relevant_keywords) for contents, eq in zip(per_query_contents, queries)]
    rrs = [reciprocal_rank(contents, eq.relevant_keywords) for contents, eq in zip(per_query_contents, queries)]
    return {
        "name": name,
        "n": len(queries),
        "hit_rate@1": hit_rate_at_k(ranks, 1),
        f"hit_rate@{top_k}": hit_rate_at_k(ranks, top_k),
        "mrr": mrr(rrs),
        "ranks": ranks,
    }


def _run_with_retry(write_fn, *, description: str, attempts: int = 5, base_delay: float = 2.0) -> None:
    """OneDrive 동기화/백신 등이 파일을 잠깐 잠그는 경우가 Windows에서 흔해, 짧은 backoff로
    재시도한다. 재시도를 모두 소진하면 마지막 예외를 그대로 올려서 호출부가 처리하게 한다."""
    for attempt in range(1, attempts + 1):
        try:
            write_fn()
            return
        except PermissionError as e:
            if attempt == attempts:
                raise
            wait = base_delay * attempt
            logger.warning(
                f"{description} 실패(다른 프로세스가 파일을 잠그고 있을 수 있음, {attempt}/{attempts}차 시도) "
                f"— {wait:.0f}초 후 재시도: {e}"
            )
            time.sleep(wait)


def _migrate_history_csv_if_needed() -> None:
    """HISTORY_CSV의 헤더가 현재 HISTORY_FIELDS와 다르면(예: 컬럼 추가), 기존 행을 보존한 채
    새 헤더로 다시 쓴다. 새로 생긴 컬럼은 과거 행에서 빈 값으로 채워진다."""
    if not HISTORY_CSV.exists():
        return
    with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_fieldnames = reader.fieldnames or []
        rows = list(reader)
    if existing_fieldnames == HISTORY_FIELDS:
        return
    with open(HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in HISTORY_FIELDS})
    logger.info(f"{HISTORY_CSV.name} 스키마를 새 컬럼에 맞춰 마이그레이션했습니다.")


def record_history(
    stages: list[dict],
    top_k: int,
    similarity_threshold: float,
    false_positive_rate: float,
    false_positive_count: int,
    duration_seconds: float,
    n_positive_processed: int,
    n_positive_errors: int,
    n_negative_processed: int,
    n_negative_errors: int,
    judge_n: int,
    judge_errors: int,
    judge_mean_score: float | None,
    judge_min_score: float | None,
    judge_below_6_count: int,
) -> None:
    """측정 수치 + 실행 시간/처리 건수/오류 여부를 HISTORY_CSV에 한 줄 추가(append)한다."""
    hit_key = f"hit_rate@{top_k}"
    by_name = {stage["name"]: stage for stage in stages}
    has_errors = (n_positive_errors + n_negative_errors) > 0

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": round(duration_seconds, 1),
        "n_positive_total": len(EVAL_SET),
        "n_positive_processed": n_positive_processed,
        "n_positive_errors": n_positive_errors,
        "n_negative_total": len(NEGATIVE_QUERIES),
        "n_negative_processed": n_negative_processed,
        "n_negative_errors": n_negative_errors,
        "has_errors": has_errors,
        "top_k": top_k,
        "similarity_threshold": similarity_threshold,
        "dense_hit_rate@1": by_name["dense-only"]["hit_rate@1"],
        "dense_hit_rate@k": by_name["dense-only"][hit_key],
        "dense_mrr": by_name["dense-only"]["mrr"],
        "keyword_hit_rate@1": by_name["keyword-only"]["hit_rate@1"],
        "keyword_hit_rate@k": by_name["keyword-only"][hit_key],
        "keyword_mrr": by_name["keyword-only"]["mrr"],
        "hybrid_hit_rate@1": by_name["hybrid (RRF)"]["hit_rate@1"],
        "hybrid_hit_rate@k": by_name["hybrid (RRF)"][hit_key],
        "hybrid_mrr": by_name["hybrid (RRF)"]["mrr"],
        "final_hit_rate@1": by_name["final (hybrid+rerank)"]["hit_rate@1"],
        "final_hit_rate@k": by_name["final (hybrid+rerank)"][hit_key],
        "final_mrr": by_name["final (hybrid+rerank)"]["mrr"],
        "false_positive_rate": false_positive_rate,
        "false_positive_count": false_positive_count,
        "judge_n": judge_n,
        "judge_errors": judge_errors,
        "judge_mean_score": round(judge_mean_score, 2) if judge_mean_score is not None else "",
        "judge_min_score": judge_min_score if judge_min_score is not None else "",
        "judge_below_6_count": judge_below_6_count,
    }

    def _write() -> None:
        _migrate_history_csv_if_needed()
        BUILD_AND_TEST_DIR.mkdir(parents=True, exist_ok=True)
        is_new_file = not HISTORY_CSV.exists()
        with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
            if is_new_file:
                writer.writeheader()
            writer.writerow(row)

    _run_with_retry(_write, description=f"{HISTORY_CSV.name} 쓰기")
    logger.info(f"측정 기록 저장됨: {HISTORY_CSV}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = get_settings()
    run_started_at = datetime.now(timezone.utc)
    perf_start = time.perf_counter()
    logger.info(f"=== 검색 품질 평가 시작 (top_k={settings.top_k}, similarity_threshold={settings.similarity_threshold}) ===")

    # ------------------------------------------------------------------
    # 1) 검색(retrieval) 품질: Hit Rate / MRR
    # ------------------------------------------------------------------
    processed_queries: list[EvalQuery] = []
    dense_per_query: list[list[SearchTestResult]] = []
    keyword_per_query: list[list[SearchTestResult]] = []
    hybrid_per_query: list[list[SearchTestResult]] = []
    final_per_query: list[list[SearchTestResult]] = []
    positive_errors = 0

    for eq in EVAL_SET:
        try:
            query_embedding = embed_text(eq.query)
            dense = search_similar_test(query_embedding, top_k=settings.retrieval_pool_size)
            keyword = search_keyword_test(eq.query, top_k=settings.retrieval_pool_size)
            fused = reciprocal_rank_fusion(dense, keyword)[: settings.rerank_pool_size]
            final = rerank(eq.query, fused, top_k=settings.top_k)
        except Exception:
            positive_errors += 1
            logger.exception(f"질의 처리 실패(건너뜀): {eq.query!r}")
            continue

        processed_queries.append(eq)
        dense_per_query.append(dense)
        keyword_per_query.append(keyword)
        hybrid_per_query.append(fused)
        final_per_query.append(final)

        logger.info(f"[검색 처리 완료] {eq.query!r}")

    n_positive_processed = len(processed_queries)
    if positive_errors:
        logger.warning(f"정답 있는 질의 중 {positive_errors}건 처리 실패 (전체 {len(EVAL_SET)}건 중 {n_positive_processed}건 처리됨)")

    stages = [
        run_stage("dense-only", [[r.content for r in rs] for rs in dense_per_query], processed_queries, settings.top_k),
        run_stage("keyword-only", [[r.content for r in rs] for rs in keyword_per_query], processed_queries, settings.top_k),
        run_stage("hybrid (RRF)", [[r.content for r in rs] for rs in hybrid_per_query], processed_queries, settings.top_k),
        run_stage("final (hybrid+rerank)", [[r.content for r in rs] for rs in final_per_query], processed_queries, settings.top_k),
    ]

    hit_key = f"hit_rate@{settings.top_k}"
    logger.info("=== 검색 품질 측정 결과 (N=%d) ===" % n_positive_processed)
    header = f"{'단계':<24}{'HitRate@1':>12}{hit_key:>16}{'MRR':>10}"
    logger.info(header)
    logger.info("-" * len(header))
    for stage in stages:
        logger.info(
            f"{stage['name']:<24}"
            f"{stage['hit_rate@1']:>12.2%}"
            f"{stage[hit_key]:>16.2%}"
            f"{stage['mrr']:>10.3f}"
        )

    logger.info("=== 질의별 상세 (final 단계 기준 순위, None=미검색) ===")
    for eq, rank in zip(processed_queries, stages[-1]["ranks"]):
        marker = "OK" if rank is not None and rank <= settings.top_k else "MISS"
        logger.info(f"[{marker:<4}] rank={str(rank):<4} query={eq.query!r}")

    # --- 오탐(false positive) 검증: rag.py의 실제 grounding 로직 재현 ---
    false_positives = 0
    negative_errors = 0
    n_negative_processed = 0
    logger.info(f"=== 오탐 검증 (N={len(NEGATIVE_QUERIES)}, threshold={settings.similarity_threshold}) ===")
    for query in NEGATIVE_QUERIES:
        try:
            query_embedding = embed_text(query)
            dense = search_similar_test(query_embedding, top_k=1)
        except Exception:
            negative_errors += 1
            logger.exception(f"오탐 검증 질의 처리 실패(건너뜀): {query!r}")
            continue

        n_negative_processed += 1
        top_score = dense[0].score if dense else 0.0
        grounded = bool(dense) and top_score >= settings.similarity_threshold
        if grounded:
            false_positives += 1
        marker = "FALSE POSITIVE" if grounded else "OK (ungrounded)"
        top_snippet = dense[0].content[:40] if dense else ""
        logger.info(f"[{marker:<16}] score={top_score:.3f} query={query!r} top1={top_snippet!r}")

    if negative_errors:
        logger.warning(f"오탐 검증 질의 중 {negative_errors}건 처리 실패 (전체 {len(NEGATIVE_QUERIES)}건 중 {n_negative_processed}건 처리됨)")

    fp_rate = false_positives / n_negative_processed if n_negative_processed else 0.0
    logger.info(f"False Positive Rate: {fp_rate:.2%} ({false_positives}/{n_negative_processed})")

    # ------------------------------------------------------------------
    # 2) 답변(generation) 품질: LLM-as-a-judge
    #    실제 /chat과 동일한 answer_query()를 그대로 호출해서 최종 답변을 채점한다.
    #    EVAL_SET(정답 있는 질의) + NEGATIVE_QUERIES(근거 없어야 하는 질의) 전부 포함.
    # ------------------------------------------------------------------
    all_judge_queries = [eq.query for eq in EVAL_SET] + list(NEGATIVE_QUERIES)
    logger.info(f"=== LLM-as-a-judge 답변 품질 평가 시작 (N={len(all_judge_queries)}) ===")

    judge_scores: list[float] = []
    judge_errors = 0
    for i, query in enumerate(all_judge_queries, start=1):
        try:
            result = answer_query(query)
            context_snippets = [s.content for s in result.sources]
            score = _judge_answer(query, context_snippets, result.answer)
        except Exception:
            judge_errors += 1
            logger.exception(f"[{i}/{len(all_judge_queries)}] 답변 채점 실패(건너뜀): {query!r}")
            continue

        if score < 0:
            judge_errors += 1
            logger.warning(f"[{i}/{len(all_judge_queries)}] 채점 파싱 실패: {query!r}")
            continue

        judge_scores.append(score)
        marker = "LOW" if score < 6 else "OK"
        logger.info(f"[{marker:<4}] score={score:.0f}/10 grounded={result.grounded} query={query!r}")

    judge_n = len(judge_scores)
    judge_mean = (sum(judge_scores) / judge_n) if judge_n else None
    judge_min = min(judge_scores) if judge_scores else None
    judge_below_6 = sum(1 for s in judge_scores if s < 6)

    logger.info("=== 답변 품질 요약 ===")
    if judge_n:
        logger.info(f"평균 점수: {judge_mean:.2f}/10  |  최저 점수: {judge_min:.0f}/10  |  6점 미만: {judge_below_6}/{judge_n}건")
    else:
        logger.warning("채점 가능한 답변이 하나도 없습니다 (전부 오류).")

    duration_seconds = time.perf_counter() - perf_start

    record_saved = True
    try:
        record_history(
            stages,
            settings.top_k,
            settings.similarity_threshold,
            fp_rate,
            false_positives,
            duration_seconds,
            n_positive_processed,
            positive_errors,
            n_negative_processed,
            negative_errors,
            judge_n,
            judge_errors,
            judge_mean,
            judge_min,
            judge_below_6,
        )
    except Exception:
        record_saved = False
        logger.exception(
            f"{HISTORY_CSV.name} 저장 실패(재시도 소진) — 이번 실행의 수치는 위 콘솔/{LOG_FILE.name} 로그로만 남았습니다. "
            "필요하면 다시 실행해 CSV 기록을 재시도하세요."
        )

    total_errors = positive_errors + negative_errors + judge_errors
    had_any_error = total_errors > 0 or not record_saved
    logger.info(
        f"=== 실행 요약 === 시작={run_started_at.isoformat(timespec='seconds')}, "
        f"실행 시간={duration_seconds:.1f}s, "
        f"처리 건수=positive {n_positive_processed}/{len(EVAL_SET)}, negative {n_negative_processed}/{len(NEGATIVE_QUERIES)}, "
        f"judge {judge_n}/{len(all_judge_queries)}, "
        f"오류 발생={'예' if had_any_error else '아니오'}"
        f"(처리 오류 {total_errors}건{', CSV 기록 실패' if not record_saved else ''})"
    )


if __name__ == "__main__":
    main()
