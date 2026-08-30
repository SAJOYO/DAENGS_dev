"""크롤러 Beat — cadence 기본값 · due 판정 · 태스크 (RAG-044).

**브로커 없이 돈다.** `celery_app` 은 `REDIS_URL` 이 없으면 `memory://` 로 뜨고 연결은 워커가
뜰 때 처음 시도되므로, 태스크 함수를 직접 부르는 이 테스트는 Redis 를 켜지 않는다.

due 판정은 `seeds`·`implemented`·`last` 를 전부 인자로 받게 해 뒀다. 그래서 여기 있는 검사
대부분이 **`data/` 없이** 돈다 — 부분 코퍼스 PC 에서도 판정만은 그대로 검증된다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from daengs_life.crawler.core import cadence, config
from daengs_life.tasks import crawl
from daengs_life.tasks.celery_app import app

NOW = datetime(2026, 8, 29, 4, 0, tzinfo=config.KST)


def seed(sid: str, *, domain: str = "subsidy", method: str = "html",
         status: str = "verified", **extra) -> dict:
    return {"id": sid, "domain": domain, "method": method, "status": status, **extra}


# --------------------------------------------------------------- cadence 기본값

def test_law_is_manual_even_though_its_method_is_api_or_html() -> None:
    """**이 카드가 정정한 자리다.** 원칙 4 의 '법령 manual' 은 domain 조건이다.

    시드의 `method` 에는 `law` 가 없다 — 법령 4건은 `domain: law` + `method: api|html` 이라,
    method 만 보면 전부 weekly 가 되어 매주 법령을 긁는다.
    """
    assert cadence.default_cadence(seed("law-drf-api", domain="law", method="api")) == "manual"
    assert cadence.default_cadence(seed("easylaw-pet", domain="law", method="html")) == "manual"


def test_pdf_entry_is_quarterly_and_the_rest_is_weekly() -> None:
    assert cadence.default_cadence(seed("x", domain="insurance", method="pdf-entry")) == "quarterly"
    assert cadence.default_cadence(seed("x", domain="subsidy", method="html")) == "weekly"
    assert cadence.default_cadence(seed("x", domain="subsidy", method="api")) == "weekly"


def test_the_seed_overrides_the_default() -> None:
    """시드에는 **예외만** 적는다 (메모 ②). 적힌 값이 기본값을 이긴다."""
    assert cadence.cadence_of(seed("x", domain="law", method="html", cadence="daily")) == "daily"


def test_an_unknown_cadence_is_loud() -> None:
    """오타를 조용히 weekly 로 떨어뜨리면 로그만 봐서는 정상으로 보인다."""
    with pytest.raises(ValueError, match="cadence 가 이상하다"):
        cadence.cadence_of(seed("x", cadence="weekley"))


def test_every_real_seed_has_a_valid_cadence() -> None:
    """실제 `seed_sources.yaml` 30항목이 전부 판정을 통과하는지.

    위 예외가 Beat 한복판에서 처음 터지면 그날 아무것도 안 받는다. 시드를 고치는 카드가
    여기서 먼저 걸리게 한다.
    """
    if config.SEED_FILE is None or not config.SEED_FILE.exists():
        pytest.skip("data/ 가 없는 PC")
    from daengs_life.crawler.core import registry
    for sid, s in registry.load_seeds().items():
        assert cadence.cadence_of(s) in cadence.CADENCE_DAYS, sid


# ------------------------------------------------------------------ due 판정

def test_no_log_means_due() -> None:
    """로그가 없으면 due 다 — 안 그러면 처음 도는 PC 에서 영영 안 받는다."""
    assert cadence.is_due(seed("x"), None, NOW) is True


def test_before_the_deadline_is_not_due() -> None:
    assert cadence.is_due(seed("x"), NOW - timedelta(days=6, hours=23), NOW) is False


def test_after_the_deadline_is_due() -> None:
    assert cadence.is_due(seed("x"), NOW - timedelta(days=7), NOW) is True


def test_quarterly_holds_much_longer_than_weekly() -> None:
    pdf = seed("x", domain="insurance", method="pdf-entry")
    assert cadence.is_due(pdf, NOW - timedelta(days=30), NOW) is False
    assert cadence.is_due(pdf, NOW - timedelta(days=91), NOW) is True


def test_manual_is_never_due() -> None:
    """법령은 주기가 아니라 사건으로 바뀐다. 100년이 지나도 Beat 는 안 건드린다."""
    law = seed("law-drf-api", domain="law", method="html")
    assert cadence.is_due(law, None, NOW) is False
    assert cadence.is_due(law, NOW - timedelta(days=36500), NOW) is False


@pytest.mark.parametrize("s, expected", [
    (seed("kma", domain="realtime", method="api"), "domain=realtime"),
    (seed("animal-go-kr", status="blocked"), "status=blocked"),
    (seed("dead", status="not-found"), "status=not-found"),
    (seed("law-x", domain="law"), "cadence=manual"),
    (seed("ok"), None),
])
def test_what_falls_out_before_cadence_is_even_asked(s: dict, expected: str | None) -> None:
    assert cadence.skip_reason(s, implemented=True) == expected


def test_an_unimplemented_source_is_not_a_candidate() -> None:
    """`data-registration-lookup` 처럼 시드에는 있고 모듈이 없는 소스가 실제로 있다."""
    assert cadence.skip_reason(seed("x"), implemented=False) == "모듈 미구현"


def test_due_sources_keeps_the_seed_order() -> None:
    seeds = {s["id"]: s for s in [
        seed("a"), seed("kma", domain="realtime", method="api"), seed("b"),
        seed("law", domain="law"), seed("c"), seed("nope"),
    ]}
    got = cadence.due_sources(seeds, implemented={"a", "kma", "b", "law", "c"}, now=NOW, last={})
    assert got == ["a", "b", "c"]


# ------------------------------------------------------- crawl_log.jsonl 읽기

def write_log(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def test_a_missing_log_is_an_empty_dict_not_an_error(tmp_path: Path) -> None:
    assert cadence.last_success(tmp_path / "없는파일.jsonl") == {}


def test_the_latest_success_per_source_wins(tmp_path: Path) -> None:
    """로그는 대상(slug) 단위라 소스 하나가 여러 줄을 남긴다."""
    log = write_log(tmp_path / "crawl_log.jsonl", [
        {"source_id": "a", "fetched_at": "2026-08-01T10:00:00+09:00", "error": None},
        {"source_id": "a", "fetched_at": "2026-08-20T10:00:00+09:00", "error": None},
        {"source_id": "a", "fetched_at": "2026-08-10T10:00:00+09:00", "error": None},
        {"source_id": "b", "fetched_at": "2026-08-05T10:00:00+09:00", "error": None},
    ])
    assert cadence.last_success(log) == {
        "a": datetime(2026, 8, 20, 10, tzinfo=config.KST),
        "b": datetime(2026, 8, 5, 10, tzinfo=config.KST),
    }


def test_a_failed_line_does_not_move_the_clock(tmp_path: Path) -> None:
    """전부 실패한 날은 시각이 안 밀린다 — 그래서 다음 날 다시 due 다."""
    log = write_log(tmp_path / "crawl_log.jsonl", [
        {"source_id": "a", "fetched_at": "2026-08-01T10:00:00+09:00", "error": None},
        {"source_id": "a", "fetched_at": "2026-08-28T10:00:00+09:00", "error": "HTTP 503"},
    ])
    assert cadence.last_success(log) == {"a": datetime(2026, 8, 1, 10, tzinfo=config.KST)}


def test_a_torn_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    """append-only 라 중간에 끊긴 줄이 남을 수 있다. 한 줄 때문에 그날 전체가 멈추면 안 된다."""
    path = tmp_path / "crawl_log.jsonl"
    path.write_text(
        json.dumps({"source_id": "a", "fetched_at": "2026-08-01T10:00:00+09:00", "error": None}) + "\n"
        + '{"source_id": "b", "fetch\n'                      # 쓰다 끊긴 줄
        + "\n"                                               # 빈 줄
        + json.dumps({"source_id": "c", "fetched_at": "2026-08-02T10:00:00+09:00", "error": None}) + "\n",
        encoding="utf-8")
    assert set(cadence.last_success(path)) == {"a", "c"}


def test_the_real_log_is_readable() -> None:
    """실제 `crawl_log.jsonl` 이 이 판정을 통과하는지 — 형식이 바뀌면 여기서 걸린다."""
    if config.CRAWL_LOG is None or not config.CRAWL_LOG.exists():
        pytest.skip("data/ 가 없는 PC")
    got = cadence.last_success()
    assert got, "성공한 줄이 하나도 없다 — 로그 형식이 바뀌었거나 error 판정이 뒤집혔다"
    assert all(t.tzinfo is not None for t in got.values())


# ------------------------------------------------------------------- 태스크

class FakeResult:
    """`crawler.run.RunResult` 중 태스크가 읽는 것만."""

    def __init__(self, changed_slugs=(), unavailable=None, new_slugs=()) -> None:
        self.fetched, self.failed, self.skipped, self.run_id = 3, 0, 0, "20260829-040000"
        self.changed_slugs = list(changed_slugs)
        self.new_slugs = list(new_slugs)
        self.changed = len(self.changed_slugs)
        self.unavailable = unavailable


@pytest.fixture
def fake_crawl(monkeypatch):
    """시드·구현 여부·수집을 전부 가짜로. 네트워크도 data/ 도 안 탄다."""
    seeds = {s["id"]: s for s in [
        seed("fresh"), seed("stale"), seed("kma", domain="realtime", method="api"),
        seed("law-x", domain="law"),
    ]}
    monkeypatch.setattr(crawl.registry, "load_seeds", lambda: seeds)
    monkeypatch.setattr(crawl.registry, "resolve", lambda s: object())
    monkeypatch.setattr(cadence, "last_success", lambda *a, **k: {
        "fresh": datetime.now(config.KST) - timedelta(days=1),
        "stale": datetime.now(config.KST) - timedelta(days=30),
    })

    called: list[str] = []

    def fake_run(source_id, **kw):
        called.append(source_id)
        return FakeResult(changed_slugs=["doc-1"] if source_id == "stale" else [])

    monkeypatch.setattr(crawl.crawler_run, "run", fake_run)
    # 개정 조회는 기본으로 **아무것도 없음**. 가짜 시드는 revision_key 가 없어 어차피 안 보지만,
    # 여기서 명시해 두면 아래 테스트가 무엇을 덮어쓰는지 보인다 (RAG-054).
    monkeypatch.setattr(crawl.revision, "probe_sources", lambda seeds, **kw: {})
    return called


@pytest.fixture
def dispatched(monkeypatch):
    """`crawl_source` 를 발사하는 대신 인자를 모은다.

    **RAG-047 에서 태스크가 둘로 갈렸다** — `crawl_due` 는 고르기만 하고 수집은
    `crawl_source` 가 한다. 그래서 여기서 보는 것은 "무엇을 골랐나"이고, "그것을 어떻게
    수집하나"는 아래 `crawl_source` 쪽 테스트가 본다.
    """
    sent: list[tuple] = []

    class _Sent:
        id = "task-fake"

    def fake_apply_async(args=(), queue=None, **kw):
        sent.append((*args, queue))
        return _Sent()

    monkeypatch.setattr(crawl.crawl_source, "apply_async", fake_apply_async)
    return sent


def test_beat_picks_only_the_due_ones(fake_crawl, dispatched) -> None:
    out = crawl.crawl_due()
    assert dispatched == [("stale", "due", "crawl")]  # fresh 는 기한 전, kma·law-x 는 후보 밖
    assert out["mode"] == "due" and out["selected"] == ["stale"]
    assert fake_crawl == []                           # 고르기만 한다. 수집은 별도 태스크다


def test_source_ids_skips_the_due_check_entirely(fake_crawl, dispatched) -> None:
    """수동 트리거는 **같은 경로**를 override 한다 (RAG-001 요구사항 ②③).

    거르지도 않는다 — 사람이 이름을 대고 부른 manual 소스를 'manual 이라서' 안 받으면
    법령은 영영 못 받는다.
    """
    out = crawl.crawl_due(source_ids=["fresh", "law-x"])
    assert [d[0] for d in dispatched] == ["fresh", "law-x"]
    assert all(d[1] == "manual" for d in dispatched)   # trigger 가 행에 그대로 남는다
    assert out["mode"] == "manual"


def test_dispatch_goes_to_the_crawl_queue(fake_crawl, dispatched) -> None:
    """기본 `celery` 큐로 보내면 실시간 워커가 가져가려다 실패하거나 아무도 안 가져간다."""
    crawl.crawl_due(source_ids=["fresh"])
    assert dispatched[0][-1] == crawl.QUEUE == "crawl"


def test_one_undeliverable_source_does_not_stop_the_rest(fake_crawl, monkeypatch) -> None:
    """원칙 5 의 절반 — 이제는 **발사 단계**에서 지킨다."""
    def flaky(args=(), queue=None, **kw):
        if args[0] == "b":
            raise OSError("브로커가 죽었다")
        return type("R", (), {"id": f"task-{args[0]}"})()

    monkeypatch.setattr(crawl.crawl_source, "apply_async", flaky)
    out = crawl.crawl_due(source_ids=["a", "b", "c"])
    assert out["selected"] == ["a", "b", "c"]
    assert out["dispatched"][0] == "task-a" and out["dispatched"][2] == "task-c"
    assert "발사 실패" in out["dispatched"][1] and "OSError" in out["dispatched"][1]


# ------------------------------------------------------------- 개정 감지 (RAG-054)

def _verdict(sid, slug, kind, prev=None, cur=None):
    from daengs_life.crawler.core.revision import Verdict
    return Verdict(sid, slug, kind, None, cur, prev)


def test_revised_law_is_dispatched_with_the_revision_trigger(fake_crawl, dispatched, monkeypatch) -> None:
    """법령은 cadence manual 이라 주기로는 영영 안 받힌다 — 시행일자가 바뀐 것만 이 길로 받는다."""
    monkeypatch.setattr(crawl.revision, "probe_sources", lambda seeds, **kw: {
        "law-x": [_verdict("law-x", "law-x-act", "revised", "2026-07-07", "2026-10-01"),
                  _verdict("law-x", "law-x-decree", "same", "2026-06-03", "2026-06-03")],
        "law-y": [_verdict("law-y", "law-y-act", "same", "2026-01-01", "2026-01-01")],
    })
    out = crawl.crawl_due()
    assert dispatched == [("stale", "due", "crawl"), ("law-x", "revision", "crawl")]
    assert out["revised"] == ["law-x"] and out["selected"] == ["stale"]


def test_a_dead_probe_does_not_stop_the_due_dispatch(fake_crawl, dispatched, monkeypatch) -> None:
    """법제처가 점검 중이면 개정은 내일 보고, 오늘의 due 는 그대로 받는다."""
    def boom(seeds, **kw):
        raise OSError("법제처 점검 중")
    monkeypatch.setattr(crawl.revision, "probe_sources", boom)
    out = crawl.crawl_due()
    assert dispatched == [("stale", "due", "crawl")] and out["revised"] == []


def test_manual_trigger_does_not_probe(fake_crawl, dispatched, monkeypatch) -> None:
    """관리자가 이름을 대고 부른 것은 그것만 받는다 — 조회를 끼워 넣으면 수동 트리거가 느려진다."""
    monkeypatch.setattr(crawl.revision, "probe_sources",
                        lambda seeds, **kw: (_ for _ in ()).throw(AssertionError("불리면 안 된다")))
    crawl.crawl_due(source_ids=["fresh"])
    assert [d[0] for d in dispatched] == ["fresh"]


def test_crawl_source_reports_superseded_editions(fake_crawl, monkeypatch) -> None:
    """약관: 새 판 slug 가 같은 상품의 옛 slug 를 대체했다 — 받은 뒤에 판정하고 결과에 담는다."""
    monkeypatch.setattr(crawl.crawler_run, "run",
                        lambda sid, **kw: FakeResult(changed_slugs=["t-new"], new_slugs=["t-new"]))
    monkeypatch.setattr(crawl.registry, "build", lambda sid: object())
    monkeypatch.setattr(crawl.revision, "superseded",
                        lambda src, new: [_verdict("stale", "t-old", "superseded", "t-old", "t-new")])
    out = crawl.crawl_source("stale", "due")
    assert out["superseded"] == [("t-old", "t-new")]


# ------------------------------------------------------------------- crawl_source

def test_crawl_source_reports_unavailable_without_retrying(monkeypatch, fake_crawl) -> None:
    """키 미설정은 실패가 아니라 사람이 고쳐야 하는 것이다 — 예외가 아니라 정상 반환이다.

    예외로 올리면 `autoretry_for` 가 세 번 더 걸어서 같은 결과를 세 번 더 받는다.
    """
    monkeypatch.setattr(crawl.crawler_run, "run",
                        lambda sid, **kw: FakeResult(unavailable="LAW_OC 가 없다"))
    out = crawl.crawl_source("law-x", "manual")
    assert out == {"source_id": "law-x", "unavailable": "LAW_OC 가 없다"}


def test_crawl_source_stops_at_collection(fake_crawl) -> None:
    """**적재로 이어 붙이지 않는다** (메모 ③). 바뀐 것은 알리기만 한다."""
    out = crawl.crawl_source("stale", "due")
    assert out["changed_slugs"] == ["doc-1"]
    assert out["fetched"] == 3 and out["run_id"] == "20260829-040000"


def test_crawl_source_retries_are_bounded(fake_crawl) -> None:
    """재시도는 **지수 백오프로 3회까지** 다 (RAG-001 원칙 5). 무한히 걸면 예절을 깬다."""
    assert crawl.crawl_source.max_retries == 3
    assert crawl.crawl_source.retry_backoff is True


def test_crawl_source_does_not_reach_into_loading(fake_crawl) -> None:
    """반환값에 적재 흔적이 없다 — 수집에서 멈춘다는 것을 계약으로 박아 둔다 (메모 ③)."""
    out = crawl.crawl_source("stale", "due")
    assert "loaded" not in out and "chunks" not in out


# -------------------------------------------------------------- Beat 등록

def test_beat_registers_exactly_one_crawl_schedule() -> None:
    """원칙 4 — 소스 30개의 주기를 Beat 에 30줄로 옮겨 적지 않는다."""
    crawl_entries = [e for e in app.conf.beat_schedule.values()
                     if str(e["task"]).startswith("daengs_life.tasks.crawl.")]
    assert len(crawl_entries) == 1
    assert crawl_entries[0]["task"] == "daengs_life.tasks.crawl.crawl_due"


def test_the_worker_actually_loads_the_crawl_module() -> None:
    """`include` 에 없으면 Beat 는 보내는데 워커가 '등록 안 된 태스크'라며 버린다."""
    assert "daengs_life.tasks.crawl" in app.conf.include
    assert "daengs_life.tasks.crawl.crawl_due" in app.tasks


def test_the_crawl_schedule_goes_to_its_own_queue() -> None:
    """크롤과 프리페치가 같은 큐에 있으면 `--concurrency 1` 워커가 둘 다 먹는다.

    그러면 04:00 크롤이 도는 10~15분 동안 프리페치가 줄을 서고, 끝나는 순간 10여 개가 몰아서
    실행되며 팀 공용 일 예산을 쓴다 (D-019). 큐가 갈려 있어야 크롤 워커가 크롤만 먹는다.
    """
    crawl_entry = app.conf.beat_schedule["crawl-due-sources"]
    assert crawl_entry["options"]["queue"] == "crawl"
    # 프리페치는 기본 큐 그대로다 — 옮기면 RT-002 의 워커가 못 받는다.
    assert "options" not in app.conf.beat_schedule["warm-active-grids"]


def test_the_schedule_is_daily_at_dawn_kst() -> None:
    entry = app.conf.beat_schedule["crawl-due-sources"]["schedule"]
    assert entry.hour == {4} and entry.minute == {0}
    assert app.conf.timezone == "Asia/Seoul"
