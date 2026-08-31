#!/usr/bin/env python
"""크롤 코퍼스를 zip 하나로 싼다 — 서버로 옮기기 위해. 의존성 0.

    uv run --no-project --python 3.12 python tools/corpus_pack.py
    uv run --no-project --python 3.12 python tools/corpus_pack.py --data-dir D:/x/data --out corpus.zip
    → corpus-20260830-0412.zip   raw 631 files / 42.1 MB   crawl_log.jsonl 312 lines   sha256 3f9a…

## 왜 있나

서버에는 SSH 도 SMB 도 없다 (열린 것은 DB 5432 와 Redis 6379 뿐). 그래서 코퍼스는
**파일 하나를 사람이 옮기는 것**으로 정했다 (RAG-050). 이 스크립트는 그 파일을 만든다 —
`Compress-Archive` 로도 되지만 그러면 `manifests/` 층이 사라져 서버에서 폴더를 다시
짜야 하고, 몇 개를 쌌는지도 안 남는다.

싸는 것은 둘뿐이다:

    raw/**                       원본 + 같은 이름의 .meta.json  (불변. RAG-008)
    manifests/crawl_log.jsonl    크롤 로그 — **이게 빠지면 이관이 아니다.**

`processed/` 는 싸지 않는다. 파싱·청킹·임베딩은 개발 PC 의 일이고(RAG-044 ⑤) 서버는
수집만 한다. `manifests/seed_sources.yaml` 도 안 싼다 — git 에 있어 체크아웃이 가진다.

⚠ **로그가 왜 같이 가야 하나** — 워커는 `crawl_log.jsonl` 의 소스별 마지막 성공 시각으로
due 를 판정한다 (RAG-044 ③). 원본만 옮기면 서버는 "받은 적 없다"고 보고 04:00 에 후보
전부를 다시 받는다 — 10~15분 걸려 받아 봐야 sha256 이 같아 `same` 으로 끝나는 낭비고,
그날 개정된 문서가 있었으면 개발 PC 와 다른 코퍼스가 된다.

⚠ **옮기기 직전에 싸라.** 공유 코퍼스는 여러 워크트리가 동시에 수집한다 — 이 스크립트를
만들던 날에도 다른 카드가 4분 전에 약관을 받고 있었다. 어제 싼 zip 을 오늘 옮기면 그
사이 받은 것이 서버에 없고, 서버는 그것을 "받은 적 없다"로 보고 다시 받는다.

풀 때(서버, PowerShell):

    Expand-Archive corpus-*.zip -DestinationPath C:\\deploy\\daengs\\corpus -Force

`-Force` 는 같은 이름을 덮어쓴다 — 원본은 불변이고 로그는 서버 것이 상위 집합이 되므로
(이관 뒤에는 서버가 정본이다) 두 번째 이관부터는 방향이 반대다: 서버에서 싸서 개발 PC 에
푼다. 그쪽은 파이썬이 없어 `Compress-Archive` 를 쓴다 — README "코퍼스" 절.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def pack(data_dir: Path, out: Path) -> int:
    raw = data_dir / "raw"
    log = data_dir / "manifests" / "crawl_log.jsonl"
    if not raw.is_dir():
        print(f"raw/ 가 없다: {raw}", file=sys.stderr)
        return 2
    if not log.is_file():
        # 로그 없는 zip 을 만들어 주면 그것을 옮기고 "이관했다"고 믿는다. 만들지 않는다.
        print(f"crawl_log.jsonl 이 없다: {log} — 로그 없는 이관은 이관이 아니다 (위 ⚠)", file=sys.stderr)
        return 2

    files = sorted(p for p in raw.rglob("*") if p.is_file() and p.name != ".gitkeep")
    raw_bytes = sum(p.stat().st_size for p in files)
    with log.open(encoding="utf-8") as f:
        log_lines = sum(1 for line in f if line.strip())

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            z.write(p, p.relative_to(data_dir).as_posix())
        z.write(log, log.relative_to(data_dir).as_posix())

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"{out.name}   raw {len(files)} files / {raw_bytes / 1e6:.1f} MB"
          f"   crawl_log.jsonl {log_lines} lines   zip {out.stat().st_size / 1e6:.1f} MB")
    print(f"sha256 {digest}")
    print()
    print("서버에서 푼 뒤 확인할 것 (<corpus> = 최상단 .env 의 DAENGS_CORPUS_DIR):")
    print(f"  (Get-ChildItem <corpus>\\raw -Recurse -File).Count            → {len(files)}")
    print(f"  (Get-Content <corpus>\\manifests\\crawl_log.jsonl).Count      → {log_lines}")
    print("  docker compose exec crawler-worker uv run --no-sync python -m daengs_life.crawler due")
    print("      → due 가 후보 전체보다 작으면 로그가 같이 온 것")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="크롤 코퍼스(raw/ + crawl_log.jsonl)를 zip 하나로 싼다")
    p.add_argument("--data-dir", type=Path, default=REPO / "data",
                   help="data/ 위치 (기본: 이 체크아웃의 data/. 워크트리면 메인 체크아웃 경로를 줄 것)")
    p.add_argument("--out", type=Path, default=None,
                   help="출력 zip (기본: ./corpus-YYYYMMDD-HHMM.zip)")
    a = p.parse_args(argv)
    out = a.out or Path.cwd() / f"corpus-{datetime.now():%Y%m%d-%H%M}.zip"
    return pack(a.data_dir.resolve(), out)


if __name__ == "__main__":
    sys.exit(main())
