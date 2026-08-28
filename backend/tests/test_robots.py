"""robots.txt 판정 — 표준(RFC 9309 §2.2.2)의 longest-match.

`urllib.robotparser` 는 **파일에 먼저 적힌 규칙**이 이긴다. 표준은 **가장 긴 패턴**이 이긴다.
정부24 가 정확히 그 차이에 걸려서(§2.2.2 대로면 열려 있는 경로를 urllib 은 막는다) 직접 구현했고,
이 테스트가 그 차이를 고정한다.
"""
from __future__ import annotations

from daengs_life.crawler.core.fetch import Robots

UA = "daengs-life-crawler/0.1 (+mailto:choiyc05@gmail.com)"

# 실물 www.gov.kr/robots.txt 의 앞부분 (2026-08-27 확인). Disallow 가 먼저 온다.
GOV_KR = """User-agent: *
Disallow: /
Allow: /portal/main 
Allow: /mw/AA020InfoCappView.do
Allow: /yearendpay*
Allow: /$
"""

# 실물 www.animal.go.kr/robots.txt (2026-08-27 확인). 사이트 전체가 막혀 있다.
ANIMAL_GO_KR = "User-agent: *\nDisallow: /"


def test_allow_wins_over_earlier_disallow_when_longer() -> None:
    r = Robots(GOV_KR)
    assert r.allowed(UA, "/mw/AA020InfoCappView.do?CappBizCD=15410000003")
    assert not r.allowed(UA, "/portal/service/serviceInfo/PTR000051610")


def test_site_wide_disallow_blocks_everything() -> None:
    r = Robots(ANIMAL_GO_KR)
    assert not r.allowed(UA, "/front/community/show.do?boardId=contents&seq=66")
    assert not r.allowed(UA, "/")


def test_wildcard_and_end_anchor() -> None:
    r = Robots(GOV_KR)
    assert r.allowed(UA, "/yearendpay/intro.do")      # `*` 는 중간의 무엇이든
    assert r.allowed(UA, "/")                          # `Allow: /$` 는 루트만
    assert not r.allowed(UA, "/portal/anything")


def test_empty_disallow_means_nothing_is_blocked() -> None:
    r = Robots("User-agent: *\nDisallow:\n")
    assert r.allowed(UA, "/anything")


def test_our_group_beats_wildcard_group() -> None:
    r = Robots(
        "User-agent: *\nDisallow: /\n\n"
        "User-agent: daengs-life-crawler\nAllow: /\nDisallow: /private\n"
    )
    assert r.allowed(UA, "/front/index.do")            # 우리 그룹이 선택된다
    assert not r.allowed(UA, "/private/x")             # 그 안에서는 긴 패턴이 이긴다


def test_comments_and_blank_lines_are_ignored() -> None:
    r = Robots("# 주석\nUser-agent: *   # 여기도\nDisallow: /admin\n\n")
    assert r.allowed(UA, "/public")
    assert not r.allowed(UA, "/admin/x")


def test_no_rules_means_allowed() -> None:
    assert Robots("").allowed(UA, "/anything")
