"""F0-2 정찰 (#343 · RAG-079 ②③④) — 접근 가능성이 아니라 **유용성까지** 센다.

    uv run python tools/f0_2_recon.py nias                       # nias-pet 메뉴 전수 · 안 받는 페이지의 키워드 수
    uv run python tools/f0_2_recon.py admrul "반려동물 사료"      # law.go.kr 행정규칙 검색 (OC 필요)
    uv run python tools/f0_2_recon.py urls tools/f0_2_candidates.txt   # 후보 URL: 상태 · robots · 형식 · 크기 · 키워드

한 번 돌리고 결과를 `docs/life/data-sources.md` §10 "2026-09 정찰 2" 에 붙이는 일회성 스크립트다.
`data-sources.md` §10 의 2026-09-06 정정이 이 스크립트의 이유다 — ✅ 였던 사료관리법이 `반려동물` 0회였다.
robots 는 `Fetcher` 가 그대로 지킨다(막힌 URL 은 받지 않고 '🚫 robots' 로 적는다).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from daengs_life.crawler.core.fetch import Fetcher
from daengs_life.crawler.sources.registration.nias_pet import BASE, WANTED

KEYWORDS = ("반려동물", "반려견", "강아지", "개")
FOOD = ("사료", "먹", "급여")


def _text(content: bytes, content_type: str) -> str | None:
    if "pdf" in content_type.lower():
        try:
            import fitz  # pymupdf — optional `pdf` 그룹, 개발 venv 에는 없음
        except ImportError:
            return None
        doc = fitz.open(stream=content, filetype="pdf")
        return "\n".join(page.get_text() for page in doc)
    soup = BeautifulSoup(content, "lxml")
    for t in soup.select("script, style, nav, header, footer"):
        t.decompose()
    return soup.get_text(" ", strip=True)


def _counts(text: str) -> dict[str, int]:
    out = {k: text.count(k) for k in KEYWORDS + FOOD}
    out["조"] = len(re.findall(r"제\d+조", text))  # 조 번호 유무 — cited 축이 성립하나
    return out


def _row(cols: list[str]) -> str:
    return "| " + " | ".join(str(c) for c in cols) + " |"


def cmd_nias(fetcher: Fetcher) -> None:
    res = fetcher.get(f"{BASE}/companion/index.do")
    soup = BeautifulSoup(res.content, "lxml")
    menus: dict[str, str] = {}
    for a in soup.select('a[href*="new_petBoard.do"]'):
        menus.setdefault(a.get_text(" ", strip=True), urljoin(BASE, a["href"]))
    print(f"nias-pet 메뉴 {len(menus)}장 · 지금 받는 것 {len(WANTED)}장\n")
    print(_row(["메뉴", "받는가", "글자", "반려동물", "반려견", "사료", "먹", "조 번호"]))
    print(_row(["---"] * 8))
    for label, url in menus.items():
        if label in WANTED:
            print(_row([label, "✅ 지금", "—", "—", "—", "—", "—", "—"]))
            continue
        r = fetcher.get(url)
        text = _text(r.content, r.content_type) if r.status == 200 else ""
        text = text or ""
        c = _counts(text)
        print(_row([label, f"HTTP {r.status}", len(text), c["반려동물"], c["반려견"], c["사료"], c["먹"], c["조"]]))


def cmd_admrul(fetcher: Fetcher, query: str) -> None:
    from daengs_life.crawler.core.config import LAW_OC

    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_OC}&target=admrul&type=XML&display=50&query={quote(query)}"
    r = fetcher.get(url)
    soup = BeautifulSoup(r.content, "xml")
    items = soup.find_all("admrul")
    print(f"admrul 검색 「{query}」 — {len(items)}건 (HTTP {r.status})\n")
    print(_row(["행정규칙명", "종류", "소관", "시행일", "ID"]))
    print(_row(["---"] * 5))
    def g(it, n: str) -> str:
        found = it.find(n)
        return found.get_text(strip=True) if found else ""

    for it in items:
        print(_row([g(it, "행정규칙명"), g(it, "행정규칙종류"), g(it, "소관부처명"),
                     g(it, "시행일자"), g(it, "행정규칙ID")]))


def cmd_urls(fetcher: Fetcher, path: Path) -> None:
    print(_row(["URL", "메모", "robots", "HTTP", "형식", "글자", "반려동물", "반려견", "사료", "조 번호"]))
    print(_row(["---"] * 10))
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, _, note = line.partition("  ")
        if not fetcher.allowed(url):
            print(_row([url, note, "🚫", "—", "—", "—", "—", "—", "—", "—"]))
            continue
        try:
            r = fetcher.get(url)
        except RuntimeError as exc:
            print(_row([url, note, "✅", f"실패 {exc}", "—", "—", "—", "—", "—", "—"]))
            continue
        is_pdf = "pdf" in r.content_type.lower()
        text = _text(r.content, r.content_type) if r.status == 200 else ""
        if r.status == 200 and is_pdf and text is None:
            kind = "pdf(파서 없음)"
            print(_row([url, note, "✅", r.status, kind, len(r.content), "—", "—", "—", "—"]))
            continue
        text = text or ""
        c = _counts(text)
        kind = "pdf" if is_pdf else ("html" if "html" in r.content_type.lower() else r.content_type[:20])
        print(_row([url, note, "✅", r.status, kind, len(text), c["반려동물"], c["반려견"], c["사료"], c["조"]]))


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    fetcher = Fetcher()
    cmd, *rest = argv
    if cmd == "nias":
        cmd_nias(fetcher)
    elif cmd == "admrul":
        cmd_admrul(fetcher, " ".join(rest) or "반려동물 사료")
    elif cmd == "urls":
        cmd_urls(fetcher, Path(rest[0]) if rest else Path("tools/f0_2_candidates.txt"))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
