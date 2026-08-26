# web_ingest.py = URL 하나를 받아서 "가져오기 -> 본문 추출 -> 청킹 -> 임베딩 -> 저장"까지
# 전부 자동으로 해주는 진짜 수집 파이프라인 (로드맵 ⑧ PDF/Web Parsing).
#
# 지금까지(scripts/ingest_real_sources.py, ingest_chunked_sources.py)는 제가 URL을 손으로
# 읽고, 사실을 직접 확인해서 한국어로 옮겨 적은 "정적 데이터셋"이었습니다. 이 모듈은 그
# 대신 실제로 HTTP 요청을 보내고, HTML을 파싱해서 본문을 뽑아내고, 그걸 자동으로 저장까지
# 하는 반복 가능한 파이프라인입니다.
#
# 번역은 하지 않습니다: 이전에 로컬 LLM(qwen2.5:7b)이 사실을 오역하는 문제를 발견했기
# 때문에("distemper"를 "독감"으로 잘못 옮김), 신뢰할 수 없는 자동 번역보다는 원문 언어
# 그대로 저장하는 쪽을 선택했습니다. bge-m3는 다국어 임베딩 모델이라 한국어 질문으로도
# 영어 문서를 검색할 수 있고, 생성 모델이 최종 답변은 한국어로 만들어줍니다.

import re

import httpx
from bs4 import BeautifulSoup

from app.repository import DocumentRecord, insert_document
from app.services.chunking import chunk_text
from app.services.embedding import embed_text

# 사람이 쓰는 브라우저인 것처럼 User-Agent를 밝혀서(정직하게), 일부 사이트가 봇 요청을
# 무조건 차단하는 걸 피함. 실제 이름을 남기는 게 매너 있는 스크래핑 관행입니다.
_USER_AGENT = "dog-ai-assistant-prototype/0.1 (personal RAG project)"

# 본문이 아닌 부분(내비게이션, 광고, 스크립트 등)으로 흔히 쓰이는 HTML 태그.
# 이런 태그를 통째로 제거한 뒤 남는 텍스트를 "본문"으로 간주합니다.
# (완벽한 readability 알고리즘은 아니고, 실용적인 수준의 휴리스틱입니다.)
_NON_CONTENT_TAGS = ["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]

# 실전 사이트(특히 Next.js/React 기반)는 <nav>/<footer> 같은 시맨틱 태그를 안 쓰고
# CSS 모듈 클래스명(예: "Breadcrumb_breadcrumbContainer__BtsRt")을 쓰는 경우가 많습니다.
# 태그 이름 대신 class/id "안에 이 키워드가 포함되어 있는지"로 걸러내면 이런 경우도 잡을 수 있습니다.
# (실제로 Merck Veterinary Manual 페이지를 수집해보니 <nav>/<footer> 태그가 없어서
#  브레드크럼/저작권 문구/퀴즈 광고가 그대로 섞여 들어온 것을 확인하고 추가함.)
_NOISE_CLASS_KEYWORDS = [
    "breadcrumb", "footer", "cookie", "nav", "menu", "sidebar", "social",
    "promo", "banner", "advert", "newsletter", "subscribe", "share",
    "quiz", "testyourknowledge", "relatedcontent", "toc",
]


def _is_noise_element(tag) -> bool:
    class_and_id = " ".join(tag.get("class", []) or []) + " " + (tag.get("id") or "")
    class_and_id = class_and_id.lower()
    return any(keyword in class_and_id for keyword in _NOISE_CLASS_KEYWORDS)


# 구조적으로 못 거른 텍스트가 남아있을 때를 대비한 마지막 안전망 (예: "© 2026 ... All rights reserved.").
_COPYRIGHT_PATTERN = re.compile(r"©\s*\d{4}.*?rights reserved\.?", re.IGNORECASE)


def fetch_html(url: str, timeout: float = 15.0) -> str:
    response = httpx.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout, follow_redirects=True)
    response.raise_for_status()  # 4xx/5xx 응답이면 예외를 던짐 (호출한 쪽에서 처리)
    return response.text


def extract_main_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(_NON_CONTENT_TAGS):
        tag.decompose()  # 해당 태그와 그 안의 내용을 통째로 트리에서 제거

    # 태그 이름이 아니라 class/id 키워드로 노이즈를 찾아 제거 (위 _NOISE_CLASS_KEYWORDS 설명 참고).
    # soup.find_all(True)는 "모든 태그"를 의미. list(...)로 감싸는 이유: decompose()로 트리를
    # 바꾸면서 동시에 순회하면 안 되므로, 먼저 리스트로 확정해두고 그 다음에 하나씩 제거.
    for tag in list(soup.find_all(True)):
        if tag.parent is not None and _is_noise_element(tag):
            tag.decompose()

    # <article> 이나 <main> 태그가 있으면 그게 진짜 본문일 확률이 높음 (시맨틱 HTML 관례).
    # 없으면 <body> 전체에서라도 텍스트를 뽑음.
    container = soup.find("article") or soup.find("main") or soup.find("body") or soup

    # get_text(separator=" ") : 태그 경계마다 공백을 넣어서, 서로 다른 요소의 텍스트가
    # 붙어버리는 것을 방지 (예: "<h1>제목</h1><p>본문</p>" -> "제목 본문", 안 붙으면 "제목본문").
    text = container.get_text(separator=" ")
    # 여러 줄바꿈/공백을 하나의 공백으로 정리.
    text = " ".join(text.split())
    # 구조적 필터를 통과했더라도 남아있을 수 있는 저작권 문구를 텍스트 레벨에서 한 번 더 제거.
    text = _COPYRIGHT_PATTERN.sub("", text)
    return text.strip()


def ingest_url(
    url: str, source_tag: str | None = None, max_sentences: int = 3, overlap_sentences: int = 1
) -> list[DocumentRecord]:
    """URL 하나를 실제로 가져와서 청킹 후 지식베이스에 저장. 저장된 문서 목록을 반환."""
    html = fetch_html(url)
    text = extract_main_text(html)
    chunks = chunk_text(text, max_sentences=max_sentences, overlap_sentences=overlap_sentences)

    records = []
    for chunk in chunks:
        embedding = embed_text(chunk)
        record = insert_document(chunk, embedding, source_tag, url)
        records.append(record)
    return records
