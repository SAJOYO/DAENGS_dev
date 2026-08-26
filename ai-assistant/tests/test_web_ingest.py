from unittest.mock import MagicMock, patch

from app.repository import DocumentRecord
from app.services.web_ingest import extract_main_text, ingest_url

SAMPLE_HTML = """
<html>
<head><title>테스트 페이지</title></head>
<body>
  <nav>홈 소개 연락처</nav>
  <header>사이트 헤더</header>
  <script>console.log('무시되어야 함');</script>
  <article>
    <h1>강아지 건강 정보</h1>
    <p>첫 번째 문단입니다. 여기 진짜 본문이 있습니다.</p>
    <p>두 번째 문단입니다. 이것도 본문입니다.</p>
  </article>
  <footer>저작권 2026</footer>
</body>
</html>
"""


def test_extract_main_text_removes_nav_and_scripts():
    text = extract_main_text(SAMPLE_HTML)

    assert "진짜 본문" in text
    assert "홈 소개 연락처" not in text
    assert "console.log" not in text
    assert "저작권" not in text


def test_extract_main_text_prefers_article_tag():
    html = "<html><body><nav>메뉴</nav><article>본문 내용</article></body></html>"
    text = extract_main_text(html)
    assert text == "본문 내용"


def test_extract_main_text_removes_css_module_style_noise():
    # <nav>/<footer> 태그 없이 CSS 모듈 클래스명만 쓰는 실전 사이트(Next.js 등) 패턴을 재현.
    html = """
    <html><body>
      <div class="Breadcrumb_breadcrumbContainer__BtsRt">홈 > 강아지 > 케어</div>
      <article>
        <p>진짜 본문 내용입니다.</p>
      </article>
      <div class="Footer_copyrightWrap__5_9Jb">© 2026 Example Corp. All rights reserved.</div>
      <a class="TestYourKnowledge_link__fHmmM">Take a Quiz!</a>
    </body></html>
    """
    text = extract_main_text(html)

    assert "진짜 본문 내용입니다" in text
    assert "홈 > 강아지 > 케어" not in text
    assert "rights reserved" not in text.lower()
    assert "Take a Quiz" not in text


def test_extract_main_text_strips_stray_copyright_line():
    html = "<article>본문입니다. © 2026 Some Corp, Inc. All rights reserved. 그 다음 문장.</article>"
    text = extract_main_text(html)
    assert "rights reserved" not in text.lower()
    assert "본문입니다" in text
    assert "그 다음 문장" in text


@patch("app.services.web_ingest.insert_document")
@patch("app.services.web_ingest.embed_text")
@patch("app.services.web_ingest.fetch_html")
def test_ingest_url_fetches_chunks_and_stores(mock_fetch, mock_embed, mock_insert):
    mock_fetch.return_value = "<article>첫 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다.</article>"
    mock_embed.return_value = [0.1, 0.2]
    mock_insert.side_effect = lambda content, embedding, tag, url: DocumentRecord(
        id="fake-id", content=content, source_tag=tag, source_url=url, created_at=None
    )

    records = ingest_url("https://example.com/dog-care", source_tag="test", max_sentences=2, overlap_sentences=1)

    mock_fetch.assert_called_once_with("https://example.com/dog-care")
    assert len(records) >= 1
    # 모든 저장 호출에 원래 URL이 source_url로 전달됐는지 확인.
    for call in mock_insert.call_args_list:
        assert call.args[3] == "https://example.com/dog-care"
