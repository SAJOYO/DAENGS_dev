"""이미 받아 둔 `.meta.json` 의 `category` 를 **명시한 대로** 다시 붙인다 (RAG-067).

────────────────────────────────────────────────────────────────────────────
왜 필요한가 — `category` 는 크롤 시점에 박힌다
────────────────────────────────────────────────────────────────────────────
    크롤  store.py  → .meta.json 에 category 를 박는다      ← 원천
    파싱  parse     → meta["category"] 를 parsed 헤더로
    청킹  chunk     → parsed 헤더 → 청크
    적재  load      → documents.category

그래서 **소스 클래스의 `category` 를 고쳐도 이미 받아 둔 문서는 안 바뀐다.**
`roadmap.md` §4 가 `A7` 을 "적재만"으로 적어 둔 것은 *비싼 쪽(재인코딩)* 에 대해서는 맞고
*단계 수* 에 대해서는 틀렸다 — 적재 위에 이 정정과 재파싱이 얹힌다.

**재크롤은 답이 아니다.** 새 날짜로 받으면 `doc_id` 가 바뀌고 → `chunk_id` 4,673개가 전부
새것이 되고 → 증분 임베딩이 그것을 "새 행"으로 보아 **다시 인코딩한다**(약 30분).
내용은 한 글자도 안 바뀌는데 그 값을 낸다. 이건 내용 변경이 아니라 **분류 정정**이다.

────────────────────────────────────────────────────────────────────────────
⚠ 왜 "소스 클래스와 동기화"가 아닌가 — 첫 판이 그렇게 짰다가 6건을 되돌릴 뻔했다
────────────────────────────────────────────────────────────────────────────
클래스의 `category` 는 **기본값**이지 그 소스 문서 전부의 값이 아니다. `Target.meta["category"]`
오버라이드가 있어서(RAG-065 ⑥) 한 소스가 두 값을 갖는다:

    nias-pet      클래스 policy · 실제로는 등록제 해설 policy + 사료 해설 **food**
    law-drf-api   클래스 policy · 실제로는 동물보호법 policy + 사료관리법 **food**

"클래스가 원천"으로 짜면 이 6건을 `food` → `policy` 로 **되돌린다.** 미리보기에서 잡혔다.
오버라이드를 알아내려면 `discover()` 를 다시 돌려야 하는데 그건 네트워크다.

그래서 전제를 바꿨다 — **이 파일이 의도한 재분류를 명시한다.** 무엇을 왜 바꾸는지가 코드에
남고, 적어 두지 않은 것은 절대 안 건드린다.

────────────────────────────────────────────────────────────────────────────
⚠ 고친 뒤에는 `rag parse --force` 가 필요하다
────────────────────────────────────────────────────────────────────────────
`is_current()` 가 비교하는 `raw_sha256` 은 **원본 파일(PDF/HTML)의 해시**라 `.meta.json` 을
고쳐도 그대로다. #270 이 넣은 `parser_version` 가드도 이건 못 잡는다 — 파서 판은 안 바뀌었다.
**산출물이 낡았는지 판정할 때 meta 를 안 본다**는 것이 지금 설계의 사각지대이고,
RAG-067 에 그대로 적어 뒀다.

사용:
    uv run python tools/retag_meta_category.py            # 무엇이 바뀔지만 본다
    uv run python tools/retag_meta_category.py --write    # 실제로 고친다
    python -m daengs_life.rag parse --force               # 그 다음 이것
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daengs_life.crawler.core import config                     # noqa: E402

# (source_id, 옛 category) → 새 category.
#
# **옛 값을 함께 적는 것이 안전장치다.** 값이 이미 다르면(사람이 손댔거나 오버라이드가 있거나)
# 건너뛴다 — 이 표는 "무엇을 무엇으로" 를 다 말해야 하고, 짐작으로 덮어쓰지 않는다.
RETAG: dict[tuple[str, str], str] = {
    # 2026-09-06 (RAG-067 / #271) — 보험을 policy 에서 갈랐다. 4,673행 = 코퍼스의 47.5%.
    ("insurer-terms-pdfs", "policy"): "insurance",
    ("knia-disclosure", "policy"): "insurance",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="실제로 고친다. 기본은 미리보기")
    args = ap.parse_args()

    config.require_data_dir()
    changed: list[tuple[str, str, str]] = []
    scanned = 0

    for path in sorted(config.RAW_DIR.rglob("*.meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        scanned += 1
        target = RETAG.get((meta.get("source_id", ""), meta.get("category", "")))
        if target is None:
            continue                    # 표에 없거나 이미 새 값이다 — 멱등이 여기서 나온다
        changed.append((path.name, meta["category"], target))
        if args.write:
            meta["category"] = target
            path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8", newline="\n")

    print(f"훑은 meta {scanned}건 · 고칠 것 {len(changed)}건"
          f"{'  (고쳤다)' if args.write else '  (미리보기 — --write 로 적용)'}")
    for name, before, after in changed:
        print(f"  {before:10s} → {after:10s}  {name}")
    if changed and not args.write:
        print("\n고친 뒤에는 `rag parse --force` 가 필요하다 — 위 ⚠ 참고")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
