"""서울시 고시공고 — 한 요청으로 훑고, 한 행짜리 응답을 원본으로 (RAG-053).

네트워크도 `data/` 도 안 탄다. 응답 JSON 은 실물(2026-08-30, `tvvWcmBoardB0277New`)에서 이 테스트가
보는 필드만 남긴 것이다 — `BOARD_ID` 가 `464874.0` 처럼 실수로 오는 것까지 그대로.

지키려는 것은 정찰에서 실제로 데인 셋이다:
  ① 필터 인자가 없어 **여기서 거른다** — 복합 키워드에 걸린 행만 Target 이 되고, 걸린 이유가 남는다
  ② 원본은 `{i}/{i}` 슬라이스라 인덱스가 밀리면 엉뚱한 글이 온다 — `extract()` 가 `BOARD_ID` 를 대조한다
  ③ 응답에 `list_total_count` 가 있어 바이트 지문이면 매일 `changed` 다 — 텍스트 지문이어야 한다
"""
from __future__ import annotations

import json

import pytest

from daengs_life.crawler.core import config, store
from daengs_life.crawler.core.fetch import FetchResult
from daengs_life.crawler.sources.base import Target
from daengs_life.crawler.sources.subsidy import _pet_keywords, seoul_notice_api as sn


def row(board_id: int, title: str, contents: str = "", **extra) -> dict:
    return {"BOARD_ID": float(board_id), "TITLE": title, "CONTENTS": contents,
            "START_DATE": "2026-08-28", "END_DATE": "2026-09-11", "ORGAN": "서울시",
            "TEL": "02-2133-0000", "CREATE_DATE": "2026-08-28", "UPDATE_DATE": "",
            "DEADLINE_DATE": extra.pop("deadline", ""), "FILE_URL1": extra.pop("file", ""),
            "FILE_URL2": "", "FILE_URL3": "", "FILE_URL4": "", "FILE_URL5": ""}


def payload(rows: list[dict], *, total: int = 12133) -> bytes:
    return json.dumps({sn.SERVICE: {"list_total_count": total,
                                    "RESULT": {"CODE": "INFO-000", "MESSAGE": "정상 처리되었습니다"},
                                    "row": rows}}, ensure_ascii=False).encode("utf-8")


def result(content: bytes, url: str = "http://x/", status: int = 200) -> FetchResult:
    return FetchResult(url=url, final_url=url, status=status, content=content,
                       content_type="application/json", elapsed_sec=0.1)


class FakeFetcher:
    def __init__(self, content: bytes) -> None:
        self.content, self.urls = content, []

    def get(self, url: str) -> FetchResult:
        self.urls.append(url)
        return result(self.content, url)


ROWS = [
    row(464874, "계약직 직원 채용 재공고"),
    row(464873, "건설업 폐업신고 처리내역 공고"),
    row(455524, "2026년 반려동물 항생제 내성균 모니터링사업 참여 모집 공고",
        deadline="2026-04-30", file="https://seoulboard.seoul.go.kr/comm/getFile?upperNo=455524"),
    row(455000, "수의사 채용 공고", "동물등록 업무 담당"),          # 본문에만 걸린다
    row(454000, "민원 반려 처리 안내"),                              # 단독 '반려' — 걸리면 안 된다
]


@pytest.fixture
def src(monkeypatch: pytest.MonkeyPatch) -> sn.SeoulNoticeApi:
    monkeypatch.setattr(config, "SEOUL_OPEN_DATA_KEY", "k" * 32)
    return sn.SeoulNoticeApi({"id": "seoul-notice-api", "domain": "subsidy", "org": "서울특별시"})


# ------------------------------------------------------------------ discover

def test_one_request_then_filter_here(src) -> None:
    """① 목록은 **한 요청**(1/WINDOW)이고, 걸린 행만 Target 이 된다. 걸린 이유가 meta 에 남는다."""
    f = FakeFetcher(payload(ROWS))
    targets = src.discover(f)
    assert f.urls == [f"{sn.BASE}/{'k' * 32}/json/{sn.SERVICE}/1/{sn.WINDOW}/"]
    assert [t.meta["board_id"] for t in targets] == ["455524", "455000"]
    assert targets[0].meta["matched_by"] == ["반려동물"]
    assert targets[1].meta["matched_by"] == ["동물등록"]            # 제목이 아니라 본문에서


def test_target_is_the_single_row_slice(src) -> None:
    """② 원본 URL 은 그 행의 `{i}/{i}` 슬라이스다 — 인덱스는 목록에서의 위치(1부터)."""
    t = src.discover(FakeFetcher(payload(ROWS)))[0]
    assert t.url.endswith(f"/{sn.SERVICE}/3/3/") and t.meta["index"] == 3
    assert t.slug == "seoul-notice-api-455524" and t.ext == "json"
    assert t.meta["citation_url"] == f"{sn.NOTICE_PAGE}/455524"


def test_the_lone_word_banryeo_does_not_match() -> None:
    """RAG-034 — 返戾(신청 반려)와 겹치는 단독 `반려` 는 키워드가 아니다."""
    assert _pet_keywords.matches("민원 반려 처리 안내") == []
    assert all(len(k) >= 3 for k in _pet_keywords.KEYWORDS)


def test_order_assumption_is_checked_every_run(src) -> None:
    """정렬이 깨지면 슬라이스가 엉뚱한 글을 가리킨다 — 조용히 넘어가지 않는다."""
    with pytest.raises(RuntimeError, match="내림차순"):
        src.discover(FakeFetcher(payload(list(reversed(ROWS)))))


def test_key_missing_says_where_to_get_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SEOUL_OPEN_DATA_KEY", "")
    with pytest.raises(RuntimeError, match="SEOUL_OPEN_DATA_KEY"):
        sn.SeoulNoticeApi({"id": "seoul-notice-api", "domain": "subsidy"}).discover(FakeFetcher(b""))


def test_api_error_is_spelled_out(src) -> None:
    """실패 응답은 서비스명 키가 없고 `RESULT` 만 온다. 코드를 사람 말로 옮긴다."""
    body = json.dumps({"RESULT": {"CODE": "ERROR-310", "MESSAGE": "해당하는 서비스를 찾을 수 없습니다."}}).encode()
    with pytest.raises(RuntimeError, match="ERROR-310"):
        src.discover(FakeFetcher(body))


# ------------------------------------------------------------------- extract

def _target(board_id: str = "455524") -> Target:
    return Target(url="http://x/3/3/", slug=f"seoul-notice-api-{board_id}", ext="json",
                  meta={"board_id": board_id, "citation_url": f"{sn.NOTICE_PAGE}/{board_id}",
                        "matched_by": ["반려동물"]})


def test_extract_reads_the_row_and_keeps_attachments_as_urls(src) -> None:
    ext = src.extract(result(payload([ROWS[2]])), _target())
    assert ext.title.startswith("2026년 반려동물")
    assert ext.published_at == "2026-08-28"
    assert "공고마감일: 2026-04-30" in ext.text and "내용:" not in ext.text   # 본문이 비면 줄을 안 만든다
    assert ext.extra["attachments"] == ["https://seoulboard.seoul.go.kr/comm/getFile?upperNo=455524"]
    assert ext.extra["citation_url"].endswith("#view/455524")


def test_a_shifted_index_fails_loudly(src) -> None:
    """② 새 글이 올라와 인덱스가 밀리면 다른 공고가 온다. 저장하지 않고 죽는다."""
    with pytest.raises(RuntimeError, match="BOARD_ID 가 다르다"):
        src.extract(result(payload([ROWS[1]])), _target("455524"))


def test_slice_must_be_exactly_one_row(src) -> None:
    with pytest.raises(RuntimeError, match="한 행이어야"):
        src.extract(result(payload(ROWS[:2])), _target())


# ---------------------------------------------------------------- fingerprint

def test_fingerprint_is_text_so_total_count_noise_does_not_mean_changed(src) -> None:
    """③ 같은 공고인데 `list_total_count` 만 다른 두 응답 — 지문이 같아야 한다."""
    assert src.fingerprint == "text"
    a, b = payload([ROWS[2]], total=12133), payload([ROWS[2]], total=12140)
    assert a != b
    ea, eb = src.extract(result(a), _target()), src.extract(result(b), _target())
    assert store.sha256_text(ea.text) == store.sha256_text(eb.text)


def test_store_honours_the_text_fingerprint(src, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """`store.save` 가 그 속성을 실제로 본다 — 두 번째 저장이 `same` 이어야 한다."""
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(config, "CRAWL_LOG", tmp_path / "crawl_log.jsonl")
    st = store.Store()
    t = _target()
    first = st.save(src, t, result(payload([ROWS[2]], total=12133)),
                    src.extract(result(payload([ROWS[2]], total=12133)), t))
    second = st.save(src, t, result(payload([ROWS[2]], total=12140)),
                     src.extract(result(payload([ROWS[2]], total=12140)), t))
    assert first.changed is True and second.changed is False
