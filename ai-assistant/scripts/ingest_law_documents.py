# ingest_law_documents.py = 동물보호법 계열 법령(모법/시행령/시행규칙)을
# 조(article) 단위로 구조 기반 청킹하여 documents_test에 저장하는 스크립트.
#
# 법제처 Open API(인증키 필요)를 쓰는 대신, 이미 그 API 데이터를 조/항 구조로
# 정리해서 공개 HTML로 서빙하는 두 개의 법률 정보 사이트(nepla.ai, ulex.co.kr)를
# 크롤링합니다 — web_ingest.py와 같은 방식(정직한 User-Agent, 실제 HTTP 요청)이지만,
# 여기서는 본문 전체를 뭉치지 않고 조(article) 경계를 유지한 채로 파싱합니다.
#
# 청크 ID = 인용 문자열("동물보호법 제15조제2항" 등)이며 documents_test.section에
# 그대로 저장됩니다. 조문이 너무 길면(800자 초과) 항(①②③...) 단위로 쪼갭니다.

import re

import httpx
from bs4 import BeautifulSoup

from app.repository_documents_test import insert_document_test
from app.services.embedding import embed_text

_USER_AGENT = "dog-ai-assistant-prototype/0.1 (personal RAG project)"
_CHUNK_CHAR_THRESHOLD = 800
_HANG_MARKERS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def fetch(url: str) -> str:
    response = httpx.get(url, headers={"User-Agent": _USER_AGENT}, timeout=20.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def make_citation(law_name: str, jo: str, hang: int | None = None) -> str:
    citation = f"{law_name} 제{jo}조"
    if hang:
        citation += f"제{hang}항"
    return citation


def split_by_hang(body_text: str) -> list[tuple[int | None, str]]:
    """항(①②③...) 경계로 본문을 쪼갬. 항 표시가 없으면 전체를 하나로 반환."""
    positions = [(i, ch) for i, ch in enumerate(body_text) if ch in _HANG_MARKERS]
    if not positions:
        return [(None, body_text.strip())]

    segments: list[tuple[int | None, str]] = []
    if positions[0][0] > 0:
        segments.append((None, body_text[: positions[0][0]].strip()))
    for idx, (pos, ch) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(body_text)
        hang_num = _HANG_MARKERS.index(ch) + 1
        segments.append((hang_num, body_text[pos:end].strip()))
    return [(n, t) for n, t in segments if t]


def parse_nepla(html: str) -> list[dict]:
    """nepla.ai 조문 페이지 파서. 조마다 <div class="...articleArea..." id="15조">
    안에 제목 <p class="...bold...">와 본문 <div class="...articleContents...">가 있음."""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    for area in soup.select('div[class*="articleArea"]'):
        jo_id = area.get("id", "") or ""
        m = re.match(r"(\d+)조(?:의(\d+))?", jo_id)
        if not m:
            continue
        jo = m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")
        title_el = area.select_one('p[class*="bold"]')
        title = title_el.get_text(" ", strip=True) if title_el else ""
        title = re.sub(r"^제\d+조(?:의\d+)?\s*", "", title).strip("() ")
        content_el = area.select_one('div[class*="articleContents"]')
        body = content_el.get_text(" ", strip=True) if content_el else ""
        articles.append({"jo": jo, "title": title, "body": body})
    return articles


def parse_ulex(html: str) -> list[dict]:
    """ulex.co.kr 조문 페이지 파서. 제목+본문이 <h3 class="content__h"> 하나에
    통째로 들어있고 끝에 "연혁" 링크 텍스트가 붙음."""
    soup = BeautifulSoup(html, "html.parser")
    articles = []
    for h in soup.select("div.content h3.content__h"):
        text = h.get_text(" ", strip=True)
        text = re.sub(r"\s*연혁\s*$", "", text)
        m = re.match(r"제(\d+)조(?:의(\d+))?\(([^)]*)\)\s*(.*)", text, re.DOTALL)
        if not m:
            continue
        jo = m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")
        articles.append({"jo": jo, "title": m.group(3), "body": m.group(4)})
    return articles


def build_chunks(articles: list[dict], law_name: str) -> list[dict]:
    chunks = []
    for art in articles:
        jo, title, body = art["jo"], art["title"], art["body"]
        full_text = f"제{jo}조({title}) {body}".strip() if title else f"제{jo}조 {body}".strip()
        if len(full_text) <= _CHUNK_CHAR_THRESHOLD:
            chunks.append({"content": full_text, "section": make_citation(law_name, jo)})
            continue
        for hang_num, seg_text in split_by_hang(body):
            content = f"제{jo}조({title}) {seg_text}" if hang_num is None else f"{law_name} 제{jo}조 {seg_text}"
            chunks.append({"content": content, "section": make_citation(law_name, jo, hang_num)})
    return chunks


def ingest_law(law_name: str, url: str, parser) -> int:
    html = fetch(url)
    articles = parser(html)
    print(f"[{law_name}] 조문 파싱: {len(articles)}건")

    chunks = build_chunks(articles, law_name)
    for chunk in chunks:
        embedding = embed_text(chunk["content"])
        insert_document_test(
            content=chunk["content"],
            embedding=embedding,
            category="animal-protection-law",
            subcategory="조문",
            metadata={"law_name": law_name, "source_url": url},
            source="법제처 국가법령정보센터(원문)",
            source_type="statute",
            document_title=law_name,
            section=chunk["section"],
            source_url=url,
        )
    print(f"[{law_name}] 청크 삽입: {len(chunks)}건")
    return len(chunks)


def main() -> None:
    total = 0
    total += ingest_law(
        "동물보호법",
        "https://www.nepla.ai/law/%EB%8F%99%EB%AC%BC%EB%B3%B4%ED%98%B8%EB%B2%95",
        parse_nepla,
    )
    total += ingest_law(
        "동물보호법 시행규칙",
        "https://www.nepla.ai/law/%EB%8F%99%EB%AC%BC%EB%B3%B4%ED%98%B8%EB%B2%95%20%EC%8B%9C%ED%96%89%EA%B7%9C%EC%B9%99",
        parse_nepla,
    )
    total += ingest_law(
        "동물보호법 시행령",
        "https://www.ulex.co.kr/%EB%B2%95%EB%A5%A0/250531-010624-%EB%8F%99%EB%AC%BC%EB%B3%B4%ED%98%B8%EB%B2%95%EC%8B%9C%ED%96%89%EB%A0%B9",
        parse_ulex,
    )
    print(f"총 {total}개 청크 삽입 완료")


if __name__ == "__main__":
    main()
