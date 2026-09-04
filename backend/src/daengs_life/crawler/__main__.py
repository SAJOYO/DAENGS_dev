"""CLI.

  python -m crawler list                                   # yaml 소스 목록 + 구현 여부
  python -m crawler due                                    # 지금 due 인 소스 — Beat 가 볼 것과 같은 판정
  python -m crawler run --source easylaw-pet --dry-run --limit 3
  python -m crawler run --source easylaw-pet [--force]
  python -m crawler revisions [--source law-drf-api]     # 개정 판정만 — 받지 않는다 (RAG-054)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import run as run_mod
from .core import cadence, config, registry, revision

# 윈도우 콘솔의 기본 인코딩(cp949)으로는 한글 안내 메시지가 깨지고 일부 기호는 아예 예외를 낸다.
# errors="replace" 라 어떤 터미널에서도 출력 때문에 죽지는 않는다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# 화면에 찍을 이름. 키는 `run.TargetOutcome.state` 다.
STATE_LABEL = {
    "new": "NEW",
    "changed": "CHANGED",
    "raw-missing": "RAW-MISSING",     # meta 만 있고 원본이 없어 다시 받음 (다른 PC 에서 clone 한 경우)
    "forced": "FORCED",
    "same": "same",
}


def cmd_list(_: argparse.Namespace) -> int:
    seeds = registry.load_seeds()
    for sid, s in seeds.items():
        impl = "x" if registry.resolve(s) else " "       # ASCII 고정 — 윈도우 cp949 콘솔이 못 찍는다
        print(f"[{impl}] {sid:28s} {s['domain']:13s} {s['method']:9s} auth={s['auth']:11s} {s['status']}")
    return 0


def cmd_due(_: argparse.Namespace) -> int:
    """**아무것도 받지 않고** 판정만 찍는다. Beat 의 `crawl_due` 가 고르는 것과 같은 함수를 탄다.

    코퍼스를 옮긴 직후 "로그가 같이 왔나"를 확인하는 자리다 (RAG-050). 로그가 안 왔으면 후보
    전부가 `[due]` 로 뜬다 — 그 상태로 04:00 을 넘기면 서버가 처음부터 다 받아 개발 PC 와 다른
    코퍼스를 만든다. `crawl_log.jsonl` 이 아예 없으면 첫 줄에 그렇게 말한다.
    """
    seeds = registry.load_seeds()
    now = datetime.now(config.KST)
    log_path = config.CRAWL_LOG
    if log_path is None or not log_path.is_file():
        print(f"crawl_log.jsonl 이 없다 ({log_path}) — 후보 전부가 due 다", file=sys.stderr)
    last = cadence.last_success()

    due = candidates = 0
    for sid, s in seeds.items():
        implemented = registry.resolve(s) is not None
        reason = cadence.skip_reason(s, implemented=implemented)
        if reason is not None:
            print(f"[skip] {sid:28s} {reason}")
            continue
        candidates += 1
        when = last.get(sid)
        if cadence.is_due(s, when, now):
            due += 1
            tag = "[due] "
        else:
            tag = "[    ]"
        ago = f"{(now - when).days}d ago" if when else "never"
        print(f"{tag} {sid:28s} {cadence.cadence_of(s):9s} last={when.isoformat(timespec='minutes') if when else '-':26s} {ago}")
    print(f"\ndue {due} / 후보 {candidates} / 시드 {len(seeds)}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """**수집 자체는 `run.run()` 이 한다** (RAG-044). 여기 남은 것은 화면 출력뿐이다.

    옮기기 전에는 이 함수 안에 루프가 있어 Celery 태스크가 부를 것이 없었다. 콜백으로 찍는
    이유는 받는 즉시 한 줄씩 나와야 해서다 — 대상당 1.5초 이상이라 다 끝나고 찍으면 멈춘 것처럼
    보인다.
    """
    def on_discover(n: int, limit: int) -> None:
        print(f"[{args.source}] discover: {n} targets" + (f" (limit {limit})" if limit else ""))

    def on_target(o: run_mod.TargetOutcome) -> None:
        tag = f"[{o.index}/{o.total}] {o.slug}"
        if o.state == "robots":
            print(f"{tag}  SKIP robots.txt disallow  {o.url}")
        elif o.state == "net-fail":
            print(f"{tag}  FAIL {o.error}")
        elif o.state == "http-fail":
            print(f"{tag}  HTTP {o.status}  {o.url}")
        elif o.state == "extract-fail":
            print(f"{tag}  EXTRACT ERROR\n{o.detail}")
        else:
            print(f"{tag}  {STATE_LABEL[o.state]:11s} {o.status} {o.bytes:>7,}B  {o.elapsed_sec:.1f}s")
            ext = o.extracted
            if (args.dry_run or args.verbose) and ext is not None:
                print(f"        title   : {ext.title}")
                print(f"        chars   : {len(ext.text):,}   published_at: {ext.published_at}")
                print(f"        cites   : {len(ext.cites)}  {ext.cites[:4]}")
                print(f"        preview : {ext.text[:160].replace(chr(10), ' ')}")
                if ext.extra:
                    print(f"        extra   : {ext.extra}")
            elif o.raw_file:
                print(f"        -> raw/{o.raw_file}")

    result = run_mod.run(args.source, limit=args.limit, dry_run=args.dry_run, force=args.force,
                         on_discover=on_discover, on_target=on_target)

    if result.unavailable:
        # 키 미설정·시드 URL 사망처럼 '고쳐야 실행되는' 조건. 스택트레이스 대신 안내만 낸다.
        print(f"[{result.source_id}] 수집 불가\n  {result.unavailable}", file=sys.stderr)
        return 2

    print(f"\n[{result.source_id}] fetched {result.fetched}, changed {result.changed}, "
          f"failed {result.failed}, skipped {result.skipped}"
          + (f", restored {result.restored} (meta only, raw was missing)" if result.restored else "")
          + ("   (dry-run: nothing written)" if args.dry_run else f"   run_id={result.run_id}"))
    return 1 if result.failed else 0


def cmd_revisions(args: argparse.Namespace) -> int:
    """개정 판정 — **원본을 받지 않고** 시행일자만 대조한다. Beat 의 `crawl_due` 가 매일 하는 것과 같은 함수.

    기본은 cadence `manual` 인 소스(법령)다. `--source` 로 하나를 집으면 cadence 와 무관하게 본다.
    종료 코드는 판정 결과와 무관하게 0 이다 — 개정이 있다는 것은 실패가 아니다.
    """
    seeds = registry.load_seeds()
    if args.source:
        if args.source not in seeds:
            print(f"unknown source id: {args.source}", file=sys.stderr)
            return 2
        seeds = {args.source: seeds[args.source]}
    verdicts = revision.probe_sources(seeds, only_manual=not args.source)
    if not verdicts:
        print("판정할 소스가 없다 — revision_key 가 있는 manual 소스가 없거나 --source 가 그런 소스가 아니다")
        return 0
    total = 0
    for sid, vs in verdicts.items():
        if not vs:
            print(f"[{sid}] 조회 실패 또는 대상 없음 (위 경고 참고)")
            continue
        for v in vs:
            tag = {"same": "[    ]", "new": "[new ]", "revised": "[REV ]"}.get(v.kind, f"[{v.kind}]")
            print(f"{tag} {sid:24s} {v.slug:44s} {v.previous or '-':10s} → {v.current or '-'}")
            total += v.actionable
    print(f"\n받아야 할 것 {total}건")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m crawler")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="seed_sources.yaml 소스 목록과 구현 여부").set_defaults(fn=cmd_list)
    sub.add_parser("due", help="지금 due 인 소스 — 받지 않고 판정만 (Beat 와 같은 함수)").set_defaults(fn=cmd_due)

    r = sub.add_parser("run", help="소스 하나 수집")
    r.add_argument("--source", required=True, help="seed_sources.yaml 의 id")
    r.add_argument("--limit", type=int, default=0, help="앞에서 N개만")
    r.add_argument("--dry-run", action="store_true", help="아무것도 쓰지 않고 추출 결과만 출력")
    r.add_argument("--force", action="store_true", help="sha256 같아도 새 파일로 저장")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(fn=cmd_run)

    v = sub.add_parser("revisions", help="개정 판정 — 시행일자만 대조, 받지 않음 (기본: cadence manual 소스)")
    v.add_argument("--source", help="이 소스 하나만 (cadence 무관)")
    v.set_defaults(fn=cmd_revisions)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
