"""이 저장소를 **사본으로 내보낸다** — 팀 모노레포의 `geo/` 가 받는 것.

    uv run python -m scripts.export_copy ../DAENGS_dev/geo

무엇이 빠지나, 그리고 왜:

| 빠지는 것 | 이유 |
| --- | --- |
| `android/` | 앱의 셸은 DAENGS_APP 이다. 이관은 별도이고 백엔드 사본에 낄 이유가 없다 |
| `.github/` | 워크플로는 저장소 루트에서만 돈다. 폴더 안에 두면 조용히 안 돈다 |
| `docs/decisions/` `docs/research/` `docs/explorations/` | **이 저장소의 작업 방식**이다. 받는 쪽은 결정 기록 체계가 따로 있어서, 같이 가면 번호가 서로를 가리킨다 |

`docs/contracts/` 는 **남는다.** 바깥에 주는 것이고, 사본이 존재하는 이유다.

## 링크를 고쳐서 내보내는 이유

빠진 문서를 가리키는 마크다운 링크가 47개다. 그대로 두면 받는 쪽에서 전부 404 다.
그래서 내보낼 때 **원본 저장소의 절대 URL로 바꾼다** — 사본을 읽는 사람이 결정 문서를
보려 하면 원본으로 보내는 게 맞고, 그게 "작업은 원본에서" 와도 같은 방향이다.

**사본을 손으로 만들지 마라.** 이 변환을 빠뜨리면 링크가 깨진 채로 올라가고, 그건 리뷰에서
안 보인다 — diff 에는 멀쩡한 마크다운으로 보인다.
"""

import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ORIGIN = "https://github.com/rkbuhtig/DAENGS_geo/blob/main"

DROP = ("android", ".github", "docs/decisions", "docs/research", "docs/explorations")
# 링크가 이 중 하나로 해석되면 절대 URL 로 바꾼다.
DROPPED_DOCS = ("docs/decisions", "docs/research", "docs/explorations")

_LINK = re.compile(r"\]\((?!https?://|#)([^)]+)\)")


def rewrite_links(text: str, doc: Path) -> tuple[str, int]:
    """`doc`(저장소 상대 경로) 안의 링크 중 빠진 문서를 가리키는 것만 절대 URL로.

    상대 깊이가 파일마다 다르다 (`decisions/x.md` · `../research/x.md` ·
    `../../decisions/x.md`). 문자열 치환으로는 못 잡으므로 **실제로 해석해서** 판단한다.
    """
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        target = match.group(1)
        anchor = ""
        if "#" in target:
            target, anchor = target.split("#", 1)
            anchor = "#" + anchor
        if not target:
            return match.group(0)

        resolved = (doc.parent / target).as_posix()
        # `..` 를 실제로 접는다. Path.resolve() 는 파일이 없으면 쓸 수 없다.
        parts: list[str] = []
        for piece in resolved.split("/"):
            if piece == "..":
                if parts:
                    parts.pop()
            elif piece not in ("", "."):
                parts.append(piece)
        flat = "/".join(parts)

        if not flat.startswith(DROPPED_DOCS):
            return match.group(0)
        count += 1
        return f"]({ORIGIN}/{flat}{anchor})"

    return _LINK.sub(repl, text), count


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    out = Path(sys.argv[1]).resolve()

    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # git archive 는 **추적 파일만** 준다. .env · .venv · local.properties 가 원천적으로 안 샌다.
    archive = subprocess.run(
        ["git", "-C", str(REPO), "archive", "HEAD"], capture_output=True, check=True,
    ).stdout
    import io
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(out, filter="data")

    for path in DROP:
        shutil.rmtree(out / path, ignore_errors=True)

    rewritten = links = 0
    for md in sorted(out.rglob("*.md")):
        rel = md.relative_to(out)
        text = md.read_text(encoding="utf-8")
        new, n = rewrite_links(text, rel)
        if n:
            md.write_text(new, encoding="utf-8", newline="\n")
            rewritten += 1
            links += n

    files = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"{out} ← DAENGS_geo@{head}")
    print(f"  파일 {files}개 (빠진 것: {', '.join(DROP)})")
    print(f"  링크 {links}개를 원본 절대 URL 로 바꿈 ({rewritten}개 문서)")
    print()
    print("받는 쪽에서:")
    print(f'  git add -A geo && git commit -m "chore(geo): DAENGS_geo@{head} 동기화"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
