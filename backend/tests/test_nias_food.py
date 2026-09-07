"""음식·사료 범주가 코퍼스에 들어오는 길 (RAG-065 / F1 · #268).

세 가지를 고정한다. 셋 다 **네트워크도 DB도 data/ 도 쓰지 않는다.**

  ① 소스 하나가 `policy` 와 `food` 를 같이 받을 수 있는가 (`Target.meta["category"]`)
  ② 탭 6장짜리 건강상식 페이지에서 **먹이 두 절만** 남는가
  ③ 증상 문장이 잘리고 **판단 문장은 남는가**

③ 이 이 카드에서 사람이 정한 경계다 — *"넣되 증상 문장은 자른다"* (2026-09-06).
"초콜릿 먹여도 되나요"에는 답하고, "먹었어요"는 `A3a` 의 `emergency` 가 거절한다.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from daengs_life.crawler.core import config, store
from daengs_life.crawler.core.fetch import FetchResult
from daengs_life.crawler.sources.base import Target
from daengs_life.crawler.sources.law.law_drf_api import LawDrfApi
from daengs_life.crawler.sources.registration.nias_pet import WANTED
from daengs_life.rag.core.io import RawDoc
from daengs_life.rag.stages.parse.parsers.registration import nias_pet as parser

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

CATEGORIES = {"policy", "travel", "food"}       # db/init/01_schema.sql 의 CHECK


# ------------------------------------------------------------------ ① 대상별 category

def _page(tabs: str) -> bytes:
    return textwrap.dedent(f"""\
        <html><body><section id="contents">
          <h2 class="pageTitle">반려견 건강상식</h2>
          <div class="tab_container">{tabs}</div>
        </section></body></html>
    """).encode()


def _tab(title: str, inner: str = "") -> str:
    return f'<div class="tab_content"><div><h3 class="subtit">{title}</h3>{inner}</div></div>'


class _Src:
    id, domain, category, subcategory = "nias-pet", "registration", "policy", "pet-life-guide"
    source_type, format, trust_level, license, org = "web", "html", "official", "", "국립축산과학원"


def _saved_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, meta: dict) -> dict:
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(config, "require_data_dir", lambda: None)
    monkeypatch.setattr(config, "redact", lambda u: u)
    res = FetchResult(url="https://x/y", final_url="https://x/y", status=200,
                      content=b"<html></html>", content_type="text/html", elapsed_sec=0.0)
    target = Target(url="https://x/y", slug="nias-pet-probe", ext="html", meta=meta)
    store.Store().save(_Src(), target, res, None)
    written = next((tmp_path / "raw" / "registration").glob("*.meta.json"))
    return json.loads(written.read_text(encoding="utf-8"))


def test_a_target_can_override_the_sources_category(tmp_path, monkeypatch) -> None:
    """사료 페이지는 `food` 로 간다 — 소스 클래스는 여전히 `policy` 인데도."""
    meta = _saved_meta(tmp_path, monkeypatch, {"category": "food", "subcategory": "pet-food"})
    assert meta["category"] == "food"
    assert meta["subcategory"] == "pet-food"


def test_without_an_override_the_source_default_still_wins(tmp_path, monkeypatch) -> None:
    """옛 7장은 아무것도 안 바뀐다. 이 줄이 없으면 되돌아보기 어려운 회귀가 조용히 난다."""
    meta = _saved_meta(tmp_path, monkeypatch, {"subcategory": "registration"})
    assert meta["category"] == "policy"


def test_every_declared_category_is_one_the_db_accepts() -> None:
    """`documents.category` 는 CHECK 가 걸린 열이라 없는 값을 넣으면 **적재가 죽는다.**

    오타 하나가 파이프라인 끝(적재)에서야 터지는 것을 여기서 막는다.
    """
    declared = {p.category for p in WANTED.values()} | {law.category for law in LawDrfApi.LAWS}
    assert declared <= CATEGORIES, f"CHECK 밖의 값: {sorted(declared - CATEGORIES)}"


def test_the_feed_laws_are_food_and_the_apartment_laws_are_not() -> None:
    by_slug = {law.slug: law for law in LawDrfApi.LAWS}
    assert [by_slug[s].category for s in
            ("feed-control-act", "feed-control-decree", "feed-control-rule")] == ["food"] * 3
    assert by_slug["apartment-mgmt-decree"].category == "policy"


def test_slugs_stay_unique_across_the_law_table() -> None:
    """slug 가 겹치면 raw/ 에서 한 법령이 다른 법령을 덮어쓴다 — 파일명이 곧 주소다."""
    slugs = [law.slug for law in LawDrfApi.LAWS]
    assert len(slugs) == len(set(slugs))


# ------------------------------------------------------------------ ② 절 선별

def _parse(raw: bytes):
    doc = RawDoc(meta={"source_id": "nias-pet", "document_title": "반려견 건강상식",
                       "source_url": "https://x/y"},
                 path=Path("raw/registration/nias-pet-dog-food__20260906.html"),
                 meta_path=Path("raw/registration/nias-pet-dog-food__20260906.meta.json"))
    return parser.parse(raw, doc)


def _texts(parsed) -> str:
    return " ".join(getattr(e, "text", "") for e in parsed.elements)


def test_only_the_two_food_sections_survive() -> None:
    raw = _page(_tab("예방접종 시기", "<p>1차(6주) 종합백신</p>")
                + _tab("반려견 수명(연령표)", "<p>평균 수명은 12년입니다.</p>")
                + _tab("올바른 먹이 선택 가이드", "<p>건식타입은 수분이 10%미만인 사료입니다.</p>")
                + _tab("급여해서는 안되는 음식", "<dl><dt>초콜릿</dt><dd>좋지 않습니다.</dd></dl>"))
    text = _texts(_parse(raw))
    assert "올바른 먹이 선택 가이드" in text and "급여해서는 안되는 음식" in text
    # roadmap §5 가 🚫 한 건강 상식이 따라 들어오지 않는다
    assert "예방접종" not in text and "종합백신" not in text
    assert "수명" not in text and "12년" not in text


def test_a_page_without_tabs_is_left_alone() -> None:
    """옛 7장과 「일반사료 구입 요령」은 탭이 없다. 선별 규칙이 그것들을 건드리면 안 된다."""
    raw = ("<html><body><section id='contents'><h2 class='pageTitle'>일반사료 구입 요령</h2>"
           "<h3 class='subtit'>사료 표시제도</h3>"
           "<p>「사료관리법」에서는 사료용기나 포장에 표시하도록 정하고 있습니다.</p>"
           "</section></body></html>").encode()
    assert "사료관리법" in _texts(_parse(raw))


def test_a_renamed_section_raises_instead_of_indexing_nothing() -> None:
    """절 제목이 바뀌면 **빈 문서를 조용히 적재하지 않는다.**

    빈 채로 흘러가면 "받았는데 답을 못 한다"로만 보이고 원인이 안 보인다.
    """
    raw = _page(_tab("예방접종 시기") + _tab("반려견 수명(연령표)"))
    with pytest.raises(RuntimeError, match="남길 절이 하나도 없다"):
        _parse(raw)


# ------------------------------------------------------------------ ③ 증상 절단

def test_the_verdict_stays_and_the_symptoms_go() -> None:
    kept = parser.cut_symptoms(
        "초콜릿이 강아지에게 좋지 않다는 사실은 많은 분들이 알고 계실 것입니다. "
        "초콜릿에 들어있는 독소는 테오브로민이라는 것인데, 이 성분으로 인하여 구토와 설사, "
        "갈증과 심장에 부정맥을 일으킬 수 있습니다. "
        "심한 경우 근육경련, 발작, 심장부정맥 등으로 강아지의 생명을 잃을 수도 있습니다."
    )
    assert kept == "초콜릿이 강아지에게 좋지 않다는 사실은 많은 분들이 알고 계실 것입니다."


def test_an_item_that_is_all_symptoms_keeps_nothing() -> None:
    """`포도, 건포도` 가 이렇다 — 두 문장이 다 증상이다.

    **이것이 정상이다.** 절 제목과 항목 이름이 남으므로 "포도 먹여도 되나요"에는 답한다.
    빈 자리를 채우려고 문장을 지어내지 않는다.
    """
    assert parser.cut_symptoms(
        "포도에는 신독성이 있어서 강아지에게 신부전을 일으켜, 포도 단 몇 알로 3~4시간 안에 "
        "강아지가 목숨을 잃을 수도 있습니다. "
        "증상으로는 구토와 설사가 나타나고, 무기력과 식욕감퇴의 증상이 나타나게 됩니다."
    ) == ""


def test_the_item_name_survives_even_when_its_body_is_cut() -> None:
    raw = _page(_tab("올바른 먹이 선택 가이드", "<p>건식과 습식으로 나뉩니다.</p>")
                + _tab("급여해서는 안되는 음식",
                       "<dl><dt>포도, 건포도</dt>"
                       "<dd>증상으로는 구토와 설사가 나타나고, 무기력이 나타나게 됩니다.</dd></dl>"))
    text = _texts(_parse(raw))
    assert "포도, 건포도" in text
    assert "구토" not in text and "설사" not in text


def test_the_food_choice_guide_is_not_touched_by_the_symptom_cut() -> None:
    """절단은 「급여해서는 안되는 음식」에만 건다. 먹이 가이드는 원문 그대로다."""
    raw = _page(_tab("올바른 먹이 선택 가이드",
                     "<dl><dt>흡수율</dt><dd>흡수율이 떨어진다면 영양불균형이 오게 됩니다.</dd></dl>")
                + _tab("급여해서는 안되는 음식", "<dl><dt>양파</dt><dd>독성작용이 일어납니다.</dd></dl>"))
    assert "영양불균형" in _texts(_parse(raw))
