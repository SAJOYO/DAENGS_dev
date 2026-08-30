"""개정 판정 — "바뀜"과 "개정"을 가른다 (RAG-054, C3).

`changed: true` 는 sha256 이 달라졌다는 뜻일 뿐이다. 배너 문구가 바뀌어도, PDF 가 다시 생성돼
타임스탬프만 달라도 changed 다. 여기서는 **소스가 선언한 판 식별자**로 개정을 판정한다:

  `revision_key = "published_at"`  같은 slug 의 **시행일자**가 달라졌나 — 법령 (law.go.kr 웹 · DRF)
  `revision_key = "slug"`          **같은 제목의 새 slug** 가 나타났나 — 약관 (판이 파일명에 박힌다)
  `revision_key = None`            판정 불가. 바뀐 것은 바뀐 것으로만 남는다 (안내 페이지 등)

두 판정 모두 **원본을 받지 않는다.** `published_at` 모드는 `discover()` 만 부른다 — 법령은
껍데기 페이지(1.2KB)나 목록 검색이라 가볍고, 그것이 cadence `manual` 인 법령을 매일 깨울 수
있는 이유다 (RAG-044 ① 은 법령을 주기에서 뺐고, 개정은 주기가 아니라 사건이다). `slug` 모드는
이미 끝난 수집의 결과(새 slug 목록)만 본다 — 약관은 quarterly 수집이 목록을 다시 읽으므로
요청을 더 내지 않는다.

**DB 를 모른다** (RAG-001 원칙 1). 이전 판은 `data/raw/**/*.meta.json` 에서 읽는다 —
`published_at` 과 `document_title` 이 거기 남는다 (`store.py`). 그래서 `python -m crawler
revisions` 가 브로커·DB 없이 돈다.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from . import config
from .fetch import Fetcher

log = logging.getLogger(__name__)

Kind = str   # new | revised | same | superseded


@dataclass(frozen=True)
class Verdict:
    source_id: str
    slug: str
    kind: Kind
    title: str | None = None
    current: str | None = None      # 지금 판 (시행일자 또는 새 slug)
    previous: str | None = None     # 저장된 판 (시행일자 또는 옛 slug)

    @property
    def actionable(self) -> bool:
        """받아야 하거나(new · revised) 사람이 정리해야 하는 것(superseded)."""
        return self.kind != "same"


def _ymd(value: object) -> str | None:
    """`20260707` · `2026-07-07` · `2026. 7. 7.` 을 한 모양으로. 없으면 None."""
    d = re.sub(r"\D", "", str(value or ""))
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else None


def latest_metas(source_id: str, domain: str) -> dict[str, dict[str, Any]]:
    """소스의 slug 별 **최신** meta. 파일명 규칙 `{slug}__{YYYYMMDD}.meta.json` 이라 정렬이 곧 시간이다."""
    raw = config.RAW_DIR
    if raw is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for path in sorted((raw / domain).glob(f"{source_id}-*__*.meta.json")):
        slug = path.name.rsplit("__", 1)[0]
        try:
            out[slug] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                       # 깨진 meta 하나가 판정 전체를 막지 않는다
    return out


# ------------------------------------------------------------------ published_at 모드

def probe(src, fetcher: Fetcher) -> list[Verdict]:
    """`discover()` 만 부르고 저장된 판과 시행일자를 대조한다. **원본은 받지 않는다.**

    `src.revision_key` 가 `"published_at"` 이 아니면 빈 목록이다 — 판정할 근거가 없는 소스를
    억지로 판정하면 오탐만 남는다.
    """
    if getattr(src, "revision_key", None) != "published_at":
        return []
    stored = latest_metas(src.id, src.domain)
    verdicts: list[Verdict] = []
    for t in src.discover(fetcher):
        current = _ymd(t.meta.get("published_at") or t.meta.get("efYd"))
        prev = stored.get(t.slug)
        title = t.meta.get("title")
        if prev is None:
            verdicts.append(Verdict(src.id, t.slug, "new", title, current, None))
            continue
        previous = _ymd(prev.get("published_at"))
        kind = "revised" if (current and previous and current != previous) else "same"
        verdicts.append(Verdict(src.id, t.slug, kind, title, current, previous))
    return verdicts


def probe_sources(seeds: dict[str, dict[str, Any]], *, only_manual: bool = True) -> dict[str, list[Verdict]]:
    """판정 가능한 소스 전부를 조회한다. **한 소스가 죽어도 나머지는 본다.**

    기본은 cadence `manual` 인 소스만이다 — 주기가 있는 소스는 어차피 주기에 받고, 그때
    `store` 의 sha256 이 바뀜을 잡는다. manual 은 아무도 안 받으므로 여기가 유일한 눈이다.
    """
    from . import cadence, registry          # 순환 import 회피 — registry 가 sources 를 끌고 온다

    out: dict[str, list[Verdict]] = {}
    candidates = [
        (sid, seed) for sid, seed in seeds.items()
        if seed.get("status") not in cadence.SKIP_STATUSES
        and seed.get("domain") not in cadence.SKIP_DOMAINS
        and (not only_manual or cadence.cadence_of(seed) == "manual")
    ]
    for sid, seed in candidates:
        cls = registry.resolve(seed)
        if cls is None or getattr(cls, "revision_key", None) != "published_at":
            continue
        try:
            with Fetcher() as fetcher:
                out[sid] = probe(cls(seed), fetcher)
        except Exception as e:                  # noqa: BLE001 — 격리. 다음 소스를 본다
            log.warning("개정 조회 실패 (%s): %s", sid, e)
            out[sid] = []
    return out


# ------------------------------------------------------------------------ slug 모드

def superseded(src, new_slugs: list[str]) -> list[Verdict]:
    """새 slug 가 **같은 제목의 옛 slug** 를 대체하는지. 약관처럼 판이 slug 에 박힌 소스용.

    같은 slug 의 바이트가 바뀐 것은 여기서 개정이 아니다 — 같은 판을 다시 생성한 것(타임스탬프)
    이라 `changed` 로만 남는다. 그것이 이 모드가 오탐을 거르는 방식이다.
    """
    if getattr(src, "revision_key", None) != "slug" or not new_slugs:
        return []
    stored = latest_metas(src.id, src.domain)
    by_title: dict[str, list[str]] = {}
    for slug, meta in stored.items():
        title = (meta.get("document_title") or "").strip()
        if title:
            by_title.setdefault(title, []).append(slug)

    out: list[Verdict] = []
    for new in new_slugs:
        title = (stored.get(new, {}).get("document_title") or "").strip()
        olds = [s for s in by_title.get(title, []) if s != new]
        for old in sorted(olds):
            out.append(Verdict(src.id, old, "superseded", title, current=new, previous=old))
    return out


__all__ = ["Verdict", "latest_metas", "probe", "probe_sources", "superseded"]
