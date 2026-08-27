"""테스트가 인용한 `Decision: #N` 이 실재하는지 지킨다.

**왜 필요한가**: 결정 번호를 손으로 적으면 틀린다. 틀린 번호는 없는 번호보다 나쁘다 —
다음 사람과 에이전트가 **엉뚱한 결정을 근거로 삼고** 멀쩡한 테스트를 지우거나, 죽은
계약을 살아 있다고 믿는다. 규율로 막지 말고 여기서 막는다.

규칙은 "모든 테스트에 결정을 달아라"가 아니다. **"달았으면 실재해야 한다"** 이다.
정책·제품 결정에서 직접 파생된 테스트에만 붙인다.
"""

import re
from pathlib import Path

TESTS_DIR = Path(__file__).parent
DECISIONS_MD = TESTS_DIR.parent / "docs" / "decisions" / "README.md"

# 표의 첫 칸이 결정 번호다: `| 37 | **undo 의 단위는...` .
_DECISION_ROW = re.compile(r"^\|\s*(\d+)\s*\|", re.MULTILINE)
# 인용 형식: `Decision: #35` 또는 `Decision: #35, #18`
_CITATION = re.compile(r"Decision:\s*((?:#\d+\s*,?\s*)+)")
_NUMBER = re.compile(r"#(\d+)")


def declared_decisions() -> list[int]:
    """표에 적힌 순서 그대로. **중복을 지우지 않는다** — 아래 가드가 그걸 본다."""
    return [int(n) for n in _DECISION_ROW.findall(DECISIONS_MD.read_text(encoding="utf-8"))]


def known_decisions() -> set[int]:
    return set(declared_decisions())


def citations() -> list[tuple[Path, int]]:
    """이 파일 자신은 제외한다.

    형식을 설명하려면 여기에 예시(`Decision: #35`)를 적어야 하는데, 그걸 세면 **다른
    테스트의 인용이 전부 사라져도 가드가 통과한다.** 자기 자신으로 만족하는 검사는
    검사가 아니다.
    """
    found: list[tuple[Path, int]] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path.resolve() == Path(__file__).resolve():
            continue
        text = path.read_text(encoding="utf-8")
        for block in _CITATION.findall(text):
            found += [(path, int(n)) for n in _NUMBER.findall(block)]
    return found


def test_cited_decisions_exist():
    known = known_decisions()
    assert known, f"결정 표를 못 읽었다: {DECISIONS_MD}"

    missing = sorted({
        (path.relative_to(TESTS_DIR).as_posix(), number)
        for path, number in citations()
        if number not in known
    })

    assert not missing, (
        "실재하지 않는 결정을 인용한다. 번호를 고치거나 결정 문서에 추가하라:\n"
        + "\n".join(f"  {path} → #{number}" for path, number in missing)
    )


def test_the_convention_is_still_in_use():
    """인용이 0건이면 형식이 바뀌었거나 규칙이 조용히 사라진 것이다 — 위 검사가 무력해진다."""
    assert citations(), "Decision: #N 인용이 하나도 없다. 형식이 바뀌었는지 확인하라"


def test_no_two_decisions_claim_the_same_number():
    """번호는 결정의 이름이다. 둘이 같은 번호를 잡으면 인용이 어느 쪽인지 알 수 없다.

    **어떻게 일어나나**: 두 PR 이 각자 브랜치에서 다음 번호를 집는다. 먼저 머지된 쪽이
    쓰던 번호를 나중 쪽이 이미 문서에 박아둔 채로 들어온다. 실제로 #63 이 그렇게 두 번
    쓰였고(커뮤니티 근거 기각 · Place 우선 발견), 인용 20여 곳이 파일마다 다른 결정을
    가리키게 됐다. 존재 검사만으로는 안 잡힌다 — 둘 다 존재하니까.

    `migrations/` 의 `011` 두 개와 같은 실패다. 그때는 우연히 무사했고, 이번엔 문서가
    조용히 거짓말을 했다.
    """
    numbers = declared_decisions()
    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not dupes, f"같은 번호를 두 결정이 잡았다: {dupes}"
