"""긴급도 병합 규칙. **두 층으로 갈린 이유**가 여기 다 들어 있다.

한 값으로 합치면 둘 중 하나가 반드시 틀린다:
  최댓값만 쓰면  → 규칙 오탐 하나에 세션이 응급 모드에 갇히고 사용자가 못 내린다
  사용자만 쓰면  → "안 급해요" 한마디에 호흡 이상 경고가 사라진다
그래서 안전 표면은 최댓값, 행동 계획은 사용자 우선으로 **따로** 뽑는다.
"""

import pytest
from pydantic import ValidationError

from app.discovery.semantics import (
    TimeIntent,
    UrgencySignal,
    planning_urgency,
    safety_urgency,
)

USER_CALM = UrgencySignal(value="normal", origin="user")
USER_RUSH = UrgencySignal(value="urgent", origin="user")
RULE_RUSH = UrgencySignal(value="urgent", origin="rule", reason="breathing_safety_rule")


def test_no_signal_is_normal():
    assert safety_urgency([]) == ("normal", [])
    assert planning_urgency([]) == "normal"


def test_safety_takes_the_max_even_when_the_user_says_it_is_fine():
    """안전 문구는 사용자가 억제할 수 없다. 이유도 같이 나와야 말해줄 수 있다."""
    value, reasons = safety_urgency([USER_CALM, RULE_RUSH])
    assert value == "urgent"
    assert "breathing_safety_rule" in reasons


def test_planning_lets_the_user_stand_down():
    """행동 계획은 사용자가 이긴다.

    "예전에 숨을 헐떡인 적 있어서 검진 받으려고요"에 규칙이 걸려도, 사용자가 안 급하다면
    차량 우선·정렬 변경·전화 CTA 로 세션을 끌고 가면 안 된다.
    """
    assert planning_urgency([USER_CALM, RULE_RUSH]) == "normal"


def test_planning_follows_the_rule_when_the_user_said_nothing():
    """사용자가 긴급도를 말한 적 없으면 규칙이 이끈다 — 침묵은 부정이 아니다."""
    assert planning_urgency([RULE_RUSH]) == "urgent"


def test_user_can_also_raise_urgency():
    assert planning_urgency([USER_RUSH]) == "urgent"
    assert safety_urgency([USER_RUSH])[0] == "urgent"


def test_the_two_layers_disagree_on_purpose():
    """같은 입력에서 서로 다른 답이 나오는 게 설계다 — 경고는 남기고 계획은 사용자를 따른다."""
    signals = [USER_CALM, RULE_RUSH]
    assert safety_urgency(signals)[0] == "urgent"
    assert planning_urgency(signals) == "normal"


def test_time_intent_requires_a_known_kind():
    """`at` 하나로 뭉쳐 있던 것을 가른 이유가 kind 다. 아무 값이나 받으면 도로아미타불."""
    with pytest.raises(ValidationError):
        TimeIntent(kind="whenever", at="2026-08-20T15:00:00Z")
    with pytest.raises(ValidationError):
        TimeIntent(kind="depart_at", at="2026-08-20T15:00:00")
