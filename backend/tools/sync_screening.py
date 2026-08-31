r"""원본 저장소에서 스크리닝 코드를 가져옵니다 (D-039).

    uv run python tools/sync_screening.py C:\path\to\deeplearning_test
    uv run python tools/sync_screening.py <원본> --check     # 쓰지 않고 차이만

왜 스크립트가 필요한가
----------------------
`daengs_screening/` 은 **원본 저장소의 사본**입니다 —
[gayeoniee/deeplearning_test](https://github.com/gayeoniee/deeplearning_test).
전에는 최상위 `skin-screening/` 이라 파일을 **그냥 복사**하면 끝이었고
"바이트 단위 동일" 로 확인할 수 있었습니다.

backend 안으로 들어오면서 패키지 이름이 `src` → `daengs_screening` 으로 바뀌어
그 방법이 깨졌습니다. import 를 손으로 고치면 빠뜨리기 쉬워서, **치환 규칙을
코드로 박아 둡니다.** 2단계 백본 재학습·1단계 확률 보정이 남아 있어 앞으로도
가져올 일이 있습니다.

돌린 뒤 `git diff` 가 비어 있으면 원본과 같다는 뜻입니다.

⚠️ 원본을 고치고 여기로 가져오세요. **여기서 고치면 갈라지고, 갈라져도 아무도
   모릅니다.** 규칙은 `src/daengs_screening/CLAUDE.md`.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # backend/
PKG = HERE / "src" / "daengs_screening"
TESTS = HERE / "tests"

#: 원본 경로 → 사본 경로. 원본 구조가 바뀌면 여기만 고칩니다.
FILES: dict[str, Path] = {
    **{f"src/{m}.py": PKG / f"{m}.py" for m in (
        "agent", "message", "config", "crop", "env",
        "infer", "models", "stages", "calibrate", "data", "evaluate")},
    "tools/box_error.py": PKG / "box_error.py",
    "demo/index.html": PKG / "static" / "index.html",
    "tests/test_agent.py": TESTS / "test_screening_agent.py",
    "tests/test_screening_message.py": TESTS / "test_screening_message.py",
}

#: ⚠️ `serve.py` 는 **가져오지 않습니다.** 원본은 단독 서버(`build_app`)이고
#:   여기는 backend 에 붙는 라우터(`service.py`)라 모양이 다릅니다. 원본의
#:   엔드포인트가 바뀌면 `service.py` 를 손으로 맞추세요.
SKIP_NOTE = "serve.py (원본은 단독 서버, 여기는 라우터 — service.py 를 손으로)"


def rewrite(text: str, *, is_test: bool) -> str:
    """원본의 `src` 패키지 이름을 이 저장소의 것으로 바꿉니다."""
    text = re.sub(r"\bfrom src\.", "from daengs_screening.", text)
    text = re.sub(r"\bfrom src import\b", "from daengs_screening import", text)
    text = re.sub(r"\bimport src\.", "import daengs_screening.", text)
    if is_test:
        # 테스트는 저장소 루트가 아니라 backend/src 를 sys.path 에 넣습니다
        text = text.replace(
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1]))',
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))')
        # 파일 경로로 소스를 읽는 검사들
        text = text.replace('parents[1] / "src" / "agent.py"',
                            'parents[1] / "src" / "daengs_screening" / "agent.py"')
        text = text.replace('/ "src" / "infer.py"',
                            '/ "src" / "daengs_screening"\n                                    / "infer.py"')
        text = text.replace('tmp = Path(__file__).resolve().parents[1] / "README.md"',
                            'tmp = Path(__file__).resolve().parents[1] / "src" / "daengs_screening" / "README.md"')
        text = text.replace(
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))\nimport box_error as be',
            'from daengs_screening import box_error as be')
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("origin", help="deeplearning_test 저장소 경로")
    ap.add_argument("--check", action="store_true", help="쓰지 않고 다른 파일만 보고")
    a = ap.parse_args(argv)

    origin = Path(a.origin).expanduser().resolve()
    if not (origin / "src" / "agent.py").exists():
        print(f"❌ 원본이 아닌 것 같습니다: {origin}\n   (src/agent.py 가 없습니다)")
        return 1

    changed, missing = [], []
    for rel, dst in FILES.items():
        src = origin / rel
        if not src.exists():
            missing.append(rel)
            continue
        text = rewrite(src.read_text(encoding="utf-8"),
                       is_test=rel.startswith("tests/"))
        if dst.exists() and dst.read_text(encoding="utf-8") == text:
            continue
        changed.append(rel)
        if not a.check:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(text, encoding="utf-8")

    print(f"원본: {origin}")
    print(f"  건너뜀: {SKIP_NOTE}")
    if missing:
        print(f"  ⚠️ 원본에 없는 파일 {len(missing)}개: {missing}")
    if not changed:
        print("  ✅ 이미 같습니다 — 가져올 것이 없습니다")
        return 0
    verb = "다름" if a.check else "가져옴"
    print(f"  {verb} {len(changed)}개:")
    for rel in changed:
        print(f"    · {rel}")
    if not a.check:
        print("\n다음: git diff 로 확인하고 tests/test_screening_agent.py 를 돌리세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
