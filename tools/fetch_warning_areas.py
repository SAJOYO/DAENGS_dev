"""기상특보구역 ↔ 관할 행정구역 표를 날씨누리에서 받아 realtime 패키지 옆에 굽는다.

일회성 스크립트다 (CLAUDE.md `tools/` 규칙) — `uv run --no-project tools/fetch_warning_areas.py`.
표가 바뀌는 일은 드물고(기상청 개편 때뿐), 바뀌면 이 스크립트를 다시 돌려 CSV 를 교체한다.
런타임은 이 파일을 import 하지 않는다 — 읽는 쪽은 `daengs_life.realtime.warning_areas` 다.
출력이 `data/reference/` 가 아닌 이유는 그 모듈 docstring 에 있다 (배포마다 DATA_DIR 이 다르다).

출처: https://www.weather.go.kr/w/forecast/guide/wrn-area.do?stn=<발표관서>
  robots.txt 가 이 경로를 허용한다 (2026-09-03 확인). 발표관서 10개를 각각 한 번씩 받는다.

**해상 구역은 담지 않는다.** 육상 산책 판정에 안 쓰이고, 이름만 늘어 `_mentions` 의
부분일치 방어를 시험만 늘린다 (`앞바다`·`먼바다`·`해상`).
"""
from __future__ import annotations

import csv
import html
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

URL = "https://www.weather.go.kr/w/forecast/guide/wrn-area.do?stn={stn}"
# 발표관서. 108(전국)은 나머지 아홉을 합친 것이라 받지 않는다 — 받으면 전 행이 두 번 들어온다.
OFFICES = (109, 105, 131, 133, 146, 156, 143, 159, 184)
SEA = ("앞바다", "먼바다", "해상")
OUT = (Path(__file__).resolve().parent.parent
       / "backend" / "src" / "daengs_life" / "realtime" / "warning_areas.csv")
KST = timezone(timedelta(hours=9))

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _cells(row: str) -> list[str]:
    """**여는 태그로 자른다.** 원본이 `<td scope="row">수도권기상청<td>서울동남권</td>` 처럼
    첫 칸을 안 닫는다 — 닫는 태그를 기준으로 짝을 맞추면 관서와 구역명이 한 칸으로 붙는다.
    """
    return [_text(part) for part in re.split(r"<t[dh][^>]*>", row)[1:]]


def _rows(page: str) -> list[tuple[str, str, str]]:
    out = []
    for row in _ROW.findall(page):
        cells = [c for c in _cells(row) if c]
        # 발표관서 · 특보구역명 · 관할구역
        if len(cells) < 3 or not cells[0].endswith(("기상청", "기상지청", "기상대")):
            continue
        out.append((cells[0], cells[1], cells[2]))
    return out


def main() -> int:
    seen: set[tuple[str, str]] = set()
    rows: list[tuple[str, str, str]] = []
    for stn in OFFICES:
        req = urllib.request.Request(URL.format(stn=stn), headers={"User-Agent": "daengs/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # 고정 https 주소
            page = resp.read().decode("utf-8", "replace")
        got = _rows(page)
        kept = 0
        for office, area, covers in got:
            if any(s in area for s in SEA):
                continue
            key = (area, covers)
            if key in seen:
                continue
            seen.add(key)
            rows.append((office, area, covers))
            kept += 1
        print(f"stn={stn}: 표 {len(got)}행 → 육상 {kept}행", file=sys.stderr)
        time.sleep(1)

    if not rows:
        print("한 행도 못 받았다 — 페이지 구조가 바뀌었을 수 있다", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        fh.write(f"# 출처: {URL.format(stn='<발표관서>')}\n")
        fh.write(f"# 수집: {datetime.now(KST):%Y-%m-%d %H:%M} KST · tools/fetch_warning_areas.py\n")
        fh.write("# 해상 구역(앞바다·먼바다·해상)은 뺐다. 육상 특보만 산책 판정에 쓴다\n")
        writer = csv.writer(fh)
        writer.writerow(("office", "warning_area", "covers"))
        writer.writerows(sorted(rows, key=lambda r: (r[0], r[1])))
    print(f"{OUT} — {len(rows)}행", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
