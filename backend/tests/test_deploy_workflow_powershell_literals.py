"""Regression guard for the Windows deploy step that failed to parse (runs 33726176205 … 33728887673).

The self-hosted Windows runner hands the inline `run:` body to Windows PowerShell 5.1 as a temp
.ps1; a Korean single-quoted literal in the executable part of the "Compose 서비스 기동" step
(`throw '배포 변경 파일을 …'`) arrived byte-mangled, the quote never closed, and the whole step
— including `docker compose restart backend` — never executed (fix/deploy-powershell-parser).
Comments are fine (the parser drops them); executable single-quoted literals must stay ASCII.
No YAML library: the block is located by its step name and its comment lines are ignored.
"""

from __future__ import annotations

import re
from pathlib import Path

DEPLOY_YML = Path(__file__).parents[2] / ".github" / "workflows" / "deploy.yml"
STEP_NAME = "name: Compose 서비스 기동"
_SINGLE_QUOTED = re.compile(r"'([^']*)'")


def _compose_step_body() -> list[str]:
    lines = DEPLOY_YML.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if STEP_NAME in line)
    assert "run: |" in lines[start + 1]
    body: list[str] = []
    for line in lines[start + 2 :]:
        if line.strip() and not line.startswith("          "):
            break
        body.append(line.strip())
    return body


def test_compose_step_executable_single_quoted_literals_are_ascii() -> None:
    offenders = [
        (line, literal)
        for line in _compose_step_body()
        if line and not line.startswith("#")
        for literal in _SINGLE_QUOTED.findall(line)
        if not literal.isascii()
    ]
    assert offenders == [], offenders


def test_compose_step_keeps_the_explicit_backend_restart_after_up() -> None:
    body = [line for line in _compose_step_body() if line and not line.startswith("#")]
    assert body.index("docker compose up -d") < body.index("docker compose restart backend")
