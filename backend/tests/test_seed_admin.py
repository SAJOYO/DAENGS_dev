"""cli/seed_admin.py — 최초 관리자 계정 SQL 생성기.

getpass 가 파이프가 아니라 콘솔에서 직접 읽어서 셸로는 자동 검증이 안 됩니다.
그래서 여기서 monkeypatch 로 대신 넣습니다.

**여기서 지키려는 것은 두 가지입니다** — 평문이 어디에도 남지 않는 것과,
따옴표가 든 입력이 SQL 을 깨지 않는 것.
"""

import sys

import pytest

from daengs_backend.cli import seed_admin
from daengs_backend.core.password import verify_password

GOOD_PASSWORD = "super-secret-pw-1234"


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """getpass 가 돌려줄 값을 순서대로 넣습니다."""

    def _set(*values: str) -> None:
        queue = list(values)
        monkeypatch.setattr(seed_admin.getpass, "getpass", lambda _prompt: queue.pop(0))

    return _set


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["seed-admin", *argv])
    seed_admin.main()


class TestQuote:
    def test_작은따옴표를_두_개로_바꾼다(self) -> None:
        """O'Brien 같은 이름이 SQL 을 깨뜨리면 안 됩니다."""
        assert seed_admin._quote("O'Brien") == "'O''Brien'"

    def test_SQL_을_끊으려는_입력도_리터럴로_남는다(self) -> None:
        injected = "x'); DROP TABLE admin_users; --"

        quoted = seed_admin._quote(injected)

        # 따옴표가 전부 짝을 이루면 문장이 끊기지 않습니다.
        assert quoted.count("'") % 2 == 0
        assert quoted.startswith("'") and quoted.endswith("'")


class TestInsert:
    def test_INSERT_문을_낸다(self, monkeypatch, answers, capsys) -> None:  # noqa: ANN001
        answers(GOOD_PASSWORD, GOOD_PASSWORD)

        _run(monkeypatch, "--login-id", "daengs", "--name", "댕스 관리자")

        out = capsys.readouterr().out
        assert "INSERT INTO admin_users" in out
        assert "'daengs'" in out
        assert "'ADMIN'" in out

    def test_평문은_출력에_없다(self, monkeypatch, answers, capsys) -> None:  # noqa: ANN001
        """해시만 나가야 합니다. 평문이 섞이면 psql 히스토리에 그대로 남습니다."""
        answers(GOOD_PASSWORD, GOOD_PASSWORD)

        _run(monkeypatch, "--login-id", "daengs", "--name", "x")

        assert GOOD_PASSWORD not in capsys.readouterr().out

    def test_해시가_진짜_그_비밀번호다(self, monkeypatch, answers, capsys) -> None:  # noqa: ANN001
        answers(GOOD_PASSWORD, GOOD_PASSWORD)

        _run(monkeypatch, "--login-id", "daengs", "--name", "x")

        out = capsys.readouterr().out
        digest = out.split("'daengs', '")[1].split("'")[0]
        assert verify_password(digest, GOOD_PASSWORD)

    def test_ON_CONFLICT_를_붙이지_않는다(self, monkeypatch, answers, capsys) -> None:  # noqa: ANN001
        """조용히 넘어가면 "비밀번호가 왜 안 바뀌지" 로 헤맵니다.

        UNIQUE 제약이 시끄럽게 막아 주는 편이 낫습니다.
        """
        answers(GOOD_PASSWORD, GOOD_PASSWORD)

        _run(monkeypatch, "--login-id", "daengs", "--name", "x")

        assert "ON CONFLICT" not in capsys.readouterr().out

    def test_이름의_따옴표가_이스케이프된다(self, monkeypatch, answers, capsys) -> None:  # noqa: ANN001
        answers(GOOD_PASSWORD, GOOD_PASSWORD)

        _run(monkeypatch, "--login-id", "daengs", "--name", "O'Brien")

        assert "'O''Brien'" in capsys.readouterr().out

    def test_매번_다른_해시가_나온다(self, monkeypatch, answers, capsys) -> None:  # noqa: ANN001
        """salt 가 매번 달라서입니다. 여러 번 돌려도 부작용이 없다는 근거이기도 합니다."""
        outs = []
        for _ in range(2):
            answers(GOOD_PASSWORD, GOOD_PASSWORD)
            _run(monkeypatch, "--login-id", "daengs", "--name", "x")
            outs.append(capsys.readouterr().out)

        assert outs[0] != outs[1]


class TestReset:
    def test_UPDATE_문을_낸다(self, monkeypatch, answers, capsys) -> None:  # noqa: ANN001
        answers(GOOD_PASSWORD, GOOD_PASSWORD)

        _run(monkeypatch, "--login-id", "daengs", "--name", "x", "--reset")

        out = capsys.readouterr().out
        assert "UPDATE admin_users" in out
        assert "INSERT" not in out

    def test_기본값은_INSERT_다(self, monkeypatch, answers, capsys) -> None:  # noqa: ANN001
        """덮어쓰기는 --reset 을 붙였을 때만 나와야 합니다."""
        answers(GOOD_PASSWORD, GOOD_PASSWORD)

        _run(monkeypatch, "--login-id", "daengs", "--name", "x")

        assert "UPDATE" not in capsys.readouterr().out


class TestGuards:
    def test_두_번_다르면_멈춘다(self, monkeypatch, answers) -> None:  # noqa: ANN001
        """오타를 알아채는 시점이 "로그인이 안 될 때"면 이미 늦습니다."""
        answers(GOOD_PASSWORD, "typo-typo-typo-1234")

        with pytest.raises(SystemExit):
            _run(monkeypatch, "--login-id", "daengs", "--name", "x")

    def test_짧으면_멈춘다(self, monkeypatch, answers) -> None:  # noqa: ANN001
        answers("short", "short")

        with pytest.raises(SystemExit):
            _run(monkeypatch, "--login-id", "daengs", "--name", "x")

    def test_아이디가_길면_멈춘다(self, monkeypatch, answers) -> None:  # noqa: ANN001
        """login_id 는 VARCHAR(50) 입니다. SQL 을 실행하고 나서 알면 늦습니다."""
        answers(GOOD_PASSWORD, GOOD_PASSWORD)

        with pytest.raises(SystemExit):
            _run(monkeypatch, "--login-id", "x" * 51, "--name", "x")

    def test_모르는_role_은_거부한다(self, monkeypatch, answers) -> None:  # noqa: ANN001
        """DB 의 CHECK 제약과 같은 목록입니다. 여기서 막아야 붙여넣기 전에 압니다."""
        answers(GOOD_PASSWORD, GOOD_PASSWORD)

        with pytest.raises(SystemExit):
            _run(monkeypatch, "--login-id", "daengs", "--name", "x", "--role", "GOD")
