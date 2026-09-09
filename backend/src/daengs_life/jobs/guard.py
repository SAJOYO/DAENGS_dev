"""적재 가드 — 사람 승인 대신 기계가 보는 셋 (D-062, `docs/deploy/corpus-pipeline.md` §3).

① 행 수 급감: 적재 뒤 남을 행이 지금보다 `max_drop` 비율 이상 적으면 막는다. 코퍼스 절반이
   파싱에서 조용히 빠졌을 때 stale prune 이 그 절반을 지우는 사고를 막는 자리다.
② 파서 예외는 여기 없다 — `rag parse` 가 예외 1건이면 종료 코드 1 이라 `stages.run_parse` 가 멈춘다.
③ 메타데이터 손실: `stages.load.metadata_loss` 가 잡은 키(RAG-066 ①). 예전에 `org` 2,592행이
   그렇게 지워졌고 아무 에러도 안 났다.

**순수 함수다.** DB 도 파일도 안 본다 — 그래서 숫자만 넣고 테스트한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Verdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def check(before: int, planned: int, losing: list[tuple[str, int, int]], *,
          max_drop: float = 0.2) -> Verdict:
    """`before` 지금 행 수 · `planned` 적재 뒤 행 수 · `losing` = metadata_loss() 반환값."""
    reasons: list[str] = []

    if before > 0 and planned < before:
        drop = (before - planned) / before
        if drop >= max_drop:
            reasons.append(f"행 수가 {before:,} → {planned:,} 로 {drop:.0%} 줄어든다"
                           f" (한계 {max_drop:.0%})")

    for key, in_db, incoming in losing:
        reasons.append(f"메타 키 {key!r} 가 사라진다 (DB {in_db:,}행 → 이번 {incoming}행)")

    return Verdict(ok=not reasons, reasons=reasons)


__all__ = ["Verdict", "check"]
