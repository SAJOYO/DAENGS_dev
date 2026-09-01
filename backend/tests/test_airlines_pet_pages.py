"""항공사 반려동물 안내 — **사별 정규화 규칙** (RAG-046).

`data/raw/` 도 네트워크도 안 탄다 (RAG-030 ①). 아래 HTML 은 실물에서 이 테스트가 보는 부분만
남긴 것이다.

여기서 지키려는 것은 **착수 정찰에서 실제로 데인 셋**이다:
  ① 이스타 표에는 한/일/중/대만/태 다섯 언어가 한 셀에 쌓여 있다. 안 지우면 본문이 6,703자로
     부풀고 렉시컬 검색이 일본어를 친다
  ② 그런데 **영어는 지우면 안 된다** — 이스타의 운송 요금이 영어 문장에만 있다. 라틴 문자
     비율로 거르는 휴리스틱을 쓰면 요금과 공항 코드가 같이 날아간다
  ③ 에어프레미아는 Next.js 라 **요금·규격이 DOM 에 없고 i18n 페이로드에 있다.** DOM 만 지문으로
     삼으면 요금이 바뀌어도 `same` 으로 끝난다 — 받았는데 개정을 못 잡는 조용한 실패다
"""
from __future__ import annotations

import pytest

from daengs_life.crawler.core import nextpayload
from daengs_life.crawler.core.fetch import FetchResult
from daengs_life.crawler.sources.base import Target
from daengs_life.crawler.sources.transport import airlines_pet_pages as air

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# 이스타 — 한 셀에 다섯 언어가 쌓인 모양 그대로. 클래스의 언어 접미사가 유일한 단서다.
EASTAR_HTML = """<html><body><article class="wrap">
  <h3>반려동물을 동반하는 고객</h3>
  <table><caption>반려동물을 동반하는 고객</caption>
    <tr><th>구분</th><td>내용</td></tr>
    <tr><th>운송 가능 노선</th>
      <td>
        <span class="PNWIM00004_KR">일본 : 도쿄 (나리타, NRT), 오사카 (간사이, KIX)</span>
        <span class="PNWIM00004_JP">日本:東京/成田（NRT）、大阪/関西（KIX）</span>
        <span class="PNWIM00004_CN">日本航线：东京（成田，NRT）</span>
        <span class="PNWIM00004_TW">日本航線：東京（成田，NRT）</span>
        <span class="PNWIM00004_TH">เส้นทางสู่ญี่ปุ่น : โทเคียว</span>
        <span>Routes to Japan : ToKyo (Narita, NRT)</span>
      </td></tr>
    <tr><th>반입 기준</th><td>반려동물과 운송용기 무게의 총합이 9kg 이내</td></tr>
    <tr><th>운송 요금</th>
      <td>1 passenger (1 pet) / KRW 30,000 per segment
          Japan : KRW 120,000 / USD 120 / JPY 12,000</td></tr>
  </table>
</article></body></html>"""

# 에어프레미아 — 표는 **머리만** 있고 값은 RSC 페이로드에 있다. 실물의 이스케이프를 그대로 흉내낸다
# (`\\"key\\":\\"value\\"` · `\\u003cbr /\\u003e`).
AIRPREMIA_HTML = """<html><body>
  <h1>반려동물 동반 손님</h1>
  <div id="need_pet_panel1">
    <h2>운송 가능한 동물</h2><p>개, 고양이, 새</p>
    <h2>요금 안내</h2>
    <table><tr><th>32 kg 이하</th><th>33 kg ~ 45 kg</th></tr></table>
  </div>
  <div id="need_pet_panel2"><h2>시·청각 장애인 안내견</h2><p>보조견 안내</p></div>
  <script>self.__next_f.push([1,"{\\"need_pet_fare_32kg_nea_kr\\":\\"KRW 130,000\\",
   \\"need_pet_fare_33kg_useu_kr\\":\\"KRW 580,000\\",
   \\"need_pet_guide_1_2_onboard_dscrpt2\\":\\"가로 38cm이하\\u003cbr /\\u003e높이 23cm\\",
   \\"need_pet_popup_ferocious_species\\":\\"도사견, 핏불 테리어\\",
   \\"need_pet_fare_32kg_nea_kr\\":\\"KRW 999,999\\"}"])</script>
</body></html>"""


def result(html: str) -> FetchResult:
    return FetchResult(url="https://x/", final_url="https://x/", status=200,
                       content=html.encode("utf-8"), content_type="text/html", elapsed_sec=0.1)


def target(key: str, container: str, *, payload: bool, title: str = "제목") -> Target:
    return Target(url="https://x/", slug=f"airlines-pet-pages-{key}", ext="html",
                  meta={"title": title, "airline_key": key,
                        "container": container, "payload": payload})


def extract(html: str, tgt: Target):
    return air.AirlinesPetPages({"id": "airlines-pet-pages"}).extract(result(html), tgt)


# ------------------------------------------------------- ① 다국어 정리 (이스타)

def test_foreign_language_blocks_are_dropped() -> None:
    """일·중·대만·태는 지운다. **한국어(`_KR`)는 접미사가 있어도 남는다.**"""
    text = extract(EASTAR_HTML, target("eastar", "article.wrap", payload=False)).text
    assert "일본 : 도쿄 (나리타, NRT)" in text            # _KR — 남아야 한다
    assert "日本" not in text and "เส้นทาง" not in text     # _JP · _TH — 지워져야 한다
    assert "日本航线" not in text and "日本航線" not in text   # _CN · _TW


def test_english_survives_because_the_fare_lives_only_there() -> None:
    """**이 테스트가 이 파일의 핵심이다.** 영어를 지우면 요금이 통째로 사라진다.

    라틴 문자 비율로 거르고 싶어지는 모양인데, 그러면 `KRW 30,000` 과 공항 코드가 같이 날아간다.
    그래서 **명시적으로 표시된 것만** 지운다.
    """
    text = extract(EASTAR_HTML, target("eastar", "article.wrap", payload=False)).text
    assert "KRW 30,000 per segment" in text
    assert "USD 120" in text and "JPY 12,000" in text
    assert "Routes to Japan" in text                      # 접미사가 없는 영어 블록


def test_korean_only_measurements_survive() -> None:
    text = extract(EASTAR_HTML, target("eastar", "article.wrap", payload=False)).text
    assert "9kg 이내" in text


# ------------------------------------------- ② i18n 페이로드 (에어프레미아)

def test_the_fare_comes_from_the_payload_not_the_dom() -> None:
    """DOM 의 표에는 머리(`32 kg 이하`)만 있고 금액이 없다. 페이로드를 안 읽으면 못 받는다."""
    tgt = target("airpremia", "#need_pet_panel1", payload=True)
    dom_only = extract(AIRPREMIA_HTML, target("airpremia", "#need_pet_panel1", payload=False))
    full = extract(AIRPREMIA_HTML, tgt)

    assert "KRW 130,000" not in dom_only.text            # DOM 만 읽으면 금액이 없다
    assert "KRW 130,000" in full.text and "KRW 580,000" in full.text
    assert "가로 38cm이하" in full.text                    # \\u003cbr /\\u003e 가 풀려야 보인다
    assert "도사견, 핏불 테리어" in full.text


def test_payload_tags_and_unicode_escapes_are_unwrapped() -> None:
    values = nextpayload.values(AIRPREMIA_HTML, air.PAYLOAD_PREFIX)
    joined = "\n".join(values)
    assert "\\u003c" not in joined and "<br" not in joined


def test_duplicate_payload_keys_keep_the_first_value() -> None:
    """같은 키가 두 번 나오면 첫 것만 쓴다 — 그래야 지문이 흔들리지 않는다."""
    values = nextpayload.values(AIRPREMIA_HTML, air.PAYLOAD_PREFIX)
    assert "KRW 130,000" in values and "KRW 999,999" not in values


def test_payload_order_is_document_order() -> None:
    """정렬하거나 set 으로 돌리면 사이트가 안 바뀌어도 지문이 흔들린다."""
    values = nextpayload.values(AIRPREMIA_HTML, air.PAYLOAD_PREFIX)
    assert values.index("KRW 130,000") < values.index("KRW 580,000")


def test_the_service_animal_tab_is_not_collected() -> None:
    """두 탭을 합치면 `h2` 가 겹쳐 같은 제목의 청크가 둘 생긴다."""
    text = extract(AIRPREMIA_HTML, target("airpremia", "#need_pet_panel1", payload=True)).text
    assert "시·청각 장애인 안내견" not in text and "보조견 안내" not in text


# ------------------------------------------------------------- ③ 경고 신호

def test_a_missing_container_is_reported_not_guessed() -> None:
    got = extract(EASTAR_HTML, target("eastar", "#없는것", payload=False))
    assert got.text == "" and "컨테이너" in got.extra["warning"]


def test_a_thin_payload_warns_but_still_keeps_the_dom() -> None:
    """페이로드 규격이 바뀌어도 DOM 만이라도 받아 둔다 — 옛 원본만 남는 것보다 낫다."""
    got = extract(AIRPREMIA_HTML, target("airpremia", "#need_pet_panel1", payload=True))
    assert got.extra["payload_keys"] == 4 < air.AIRPREMIA_MIN_PAYLOAD
    assert "페이로드" in got.extra["warning"]
    assert "운송 가능한 동물" in got.text                   # DOM 은 살아 있다


def test_a_table_less_page_warns_where_the_numbers_live_in_tables() -> None:
    html = "<html><body><article class='wrap'><h3>반려동물</h3><p>표가 사라졌다</p></article></body></html>"
    got = extract(html, target("eastar", "article.wrap", payload=False))
    assert got.extra["tables"] == 0 and "표가 없다" in got.extra["warning"]


# --------------------------------------------------------------- ④ 제목 규칙

def test_the_title_comes_from_the_adapter_not_the_page_heading() -> None:
    """에어프레미아의 `h1` 은 패널 밖이라 페이지 머리글을 쓰면 `운송 가능한 동물` 이 잡힌다."""
    got = extract(AIRPREMIA_HTML,
                  target("airpremia", "#need_pet_panel1", payload=True,
                         title="에어프레미아 반려동물 동반 손님"))
    assert got.title == "에어프레미아 반려동물 동반 손님"
    assert got.extra["page_heading"] == "운송 가능한 동물"   # 바뀌면 보이도록 남긴다


# ----------------------------------------------------- ⑤ 이스타 페이지 판정

def test_the_pinned_page_is_verified_by_content() -> None:
    """URL 을 박아 두되 내용으로 검증한다 — 개편 뒤 옛 페이지를 계속 받는 것을 막는다."""
    class FakeFetcher:
        def __init__(self, html: str) -> None:
            self.html = html

        def get(self, _url: str) -> FetchResult:
            return result(self.html)

    # `article.wrap` **하나** 안에 실물만큼(4,395자) 들어 있어야 통과한다 —
    # 문서를 여러 개 이어 붙이면 `select_one` 이 첫 것만 보므로 그 방식으로는 못 만든다
    pet = ("<html><body><article class='wrap'><h3>반려동물을 동반하는 고객</h3>"
           + "<p>반려동물과 운송용기 무게의 총합이 9kg 이내입니다. </p>" * 120
           + "</article></body></html>")
    assert air._looks_like_pet_page(FakeFetcher(pet), "https://x/") is True

    # 반려동물 안내가 아닌 페이지 (길이는 충분하지만 표식이 없다)
    other = ("<html><body><article class='wrap'>"
             + "<p>지정 좌석 구매안내입니다. </p>" * 200 + "</article></body></html>")
    assert air._looks_like_pet_page(FakeFetcher(other), "https://x/") is False

    # 길이만 모자라도 아니다 — 페이지가 껍데기가 된 경우
    thin = "<html><body><article class='wrap'><h3>반려동물</h3></article></body></html>"
    assert air._looks_like_pet_page(FakeFetcher(thin), "https://x/") is False


# ═════════════════════════════════════════════════════════════ 파서 (parse 단계)
#
# 크롤러가 원본을 저장하고, 파서는 **그 원본을 다시 읽어** IR 을 만든다. 그래서 같은 판단을
# 양쪽이 해야 하고, 어긋나면 지문과 본문이 다른 것을 가리킨다.

from daengs_life.rag.core.io import RawDoc                                    # noqa: E402
from daengs_life.rag.stages.parse.parsers.transport import (                  # noqa: E402
    airlines_pet_pages as parser,
)


def rawdoc(url: str, title: str) -> RawDoc:
    from pathlib import Path
    return RawDoc(meta={"source_url": url, "document_title": title,
                        "source": "국적 항공사 9개", "source_id": "airlines-pet-pages"},
                  path=Path("transport/airlines-pet-pages-x__20260829.html"),
                  meta_path=Path("x.meta.json"))


def test_parser_and_crawler_share_the_language_rule() -> None:
    """**두 벌이 어긋나면 여기서 잡는다.** 크롤러가 지운 언어를 파서가 안 지우면 지문에는 없는
    일본어가 코퍼스에는 들어간다 — 어느 쪽 로그에도 안 보이는 어긋남이다.
    """
    assert parser.FOREIGN_SUFFIXES == air.FOREIGN_SUFFIXES
    assert parser.PAYLOAD_PREFIX == air.PAYLOAD_PREFIX
    assert parser.EASTAR_CONTAINER == air.EASTAR_CONTAINER
    assert parser.AIRPREMIA_PANEL == air.AIRPREMIA_PANEL


def test_the_airline_is_told_apart_by_host_not_by_a_new_meta_field() -> None:
    """`.meta.json` 에는 `airline_key` 가 없다 — 호스트는 원본 안에 이미 있는 사실이다."""
    assert parser._airline_key(rawdoc("https://www.airpremia.com/a/ko/x", "t")) == "airpremia"
    assert parser._airline_key(rawdoc("https://www.eastarjet.com/newstar/x", "t")) == "eastar"
    with pytest.raises(RuntimeError, match="모르는 항공사"):
        parser.parse(b"<html></html>", rawdoc("https://www.koreanair.com/", "t"))


def test_the_fare_table_is_rebuilt_as_region_by_weight() -> None:
    """평문으로 흘리면 `KRW 580,000` 이 어느 칸의 값인지가 문장 안에서 사라진다."""
    got = parser.parse(AIRPREMIA_HTML.encode("utf-8"),
                       rawdoc("https://www.airpremia.com/a/ko/support/need/pet",
                              "에어프레미아 반려동물 동반 손님"))
    tables = [e for e in got.elements if e.type == "table"]
    assert len(tables) == 1
    t = tables[0]
    assert t.header == ["구분", "32 kg 이하", "33 kg ~ 45 kg"]
    assert ["동북아 · 한국 출발", "KRW 130,000", ""] in [r for r in t.rows]
    assert ["미주 · 한국 출발", "", "KRW 580,000"] in [r for r in t.rows]


def test_an_unknown_region_code_is_reported_not_silently_dropped() -> None:
    """`REGIONS` 에 없는 구간은 표에서 통째로 빠진다. 조용히 빠지면 요금 한 줄이 사라진다."""
    html = AIRPREMIA_HTML.replace(
        '\\"need_pet_fare_33kg_useu_kr\\":\\"KRW 580,000\\"',
        '\\"need_pet_fare_33kg_afr_kr\\":\\"KRW 700,000\\"')
    got = parser.parse(html.encode("utf-8"),
                       rawdoc("https://www.airpremia.com/x", "에어프레미아"))
    assert any("afr" in w for w in got.warnings)


def test_the_service_animal_tab_is_dropped_on_the_payload_side_too() -> None:
    """DOM 에서는 `#need_pet_panel2` 를 빼는데 페이로드에는 그 탭 문구도 실려 있다.

    여기서 안 빼면 **DOM 과 페이로드가 서로 다른 문서를 만든다.**
    """
    html = AIRPREMIA_HTML.replace(
        '\\"need_pet_popup_ferocious_species\\":\\"도사견, 핏불 테리어\\"',
        '\\"need_pet_help_guide_dscprt1\\":\\"보조서비스를 제공합니다\\"')
    got = parser.parse(html.encode("utf-8"), rawdoc("https://www.airpremia.com/x", "에어프레미아"))
    assert "보조서비스를 제공합니다" not in "\n".join(
        e.text for e in got.elements if e.type == "para")


def test_an_unknown_key_is_kept_not_thrown_away() -> None:
    """새 절이 생겼을 때 조용히 사라지면 다음 개정에서 그 절만 코퍼스에 없는 상태가 된다."""
    html = AIRPREMIA_HTML.replace(
        '\\"need_pet_popup_ferocious_species\\":\\"도사견, 핏불 테리어\\"',
        '\\"need_pet_brandnew_thing\\":\\"새로 생긴 안내\\"')
    got = parser.parse(html.encode("utf-8"), rawdoc("https://www.airpremia.com/x", "에어프레미아"))
    assert "새로 생긴 안내" in "\n".join(e.text for e in got.elements if e.type == "para")
    assert any("기타 안내" in w for w in got.warnings)


def test_the_screen_order_puts_the_heading_before_its_description() -> None:
    """페이로드 순서가 화면 순서가 아니다 — 그대로 두면 설명 뒤에 제목이 온다."""
    keys = ["need_pet_guide_1_1_dscrpt1", "need_pet_guide_1_1",
            "need_pet_guide_1_2_cargo_a", "need_pet_guide_1_2_onboard_a",
            "need_pet_guide_1_10", "need_pet_guide_1_2"]
    ordered = sorted(keys, key=parser._screen_order)
    assert ordered.index("need_pet_guide_1_1") < ordered.index("need_pet_guide_1_1_dscrpt1")
    # 화면은 기내 → 위탁 순인데 알파벳으로는 cargo 가 앞이다
    assert (ordered.index("need_pet_guide_1_2_onboard_a")
            < ordered.index("need_pet_guide_1_2_cargo_a"))
    # `_10` 은 `_2` 보다 뒤다 (문자열 비교면 앞으로 온다)
    assert ordered.index("need_pet_guide_1_2") < ordered.index("need_pet_guide_1_10")


def test_the_eastar_header_borrows_names_but_keeps_the_row() -> None:
    """`['구분','내용','내용']` 이면 청크에 `내용: … 내용: …` 이 찍혀 국내선인지 국제선인지 사라진다.

    ⚠️ 이름만 빌려 오고 **행은 지우지 않는다** — 그 행에 `위탁 운송 불가` 가 붙어 있다.
    """
    header = ["구분", "내용", "내용"]
    rows = [["운송 가능 노선", "국내선 (기내 반입만 가능, 위탁 운송 불가)", "국제선 (기내 반입만)"]]
    assert parser._eastar_header(header, rows) == ["구분", "국내선", "국제선"]
    assert parser._eastar_header(["구분", "국내선", "국제선"], rows) == ["구분", "국내선", "국제선"]


def test_the_eastar_table_title_does_not_repeat_the_document_title() -> None:
    """캡션이 문서 제목과 같아서 청크 머리가 겹친다 — 겹친 말은 검색에 보탬이 없다."""
    got = parser.parse(EASTAR_HTML.encode("utf-8"),
                       rawdoc("https://www.eastarjet.com/newstar/PGWIM00004",
                              "이스타항공 반려동물을 동반하는 고객"))
    table = [e for e in got.elements if e.type == "table"][0]
    assert table.title not in got.document_title


def test_the_title_does_not_get_the_seed_org_glued_on() -> None:
    """시드의 `org` 는 `국적 항공사 9개` 라 `with_org` 를 쓰면 제목이 망가진다."""
    got = parser.parse(EASTAR_HTML.encode("utf-8"),
                       rawdoc("https://www.eastarjet.com/x", "이스타항공 반려동물을 동반하는 고객"))
    assert got.document_title == "이스타항공 반려동물을 동반하는 고객"
