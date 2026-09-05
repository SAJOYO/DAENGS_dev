"""8단계 검색 — `documents` 에서 dense 로 top-k 를 꺼낸다 (RAG-026).

**9단계(Gemini)를 붙이기 전에 검색만 따로 떼어 둔다.** 답변이 이상할 때 검색이 틀렸는지 LLM 이
틀렸는지 가르려면 검색이 단독으로 관찰 가능해야 한다 — RAG-024 `판정 이후` 가 기준선으로 관통하기로
한 것과 같은 판단이고, 원인을 하나로 두려는 것이다.

**여기가 검색의 유일한 구현이다** (RAG-026 ②). CLI 는 이 결과를 찍기만 하고, 9단계와 FastAPI 도
같은 함수를 부른다. 경계를 안 지키면 9단계가 검색을 다시 짜게 되고, 그 순간 **검문소③이 확인한
것과 서빙이 실제로 하는 것이 달라진다** — 검문소③의 근거가 조용히 무효가 되는 자리다.

RAG-003(하이브리드·리랭커)이 들어와도 **바뀌는 것은 이 파일의 내부이지 시그니처가 아니다.**
호출하는 쪽이 검색 방식을 알면 그 교체가 세 곳을 고치는 일이 된다.

**하이브리드가 들어왔다 (RAG-035).** dense 와 렉시컬(형태소 FTS)을 각각 뽑아 RRF 로 섞는다.
그러면서 `search()` 의 첫 인자가 벡터에서 `Query` 로 바뀌었다 — 렉시컬은 벡터로 못 하고
**질의 텍스트가 있어야** 하기 때문이다. 위 문단의 규약을 어긴 것으로 보일 수 있으나,
규약의 뜻은 "호출부가 검색 방식을 알면 안 된다" 였고 그것은 지켜진다: 호출부는 여전히
`search(encode(질문))` 이고 `encode()` 가 벡터와 토큰을 함께 만들어 준다. **인자 이름이
바뀐 것을 결정으로 남기는 이유**는 `test_search.py` 가 그 목록을 단언하고 있어서다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..core import config, region, tokenize, transport
from . import embed, load

VERSION = 2          # 1 = dense 단독 / 2 = 하이브리드 (RAG-035)

# 검문소③이 보는 수이자 RAG-024 ② 의 판정 k. **같은 숫자여야 한다** — 평가에서 쓴 k 와 눈으로 보는
# k 가 다르면 6단계 점수와 8단계 인상이 어긋나도 원인을 못 짚는다
DEFAULT_K = 5

# 각 축에서 뽑는 후보 수. RRF 는 순위만 쓰므로 이 수가 곧 "몇 등까지 섞을지"다.
# 100 인 이유 — 프로토타입에서 정답이 dense 39위·렉시컬 상위권인 경우가 실익의 대부분이었다.
# 너무 크면 하위권 잡음이 RRF 점수에 섞이고, 너무 작으면 한쪽에만 잡힌 문서를 놓친다.
#
# **2026-09-04 재측정(RAG-057) — 20 부터 300 까지 요구충족이 한 칸도 안 움직였다.** 후보를
# 늘려서 잡히는 정답은 없다는 뜻이라, "못 닿는 문항"의 원인 후보에서 이 상수는 빠진다.
CANDIDATE_N = 100

# RRF 상수. RAG-003 제안값 그대로다.
# **점수가 아니라 순위만 쓰기 때문에** dense 의 코사인과 렉시컬의 ts_rank 가 스케일이 달라도
# 정규화가 필요 없다.
#
# 2026-09-04 에 가중치와 2차원으로 훑었다 (RAG-057). 10~200 어디서도 최대가 19/36 을 못 넘고,
# 19 가 나오는 칸은 **평평한 구간이 아니라 흩어진 점**이다 — 그래서 안 건드린다.
RRF_K = 60

# 렉시컬 축의 가중치. **1.0 이 아니다.**
#
# 균등 가중이면 "한 축에서 1위"가 너무 세다. 실측(2026-08-28) — Q5 의 정답 조문은
# dense 2위인데 렉시컬 49위였고, dense 15위·렉시컬 1위인 문서에 밀려 top-5 밖으로
# 나갔다. `ts_rank` 는 IDF 를 안 쓰므로 흔한 단어가 많은 짧은 청크가 쉽게 1위가 된다 —
# 그 잡음이 좋은 dense 신호를 이기면 안 된다.
#
# 골든셋 12문항 전수 스윕 결과(필수 재현율, dense 단독 = 6):
#     0.05  0.10 │ 0.15  0.20  0.30  0.35  0.45 │ 0.50  0.75  1.00
#        7     7 │    8     8     8     8     8 │    7     7     7
# **0.15~0.45 가 평평하고 그 구간에서 회귀가 없다.** 가운데인 0.3 을 잡았다 —
# 양끝은 한쪽이 무너지는 자리라 여유를 두는 편이 낫다.
#
# ⚠️ 이것은 **골든셋에 맞춘 상수**다. 문항이 늘거나 코퍼스 성격이 바뀌면 다시 재야 한다.
# RRF 자체는 재튜닝이 필요 없지만 이 가중치는 아니다 — 그 구분을 흐리지 말 것.
# 다시 잴 때는 `scratchpad` 가 아니라 이 표를 만든 방식(두 축의 순위를 뽑아 파이썬에서
# 가중 합)으로 스윕하면 된다. DB 왕복 없이 12문항이 몇 초다.
#
# ────────────────────────────────────────────────────────────────────────────
# 2026-09-04 재측정 — **위 조건이 성립해서 다시 쟀고, 0.3 을 그대로 둔다** (RAG-057)
# ────────────────────────────────────────────────────────────────────────────
# 문항이 12 → 25 로 늘고(`must` 가 있는 hand 문항) 코퍼스가 8,990문서가 됐다. 스윕은
# `backend/tools/search_sweep.py` 가 한다 — 위 문단이 말한 그 방식 그대로이고, `verify` 가
# **캐시 융합 == 실물 `search()`** 를 25/25 로 대조한다.
#
# 요구충족 / 36 (RRF_K=60 · 후보 100 · top-5):
#     0.00  0.05 │ 0.10  0.15  0.20  0.25  0.30  0.35 │ 0.40 │ 0.45  0.50 │ 0.60  0.75  1.00
#       15    17 │   18    18    18    18    18    18 │  19  │   18    18 │   17    16    16
#
# **0.40 이 1 더 얻지만 안 옮긴다.** ⓐ 그 이득은 문항 하나(I2)이고 ⓑ 0.37~0.44 로 폭이 0.08 뿐인
# 턱이라, 0.10~0.50 한가운데 있는 0.3 보다 여유가 없다 — 위 문단이 "양끝은 한쪽이 무너지는
# 자리"라며 세운 기준을 그대로 적용하면 옮기지 않는 쪽이다. 가중치·RRF_K 2차원 전수에서도
# **최대가 19 이고 19 는 흩어진 점이다.**
#
# 남은 구멍은 이 상수로 못 고친다 — 문항마다 원하는 방향이 반대다:
#     S3  정답이 lex 1위·dense 39위    → w ≥ 1.35 라야 들어온다
#     DP1 정답이 dense 5·8·26위·lex 밖 → w ≤ 0.20 라야 들어온다
#     Q3·B1 정답 별표 행이 dense 106·lex 147  → 어느 w 로도 안 들어온다 (아래)
# 질의마다 다른 가중치를 주는 것은 **다른 종류의 카드**다 (A2 지역 필터와 같은 층).
#
# **`ts_rank_cd` 는 기각했다.** 이 코퍼스에서 `ts_rank` 와 25문항 중 24문항의 렉시컬 순위가
# 완전히 같고, 요구충족은 모든 가중치에서 한 칸도 안 다르다. 정규화 플래그(1·2·16)는 전부
# **긴 문서를 깎는 방향**이라 최대가 19 → 16 으로 떨어진다 — 여기서 고치고 싶은 편향은
# 짧은 청크가 쉽게 1위가 되는 반대쪽이라, 이 축에는 쓸 플래그가 없다.
LEXICAL_WEIGHT = 0.3


@dataclass(frozen=True)
class Hit:
    """검색 결과 한 줄. `chunk_id` 를 항상 들고 다닌다 — 골든셋 라벨과 **같은 주소**라
    "이게 정답 청크인가"를 눈으로 대조할 수 있다 (RAG-022 ⑥B 의 논리 주소)."""
    rank: int
    score: float                  # 코사인 유사도 (1 - 거리). 벡터가 L2 정규화라 그대로 코사인이다
    chunk_id: str
    citation: str                 # 답변에 실을 인용 문자열 — KPI 그 자체
    citation_url: str | None
    section: str | None
    document_title: str
    content: str
    part: str | None              # "supplementary" = 부칙 (RAG-021 ①)
    # 어느 축이 이 문서를 끌어올렸는지. 검문소③에서 **왜 이게 올라왔는지**를 눈으로 가른다.
    # None = 그 축의 후보에 없었다는 뜻이다.
    dense_rank: int | None = None
    lexical_rank: int | None = None
    # 인용 확장으로 딸려 온 청크면 **그것을 끌어온 청크의 `chunk_id`** (RAG-040).
    # 두 축이 모두 None 인 히트가 왜 거기 있는지를 이 칸 하나로 설명한다.
    cited_by: str | None = None


@dataclass(frozen=True)
class Query:
    """검색 입력. **벡터와 토큰을 함께 들고 다닌다.**

    둘을 따로 넘기게 두면 한쪽만 넘기는 호출이 생기고, 그때 렉시컬 축이 조용히 빠진다 —
    결과가 그럴듯하게 나와서 알아채기 어렵다. 한 덩어리로 묶어 그 실수를 못 하게 한다.
    """
    vector: Any
    tsquery: str = ""             # `to_tsquery('simple', …)` 에 넣을 OR 식. 빈 값이면 dense 단독
    text: str = ""                # 원문. 로그·디버그용


# `<=>` 는 코사인 거리다. `db/indexes.sql` 이 `vector_cosine_ops` 로 인덱스를 만들었으므로
# 다른 연산자(`<->`, `<#>`)를 쓰면 인덱스를 타지 않는다 — 연산자를 여기 한 곳에만 적는 이유다.
#
# 하이브리드 (RAG-035) — 두 축을 각각 `CANDIDATE_N` 까지 뽑아 RRF 로 섞는다.
#   dense : 코사인 순위
#   lex   : `ts_rank(content_tsv, …)` 순위. 형태소 토큰이 `content_tokens` 에 들어 있다
#
# **`score` 는 여전히 코사인이다.** 순서는 RRF 가 정하지만 점수 칸의 뜻을 바꾸지 않았다 —
# 바꾸면 검문소③을 눈으로 볼 때 예전 랩과 비교가 안 되고, 0.02 같은 RRF 값은 사람이
# 읽어도 아무 정보가 없다. 대신 `dense_rank`·`lexical_rank` 로 **왜 올라왔는지**를 보인다.
# 그래서 점수가 순위와 단조롭지 않을 수 있는데, 그 불일치 자체가 "렉시컬이 끌어올렸다"는 신호다.
#
# 동점 처리 — `ts_rank` 는 동점이 흔해서 `row_number()` 만으로는 순위가 실행마다 흔들린다.
# `d.id` 를 2차 정렬키로 박아 **같은 코퍼스면 같은 순위**가 나오게 한다.
_SQL = """
WITH dense AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(q)s) AS rn
    FROM documents
    WHERE embedding IS NOT NULL
      {filters}
    ORDER BY embedding <=> %(q)s
    LIMIT %(n)s
), lex AS (
    -- tsquery 가 비면(%(tsq)s = '') 이 CTE 는 0행이고 결과는 dense 단독이 된다.
    SELECT d.id,
           row_number() OVER (ORDER BY ts_rank(d.content_tsv, q.q) DESC, d.id) AS rn
    FROM documents d, to_tsquery('simple', NULLIF(%(tsq)s, '')) AS q(q)
    WHERE d.content_tsv @@ q.q
      {lex_filters}
    ORDER BY ts_rank(d.content_tsv, q.q) DESC, d.id
    LIMIT %(n)s
), fused AS (
    SELECT COALESCE(dn.id, lx.id) AS id,
           COALESCE(1.0 / (%(rrf)s + dn.rn), 0)
             + COALESCE(%(wlex)s / (%(rrf)s + lx.rn), 0) AS rrf,
           dn.rn AS drn, lx.rn AS lrn
    FROM dense dn FULL OUTER JOIN lex lx ON dn.id = lx.id
)
SELECT 1 - (doc.embedding <=> %(q)s) AS score,
       doc.metadata->>'chunk_id', doc.metadata->>'citation', doc.metadata->>'citation_url',
       doc.section, doc.document_title, doc.content, doc.metadata->>'part',
       f.drn, f.lrn
FROM fused f
JOIN documents doc ON doc.id = f.id
ORDER BY f.rrf DESC, doc.embedding <=> %(q)s
LIMIT %(k)s
"""


# ---------------------------------------------------------------- 인용 확장 (RAG-040)
# **답을 아는 청크가 아니라, 답이 어디 있는지 아는 청크가 먼저 올라오는 경우가 있다.**
#
# `Q6`("광견병 접종 의무인가요?")가 그 자리였다. 코퍼스에 `광견병` 과 `의무` 를 함께 말하는
# 청크는 하나도 없는데, `#39` 가 넣은 보조금24 청크는 이렇게 되어 있다:
#
#     "부산광역시 영도구 광견병 예방접종 시술비 지원 법령: 수의사법(제30조) / 가축전염병 예방법(제15조)"
#
# `광견병` 이 희소 토큰이라 이 청크들이 렉시컬 상위를 독차지하는데(RAG-034 ⑦), 정작 의무를
# 만드는 `제15조` 본문은 39위였다. **검색이 다리를 이미 올려놓고 건너지 않은 것이다.**
# 그래서 늘리는 것은 코퍼스가 아니라 홉이다 — top-k 가 가리키는 조문을 한 번 더 가져온다.
#
# ⚠️ **`metadata` 에 `cites` 가 없다.** 크롤러의 `Extracted.cites` 가 적재까지 실려 있지 않아
# 본문에서 정규식으로 뽑는다. 재적재하면 지울 군더더기지만 그 재적재는 이 카드 밖이다.

# `법령명 + 제N조` 를 잡는다. 세 표기를 다 만난다 —
#   보조금24  "… 가축전염병 예방법(제15조)"     ← 괄호
#   해설 본문 "「동물보호법」 제2조"             ← 낫표
#   법령 본문 "동물보호법 제15조"               ← 맨몸
#
# **한 방에 잡지 않고 두 걸음으로 간다.** 처음에는 `[가-힣]+(?:\s*[가-힣]+)*?…법` 한 줄로 썼는데,
# 법령명이 **안 나오는** 긴 한글 문장에서 중첩 수량자가 폭주했다 (`제15조` 없는 청크 하나에서
# 사실상 정지). 조문 번호를 먼저 찾고 **그 앞 40자만** 되짚으면 후보 구간이 상수라 폭주할 수 없다.
# **항까지 읽는다** (RAG-058). 청크가 항 단위로 저장돼 있기 때문이다 — `동물보호법` 의
# `제101조` 는 `section` 이 `제101조제1항` … `제101조제5항` 인 다섯 행이고, 조 단위로만 읽으면
# `제101조` 로 조회해 **하나도 못 찾는다.** 뽑는 문자열과 DB 의 `section` 이 글자 그대로 같다.
# 조 단위로 저장된 법(`제16조`)도 있어서 조회 쪽에 폴백이 있다 (`_EXPAND_SQL`).
_ARTICLE_RE = re.compile(r"제\d+조(?:의\d+)?(?:제\d+항)?")
# 조문 하나에서 조 부분만. 항 조회가 빗나갔을 때의 폴백 키다.
_ARTICLE_ONLY_RE = re.compile(r"^(제\d+조(?:의\d+)?)")
# 조문 바로 앞에서 법령명을 떼어 낸다. 낱말 4개까지만 본다 — "가축전염병 예방법 시행령" 이 셋이다.
_LAW_TAIL_RE = re.compile(r"([가-힣]+(?:\s[가-힣]+){0,3})\s*[」』]?\s*\(?\s*$")
# 떼어 낸 것이 정말 법령명인가. `…법`·`…법률` 로 끝나고 `시행령`·`시행규칙` 이 붙을 수 있다.
_LAW_OK_RE = re.compile(r"(?:법|법률)(?:\s*시행령|\s*시행규칙)?$")
# 조문 앞을 얼마나 되짚을지. "가축전염병 예방법 시행령(" 이 넉넉히 들어가는 길이다.
_LOOKBACK = 40
# 시행령·시행규칙의 제목에서 모법을 떼어 낸다. `동물보호법 시행령` → `동물보호법`.
_SUBORDINATE_RE = re.compile(r"^(.+법)\s*시행(?:령|규칙)$")


def parent_law(owner_title: str | None) -> str | None:
    """이 문서가 시행령·시행규칙이면 그 모법의 이름. 아니면 `None`.

    **여기서만 모법을 만든다** — 조례·해설·약관은 대상이 아니다 (RAG-058 ①). 조례도 본문에서
    `법` 을 쓰지만 **어느 법인지 문서 제목이 말해 주지 않는다** — 조례 하나가 여러 법을 인용한다.
    """
    m = _SUBORDINATE_RE.match((owner_title or "").strip())
    return m.group(1) if m else None


def refs_in_text(text: str, owner_title: str | None = None) -> list[tuple[str, str]]:
    """텍스트가 가리키는 `(법령명, 조문)` — 등장 순, 중복 포함.

    `owner_title` 은 **이 텍스트가 들어 있는 문서**의 제목이다. 시행령·시행규칙 안에서 맨몸
    `법` 은 그 모법을 뜻하는데(`동물보호법 시행령` 의 `법 제101조제4항` = `동물보호법 제101조제4항`),
    텍스트만 봐서는 그것을 알 수 없어 소유 문서를 받는다 (RAG-058).

    **기본값이 `None` 인 것은 호환이 아니라 뜻이다** — 소유 문서를 모르면 맨몸 `법` 을 풀지
    않는다. 지어내는 것보다 못 찾는 편이 낫다.

    마지막 낱말이 `법` 인 것을 본다. 별표가 두 모양으로 쓰기 때문이다:

        근거 법조문: 법 제101조제4항제4호            → `법`
        위반행위: 소유자등이 법 제16조제2항제1호      → `소유자등이 법`

    앞에 붙은 것은 문장의 주어이지 법령명의 일부가 아니다. `가축전염병 예방법` 은 마지막
    낱말이 `예방법` 이라 걸리지 않는다.
    """
    parent = parent_law(owner_title)
    out: list[tuple[str, str]] = []
    for m in _ARTICLE_RE.finditer(text):
        window = text[max(0, m.start() - _LOOKBACK):m.start()]
        tail = _LAW_TAIL_RE.search(window)
        if not tail:
            continue
        law = " ".join(tail.group(1).split())
        if not _LAW_OK_RE.search(law):
            continue
        if law.split()[-1] == "법":
            if parent is None:
                # 소유 문서가 시행령·시행규칙이 아니다. `…이 법` 같은 산문도 여기서 걸러진다 —
                # 어떤 `document_title` 과도 안 맞아 조회에서 자연히 떨어진다.
                continue
            law = parent
        out.append((law, m.group()))
    return out


# 한 번에 몇 개까지 딸려 올 것인가. **작게 잡는다** — 확장은 근거를 늘리는 만큼 프롬프트를
# 밀어내고 조 단위 청크는 길다. 실측에서 Q6 에 필요한 것은 `제15조` 하나였다.
MAX_EXPANDED = 3

# **인용을 훑는 깊이는 반환하는 k 보다 깊다.**
#
# Q6 실측(2026-08-28) — 제15조를 가리키는 영도구 `근거법령` 청크가 **7위**였다. 서빙 k 가 5라
# 훑는 깊이를 k 에 묶으면 다리를 코앞에서 놓친다. 반대로 k 를 20으로 올리는 것은 답이 아니다:
# 프롬프트에 보조금24 신청방법 20건이 들어가고 그것은 의무를 묻는 질문에 잡음이다.
# 그래서 **읽는 깊이와 싣는 깊이를 가른다** — 20위까지 인용만 훑고, 근거로 싣는 것은 top-k 다.
EXPAND_SCAN_N = 20

# 확장분 조회. `(법령명, 조문)` **쌍**으로 맞춘다 — `section` 만으로 찾으면 안 된다.
# `제2조` 는 법·시행령·시행규칙에 다 있어서, 법령명을 빼면 엉뚱한 법의 같은 번호가 붙는다.
#
# `score` 는 여기서도 코사인이다. 순위 밖에서 들어온 청크라 RRF 점수가 없지만, 점수 칸의 뜻이
# 히트마다 달라지면 검문소③을 눈으로 읽을 수 없다.
# **항으로 먼저, 없으면 조로** (RAG-058 ②). 참조는 `제101조제4항` 처럼 항까지 말하는데 청크는
# 법마다 다르다 — `동물보호법 제101조` 는 항 단위 다섯 행이고 `제16조` 는 조 하나다. 항으로만
# 찾으면 후자를 전부 놓치고, 조로만 찾으면 전자를 전부 놓친다.
#
# `DISTINCT ON` 이 **참조 하나당 한 줄**을 보장한다. 없으면 한 참조가 항·조 두 줄을 물어
# `MAX_EXPANDED` 를 혼자 써 버린다. 정확 일치(`d.section = want.section`)를 앞에 세워
# 항이 있으면 항이 이긴다.
_EXPAND_SQL = """
SELECT score, chunk_id, citation, citation_url, section, document_title, content, part
FROM (
    SELECT DISTINCT ON (want.title, want.section)
           1 - (d.embedding <=> %(q)s) AS score,
           d.metadata->>'chunk_id' AS chunk_id,
           d.metadata->>'citation' AS citation,
           d.metadata->>'citation_url' AS citation_url,
           d.section AS section, d.document_title AS document_title,
           d.content AS content, d.metadata->>'part' AS part,
           d.embedding <=> %(q)s AS dist
    FROM documents d
    JOIN unnest(%(titles)s::text[], %(sections)s::text[], %(articles)s::text[])
         AS want(title, section, article)
      ON d.document_title = want.title
     AND d.section IN (want.section, want.article)
    WHERE d.embedding IS NOT NULL
      {filters}
    ORDER BY want.title, want.section, (d.section = want.section) DESC, d.embedding <=> %(q)s
) picked
ORDER BY dist
LIMIT %(limit)s
"""


def article_only(section: str) -> str:
    """`제101조제4항` → `제101조`. 항이 없으면 그대로."""
    m = _ARTICLE_ONLY_RE.match(section)
    return m.group(1) if m else section


def _refs_in(hit: Hit) -> list[tuple[str, str]]:
    """히트 하나가 가리키는 `(법령명, 조문)`. `citation` 도 같이 본다 — 보조금24 는 서비스명이
    `citation` 에 있고 조문은 `content` 에 있어서, 둘을 이어 붙여야 한 문장으로 읽힌다.

    **`document_title` 을 같이 넘긴다** — 시행령 안의 맨몸 `법` 을 풀려면 필요하다 (RAG-058).
    """
    return refs_in_text(f"{hit.citation}\n{hit.content}", hit.document_title)


def cited_refs(hits: list[Hit]) -> list[tuple[str, str]]:
    """히트들이 가리키는 조문 — 등장 순, 중복 제거.

    **이미 들어와 있는 조문은 뺀다.** 안 빼면 같은 청크를 두 번 싣고 `MAX_EXPANDED` 를
    그것으로 다 써 버린다.

    **조 폴백까지 보고 뺀다** (RAG-058 ②). `제16조` 가 이미 히트에 있는데 참조가
    `제16조제2항` 으로 오면, 항 조회는 빗나가고 폴백이 같은 `제16조` 를 도로 데려온다 —
    `expand_citations` 가 마지막에 거르기는 하지만 그 전에 이미 한 자리를 쓴 뒤다.
    """
    have = {(h.document_title, h.section) for h in hits}
    seen: dict[tuple[str, str], None] = {}
    for h in hits:
        for ref in _refs_in(h):
            if ref in have or (ref[0], article_only(ref[1])) in have:
                continue
            seen.setdefault(ref, None)
    return list(seen)


def expand_citations(hits: list[Hit], query: Query, *, scan: list[Hit] | None = None,
                     include_supplementary: bool = True, limit: int = MAX_EXPANDED,
                     conn=None) -> list[Hit]:
    """`hits` 가 가리키는 조문 청크를 **뒤에 덧붙인다.** 원래 순위는 손대지 않는다.

    `scan` 은 **인용을 훑을 범위**다 (기본값은 `hits` 자신). 반환할 근거보다 깊게 훑기 위해
    갈라 뒀다 — `EXPAND_SCAN_N` 참고. 훑기만 하고 싣지는 않는다.

    확장분은 `rank` 를 이어받고 `cited_by` 로 표시된다. 순위를 다시 매기지 않는 이유는
    검문소③ 때문이다 — 확장이 없었으면 무엇이 top-k 였는지가 그대로 보여야 한다.
    """
    scan = scan or hits
    refs = cited_refs(scan)[:limit]
    if not refs:
        return hits

    filters = []
    params: dict[str, Any] = {"q": query.vector, "limit": limit,
                              "titles": [r[0] for r in refs], "sections": [r[1] for r in refs],
                              # 항 조회가 빗나갔을 때의 폴백 키 (RAG-058 ②)
                              "articles": [article_only(r[1]) for r in refs]}
    if not include_supplementary:
        filters.append("AND d.metadata->>'part' IS DISTINCT FROM 'supplementary'")

    own = conn is None
    conn = conn or load.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_EXPAND_SQL.format(filters="\n  ".join(filters)), params)
            rows = cur.fetchall()
    finally:
        if own:
            conn.close()

    def puller(title: str, section: str | None) -> str | None:
        """이 조문을 끌어온 히트. 같은 조문을 여러 히트가 가리키면 **가장 높은 순위**를 돌린다.

        top-k 밖일 수 있다 — Q6 의 다리가 7위였다. 그래서 근거에 안 실린 청크의 `chunk_id` 가
        나올 수 있는데, 그것이 사실이고 검문소③이 알아야 할 것이다.

        **조 폴백으로 온 청크도 찾아 준다** (RAG-058 ②). 참조는 `제16조제2항` 인데 돌아온
        청크의 `section` 은 `제16조` 라, 있는 그대로 맞추면 못 찾고 `cited_by` 가 빈 채로
        나간다 — 그러면 두 축이 모두 `None` 인 히트가 **왜 거기 있는지 설명할 칸이 사라진다.**
        """
        for h in scan:
            refs = _refs_in(h)
            if (title, section) in refs:
                return h.chunk_id
            if any(t == title and article_only(s) == section for t, s in refs):
                return h.chunk_id
        return None

    known = {h.chunk_id for h in hits}
    extra = [
        Hit(rank=len(hits) + i + 1, score=float(score), chunk_id=cid, citation=cit or "",
            citation_url=url, section=section, document_title=title or "", content=content,
            part=part, cited_by=puller(title or "", section))
        for i, (score, cid, cit, url, section, title, content, part) in enumerate(rows)
        if cid not in known
    ]
    return hits + extra


def encode(query: str, model_key: str | None = None):
    """질의 → 벡터. **모델을 올렸다 내린다.**

    `encode_query` 를 쓰는 것이 계약이다 — Qwen3 만 질의에 공식 지시문을 붙이는 비대칭 모델이라
    (4단계 실측) 문서 경로로 넣으면 그 모델을 자기 설계와 다르게 쓰게 된다. 지금 기본값은
    승자가 아니라 기준선 `bge-m3` 이고, 그것이 RAG-024 `판정 이후` 의 결정이다.
    """
    key = model_key or config.settings.embedding_model_key
    model = embed.MODELS[key]
    st = embed.load_model(model)
    try:
        vector = embed.encode_query(model, query, st=st)
    finally:
        del st
        embed.release()
    return make_query(query, vector)


def make_query(text: str, vector) -> Query:
    """벡터를 이미 만들어 둔 caller 용 (CLI 가 질의 여러 개를 한 번에 인코딩한다).

    **토큰화는 여기 한 곳에서만 한다** — 문서 쪽(`stages/load.py`)과 같은 `core.tokenize` 를
    부른다. 둘이 갈라지면 매칭이 그냥 안 되는데 dense 가 결과를 채워 줘서 안 보인다.
    """
    return Query(vector=vector, tsquery=tokenize.tsquery(text), text=text)


def search(query: Query, *, k: int = DEFAULT_K, include_supplementary: bool = True,
           category: str | None = None, conn=None) -> list[Hit]:
    """`Query` 하나로 top-k. **DB 만 만진다** — 인코딩·토큰화는 caller 가 한다.

    나눠 둔 이유는 `--questions` 가 질의 7개를 도는데 모델을 7번 올렸다 내릴 이유가 없어서다.
    (`parse_doc` 이 쓰기를 분리한 것과 같은 모양이다.)

    **`include_supplementary` 기본이 `True` 인 것은 결정이다** (RAG-026 ①). 부칙은 세 모델 모두에서
    정답이었던 적이 없지만, **검문소③은 사람이 눈으로 보는 검사 자리라 기본값이 이미 걸러진
    결과면 무엇이 걸러졌는지를 볼 수 없다.** 서빙(9단계)의 기본값은 여기서 정하지 않는다 —
    검사 도구와 서빙이 같은 기본값을 쓸 이유가 없다.

    **교통수단 배제는 인자가 아니다** (RAG-052). `query.text` 에 수단이 하나 적혀 있으면 그 수단이
    아닌 교통 `subcategory` 를 뺀다 (`core.transport`). 인자로 빼지 않는 이유는 인용 확장(RAG-040)과
    같다 — 빼면 CLI·9단계·FastAPI 가 각자 켜고 끄게 되고 검문소③이 본 것과 서빙이 갈린다. 신호가
    없으면 아무것도 안 뺀다. 검문소③은 `rag search` 가 신호 줄을 찍어 준다.
    """
    filters, lex_filters = [], []
    # **인용을 훑을 만큼 뽑고, 근거로 싣는 것은 k 까지다** (RAG-040). 한 번의 쿼리로 끝낸다 —
    # 확장 때문에 DB 를 두 번 왕복하면 그 비용이 `/life/ask` 마다 붙는다
    params: dict[str, Any] = {"q": query.vector, "k": max(k, EXPAND_SCAN_N), "n": CANDIDATE_N,
                              "rrf": RRF_K, "wlex": LEXICAL_WEIGHT, "tsq": query.tsquery}
    if not include_supplementary:
        # part 는 metadata 안에 있고, 부칙이 아닌 청크는 아예 키가 없다 (exclude_none 으로 쓴다)
        filters.append("AND metadata->>'part' IS DISTINCT FROM 'supplementary'")
        lex_filters.append("AND d.metadata->>'part' IS DISTINCT FROM 'supplementary'")
    if category:
        filters.append("AND category = %(category)s")
        lex_filters.append("AND d.category = %(category)s")
        params["category"] = category
    if excluded := transport.exclusions(query.text):
        # 두 축 모두에 건다 — 한 축에만 걸면 RRF 가 다른 축에서 그 문서를 도로 끌어온다
        filters.append("AND subcategory <> ALL(%(excluded)s)")
        lex_filters.append("AND d.subcategory <> ALL(%(excluded)s)")
        params["excluded"] = list(excluded)

    own = conn is None
    conn = conn or load.connect()

    # **지역도 인자가 아니라 질의에서 읽는다** (RAG-063) — `transport` 와 같은 이유다.
    # 인자로 빼면 CLI·9단계·FastAPI 가 각자 켜고 끄게 되고 검문소③이 본 것과 서빙이 갈린다.
    #
    # ⚠ **`org` 이 없는 문서는 남긴다.** "부산 동래구에서 목줄 안 하면 과태료?" 의 답은 조례가
    # 아니라 동물보호법에 있고 법령에는 `org` 이 없다. 빼면 고치려던 것보다 큰 것이 사라진다.
    # 실제로 걸러지는 것은 **다른 지자체의 조례·보조금**뿐이고, 그것이 S3 의 top-8 을 채우고
    # 있던 바로 그 문서들이다 (RAG-033 ⑥).
    if kept := region.orgs(query.text, known_orgs(conn)):
        filters.append(
            "AND (metadata->>'org' IS NULL OR metadata->>'org' = ANY(%(orgs)s))")
        lex_filters.append(
            "AND (d.metadata->>'org' IS NULL OR d.metadata->>'org' = ANY(%(orgs)s))")
        params["orgs"] = list(kept)

    try:
        with conn.cursor() as cur:
            cur.execute(_SQL.format(filters="\n      ".join(filters),
                                    lex_filters="\n      ".join(lex_filters)), params)
            scanned = [
                Hit(rank=i + 1, score=float(score), chunk_id=cid, citation=cit or "",
                    citation_url=url, section=section, document_title=title or "",
                    content=content, part=part, dense_rank=drn, lexical_rank=lrn)
                for i, (score, cid, cit, url, section, title, content, part, drn, lrn)
                in enumerate(cur.fetchall())
            ]
            hits = scanned[:k]
        # **인용 확장을 인자로 빼지 않는다** (RAG-040). 빼면 CLI·9단계·FastAPI 가 각자 켜고
        # 끄게 되고, 그 순간 검문소③이 본 것과 서빙이 하는 것이 갈린다 — 이 파일이 처음부터
        # 막고 있는 그 자리다. `test_search` 가 단언하는 시그니처도 그대로 남는다.
        return expand_citations(hits, query, scan=scanned,
                                include_supplementary=include_supplementary, conn=conn)
    finally:
        if own:
            conn.close()


# 코퍼스에 실재하는 `org` 값. **표를 손으로 적지 않는다** (RAG-042 ③) — 지자체가 늘면
# 적재만으로 따라온다. 프로세스 수명 동안 캐시한다: 한 랩이 질의 33개를 도는데 같은
# `SELECT DISTINCT` 를 33번 할 이유가 없고, 코퍼스는 프로세스가 도는 중에 안 바뀐다.
_ORGS_CACHE: tuple[str, ...] | None = None


def known_orgs(conn=None) -> tuple[str, ...]:
    """`documents.metadata->>'org'` 의 고유값 전부. 적재 전이면 빈 튜플이다.

    ⚠ **빈 결과는 캐시하지 않는다.** 캐시하면 적재 전에 한 번 부른 프로세스가 그 뒤로 영영
    지역 필터를 안 켠다 — 그런데 **검색은 계속 되므로 아무 예외도 안 난다.** 이 파일이
    처음부터 경계하는 "조용히 틀리는" 모양이라, 빈 값일 때만 매번 다시 묻는다(그 비용은
    코퍼스가 없을 때만 든다).
    """
    global _ORGS_CACHE
    if _ORGS_CACHE:
        return _ORGS_CACHE
    own = conn is None
    conn = conn or load.connect()
    try:
        with conn.cursor() as cur:
            # 파라미터를 빈 dict 로 넘긴다 — psycopg 도 받고, SQL 을 받아 적는 테스트 가짜
            # 커서도 `execute(sql, params)` 두 인자를 기대한다.
            cur.execute("SELECT DISTINCT metadata->>'org' FROM documents"
                        " WHERE metadata->>'org' IS NOT NULL", {})
            _ORGS_CACHE = tuple(sorted(r[0] for r in cur.fetchall() if r[0]))
    finally:
        if own:
            conn.close()
    return _ORGS_CACHE


def forget_orgs() -> None:
    """캐시를 버린다. 적재 직후·테스트에서 쓴다."""
    global _ORGS_CACHE
    _ORGS_CACHE = None


def hand_questions() -> list[tuple[str, str, set[str], set[str]]]:
    """검증질문 1~7 → (id, 질문, must 라벨, nice 라벨).

    **질문을 코드에 박지 않고 `goldenset.yaml` 에서 읽는다** (RAG-026 ②). 박으면 질문 목록의 단일
    소스가 둘이 되고, RAG-022 가 골든셋을 git 에 넣은 이유(판단 기록의 단일 소스)가 반쯤 무너진다.
    덤으로 **정답 라벨을 알고 있으므로** 결과에 `must`/`nice` 를 표시할 수 있어, 검문소③이
    "이게 정답인가"를 손으로 세지 않아도 된다.
    """
    from . import goldenset

    gs = goldenset.load()
    # **`must_flat` 이다** — 여기가 쓰는 것은 "이 주소가 정답 층인가"뿐이라 요구의 경계가
    # 필요 없다 (RAG-055). 요구째로 봐야 하는 것은 채점이고, 그건 `evaluate` 와 `score` 다.
    # 기권·거절 문항(`expect != answer`)도 그대로 나간다 — 랩이 그 질문을 돌려야 잴 수 있다
    return [(i.id, i.question, set(i.must_flat), set(i.nice))
            for i in gs.items if i.origin == "hand"]


def tier_of(chunk_id: str, must: set[str], nice: set[str]) -> str:
    """검색 결과의 `chunk_id` 가 정답 라벨인가. 라벨은 **수집 날짜를 뺀 논리 주소**다 (RAG-022 ⑥B)."""
    from . import goldenset

    logical = goldenset.logical(chunk_id)
    return "must" if logical in must else "nice" if logical in nice else "-"
