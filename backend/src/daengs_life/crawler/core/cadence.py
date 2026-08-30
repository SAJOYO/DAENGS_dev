"""소스별 재수집 주기와 **due 판정** (RAG-001 원칙 4 · RAG-044).

원칙 4 는 "소스별 cadence 를 시드에 힌트로, Beat 는 due 소스 선별 태스크 하나만 등록"이다.
그 판정이 여기 있고 `tasks/crawl.py` 는 결과를 받아 부르기만 한다 — `tasks/realtime.py` 가
"여기 있는 것은 스케줄과 예산 판단뿐"이라고 적어 둔 것과 같은 배치다. 판정을 태스크에 두면
`python -m crawler` 로는 "무엇이 밀렸나"를 볼 수 없고, 그건 원칙 1(크롤러는 단독 실행)이
반쪽이 되는 것이다.

**cadence 기본값은 코드에 있고 시드에는 예외만 적는다.** 시드 30항목에 전부 넣으면 같은 파일을
건드리는 다른 카드와 매번 부딪힌다 (최근 PR 6개 중 5개가 `seed_sources.yaml` 을 고쳤다).

⚠ **`law` 는 `method` 가 아니라 `domain` 이다.** 원칙 4 의 "법령 manual" 을 method 로만 읽으면
법령 4건이 `api`·`html` 이라 weekly 가 된다 — 매주 법령을 긁게 되고, 그건 원칙 4 가 하지 말라던
바로 그것이다. 그래서 **domain 을 먼저 보고 method 로 떨어진다.**
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config

# cadence 이름 → 간격. `manual` 은 사람이 부를 때만 도는 것이라 due 가 될 수 없다.
# 90 이 아니라 91 인 이유는 분기(3개월)를 날짜로 옮긴 값이라서다 — 365/4 = 91.25.
CADENCE_DAYS: dict[str, int | None] = {
    "daily": 1,
    "weekly": 7,
    "quarterly": 91,
    "manual": None,
}

# 크롤 대상이 아예 아닌 것들. **cadence 를 주기 전에 거른다.**
#   realtime — 파트②(실시간 조회형)의 실황 API 다. 받아서 쌓는 문서가 아니라 그때그때 묻는
#              값이라 `.meta.json` 도 만들지 않는다 (data/README.md §5 · RAG-012).
#              method 가 `api` 라 안 거르면 weekly 로 잡혀 매주 기상청을 긁는다.
SKIP_DOMAINS = frozenset({"realtime"})
#   blocked   — robots.txt 나 차단으로 못 받는 것으로 확인된 소스
#   not-found — 시드의 URL 이 죽은 것으로 확인된 소스
SKIP_STATUSES = frozenset({"blocked", "not-found"})

Seed = dict[str, Any]


def default_cadence(seed: Seed) -> str:
    """시드에 `cadence:` 가 없을 때 쓰는 값. **domain 이 method 보다 먼저다** (위 ⚠)."""
    if seed["domain"] == "law":
        return "manual"                      # 법령 개정은 주기가 아니라 사건이다 — C3 이 감지한다
    if seed["method"] == "pdf-entry":
        return "quarterly"                   # 약관 PDF. 개정이 분기 단위로 온다
    return "weekly"                          # api · html — 공고·안내 페이지


def cadence_of(seed: Seed) -> str:
    """이 소스의 cadence. 시드에 적힌 예외가 이기고, 없으면 기본값이다."""
    cadence = seed.get("cadence") or default_cadence(seed)
    if cadence not in CADENCE_DAYS:
        # 조용히 weekly 로 떨어뜨리지 않는다. 오타 하나가 "매주 도는 소스"가 되는데
        # 로그만 보면 정상으로 보인다. `test_tasks_crawl.py` 가 시드 전체를 여기 통과시킨다.
        raise ValueError(
            f"'{seed['id']}' 의 cadence 가 이상하다: {cadence!r} — "
            f"쓸 수 있는 값은 {', '.join(CADENCE_DAYS)}")
    return cadence


def skip_reason(seed: Seed, *, implemented: bool) -> str | None:
    """due 후보에서 빠지는 이유. 후보면 None.

    `implemented` 는 `registry.resolve(seed) is not None` 이다. 인자로 받는 이유는 판정이
    yaml 만 보고 끝나게 하기 위해서다 — 여기서 import 하면 due 판정 테스트가 소스 모듈 전체를
    끌고 들어온다.
    """
    if seed["domain"] in SKIP_DOMAINS:
        return f"domain={seed['domain']}"
    if seed.get("status") in SKIP_STATUSES:
        return f"status={seed['status']}"
    if not implemented:
        return "모듈 미구현"
    if cadence_of(seed) == "manual":
        return "cadence=manual"
    return None


def last_success(log_path: Path | None = None) -> dict[str, datetime]:
    """소스별 **마지막 성공 시각**. `crawl_log.jsonl` 을 처음부터 읽는다.

    성공 = `error` 가 없는 줄이다. `store.log` 는 실패할 때만 `error` 를 채우고 그때는
    `result` 가 None 이라, 이 한 조건이 곧 "받아서 판단까지 끝났다"와 같다.

    **로그는 대상(slug) 단위**라 소스 하나의 실행이 여러 줄을 남긴다. 그중 가장 늦은 성공을
    그 소스의 시각으로 본다. 전부 실패한 날은 시각이 안 밀리고, 그래서 다음 날 다시 due 다.

    파일이 없으면 빈 dict 다 — 처음 도는 PC 에서는 **전부 due** 라는 뜻이고, 그게 맞다.
    로그가 없는데 안 받으면 영영 안 받는다.
    """
    path = log_path or config.CRAWL_LOG
    if path is None or not Path(path).is_file():
        return {}

    out: dict[str, datetime] = {}
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # append-only 로그라 중간에 끊긴 줄이 남을 수 있다. 한 줄 때문에 판정 전체가
                # 멈추면 그날 아무것도 안 받는다 — 못 읽는 줄은 없는 셈 친다.
                continue
            if row.get("error") is not None:
                continue
            when = _parse(row.get("fetched_at"))
            sid = row.get("source_id")
            if when is None or not sid:
                continue
            if sid not in out or when > out[sid]:
                out[sid] = when
    return out


def _parse(value: object) -> datetime | None:
    """`fetched_at` 은 `2026-08-29T15:04:41+09:00` 꼴이다. tz 없는 값이 와도 KST 로 본다."""
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=config.KST)


def is_due(seed: Seed, last: datetime | None, now: datetime) -> bool:
    """이 소스를 지금 받아야 하나. `manual` 은 절대 due 가 아니고, 로그가 없으면 due 다."""
    days = CADENCE_DAYS[cadence_of(seed)]
    if days is None:
        return False
    if last is None:
        return True
    return now - last >= timedelta(days=days)


def due_sources(
    seeds: dict[str, Seed],
    *,
    implemented: set[str],
    now: datetime | None = None,
    last: dict[str, datetime] | None = None,
) -> list[str]:
    """지금 받아야 하는 소스 id. **순서는 시드에 적힌 순서 그대로**다 (재현되게).

    `seeds` 와 `implemented` 를 인자로 받는 이유는 `registry` 를 여기서 부르지 않기 위해서다 —
    부르는 쪽(`tasks/crawl.py`)이 이미 registry 를 아는 자리고, 그래야 이 판정이 yaml 없이도
    테스트된다.
    """
    now = now or datetime.now(config.KST)
    last = last if last is not None else last_success()
    return [sid for sid, seed in seeds.items()
            if skip_reason(seed, implemented=sid in implemented) is None
            and is_due(seed, last.get(sid), now)]


__all__ = ["CADENCE_DAYS", "SKIP_DOMAINS", "SKIP_STATUSES",
           "cadence_of", "default_cadence", "due_sources", "is_due",
           "last_success", "skip_reason"]
