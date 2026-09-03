"""5단계 골든셋 — 로드·검증 (RAG-022).

**이 파일이 6단계의 채점 기준이고, 라벨이 곧 승자를 정한다.** 그래서 세 가지를 여기서 막는다.

**① 라벨이 가리키는 청크가 실재하는가.** 골든셋은 사람이 손으로 쓴 YAML 이라 오타가 조용히 통과한다.
없는 `chunk_id` 를 가리키는 must 는 그 문항의 Recall 을 영원히 0 으로 만들고, 증상은 6단계에서
"이 모델이 유독 못한다"로만 나타난다. RAG-021 이 반복해서 다룬 **조용한 소실**과 같은 병리다.

**② 라벨이 어느 코퍼스를 보고 쓰였는가.** `chunk_id` 에는 수집 날짜가 박혀 있다
(`crawler/core/store.py`, `stem = f"{slug}__{today}"`). `data/` 는 미추적(RAG-017)이라 새 PC 에서는
재수집이 정상 경로이고, 그때 날짜가 바뀐다. 그래서 라벨은 **날짜를 뺀 논리 주소**로 적고
(`law-drf-api-...-act#제15조`), 스냅샷 날짜는 파일 머리에 따로 둔다. 날짜가 어긋나도 깨뜨리지는
않되 — 조문 번호는 재수집해도 그대로다 — **개정으로 내용이 바뀌었을 수 있으므로 경고**한다.

**③ 채점이 정의되는가.** 문항마다 **무엇을 기대하는지**(`expect`)가 다르다. 답해야 할 문항과
기권해야 할 문항을 같은 자로 재면 둘 다 틀린 수가 나온다 — 그래서 `must` 는 `expect: answer`
에서만 있고, 나머지에서는 **비어 있어야** 한다 (RAG-055). ① 이 라벨의 오타를 막는다면 ③ 은
**라벨과 기대가 어긋나는 것**을 막는다.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

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


# 문항 하나가 무엇을 기대하는가 (RAG-055). 기본은 지금까지의 전부 — **답하는 것**이다.
#
#   answer   근거를 인용해 답해야 한다. `must` 가 채점의 자다
#   abstain  코퍼스에 답이 없어 **물러서는 것이 정답**이다. 답하면 실패
#   refuse   이 개의 몸에 대한 판단이라 **거절해야 한다**. 답하면 실패 (로드맵 §2 의 경계)
#
# `abstain` 과 `refuse` 를 한 값으로 뭉치지 않는 것은, 사용자가 보는 것이 다르기 때문이다 —
# 기권은 "자료에 없다"이고 거절은 "이건 수의사에게"다. 어댑터도 ABSTAINED / REFUSED 로 갈라 낸다.
Expectation = Literal["answer", "abstain", "refuse"]

# `refuse` 문항이 어느 경계에 걸렸는가. 값은 **계약이다** — 어댑터가 `refusal.code` 로 그대로
# 내보내므로(`orchestration/adapters/life.py`) 여기서 이름을 지어내면 앱까지 따라간다.
RefusalCode = Literal["medical_boundary", "emergency_boundary"]


class Dog(_Base):
    """이 문항을 물을 때의 반려견 프로필 (RAG-056 · 로드맵 B4).

    **문항에 붙는 이유**는 프로필이 답을 가르기 때문이다. "비행기 태울 수 있나요"는 퍼그일
    때와 비글일 때 답이 다르고, 그 차이를 재려면 랩이 같은 프로필로 물어야 한다. 질문 문장에
    "퍼그인데"를 섞으면 안 되는 것이 이 카드의 요점이다 — 그건 검색까지 바꿔서 B4 가 프롬프트로
    한 일인지 검색이 한 일인지 못 가른다.

    `breed` 는 **한국어 견종명**이다. 앱 아바타 id(`dog_pug`)를 옮기는 일은 `daengs_backend`
    의 `services/dog_context.py` 가 하고, 골든셋은 그 결과를 적는다.
    """

    breed: str | None = None
    age_months: int | None = None


class Item(_Base):
    """문항 하나.

    **`must` 의 항목 하나가 "요구" 하나다** (RAG-055). 항목이 목록이면 그 안은 **OR** — 어느
    하나만 있어도 그 요구가 채워진다. 문자열 하나는 원소가 하나인 목록과 같아서, 옛 YAML 이
    그대로 읽힌다.

    OR 이 필요한 이유는 RAG-049 ④ 가 세 랩에서 같은 모양으로 만난 것이다 — **두 층**(법령과
    그것을 인용한 해설)이나 **형제 행**(같은 문구의 여러 상품)처럼, 어느 하나로 답이 성립하는
    자리를 옛 스키마로는 적을 수 없었다. 해설을 `must` 에 **더하면** 요구가 하나 늘어 문항이
    괜히 어려워지고, `nice` 에 두면 `grounded` 가 아예 안 본다. 같은 요구의 **대안**으로 적는
    자리가 없었던 것이다.

    요구가 여럿인 것과 요구 하나 안의 대안이 여럿인 것은 다르다 —
    T1 의 `[[1~8호선, 해설], [9호선, 해설]]` 은 *"해설 하나, 또는 약관 둘 다"* 라는 뜻이다.
    """

    id: str
    added_on: date                             # 항목별 추가일 — 시드 15건과 이후 추가를 가른다
    origin: Literal["hand", "easylaw"]         # hand=사람 판단(RAG-022 ⑤) / easylaw=법제처 라벨(②)
    question: str
    expect: Expectation = "answer"             # 이 문항에 기대하는 것 (RAG-055)
    refusal_code: RefusalCode | None = None    # `expect: refuse` 일 때만. 어댑터가 내보낼 코드
    dog: Dog | None = None                     # 프로필을 얹고 묻는 문항 (RAG-056). 없으면 안 얹는다
    must: list[list[str]] = []                 # 요구 목록. 항목 하나 = 요구 하나, 그 안은 OR
    nice: list[str] = []                       # 있으면 인용이 단단해지지만 점수에는 안 들어간다
    unavailable: list[Unavailable] = []

    @field_validator("must", mode="before")
    @classmethod
    def _as_groups(cls, value: object) -> object:
        """문자열 하나를 원소 하나짜리 요구로 감싼다.

        **여기가 옛 YAML 과의 호환 지점 전부다.** 23문항이 전부 문자열 목록이라, 이 한 줄이
        없으면 스키마를 바꾸는 순간 골든셋을 통째로 다시 써야 한다.
        """
        if not isinstance(value, list):
            return value
        return [[v] if isinstance(v, str) else v for v in value]

    @model_validator(mode="after")
    def _coherent(self) -> Item:
        """**모양만 본다** — 주소가 실재하는지는 `verify()` 가 코퍼스를 보고 한다.

        섞이면 안 되는 것을 여기서 막는다. `expect` 와 `must` 가 어긋난 문항은 채점이 조용히
        0/0 이 되거나(기권 문항에 `must` 가 없어서) 영원히 실패로 세어지는데(답할 문항에
        `must` 가 없어서), 둘 다 "모델이 못한다"로만 나타난다 — 이 파일이 막으려는 병리 그대로다.
        """
        if self.expect == "refuse" and self.refusal_code is None:
            raise ValueError(f"{self.id}: expect=refuse 인데 refusal_code 가 없다")
        if self.expect != "refuse" and self.refusal_code is not None:
            raise ValueError(f"{self.id}: expect={self.expect} 인데 refusal_code 가 있다")

        if self.expect == "answer":
            if not self.must:
                raise ValueError(f"{self.id}: expect=answer 인데 must 가 없다 — 채점이 정의되지 않는다")
        elif self.must or self.nice:
            # 기권·거절이 정답인 문항에 '정답 근거'는 없다. 있으면 그것을 인용한 답변이
            # 옳아 보이게 되고, 이 문항이 재려던 것과 정반대가 된다
            raise ValueError(f"{self.id}: expect={self.expect} 인데 must/nice 가 있다")

        for group in self.must:
            if not group:
                raise ValueError(f"{self.id}: 빈 요구가 있다 — 대안이 하나도 없는 OR 은 뜻이 없다")
            if len(set(group)) != len(group):
                raise ValueError(f"{self.id}: 한 요구 안에 같은 주소가 둘 있다: {group}")
        return self

    @property
    def must_flat(self) -> list[str]:
        """요구를 가로지른 주소 전부. 중복은 등장 순으로 한 번만.

        **요구의 경계를 잃는다** — 그래도 되는 자리에만 쓴다: 주소가 실재하는지 보는 검증과,
        검색 결과에 ★ 를 찍는 `tier_of` 다. 채점은 `must` 를 요구째로 봐야 한다.
        """
        seen: dict[str, None] = {}
        for group in self.must:
            for address in group:
                seen.setdefault(address, None)
        return list(seen)


class GoldenSet(_Base):
    schema_version: int
    corpus: Corpus
    items: list[Item]

    @property
    def scored_items(self) -> list[Item]:
        """검색 지표(Hit·Recall·MRR)를 잴 수 있는 문항 = `expect: answer` 만.

        **6단계 3파전이 이것으로 돌아야 한다.** 기권·거절 문항은 정답 청크가 없어 지표가 전부
        0 인데, 그것을 문항 균등 평균에 넣으면 세 모델의 점수가 나란히 내려가면서 **차이만
        희석된다.** 재는 대상이 아닌 것을 분모에 넣지 않는다 — `unavailable` 을 분모에서 뺀
        RAG-022 ③ 과 같은 이유다.
        """
        return [i for i in self.items if i.expect == "answer"]

    @property
    def must_total(self) -> int:
        """**요구의 수**다 — 주소의 수가 아니다 (RAG-055).

        한 요구 안의 대안을 늘려도 이 수는 안 변한다. 그것이 OR 을 넣은 이유이고, 덕분에
        `lap1`~`lap14` 와 옛 6단계 덤프의 Recall 분모가 그대로 비교된다.
        """
        return sum(len(i.must) for i in self.scored_items)

    def labels(self) -> list[tuple[str, str, str]]:
        """(문항 id, 층, 논리 주소) 전부. 검증과 채점이 같은 목록을 본다.

        요구의 경계는 여기서 사라진다 — 이 목록을 읽는 `verify()` 가 묻는 것이 *"이 주소가
        실재하나"* 뿐이라서다. 대안이든 필수든 없는 청크를 가리키면 똑같이 문제다.
        """
        out = []
        for item in self.items:
            out += [(item.id, "must", a) for a in item.must_flat]
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
