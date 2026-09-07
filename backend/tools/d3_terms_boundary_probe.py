"""보험 약관 청크의 절 이름이 **잘린 문장**인지 센다 (D3 / RAG-068).

────────────────────────────────────────────────────────────────────────────
무엇을 재나
────────────────────────────────────────────────────────────────────────────
`insurer_terms_pdfs` 파서는 "4~60자이고 `보통약관`/`특별약관` 으로 끝나는 줄"을 약관 경계로
본다(`_RE_INSURANCE_TERMS`). 그런데 PDF 는 줄폭에 맞춰 문장을 자르므로 **줄 끝이 우연히 그
낱말이 되는 조각**이 걸린다:

    며, 이로써 회사가 지급하여야 할 해약환급금이 있을 때에는 보통약관   ← 51자, 제목으로 잡혔다

그 조각이 청크 id 에 그대로 실려 인용이 문장 중간이 된다. 이 스크립트가 그것을 센다.

    2026-09-06 고치기 전   농협 589 · KB 341 · 삼성 0
    2026-09-06 고친 뒤     농협   2 · KB  16 · 삼성 0

삼성이 원래 0인 것은 그 판형만 레이아웃 경계(`_LAYOUT_FIRMS`)를 쓰기 때문이다 — 나머지 둘은
정규식으로 떨어진다. **그래서 이 스크립트는 삼성이 계속 0인지도 같이 본다** (회귀 감시).

────────────────────────────────────────────────────────────────────────────
판정은 파서와 **같은 함수**를 쓴다
────────────────────────────────────────────────────────────────────────────
`_looks_like_title` 을 그대로 부른다. 여기서 규칙을 다시 쓰면 둘이 갈라지고, 갈라진 줄은
"스크립트는 통과라는데 파서는 안 그런다"로만 보인다 — RAG-042 ③ 이 `BY_SOURCE` 에서 지적한 병이다.

사용: uv run python tools/d3_terms_boundary_probe.py
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from daengs_life.rag.core import config, io                                    # noqa: E402
from daengs_life.rag.stages.parse.parsers.insurance import insurer_terms_pdfs  # noqa: E402

# 청크 id 는 `{문서}#{약관 이름} {조}` 다. 조 앞부분이 약관 이름이다.
_RE_ARTICLE = re.compile(r"\s*제\s*\d+\s*조")


def firm_of(path: Path) -> str:
    name = path.name
    return "kb" if "-kb-" in name else ("nh" if "-nh-" in name else "samsung")


def main() -> int:
    config.require_data_dir()
    names: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for path in io.chunk_files():
        if not path.name.startswith("insurer-terms-pdfs-"):
            continue
        firm = firm_of(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("type") != "chunk":
                continue
            head = _RE_ARTICLE.split(row.get("section") or "")[0].strip()
            if head:
                names[firm][head] += 1

    print(f"{'보험사':8s} {'제목':>8s} {'조각':>8s}   조각 예")
    bad_total = 0
    for firm in ("samsung", "kb", "nh"):
        good = bad = 0
        worst: list[tuple[int, str]] = []
        for name, count in names[firm].items():
            if insurer_terms_pdfs._looks_like_title(name):
                good += count
            else:
                bad += count
                worst.append((count, name))
        bad_total += bad
        sample = max(worst, default=(0, ""))[1][:52]
        print(f"{firm:8s} {good:8d} {bad:8d}   {sample}")
        for count, name in sorted(worst, reverse=True)[1:3]:
            print(f"{'':26s}   -{count:<4d} {name[:52]}")

    # 삼성은 레이아웃 경계를 쓰므로 0이어야 한다 — 0이 아니면 그 경로가 깨진 것이다.
    if names["samsung"] and any(not insurer_terms_pdfs._looks_like_title(n)
                                for n in names["samsung"]):
        print("\n⚠ 삼성에 조각이 생겼다 — 레이아웃 경계(_LAYOUT_FIRMS) 경로를 확인할 것")
    print(f"\n조각 합계 {bad_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
