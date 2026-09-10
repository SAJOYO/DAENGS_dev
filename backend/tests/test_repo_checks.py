"""`uv run check` 의 검사가 `uv run pytest` 에서도 돌게 하는 배선.

**진입점을 둘로 둔 이유** — 빠른 쪽(`uv run check`, 3초)은 매번 돌리라고 만든 것이고,
느린 쪽(`uv run pytest`, 약 6분)은 어차피 머지 전에 도는 것입니다. 둘 중 무엇을 돌려도
마이그레이션 규칙이 걸리게 하려고 양쪽에 답니다.

목록은 `daengs_backend.cli.check.CHECKS` **한 곳**에만 있습니다. 여기서 다시 적지 마세요 —
두 벌이 되면 한쪽만 고쳐지고, 그때 CI 도 로컬도 안 잡습니다.
"""

import pytest

from daengs_backend.cli import check

#: id 는 `key`(ASCII)를 씁니다. `name` 은 한글이라 pytest 가 이스케이프해 버립니다.
#: 하나만 돌리려면 `uv run pytest -k migration-windows`.
CASES = [pytest.param(item, id=item.key) for item in check.CHECKS]


@pytest.mark.parametrize("item", CASES)
def test_repo_rule(item):
    reason = check.skip_reason(item)
    if reason is not None:
        pytest.skip(f"{item.name} — {reason}")

    result = item.run()
    assert result.returncode == 0, (
        f"{item.name} 실패\n$ {' '.join(item.command)}\n{result.stdout}{result.stderr}"
    )
