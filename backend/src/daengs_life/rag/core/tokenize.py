"""한국어 형태소 토큰화 — 렉시컬 검색의 입력 (RAG-003).

**문서와 질의가 반드시 같은 함수를 통과해야 한다.** 둘의 토큰화가 어긋나면 매칭이 그냥
안 되는데 **예외가 안 난다** — dense 쪽이 결과를 채워 주니 검색이 되는 것처럼 보인다.
그래서 이 모듈은 함수 하나만 내보내고, 적재(`stages/load.py`)와 검색(`stages/search.py`)이
같은 것을 부른다.

왜 애플리케이션에서 자르나 — **PG 에 한국어 텍스트 검색 설정이 없다.** `to_tsvector('korean')`
이라는 config 자체가 없어서, 한국어 문장을 넣으면 공백 단위로만 잘린다. 그러면 "동래구는" 과
"동래구" 가 다른 토큰이 되어 조사 하나에 매칭이 깨진다. 형태소를 여기서 분리하고 PG 에는
`'simple'` 로 넘긴다 (RAG-003 의 제안 그대로).

모델은 **프로세스당 한 번** 만든다. `Kiwi()` 생성이 수백 ms 이고 스레드 안전하므로
모듈 전역에 지연 생성해 재사용한다 — 4천 청크를 적재할 때 청크마다 만들면 그것만 몇 분이다.
"""
from __future__ import annotations

import functools
import re

# 남길 품사. 앞글자만 본다.
#   N  체언(명사·대명사·수사)  — "동래구" "광견병" "과태료"
#   V  용언(동사·형용사)      — "지원하" "받"
#   S  기호·외국어·숫자        — "SRT" "2026" "제15조" 의 숫자부
#   XR 어근                   — 복합어에서 떨어져 나오는 의미 조각
#
# 버리는 것은 조사(J)·어미(E)·접사(XP/XS)·문장부호(SF/SP…)다. 검색에 기여하지 않으면서
# 문서 길이만 늘려 BM25·ts_rank 의 길이 정규화를 흐린다.
_KEEP = ("N", "V", "S", "XR")

# 한 글자 토큰은 버린다 — "수" "것" "때" 같은 의존명사가 대부분이라 노이즈다.
# 다만 **숫자·영문 한 글자는 남긴다** ("2호선" 의 "2", 조문 번호).
_ONE_CHAR_KEEP = re.compile(r"^[0-9A-Za-z]$")


@functools.lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi
    return Kiwi()


def tokens(text: str) -> list[str]:
    """형태소 토큰 목록. 문서·질의 양쪽이 이것을 쓴다.

    **붙어 있던 명사 조각을 복합어로 되붙인다.** Kiwi 는 흔치 않은 복합어를 과분할한다 —
    실측(2026-08-28)에서 `맹견` 이 `맹`(NNP) + `견`(NNG) 으로 쪼개졌고, 둘 다 한 글자라
    아래 필터에 걸려 **질의에서 통째로 사라졌다.** 그 결과 "맹견 사육 허가 필요한가요?" 의
    렉시컬 축이 `사육 | 허가 | 필요` 라는 일반어만 남아, 정답 조문을 오히려 밀어냈다
    (검문소③에서 Q5 가 1/1 → 0/1 로 깨졌다).

    조각과 복합어를 **둘 다** 낸다. 원문의 띄어쓰기가 제각각이라("동물등록" vs "동물 등록")
    한쪽만 내면 띄어쓰기 하나에 매칭이 어긋난다. 문서 길이가 조금 늘지만 `ts_rank` 의 길이
    정규화가 흡수할 정도이고, 놓치는 것보다 낫다.
    """
    if not text:
        return []
    out: list[str] = []
    run: list = []                    # 붙어 있는 체언 조각 (복합어 후보)

    def flush() -> None:
        if len(run) > 1:
            out.append("".join(t.form for t in run))
        run.clear()

    for tok in _kiwi().tokenize(text):
        tag = tok.tag or ""
        # 체언끼리 **공백 없이** 이어진 구간만 복합어로 본다. 띄어쓰기가 있으면 별개 단어다
        if tag.startswith("N"):
            if run and run[-1].start + run[-1].len == tok.start:
                run.append(tok)
            else:
                flush()
                run.append(tok)
        else:
            flush()

        if tag[:1] not in _KEEP:
            continue
        if len(tok.form) == 1 and not _ONE_CHAR_KEEP.match(tok.form):
            continue
        out.append(tok.form)
    flush()
    # 순서를 지키며 중복 제거 — 같은 토큰이 두 번 들어가면 tf 가 부풀어 순위가 흔들린다
    return list(dict.fromkeys(out))


def tokenized(text: str) -> str:
    """`documents.content_tokens` 에 넣을 형태. 공백으로 이어 붙인다.

    빈 문자열을 돌려줄 수 있다 — 본문이 기호뿐인 청크가 있을 수 있어서다. 그래도 컬럼은
    `NOT NULL` 이라 **채우지 않은 것과 빈 것은 구별된다**: 전자는 INSERT 가 실패하고
    후자는 그냥 렉시컬로 안 걸린다.
    """
    return " ".join(tokens(text))


# tsquery 에 넣어도 안전한 토큰만. Kiwi 가 뱉는 것은 한글·영문·숫자라 정상 경로에서는 전부
# 통과하지만, `'` `&` `|` `!` `(` `)` `:` `*` 는 tsquery 문법이라 하나만 새도 파싱이 깨진다.
# **거르는 쪽이 이스케이프보다 낫다** — 검색어 하나 빠지는 것과 질의 전체가 죽는 것의 차이다.
_SAFE = re.compile(r"^[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ]+$")


def tsquery(text: str) -> str:
    """질의 텍스트 → `to_tsquery('simple', …)` 에 넣을 OR 식.

        "광견병 접종 의무인가요?"  →  "광견병 | 접종 | 의무"

    **AND 가 아니라 OR 다.** AND(`plainto_tsquery` 의 기본)로 묶으면 질의어를 전부 가진 문서만
    후보가 되는데, 이 코퍼스에서는 그런 문서가 흔히 0건이다 — 실측으로 "광견병"과 "의무"를
    함께 가진 청크가 하나도 없었다. OR 로 열어 두고 순위는 `ts_rank` 와 RRF 에 맡긴다.

    빈 문자열이 나올 수 있다(토큰이 전부 걸러진 질의). 그때는 caller 가 렉시컬 축을 건너뛴다.
    """
    return " | ".join(dict.fromkeys(t for t in tokens(text) if _SAFE.match(t)))
