"""CLI.

  python -m crawler list                                   # yaml 소스 목록 + 구현 여부
  python -m crawler run --source easylaw-pet --dry-run --limit 3
  python -m crawler run --source easylaw-pet [--force]
"""
from __future__ import annotations

import argparse
import sys
import traceback

from .core import config, registry
from .core.fetch import Fetcher
from .core.store import Store

# 윈도우 콘솔의 기본 인코딩(cp949)으로는 한글 안내 메시지가 깨지고 일부 기호는 아예 예외를 낸다.
# errors="replace" 라 어떤 터미널에서도 출력 때문에 죽지는 않는다.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


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


def cmd_run(args: argparse.Namespace) -> int:
    src = registry.build(args.source)
    store = Store(dry_run=args.dry_run, force=args.force)
    n_fetched = n_changed = n_failed = n_skipped = n_restored = 0

    with Fetcher() as fetcher:
        try:
            targets = src.discover(fetcher)
        except RuntimeError as e:
            # 키 미설정·시드 URL 사망처럼 '고쳐야 실행되는' 조건. 스택트레이스 대신 안내만 낸다.
            print(f"[{src.id}] 수집 불가\n  {e}", file=sys.stderr)
            return 2
        print(f"[{src.id}] discover: {len(targets)} targets" + (f" (limit {args.limit})" if args.limit else ""))
        if args.limit:
            targets = targets[: args.limit]

        for i, t in enumerate(targets, 1):
            tag = f"[{i}/{len(targets)}] {t.slug}"
            if not fetcher.allowed(t.url):
                print(f"{tag}  SKIP robots.txt disallow  {config.redact(t.url)}")
                store.log(src, t, status=None, result=None, error="robots disallow")
                n_skipped += 1
                continue
            try:
                res = fetcher.get(t.url)
            except Exception as e:                        # 재시도 끝에도 실패
                print(f"{tag}  FAIL {e}")
                store.log(src, t, status=None, result=None, error=str(e))
                n_failed += 1
                continue
            if not res.ok:
                print(f"{tag}  HTTP {res.status}  {config.redact(t.url)}")
                store.log(src, t, status=res.status, result=None, error=f"HTTP {res.status}")
                n_failed += 1
                continue

            try:
                ext = src.extract(res, t)
            except Exception:
                print(f"{tag}  EXTRACT ERROR\n{traceback.format_exc()}")
                store.log(src, t, status=res.status, result=None, error="extract error")
                n_failed += 1
                continue

            result = store.save(src, t, res, ext)
            store.log(src, t, status=res.status, result=result)
            n_fetched += 1
            n_changed += int(result.changed)
            n_restored += int(result.reason == "raw-missing")

            state = STATE_LABEL[result.reason]
            print(f"{tag}  {state:11s} {res.status} {len(res.content):>7,}B  {res.elapsed_sec:.1f}s")
            if args.dry_run or args.verbose:
                print(f"        title   : {ext.title}")
                print(f"        chars   : {len(ext.text):,}   published_at: {ext.published_at}")
                print(f"        cites   : {len(ext.cites)}  {ext.cites[:4]}")
                print(f"        preview : {ext.text[:160].replace(chr(10), ' ')}")
                if ext.extra:
                    print(f"        extra   : {ext.extra}")
            elif result.raw_file:
                print(f"        -> raw/{result.raw_file}")

    print(f"\n[{src.id}] fetched {n_fetched}, changed {n_changed}, failed {n_failed}, skipped {n_skipped}"
          + (f", restored {n_restored} (meta only, raw was missing)" if n_restored else "")
          + ("   (dry-run: nothing written)" if args.dry_run else f"   run_id={store.run_id}"))
    return 1 if n_failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m crawler")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="seed_sources.yaml 소스 목록과 구현 여부").set_defaults(fn=cmd_list)

    r = sub.add_parser("run", help="소스 하나 수집")
    r.add_argument("--source", required=True, help="seed_sources.yaml 의 id")
    r.add_argument("--limit", type=int, default=0, help="앞에서 N개만")
    r.add_argument("--dry-run", action="store_true", help="아무것도 쓰지 않고 추출 결과만 출력")
    r.add_argument("--force", action="store_true", help="sha256 같아도 새 파일로 저장")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(fn=cmd_run)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
