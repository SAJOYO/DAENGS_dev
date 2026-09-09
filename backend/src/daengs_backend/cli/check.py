"""저장소 규칙 검사 — `uv run check`. **3초 안에 끝나는 것만** 넣습니다.

pytest 가 못 보는 자리를 봅니다. `backend/pyproject.toml` 의 `testpaths = ["tests"]` 라
pytest 는 `backend/tests/` 만 보는데, 마이그레이션 규칙을 지키는 체커는 저장소 루트
`tools/` 에 있어서 **`uv run pytest` 를 아무리 돌려도 한 번도 안 돕니다.** 그래서 2026-09 에
CI 가 그 자리에서만 12건을 잡았습니다 — 그 12건은 로컬에서 재현할 방법 자체가 없었습니다.

⚠️ **여기에 느린 것을 넣지 마세요.** 이 명령의 값어치는 "3초라서 매번 돌린다" 하나입니다.
`uv run pytest` 는 약 9분이고, 둘을 한 덩어리로 묶으면 **3초짜리까지 같이 안 돌게 됩니다.**
그래서 진입점을 둘로 나눠 두었습니다.

⚠️ **ruff 도 넣지 않았습니다.** 지금 지적이 335건 남아 있어(`daengs_life` 86 ·
`daengs_screening` 84 · `tests/` 150 …) 넣으면 **첫날부터 빨간불이라 무시하는 게 기본**이 되고,
그러면 아래 검사까지 같이 오염됩니다. 그 정리는 별도 카드입니다 (#230 "남은 것",
`.github/workflows/backend-tests.yml` 주석도 같은 이유를 답니다).

`sql` 모드는 여기 없습니다 — `psql` 과 **버리는 로컬 Postgres** 가 필요합니다(코드 첫 줄이
`PGHOST` 를 로컬로 강제합니다. 일부러 데이터를 망가뜨리는 검사라 서버 DB 를 겨누면 안 됩니다).
컨테이너 기동이 붙는 순간 3초가 깨지므로 CI 에 남겨 두었습니다.

같은 검사를 `uv run pytest` 도 돕니다 — `backend/tests/test_repo_checks.py` 가 이 파일의
`CHECKS` 를 그대로 읽습니다. **목록은 여기 한 곳에만 둡니다.** 두 벌이 되면 어긋납니다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# check.py → cli → daengs_backend → src → backend → 저장소 루트
ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class Check:
    """`tools/` 스크립트 한 번의 호출."""

    #: pytest 노드 id 와 `-k` 로 쓰는 이름. **ASCII 로 둡니다** — pytest 가 비ASCII 파라미터
    #: id 를 `이름` 처럼 이스케이프해서 CI 로그에서 못 읽고 지목도 안 됩니다.
    key: str
    name: str
    script: str
    args: tuple[str, ...] = field(default=())
    #: Windows 에서만 의미가 있는 검사. 리눅스 CI 에서는 건너뜁니다.
    windows_only: bool = False

    @property
    def command(self) -> list[str]:
        # 스크립트가 ROOT 를 자기 `__file__` 로 잡으므로 cwd 는 무관합니다.
        return [sys.executable, str(ROOT / self.script), *self.args]

    def run(self) -> subprocess.CompletedProcess[str]:
        # check=False 입니다 — 실패를 예외로 띄우지 않고 returncode 로 돌려줘야
        # 첫 실패에서 멈추지 않고 세 검사를 끝까지 돌 수 있습니다.
        return subprocess.run(self.command, capture_output=True, text=True, check=False)


CHECKS: tuple[Check, ...] = (
    Check(
        key="migration-names",
        name="db/migrations 이름·짝",
        script="tools/check_migration_names.py",
    ),
    Check(
        key="migration-coverage",
        name="db/migrations 짝·단언·등록",
        script="tools/check_migration_verification.py",
        args=("coverage",),
    ),
    Check(
        # db-migrate.yml 의 run 블록이 한글 바이트를 cp949 로 깨뜨리지 않는지 봅니다.
        # 개발 PC 가 Windows 라 여기서 그냥 돕니다 — CI 는 이것 하나 때문에
        # windows-latest(분당 2배)를 빌려 씁니다.
        key="migration-windows",
        name="db-migrate.yml 의 Windows 바이트",
        script="tools/check_migration_verification.py",
        args=("windows",),
        windows_only=True,
    ),
)


def skip_reason(check: Check) -> str | None:
    """이 환경에서 건너뛸 이유. 돌려야 하면 None."""
    if check.windows_only and os.name != "nt":
        return "Windows 에서만 의미가 있습니다"
    return None


def main() -> None:
    failed = 0
    for check in CHECKS:
        reason = skip_reason(check)
        if reason is not None:
            print(f"- {check.name} — 건너뜀 ({reason})")
            continue

        result = check.run()
        if result.returncode == 0:
            print(f"OK {check.name}")
            continue

        failed += 1
        print(f"NG {check.name}")
        # 실패한 것만 원문을 보여 줍니다. assert 기반이라 stderr 에 트레이스백이 옵니다.
        for stream in (result.stdout, result.stderr):
            if stream.strip():
                print(stream.rstrip())

    if failed:
        print(f"\n{failed}건 실패. 고치고 다시 돌리세요.")
        raise SystemExit(1)
    print("\n통과. `uv run pytest` 는 따로 돌리세요 (약 9분).")


if __name__ == "__main__":
    main()
