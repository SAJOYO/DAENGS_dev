"""판정기를 믿을 근거 — 축 A · B 가 같이 쓰는 공용 조각.

판정기도 모델이다. 그 판정을 지표로 승격하려면 판정기 자신이 검증돼야 하고, 그 검증은 축마다
따로 구현하면 안 된다 — 문턱이 둘이 되는 순간 "일치율" 이라는 낱말이 두 뜻이 된다
(`answer_quality/screening_rubric.py` 의 경고와 같은 자리).

    openai_client.py   OpenAI 구조화 출력 + 토큰 장부 — 레포에 없던 조각
    hygiene.py         계열 분리 · 날짜 핀 — 지금은 주석뿐인 규칙을 코드로 강제
    agreement.py       Cohen κ · 가중 κ · Krippendorff α · "못 잼" 과 "미달" 의 구분
    stats.py           Wilson CI · 부트스트랩 · McNemar · 검정력
    gate.py            κ 미달이면 숫자를 안 낸다 — 러너와 리포트 두 겹

판정 모델은 OpenAI 로 고정한다. 생성이 전부 Gemini 라 같은 계열이 채점하면 자기편애가 남는다
(`config.py` 의 `openai_judge_model` 주석, 2026-09-07 결정).
"""
