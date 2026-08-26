# repository.py = DB(Postgres+pgvector)에 실제 SQL을 날리는 곳.
# "리포지토리 패턴"이라고 해서, DB 관련 코드를 다른 코드(rag.py, routers/*)와 분리해두는 방식입니다.
# 다른 파일들은 SQL을 직접 쓰지 않고 이 파일의 함수(insert_document, search_similar 등)만 호출합니다.

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pgvector import Vector  # 파이썬 list[float]을 DB의 vector 타입으로 명시적으로 감싸주는 래퍼

from app.db import get_connection


# DB에서 문서 1행(row)을 조회했을 때 담아 쓰는 자료구조.
@dataclass
class DocumentRecord:
    id: UUID
    content: str
    source_tag: str | None
    created_at: datetime
    source_url: str | None = None


# 검색 결과 1건(문서 내용 + 유사도 점수)을 담는 자료구조.
@dataclass
class SearchResult:
    content: str
    source_tag: str | None
    score: float
    source_url: str | None = None


def insert_document(
    content: str,
    embedding: list[float],
    source_tag: str | None = None,
    source_url: str | None = None,
) -> DocumentRecord:
    conn = get_connection()
    try:
        # conn.execute(SQL, 파라미터) : %s 자리에 파라미터 튜플의 값들이 순서대로 들어감.
        # (SQL 문자열에 값을 직접 이어붙이지 않고 %s를 쓰는 이유: SQL 인젝션 공격을 막기 위한
        #  "파라미터 바인딩" 방식. 항상 이렇게 쓰는 게 안전합니다.)
        # Vector(embedding) : list[float]를 pgvector가 이해하는 vector 타입으로 변환해서 전달.
        # RETURNING ... : INSERT 하자마자 방금 넣은 행의 컬럼값들을 바로 돌려받음 (재조회 불필요).
        row = conn.execute(
            """
            INSERT INTO documents (content, embedding, source_tag, source_url)
            VALUES (%s, %s, %s, %s)
            RETURNING id, content, source_tag, source_url, created_at
            """,
            (content, Vector(embedding), source_tag, source_url),
        ).fetchone()  # 결과 1행을 튜플로 가져옴: (id, content, source_tag, source_url, created_at)
    finally:
        # try/finally: 중간에 에러가 나더라도 연결을 반드시 닫아서 자원 누수를 막음.
        conn.close()
    # row[0], row[1] ... 은 SELECT/RETURNING에 적은 컬럼 순서 그대로의 값.
    return DocumentRecord(id=row[0], content=row[1], source_tag=row[2], source_url=row[3], created_at=row[4])


def list_documents() -> list[DocumentRecord]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, content, source_tag, source_url, created_at FROM documents ORDER BY created_at DESC"
        ).fetchall()  # 여러 행을 한 번에 리스트[튜플]로 가져옴
    finally:
        conn.close()
    # 리스트 컴프리헨션: rows의 각 행(r)을 DocumentRecord로 변환해서 새 리스트를 만듦.
    return [
        DocumentRecord(id=r[0], content=r[1], source_tag=r[2], source_url=r[3], created_at=r[4]) for r in rows
    ]


def search_similar(query_embedding: list[float], top_k: int) -> list[SearchResult]:
    conn = get_connection()
    try:
        # <=> 는 pgvector가 제공하는 "코사인 거리" 연산자 (값이 작을수록 더 비슷함).
        # 1 - (embedding <=> %s) 로 "거리"를 "유사도(similarity, 클수록 비슷함)"로 뒤집어 계산.
        # ORDER BY embedding <=> %s : 거리가 가까운(=비슷한) 순서로 정렬.
        # LIMIT %s : 상위 top_k개만 가져옴.
        rows = conn.execute(
            """
            SELECT content, source_tag, source_url, 1 - (embedding <=> %s) AS similarity
            FROM documents
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (Vector(query_embedding), Vector(query_embedding), top_k),
        ).fetchall()
    finally:
        conn.close()
    return [
        SearchResult(content=r[0], source_tag=r[1], source_url=r[2], score=float(r[3])) for r in rows
    ]


def _build_prefix_tsquery(query: str) -> str:
    """사용자 질문을 "단어1:* | 단어2:* | ..." 형태의 접두어 매칭 tsquery 문자열로 변환.

    한국어는 "자일리톨은"처럼 명사에 조사가 그대로 붙어 하나의 토큰이 되기 때문에,
    to_tsvector('simple', ...)가 만든 토큰과 사용자가 입력한 순수 단어("자일리톨")가
    정확히 일치하지 않는 경우가 매우 흔합니다 (실제로 plainto_tsquery로 시도했다가
    "자일리톨"로 검색해도 "자일리톨은"이 매치되지 않는 문제를 확인했습니다).
    ":*" 접두어 매칭을 쓰면 "자일리톨"로 시작하는 토큰(자일리톨은, 자일리톨을, ...)을
    모두 찾을 수 있어 이 문제를 실질적으로 완화합니다. 한국어 형태소 분석기(예: mecab)를
    쓰는 것이 정석이지만, 별도 인프라 없이 표준 PostgreSQL만으로 가능한 실용적 절충입니다.

    다만 이것만으로는 "검색어 자체에 조사가 붙은 경우"(예: 사용자가 "등록을"이라고 입력)를
    못 잡습니다 — 접두어가 "등록을:*"가 되어버려서 "등록대상동물의"처럼 다른 조사/복합어가
    붙은 문서 토큰과는 애초에 안 맞습니다. 그래서 아래에서 각 단어의 흔한 조사를 먼저
    떼어내(_strip_josa) 어근만으로 접두어를 만듭니다 (law_qa_hit_rate 평가에서 keyword
    Hit@1이 2.78%까지 떨어지는 걸 보고 발견/수정함).
    """
    # 한글/영문/숫자 "단어"만 뽑아내고, tsquery 연산자로 오인될 특수문자(&, |, :, ! 등)는 제거.
    words = re.findall(r"[\w가-힣]+", query)
    return " | ".join(f"{_strip_josa(word)}:*" for word in words)


# 긴 조사부터 검사해야 "이며"를 "며"로 잘못 자르는 대신 "이며"를 통째로 먼저 뗄 수 있음.
_JOSA_SUFFIXES = sorted(
    [
        "으로부터", "에서부터", "이라도", "이나마", "조차도",
        "에서", "으로", "까지", "부터", "한테", "에게", "이나", "이며", "이고", "이라",
        "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "랑", "나", "며", "고",
    ],
    key=len,
    reverse=True,
)


def _strip_josa(word: str) -> str:
    """단어 끝의 흔한 한국어 조사를 하나 떼어낸 어근을 반환. 어근이 1글자로 줄어들면
    (너무 광범위한 접두어 매칭이 될 수 있어) 원래 단어를 그대로 반환."""
    for suffix in _JOSA_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[: -len(suffix)]
    return word


def search_keyword(query: str, top_k: int) -> list[SearchResult]:
    """전문 검색(키워드 매칭) 기반 검색. search_similar(벡터 유사도)와는 다른 방식으로 후보를 찾음.

    벡터 검색은 "의미"가 비슷한 문서를 찾는 데 강하지만, "자일리톨"처럼 특정 용어가
    정확히 들어간 문서를 정확히 짚어내는 데는 약할 수 있습니다. content_tsv(전문 검색 컬럼)를
    이용한 키워드 매칭으로 이를 보완합니다 (하이브리드 검색의 "sparse" 축).
    """
    tsquery_str = _build_prefix_tsquery(query)
    if not tsquery_str:
        return []

    conn = get_connection()
    try:
        # to_tsquery : _build_prefix_tsquery가 만든 "단어:* | 단어:*" 질의 문자열을 실제 검색 질의로 해석.
        # @@ : "이 문서의 tsvector가 이 질의와 매치되는가?" 연산자.
        # ts_rank_cd : 매치 정도를 점수화 (매치된 단어 수/밀집도가 높을수록 점수가 높음).
        rows = conn.execute(
            """
            SELECT content, source_tag, source_url,
                   ts_rank_cd(content_tsv, to_tsquery('simple', %s)) AS rank
            FROM documents
            WHERE content_tsv @@ to_tsquery('simple', %s)
            ORDER BY rank DESC
            LIMIT %s
            """,
            (tsquery_str, tsquery_str, top_k),
        ).fetchall()
    finally:
        conn.close()
    return [
        SearchResult(content=r[0], source_tag=r[1], source_url=r[2], score=float(r[3])) for r in rows
    ]


def delete_document(document_id: UUID) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM documents WHERE id = %s", (document_id,))
    finally:
        conn.close()
