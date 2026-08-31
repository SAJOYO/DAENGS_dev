"""개정 판정 — "바뀜"과 "개정"을 가른다 (RAG-054).

네트워크도 실제 `data/` 도 안 탄다. `discover()` 를 흉내 낸 가짜 소스와 tmp 의 meta 파일로
두 모드를 본다:

  published_at  같은 slug 의 시행일자가 달라지면 개정 — 원본을 받지 않고 discover 만으로 안다
  slug          같은 제목의 새 slug 가 옛 slug 를 대체 — 같은 slug 의 바이트 차이는 개정이 아니다
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from daengs_life.crawler.core import config, revision
from daengs_life.crawler.sources.base import Extracted, Source, Target


def meta(raw: Path, domain: str, slug: str, day: str, *, published_at: str | None, title: str) -> None:
    d = raw / domain
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}__{day}.meta.json").write_text(json.dumps({
        "source_id": slug.split("-law")[0] if "-law" in slug else "x",
        "document_title": title, "published_at": published_at,
        "raw_file": f"{domain}/{slug}__{day}.html", "sha256": "0" * 64,
    }, ensure_ascii=False), encoding="utf-8")


class LawLike(Source):
    """law.go.kr 웹 원문 흉내 — discover 가 시행일자를 meta 로 들고 온다."""
    id = "law-x"; domain = "law"; category = "policy"; subcategory = "act"
    source_type = "web"; format = "html"; trust_level = "law"
    revision_key = "published_at"
    TARGETS: list[tuple[str, str, str]] = []          # (slug 접미사, 제목, efYd)

    def discover(self, fetcher):
        return [Target(url=f"https://x/{s}", slug=f"{self.id}-{s}", ext="html",
                       meta={"title": t, "efYd": ef}) for s, t, ef in self.TARGETS]

    def extract(self, res, target):                   # pragma: no cover — 여기서는 안 받는다
        return Extracted(title="", text="")


class TermsLike(Source):
    """약관 흉내 — 판이 slug 에 박힌다."""
    id = "terms-x"; domain = "insurance"; category = "policy"; subcategory = "insurance"
    source_type = "document"; format = "pdf"; trust_level = "official"
    revision_key = "slug"

    def discover(self, fetcher):                      # pragma: no cover
        return []

    def extract(self, res, target):                   # pragma: no cover
        return Extracted(title="", text="")


@pytest.fixture
def raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    return tmp_path


# ------------------------------------------------------------ published_at 모드

def test_probe_reads_only_discover_and_compares_the_effective_date(raw: Path) -> None:
    """개정 = 같은 slug 의 시행일자가 달라졌다. 새 것은 new, 같으면 same. **원본은 안 받는다.**"""
    meta(raw, "law", "law-x-act", "20260827", published_at="2026-07-07", title="동물보호법")
    meta(raw, "law", "law-x-decree", "20260827", published_at="2026-06-03", title="시행령")
    src = LawLike({"id": "law-x", "domain": "law"})
    src.TARGETS = [("act", "동물보호법", "20261001"),        # 시행일자가 밀렸다 → 개정
                   ("decree", "시행령", "20260603"),          # 그대로
                   ("rule", "시행규칙", "20260603")]          # 저장된 적 없다
    got = {v.slug: v for v in revision.probe(src, fetcher=None)}
    assert got["law-x-act"].kind == "revised" and got["law-x-act"].previous == "2026-07-07" \
        and got["law-x-act"].current == "2026-10-01"
    assert got["law-x-decree"].kind == "same"
    assert got["law-x-rule"].kind == "new" and got["law-x-rule"].previous is None


def test_date_formats_are_normalised_before_comparing(raw: Path) -> None:
    """`20260707` 과 `2026-07-07` 은 같은 판이다 — 형식 차이로 개정이 뜨면 매일 울린다."""
    meta(raw, "law", "law-x-act", "20260827", published_at="2026-07-07", title="동물보호법")
    src = LawLike({"id": "law-x", "domain": "law"}); src.TARGETS = [("act", "동물보호법", "20260707")]
    assert revision.probe(src, None)[0].kind == "same"


def test_the_latest_meta_wins(raw: Path) -> None:
    """같은 slug 의 meta 가 여럿이면 **가장 최근 것**과 대조한다 — 옛 판과 비교하면 매번 개정이다."""
    meta(raw, "law", "law-x-act", "20260501", published_at="2026-01-01", title="동물보호법")
    meta(raw, "law", "law-x-act", "20260827", published_at="2026-07-07", title="동물보호법")
    src = LawLike({"id": "law-x", "domain": "law"}); src.TARGETS = [("act", "동물보호법", "20260707")]
    assert revision.probe(src, None)[0].kind == "same"


def test_sources_without_a_revision_key_are_not_judged(raw: Path) -> None:
    """근거 없는 소스를 억지로 판정하면 오탐만 남는다 — 빈 목록이다."""
    src = LawLike({"id": "law-x", "domain": "law"}); src.revision_key = None
    src.TARGETS = [("act", "동물보호법", "20260707")]
    assert revision.probe(src, None) == []


def test_probe_sources_takes_only_manual_cadence_and_isolates_failures(
        raw: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """주기가 있는 소스는 주기에 받으니 여기서 안 본다. 한 소스가 죽어도 나머지는 본다."""
    from daengs_life.crawler.core import registry

    class Boom(LawLike):
        id = "law-boom"
        def discover(self, fetcher):
            raise RuntimeError("법제처가 점검 중")

    class Fine(LawLike):
        id = "law-fine"
        TARGETS = [("act", "동물보호법", "20260707")]

    seeds = {
        "law-boom": {"id": "law-boom", "domain": "law", "method": "html", "status": "verified"},
        "law-fine": {"id": "law-fine", "domain": "law", "method": "html", "status": "verified"},
        "weekly-x": {"id": "weekly-x", "domain": "subsidy", "method": "html", "status": "verified"},
        "dead-x": {"id": "dead-x", "domain": "law", "method": "html", "status": "not-found"},
    }
    monkeypatch.setattr(registry, "resolve",
                        lambda s: {"law-boom": Boom, "law-fine": Fine, "weekly-x": Fine, "dead-x": Fine}[s["id"]])
    monkeypatch.setattr(revision, "Fetcher", lambda: _NullCtx())
    got = revision.probe_sources(seeds)
    assert set(got) == {"law-boom", "law-fine"}          # weekly 와 not-found 는 안 본다
    assert got["law-boom"] == []                          # 죽은 소스는 빈 목록, 예외는 안 올라온다
    assert [v.kind for v in got["law-fine"]] == ["new"]


class _NullCtx:
    def __enter__(self): return None
    def __exit__(self, *a): return False


# ------------------------------------------------------------------ slug 모드

def test_a_new_slug_with_the_same_title_supersedes_the_old_one(raw: Path) -> None:
    """약관: 같은 상품의 새 판 slug 가 나타났다 → 옛 slug 가 대체됐다."""
    meta(raw, "insurance", "terms-x-samsung-ZPB316050_0_20240401", "20260101",
         published_at="2024-04-01", title="위풍댕댕")
    meta(raw, "insurance", "terms-x-samsung-ZPB316090_0_20260701", "20260828",
         published_at="2026-07-01", title="위풍댕댕")
    meta(raw, "insurance", "terms-x-samsung-KR1508P_0_20260101", "20260828",
         published_at="2026-01-01", title="반려견보험 애니펫")
    src = TermsLike({"id": "terms-x", "domain": "insurance"})
    got = revision.superseded(src, ["terms-x-samsung-ZPB316090_0_20260701", "terms-x-samsung-KR1508P_0_20260101"])
    assert [(v.kind, v.previous, v.current, v.title) for v in got] == [
        ("superseded", "terms-x-samsung-ZPB316050_0_20240401", "terms-x-samsung-ZPB316090_0_20260701", "위풍댕댕")]


def test_same_slug_changed_bytes_is_not_a_revision(raw: Path) -> None:
    """같은 판을 다시 생성한 PDF(바이트만 다름)는 changed 지만 개정이 아니다 — new_slugs 가 비면 판정이 없다."""
    meta(raw, "insurance", "terms-x-samsung-KR1508P_0_20260101", "20260828",
         published_at="2026-01-01", title="반려견보험 애니펫")
    src = TermsLike({"id": "terms-x", "domain": "insurance"})
    assert revision.superseded(src, []) == []


def test_slug_mode_ignores_published_at_sources(raw: Path) -> None:
    src = LawLike({"id": "law-x", "domain": "law"})
    assert revision.superseded(src, ["law-x-act"]) == []
