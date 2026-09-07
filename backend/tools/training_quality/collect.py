"""질문을 훈련 RAG 에 먹여 **청크 본문까지** 덤프한다 (D-060 ④).

    uv run python -m tools.training_quality.collect --questions questions_v1.jsonl --label lap1

────────────────────────────────────────────────────────────────────────────
왜 별도 수집기가 필요한가 — `ChatResponse` 에 청크 본문이 없다
────────────────────────────────────────────────────────────────────────────
`RAGService.answer` 가 돌려주는 `EvidenceCard` 는 `chunk_id`·`document_id`·`chunk_index`·
`heading_path`·`score` 만 가진다. **본문이 없다.** 그런데 이 카드의 판정 축은 *"답이 그 청크에
붙어 있는가"* 라 판정자가 본문을 봐야 한다 (D-060 ④).

세 가지 방법이 있었고, 셋째를 골랐다:

  ⓐ 판정할 때 DB 를 다시 본다     `chunk_id` 로 다시 읽으면 되지만, 그러면 판정이 **그때의
                              코퍼스**를 본다. 적재를 다시 하면 옛 덤프의 판정이 조용히
                              다른 자료로 채점된다 — 랩 대조가 끊긴다
  ⓑ `EvidenceCard` 에 본문을 더한다  서빙 응답이 커진다. 평가 때문에 운영 페이로드를 바꾸는
                              것은 순서가 거꾸로다
  ⓒ **수집할 때 붙잡는다**        리트리버를 감싸 지나가는 hit 를 그대로 받아 적는다.
                              서빙 코드는 한 글자도 안 바뀌고, 덤프가 **그 시점의 자료**를
                              통째로 들고 있어 나중에 재판정해도 같은 것을 본다

⚠ **검색을 두 번 돌리지 않는다.** `retriever.search` 를 따로 또 부르면 그 사이 코퍼스가
바뀔 수 있고, 임베딩 호출도 두 번이다. 감싸는 이유가 그것이다 — #277 의 `RecordingEngine`
과 같은 수법이다.

────────────────────────────────────────────────────────────────────────────
덤프 모양
────────────────────────────────────────────────────────────────────────────
헤더 한 줄 뒤에 문항마다 한 줄(JSONL). `decision` 을 같이 적는 이유는 판정기가 `ANSWER` 만
채점하기 때문이고(D-060 ⑤), `gate` 를 적는 이유는 나중에 게이트의 자와 견주기 위해서다.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

VERSION = 1
ASSETS_DIR = Path(__file__).resolve().parents[2] / "evals" / "training_quality"


class DumpHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "header"
    version: int = VERSION
    label: str
    collected_at: str
    answer_model: str
    prompt_version: str
    top_k: int
    items: int


class RecordingRetriever:
    """리트리버를 감싸 **지나가는 hit 를 그대로 받아 적는다.** 검색은 한 번만 돈다.

    `search` 와 `gate` 만 위임한다 — `RAGService` 가 리트리버에게 그 둘만 부르기 때문이고,
    더 위임하면 감싼 것이 원본인 척하게 된다.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.last_hits: list[dict[str, Any]] = []

    def search(self, question: str, top_k: int) -> list[dict[str, Any]]:
        hits = self._inner.search(question, top_k)
        self.last_hits = list(hits)
        return hits

    def gate(self, question: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
        return self._inner.gate(question, hits)


def chunk_rows(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """hit 를 덤프에 실을 모양으로. **본문을 통째로 싣는다** — 자르는 것은 판정기의 몫이다
    (`judge.MAX_CHUNK_CHARS`). 여기서 자르면 나중에 한도를 올려도 옛 덤프가 못 따라온다."""
    out = []
    for hit in hits:
        metadata = hit.get("metadata") or {}
        heading = metadata.get("heading_path", []) if isinstance(metadata, dict) else []
        out.append(
            {
                "chunk_id": str(hit.get("chunk_id", "")),
                "document_id": str(hit.get("document_id", "")),
                "chunk_index": int(hit.get("chunk_index", 0)),
                "heading_path": [str(p) for p in heading] if isinstance(heading, list) else [],
                "score": float(hit.get("score", 0.0)),
                "text": str(hit.get("text", "")),
            }
        )
    return out


def collect_one(service: Any, recorder: RecordingRetriever, qid: str, question: str,
                top_k: int = 4) -> dict[str, Any]:
    """문항 하나 → 덤프 행 하나.

    예외를 삼키지 않는다 — 한 문항이 터지면 그 랩은 불완전하고, 그것을 조용히 넘기면
    분모가 말없이 줄어든다 (`RAG-075` ① 의 *"정직한 분모"* 와 같은 자리).
    """
    recorder.last_hits = []
    response = service.answer(question, top_k)
    return {
        "id": qid,
        "question": question,
        "answer": response.answer,
        "decision": response.decision,
        "reason": response.reason,
        "generated": response.generated,
        "model": response.model,
        "prompt_version": response.prompt_version,
        "gate": response.gate,
        "chunks": chunk_rows(recorder.last_hits),
    }


def build_service(top_k_ignored: int = 4) -> tuple[Any, RecordingRetriever]:
    """운영과 **같은 조립**에 리트리버만 감싼다. 어댑터를 바꾸지 않는 것이 요점이다 —
    평가가 보는 것이 운영이 하는 것과 달라지면 그 판정은 운영에 대해 말하지 않는다."""
    from daengs_training.service import RAGService

    service = RAGService()
    recorder = RecordingRetriever(service.retriever)
    service.retriever = recorder
    return service, recorder


def load_questions(path: Path) -> list[tuple[str, str]]:
    """`{"id": ..., "question": ...}` JSONL. 빈 줄과 `#` 주석은 넘긴다."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        qid, question = str(row["id"]).strip(), str(row["question"]).strip()
        if not qid or not question:
            raise ValueError(f"id 와 question 이 둘 다 있어야 합니다: {line[:80]}")
        if qid in seen:
            raise ValueError(f"id 가 중복입니다: {qid}")
        seen.add(qid)
        out.append((qid, question))
    if not out:
        raise ValueError(f"질문이 하나도 없습니다: {path}")
    return out


def dump_path(label: str) -> Path:
    return ASSETS_DIR / f"answers_{label}.jsonl"


def write_dump(path: Path, head: DumpHeader, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(head.model_dump_json() + "\n")
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_dump(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        raise ValueError(f"덤프가 비었습니다: {path}")
    head = json.loads(lines[0])
    if head.get("type") != "header":
        raise ValueError(f"첫 줄이 헤더가 아닙니다: {path}")
    return head, [json.loads(ln) for ln in lines[1:]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="훈련 RAG 답변 수집 (청크 본문 포함)")
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--label", required=True, help="덤프 파일 이름에 들어갑니다")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="0 이면 전부")
    args = parser.parse_args(argv)

    questions = load_questions(args.questions)
    if args.limit:
        questions = questions[: args.limit]

    service, recorder = build_service()
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for i, (qid, question) in enumerate(questions, start=1):
        row = collect_one(service, recorder, qid, question, args.top_k)
        rows.append(row)
        counts[row["decision"]] = counts.get(row["decision"], 0) + 1
        print(f"  [{i}/{len(questions)}] {qid}  {row['decision']}  청크 {len(row['chunks'])}개")

    head = DumpHeader(
        label=args.label,
        collected_at=datetime.now(UTC).isoformat(timespec="seconds"),
        answer_model=service.model_name,
        prompt_version=str(rows[0].get("prompt_version") or ""),
        top_k=args.top_k,
        items=len(rows),
    )
    out = dump_path(args.label)
    write_dump(out, head, rows)
    print(f"\n{out}  ({len(rows)}문항)")
    print("  " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    judged = sum(1 for r in rows if r["decision"] == "ANSWER" and r["chunks"])
    print(f"  판정 대상(ANSWER + 청크 있음): {judged}문항")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
