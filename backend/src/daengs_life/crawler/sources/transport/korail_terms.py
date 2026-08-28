"""코레일 여객운송약관 — 목록은 HTML, 본문은 PDF 첨부.

  목록: info.korail.com/info/contents.do?key=922
  첨부: info.korail.com/downloadContentsFile.do?key=922&fileNo=NNNN

반려동물 관련 조항은 **휴대금지 물품의 예외**로 들어 있다 — "동물(다만, … 필요한 예방접종을 한
반려동물을 전용가방 등에 넣은 경우 제외)". 그래서 이 약관은 `transport` 도메인의 1차 근거다.

────────────────────────────────────────────────────────────────────────────
정찰 (2026-08-28) — **링크 라벨을 믿으면 틀린 약관을 받는다**
────────────────────────────────────────────────────────────────────────────
목록에 첨부가 셋 걸려 있고 라벨이 이렇다:

    fileNo=1406  여객운송 약관 및 부속약관 전문(현행, '26.5.29.개정)      28쪽
    fileNo=1405  여객운송 약관 및 부속약관 전문('26.8.1.시행예정)         22쪽
    fileNo=1245  광역철도 여객운송 약관                                  44쪽

**오늘(2026-08-28) 기준으로 '시행예정'인 1405 가 이미 시행 중이다.** 8월 1일이 지났는데
페이지가 라벨을 안 고쳤다. 문서 안을 열어 확인한 것:

    1406  개정 2026-05-29 · 본문이 "애완용동물"
    1405  개정 2026-06-30 · 본문이 "반려동물"   ← 더 새 판이고 용어도 현행

**그래서 라벨의 '현행' 을 안 읽고 날짜를 읽는다.** 링크 텍스트에서 날짜를 뽑아
**오늘 이하인 것 중 가장 나중 것**을 고른다. 이러면 지금은 1405 가 잡히고, 나중에
'27.1.1.시행예정' 같은 것이 올라와도 그날이 오기 전에는 안 잡힌다.

  ⚠️ `fileNo` 를 시드에 고정하지 않는 이유가 이것이다 — 개정 때마다 새 번호가 발급되고
     라벨은 늦게 고쳐진다. 고정하면 개정 후에도 옛 약관을 계속 받게 된다
     (`law_drf_api` 가 `lsiSeq` 를 고정하지 않는 것과 같은 판단, RAG-011).

**광역철도(1245)는 날짜 라벨이 없다.** 개정 이력이 본문에만 있고 판이 하나뿐이라
경쟁할 상대가 없다 — 날짜 규칙에서 빼고 **항상 받는다.**

응답의 성질
  - `Content-Type` 이 **`application/octer-stream`** 이다. `octet` 오타이고 우리가 고칠 수
    없다 — 그래서 content-type 으로 PDF 를 판정하지 않고 **매직 바이트(`%PDF`)** 로 본다
  - 첨부 경로가 `/downloadContentsFile.do` 다. 목록 페이지가 `/info/` 아래인데 첨부는
    **한 단계 위**라, 목록 URL 에 상대경로를 이어 붙이면 404 가 난다
  - 셋 다 텍스트 레이어가 있다 (0자 페이지 0). 스캔 PDF 가 아니라 RAG-032 ③ 의 OCR 논의는
    여기서 재개할 근거가 없다
"""
from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ...core import config
from ...core.fetch import FetchResult, Fetcher
from ..base import Extracted, Source, Target

LIST_URL = "https://info.korail.com/info/contents.do?key=922"
ORIGIN = "https://info.korail.com"

# 링크 텍스트의 날짜. `'26.8.1.시행예정` · `현행, '26.5.29.개정` 둘 다 잡는다.
# 두 자리 연도만 오므로 2000 을 더한다 — 이 사이트가 네 자리를 쓴 적이 없다.
_RE_DATE = re.compile(r"'(\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})\.")

# 항상 받는 첨부. 날짜 라벨이 없어 위 규칙으로는 못 고른다
_ALWAYS = {"광역철도"}


def _label_date(text: str) -> dt.date | None:
    m = _RE_DATE.search(text)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        return dt.date(2000 + y, mo, d)
    except ValueError:                    # 라벨 오타로 2월 30일 같은 것이 오면 없는 셈 친다
        return None


class KorailTerms(Source):
    id = "korail-terms"
    domain = "transport"
    category = "travel"
    subcategory = "transport-rail"
    source_type = "document"              # 파일로 배포된 문서. 포맷은 아래 `format`
    format = "pdf"
    trust_level = "official"              # 사업자 약관. 법령이 아니다
    license = ""                          # 공공누리 표기가 없다 — 비워 둔다

    # ------------------------------------------------------------ discover
    def discover(self, fetcher: Fetcher) -> list[Target]:
        res = fetcher.get(LIST_URL)
        if not res.ok:
            raise RuntimeError(f"목록 HTTP {res.status}: {LIST_URL}")

        soup = BeautifulSoup(res.content, "lxml")
        links = [(a, " ".join(a.get_text(" ", strip=True).split()))
                 for a in soup.find_all("a", href=True)
                 if "downloadContentsFile.do" in a["href"]]
        if not links:
            raise RuntimeError(
                "첨부 링크를 못 찾았다 — 페이지 구조가 바뀌었을 수 있다.\n"
                f"  {LIST_URL} 에서 `downloadContentsFile.do` 를 찾는다.")

        today = dt.datetime.now(config.KST).date()
        dated: list[tuple[dt.date, object, str]] = []
        targets: list[Target] = []

        for a, text in links:
            if any(w in text for w in _ALWAYS):
                targets.append(self._target(a["href"], text, "gwangyeok", None))
                continue
            when = _label_date(text)
            if when is None:
                # 날짜도 없고 항상 받는 것도 아니면 판단 근거가 없다. 조용히 버리지 않는다
                raise RuntimeError(
                    f"첨부 라벨에서 날짜를 못 읽었다: {text!r}\n"
                    "  라벨 형식이 바뀌었을 수 있다. `_RE_DATE` 를 확인할 것.")
            dated.append((when, a, text))

        # **오늘 이하인 것 중 가장 나중 것.** 라벨의 '현행' 은 안 읽는다 (위 정찰)
        effective = [x for x in dated if x[0] <= today]
        if not effective:
            raise RuntimeError(
                "시행일이 오늘 이하인 약관이 하나도 없다 — 전부 시행예정이라는 뜻이라 이상하다.\n"
                f"  오늘 {today} · 라벨 {[(str(d), t) for d, _, t in dated]}")
        when, a, text = max(effective, key=lambda x: x[0])
        targets.append(self._target(a["href"], text, "passenger", when))

        # 무엇을 왜 골랐는지 남긴다. '현행' 라벨과 어긋나는 것이 정상이라 로그가 없으면 헷갈린다
        skipped = [(str(d), t) for d, _, t in dated if d != when]
        if skipped:
            print(f"  [korail-terms] 시행일 {when} 판을 고름. 건너뜀: {skipped}")
        return targets

    def _target(self, href: str, label: str, suffix: str, when: dt.date | None) -> Target:
        file_no = re.search(r"fileNo=(\d+)", href)
        return Target(
            url=urljoin(ORIGIN, href),
            slug=f"{self.id}-{suffix}",
            ext="pdf",
            meta={"title": label, "file_no": file_no.group(1) if file_no else None,
                  "label": label,
                  "published_at": when.isoformat() if when else None},
        )

    # ------------------------------------------------------------ extract
    def extract(self, res: FetchResult, target: Target) -> Extracted:
        """dry-run 미리보기용 최소 추출. **본문 구조는 파싱 단계가 한다.**

        여기서 PyMuPDF 를 쓰지 않는 것은 의도다 — `pdf` 그룹은 AGPL 이라 오프라인
        파이프라인에만 두기로 했고(RAG-032 ②), `crawler` 는 서빙 프로세스가 import 하는
        경로에 있다(RAG-009 의 `app → crawler` 한 방향). 여기서 끌어오면 그 격리가 깨진다.
        """
        if not res.content.startswith(b"%PDF"):
            # content-type 이 `application/octer-stream`(오타) 이라 그걸로는 못 가린다
            raise RuntimeError(
                f"PDF 가 아니다 (매직 바이트 없음): {res.content[:80]!r}\n"
                f"  {config.redact(target.url)}")

        return Extracted(
            title=target.meta.get("label", ""),
            # 지문은 원본 바이트 해시다 (RAG-009). PDF 는 text 를 안 뽑아도 되고,
            # 뽑으려면 여기서 pymupdf 가 필요해진다 — 위 도크스트링의 이유로 안 뽑는다
            text="",
            published_at=target.meta.get("published_at"),
            extra={"file_no": target.meta.get("file_no"),
                   "bytes": len(res.content),
                   "label": target.meta.get("label")},
        )
