"""소스 하나를 수집한다 — **CLI 와 Celery 태스크가 같이 타는 한 경로** (RAG-044).

`__main__.py` 의 `cmd_run` 안에 있던 루프를 그대로 옮겨 왔다. 옮긴 이유는 하나다:
태스크가 부를 수 있는 것이 없었다. `cmd_run(args)` 는 `argparse.Namespace` 를 받아 `print` 로
결과를 흘리고 **종료 코드만** 돌려준다 — 워커가 "몇 건이 바뀌었나"를 알 방법이 없다.

전용 수집 경로를 태스크 쪽에 새로 만드는 선택지도 있었지만 그러면 `tasks/realtime.py` 가
피한 것(프리페치 전용 경로)을 여기서 다시 만드는 것이다. 새 소스를 붙일 때 CLI 에서만 되고
Beat 에서는 조용히 안 되는 날이 온다.

**출력은 여기 없다.** CLI 의 한 줄 한 줄은 `on_discover`·`on_target` 콜백으로 나가고, 기본값은
아무것도 하지 않는다. 그래서 워커에서 부를 때 stdout 이 더러워지지 않고, CLI 는 예전과 똑같이
받는 즉시 한 줄씩 찍는다 (수집은 대상당 1.5초 이상이라 다 끝나고 찍으면 멈춘 것처럼 보인다).
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from .core import config, registry
from .core.fetch import Fetcher
from .core.store import Store, StoreResult
from .sources.base import Extracted, Source, Target


# 원본을 새로 쓴 상태들. `same` 만 아니면 바뀐 것이다.
CHANGED_STATES = frozenset({"new", "changed", "raw-missing", "forced"})
SKIPPED_STATES = frozenset({"robots"})
FAILED_STATES = frozenset({"net-fail", "http-fail", "extract-fail"})


@dataclass
class TargetOutcome:
    """대상 하나의 결말. CLI 는 한 줄로 찍고, 태스크는 세기만 한다."""

    index: int                               # 1-based
    total: int
    slug: str
    url: str                                 # redact 된 것만 담는다 — 로그·화면 어디로 가든 안전하게
    # new | changed | raw-missing | forced | same  (StoreResult.reason 그대로)
    # + robots | net-fail | http-fail | extract-fail  (아래 SKIPPED/FAILED_STATES)
    #
    # 실패를 한 이름으로 뭉치지 않는다. CLI 가 셋을 다른 문구로 찍고 있었고, 뭉치면 그 분기가
    # `error` 문자열을 다시 뜯어보는 형태가 된다 — 문구를 고치는 날 조용히 어긋난다.
    state: str
    status: int | None = None
    bytes: int = 0
    elapsed_sec: float = 0.0
    raw_file: str | None = None
    error: str | None = None
    detail: str | None = None                # extract 실패의 스택트레이스. 찍는 것은 부르는 쪽 몫이다
    extracted: Extracted | None = None       # dry-run/verbose 미리보기용. 태스크는 안 본다

    @property
    def changed(self) -> bool:
        return self.state in CHANGED_STATES


@dataclass
class RunResult:
    """소스 하나의 수집 결과 전체."""

    source_id: str
    discovered: int = 0
    run_id: str | None = None                # dry-run 이면 None
    outcomes: list[TargetOutcome] = field(default_factory=list)
    # discover 가 '고쳐야 실행되는' 조건으로 멈춤 (키 미설정·시드 URL 사망).
    # 이건 실패가 아니라 **아직 못 하는 것**이라 따로 둔다 — CLI 는 종료 코드 2, 태스크는 경고다.
    unavailable: str | None = None

    @property
    def fetched(self) -> int:
        return sum(1 for o in self.outcomes
                   if o.state not in SKIPPED_STATES and o.state not in FAILED_STATES)

    @property
    def changed(self) -> int:
        return sum(1 for o in self.outcomes if o.changed)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.state in FAILED_STATES)

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.state in SKIPPED_STATES)

    @property
    def restored(self) -> int:
        return sum(1 for o in self.outcomes if o.state == "raw-missing")

    @property
    def changed_slugs(self) -> list[str]:
        """바뀐 문서의 slug. **C3(개정 감지)의 입력이 이것이다** — 카드 메모 ③.

        수집은 여기서 멈춘다. `parse → chunk → embed → load` 는 GPU 와 검문소가 걸려 있어
        사람이 랩을 뜨고 판단하는 자리다 (RAG-002 · RAG-025).
        """
        return [o.slug for o in self.outcomes if o.changed]


def _noop_discover(_targets: int, _limit: int) -> None: ...
def _noop_target(_outcome: TargetOutcome) -> None: ...


def run(
    source_id: str,
    *,
    limit: int = 0,
    dry_run: bool = False,
    force: bool = False,
    on_discover: Callable[[int, int], None] = _noop_discover,
    on_target: Callable[[TargetOutcome], None] = _noop_target,
) -> RunResult:
    """소스 하나를 수집한다.

    `limit` 은 앞에서 N개(0 이면 전부), `dry_run` 은 아무것도 쓰지 않음, `force` 는 sha256 이
    같아도 새로 저장. 셋 다 CLI 플래그와 같은 뜻이다.

    **예외를 밖으로 안 낸다** — 대상 하나가 죽어도 나머지를 계속 받고 결과에 담는다.
    소스 자체를 못 만들 때(`registry.build` 의 KeyError)만 올라간다. 부르는 쪽이 id 를 잘못
    준 것이라 조용히 0건으로 끝나면 안 되는 종류다.
    """
    src = registry.build(source_id)
    store = Store(dry_run=dry_run, force=force)
    result = RunResult(source_id=src.id, run_id=None if dry_run else store.run_id)

    with Fetcher() as fetcher:
        try:
            targets: list[Target] = src.discover(fetcher)
        except RuntimeError as e:
            # 키 미설정·시드 URL 사망처럼 '고쳐야 실행되는' 조건. 스택트레이스를 낼 자리가 아니다.
            result.unavailable = str(e)
            return result

        result.discovered = len(targets)
        on_discover(len(targets), limit)
        if limit:
            targets = targets[:limit]

        total = len(targets)
        for i, t in enumerate(targets, 1):
            outcome = _fetch_one(src, t, i, total, fetcher=fetcher, store=store)
            result.outcomes.append(outcome)
            on_target(outcome)

    return result


def _fetch_one(src: Source, target: Target, index: int, total: int, *, fetcher: Fetcher,
               store: Store) -> TargetOutcome:
    """대상 하나. 흐름은 `cmd_run` 의 for 안쪽 그대로다 — 실패도 반드시 로그에 남는다 (규칙 4)."""
    def outcome(state: str, **kw) -> TargetOutcome:
        return TargetOutcome(index=index, total=total, slug=target.slug,
                             url=config.redact(target.url) or "", state=state, **kw)

    if not fetcher.allowed(target.url):
        store.log(src, target, status=None, result=None, error="robots disallow")
        return outcome("robots", error="robots disallow")

    try:
        res = fetcher.get(target.url)
    except Exception as e:                              # noqa: BLE001 — 재시도 끝에도 실패
        store.log(src, target, status=None, result=None, error=str(e))
        return outcome("net-fail", error=str(e))

    if not res.ok:
        store.log(src, target, status=res.status, result=None, error=f"HTTP {res.status}")
        return outcome("http-fail", status=res.status, error=f"HTTP {res.status}")

    try:
        ext = src.extract(res, target)
    except Exception:                                   # noqa: BLE001
        store.log(src, target, status=res.status, result=None, error="extract error")
        # 스택트레이스는 담아서 넘긴다 — 찍는 것은 CLI, 로그로 보내는 것은 태스크의 몫이다.
        return outcome("extract-fail", status=res.status, error="extract error",
                       detail=traceback.format_exc())

    stored: StoreResult = store.save(src, target, res, ext)
    store.log(src, target, status=res.status, result=stored)
    return outcome(stored.reason, status=res.status, bytes=len(res.content),
                   elapsed_sec=res.elapsed_sec, raw_file=stored.raw_file, extracted=ext)


__all__ = ["CHANGED_STATES", "FAILED_STATES", "SKIPPED_STATES",
           "RunResult", "TargetOutcome", "run"]
