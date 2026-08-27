"""5단계 골든셋 — 로드·검증 (RAG-022).

**이 파일이 6단계의 채점 기준이고, 라벨이 곧 승자를 정한다.** 그래서 두 가지를 여기서 막는다.

**① 라벨이 가리키는 청크가 실재하는가.** 골든셋은 사람이 손으로 쓴 YAML 이라 오타가 조용히 통과한다.
없는 `chunk_id` 를 가리키는 must 는 그 문항의 Recall 을 영원히 0 으로 만들고, 증상은 6단계에서
"이 모델이 유독 못한다"로만 나타난다. RAG-021 이 반복해서 다룬 **조용한 소실**과 같은 병리다.

**② 라벨이 어느 코퍼스를 보고 쓰였는가.** `chunk_id` 에는 수집 날짜가 박혀 있다
(`crawler/core/store.py`, `stem = f"{slug}__{today}"`). `data/` 는 미추적(RAG-017)이라 새 PC 에서는
재수집이 정상 경로이고, 그때 날짜가 바뀐다. 그래서 라벨은 **날짜를 뺀 논리 주소**로 적고
(`law-drf-api-...-act#제15조`), 스냅샷 날짜는 파일 머리에 따로 둔다. 날짜가 어긋나도 깨뜨리지는
않되 — 조문 번호는 재수집해도 그대로다 — **개정으로 내용이 바뀌었을 수 있으므로 경고**한다.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from ..core import io

GOLDENSET_PATH = Path(__file__).with_name("goldenset.yaml")   # 이 파일 옆 (RAG-023)

_DATE_SUFFIX = re.compile(r"__\d{8}")


def logical(chunk_id: str) -> str:
    """실제 `chunk_id` → 라벨이 쓰는 논리 주소. 수집 날짜만 떼어낸다 (RAG-022 ⑥B).

    `law-drf-api-...-act__20260820#제15조` -> `law-drf-api-...-act#제15조`
    """
    return _DATE_SUFFIX.sub("", chunk_id)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Corpus(_Base):
    """라벨을 판정한 코퍼스 스냅샷. 채점기가 현재 코퍼스와 대조한다.

    `date` 로 받는 이유 — 이 값은 `chunk_id` 의 `__YYYYMMDD` 와 대조되는 날짜다.
    문자열로 두면 `2026-8-20` 같은 표기가 조용히 통과하고 대조가 영원히 어긋난다.
    """
    collected_on: date
    chunker_version: int
    chunk_count: int

    @property
    def stamp(self) -> str:
        """`chunk_id` 안의 표기(`20260820`)로 맞춘 것."""
        return self.collected_on.strftime("%Y%m%d")


class Unavailable(_Base):
    """코퍼스 밖 참조. **분모에서 뺀다** — 세 모델이 같은 코퍼스를 쓰므로 모델 차이가 아니라 상수다.

    지우지 않고 남기는 이유는 그 법령을 수집하면 `must` 로 올라가야 하기 때문이다 (RAG-022 ③).
    """
    ref: str
    reason: str


class Item(_Base):
    id: str
    added_on: date                             # 항목별 추가일 — 시드 15건과 이후 추가를 가른다
    origin: Literal["hand", "easylaw"]         # hand=사람 판단(RAG-022 ⑤) / easylaw=법제처 라벨(②)
    question: str
    must: list[str]                            # 없으면 답변이 틀리는 청크. Recall 의 분자·분모
    nice: list[str] = []                       # 있으면 인용이 단단해지지만 점수에는 안 들어간다
    unavailable: list[Unavailable] = []


class GoldenSet(_Base):
    schema_version: int
    corpus: Corpus
    items: list[Item]

    @property
    def must_total(self) -> int:
        return sum(len(i.must) for i in self.items)

    def labels(self) -> list[tuple[str, str, str]]:
        """(문항 id, 층, 논리 주소) 전부. 검증과 채점이 같은 목록을 본다."""
        out = []
        for item in self.items:
            out += [(item.id, "must", a) for a in item.must]
            out += [(item.id, "nice", a) for a in item.nice]
        return out


def load(path: Path | None = None) -> GoldenSet:
    """YAML → 모델. `extra="forbid"` 라 오타 난 키는 여기서 걸린다."""
    raw = yaml.safe_load((path or GOLDENSET_PATH).read_text(encoding="utf-8"))
    return GoldenSet.model_validate(raw)


def corpus_index() -> dict[str, str]:
    """현재 청크 전부의 {논리 주소: 실제 chunk_id}.

    같은 논리 주소가 둘 이상이면(같은 문서를 두 날짜로 수집한 경우) 나중 것을 남긴다 —
    `chunk_files()` 가 정렬되어 있어 날짜가 큰 쪽이 뒤에 온다.
    """
    index: dict[str, str] = {}
    for path in io.chunk_files():
        for row in io.read_chunks(path):
            index[logical(row["chunk_id"])] = row["chunk_id"]
    return index


class Problem(_Base):
    item_id: str
    tier: str
    address: str
    kind: Literal["missing"]


def verify(gs: GoldenSet, index: dict[str, str]) -> tuple[list[Problem], list[str]]:
    """(치명적 문제, 경고). 문제가 하나라도 있으면 채점을 시작하면 안 된다.

    **경고와 문제를 섞지 않는다.** 라벨이 가리키는 청크가 없는 것은 채점을 무의미하게 만들지만,
    스냅샷 날짜가 다른 것은 대개 재수집일 뿐이라 사람이 판단할 일이다.
    """
    problems = [
        Problem(item_id=item_id, tier=tier, address=address, kind="missing")
        for item_id, tier, address in gs.labels()
        if address not in index
    ]

    warnings: list[str] = []
    collected = sorted({m.group(0)[2:] for cid in index.values()
                        if (m := _DATE_SUFFIX.search(cid))})
    if collected and gs.corpus.stamp not in collected:
        warnings.append(
            f"코퍼스 스냅샷이 다르다 — 라벨은 {gs.corpus.collected_on} 기준인데 "
            f"현재 청크의 수집일은 {', '.join(collected)} 다. "
            "조문 번호는 그대로여도 **개정으로 내용이 바뀌었을 수 있다** (RAG-022 ⑥B)"
        )
    if len(index) != gs.corpus.chunk_count:
        warnings.append(
            f"청크 수가 다르다 — 라벨은 {gs.corpus.chunk_count}개 기준인데 현재 {len(index)}개다"
        )
    return problems, warnings
