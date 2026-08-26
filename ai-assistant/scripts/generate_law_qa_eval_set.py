# generate_law_qa_eval_set.py = 동물보호법 계열 법령 청크(scripts/ingest_law_documents.py로
# 적재된 359건)에서 표본을 뽑아, 각 청크를 정답으로 하는 질문을 로컬 LLM으로 1개씩 만들고
# documents_test.id(UUID)로 연동한 평가 데이터셋을 생성합니다.
#
# 기존 evaluate_search_quality.py의 EVAL_SET은 "질의 - 키워드 목록" 방식이라 정답 판정이
# 문자열 부분일치라서 느슨합니다. 이 스크립트가 만드는 데이터셋은 "질의 - 정답 청크 id"로
# 정확히 연동되어 있어, 검색 결과의 id가 정답 id와 일치하는지로 엄격하게 채점할 수 있습니다
# (SearchTestResult에 id 필드를 추가한 이유이기도 함 — repository_documents_test.py 참고).
#
# 사용법: PYTHONPATH=src python scripts/generate_law_qa_eval_set.py

import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import ollama

# Windows 콘솔 기본 코드페이지(cp949)로는 로컬 LLM이 가끔 섞어 내는 중국어 등
# 일부 유니코드 문자를 못 찍어서 print()가 그대로 죽는 문제가 있어(build-and-test에서도
# 관찰된 generation.py의 언어 이탈 버그와 같은 현상), 콘솔 출력만 UTF-8/치환 모드로 강제.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import get_settings
from app.db import get_connection

OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent / "aidlc-docs" / "construction" / "build-and-test" / "law_qa_eval_set.json"
)

_SAMPLE_PER_LAW = 12  # 법령(모법/시행령/시행규칙)당 표본 개수 -> 총 36개 내외

_QUESTION_PROMPT = (
    "다음은 대한민국 법령 조문입니다:\n\n{content}\n\n"
    "이 조문 내용이 정답이 되는, 반려견을 키우는 일반인이 실제로 물어볼 법한 자연스러운 "
    "한국어 질문을 정확히 1개만 만들어주세요. 법률 용어를 그대로 베끼지 말고 일상적인 "
    "말투로 바꿔서 질문하세요. 질문 문장 하나만 출력하고, 다른 설명은 붙이지 마세요."
)


@dataclass
class LawQaEntry:
    id: str            # documents_test.id (UUID 문자열) — 검색 결과와 정확히 대조할 정답 id
    section: str        # 인용 문자열 (예: "동물보호법 제15조제1항")
    document_title: str
    question: str
    answer_content: str  # 정답 청크 원문 (사람이 채점 결과를 검토할 때 참고용)


def fetch_law_chunks() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, section, document_title, content
            FROM documents_test
            WHERE category = 'animal-protection-law'
            ORDER BY document_title, section
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": str(r[0]), "section": r[1], "document_title": r[2], "content": r[3]}
        for r in rows
    ]


def sample_chunks(chunks: list[dict], per_law: int, seed: int = 42) -> list[dict]:
    by_law: dict[str, list[dict]] = {}
    for c in chunks:
        by_law.setdefault(c["document_title"], []).append(c)

    rng = random.Random(seed)
    sampled = []
    for law_name, law_chunks in by_law.items():
        # 너무 짧은 조각(예: 목적 조항 한 줄)은 질문을 만들기 어려우므로 제외.
        candidates = [c for c in law_chunks if len(c["content"]) >= 40]
        sampled.extend(rng.sample(candidates, min(per_law, len(candidates))))
    return sampled


def generate_question(content: str, model: str, host: str) -> str:
    client = ollama.Client(host=host)
    response = client.chat(
        model=model,
        messages=[{"role": "user", "content": _QUESTION_PROMPT.format(content=content)}],
    )
    question = response["message"]["content"].strip()
    # 모델이 가끔 따옴표나 "Q: " 접두어를 붙이는 경우가 있어 정리.
    question = question.strip("\"'“”")
    if question.startswith("Q:"):
        question = question[2:].strip()
    return question


def main() -> None:
    settings = get_settings()
    chunks = fetch_law_chunks()
    print(f"전체 법령 청크: {len(chunks)}건")

    sampled = sample_chunks(chunks, _SAMPLE_PER_LAW)
    print(f"표본 추출: {len(sampled)}건")

    entries: list[LawQaEntry] = []
    for i, chunk in enumerate(sampled, start=1):
        try:
            question = generate_question(chunk["content"], settings.generation_model, settings.ollama_host)
        except Exception as exc:  # Ollama 연결 문제 등으로 한 건 실패해도 나머지는 계속 진행
            print(f"[{i}/{len(sampled)}] {chunk['section']} -> 실패({exc}), 건너뜀")
            continue
        entries.append(
            LawQaEntry(
                id=chunk["id"],
                section=chunk["section"],
                document_title=chunk["document_title"],
                question=question,
                answer_content=chunk["content"],
            )
        )
        print(f"[{i}/{len(sampled)}] {chunk['section']} -> {question}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in entries], f, ensure_ascii=False, indent=2)
    print(f"저장 완료: {OUTPUT_PATH} ({len(entries)}건)")


if __name__ == "__main__":
    main()
