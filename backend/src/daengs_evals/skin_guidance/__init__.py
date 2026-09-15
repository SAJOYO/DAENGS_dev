"""피부 판정 해설 에이전트(D-079)를 **실제 Gemini 로** 잰다 (#558). 1단계는 판정기 없이 코드로 센다.

    uv run python -m daengs_evals.skin_guidance.collect --label sg_v1
    uv run python -m daengs_evals.skin_guidance.collect --label sg_v1 --resume
    uv run python -m daengs_evals.skin_guidance.report --label sg_v1

#556 의 단위 테스트는 가짜 LLM 이라 "모델이 이렇게 내면 코드가 이렇게 처리한다" 만 고정한다. 이 패키지는
그 반대를 잰다 — **실제 모델이 얼마나 자주 규칙을 지키는가**, 그리고 지키지 않을 때 코드 가드가 몇 번
막았는가.

    questions.py   판정 3종 × 20문항 (보통 · 병명 · 확률 · 약 · 응급 · 무관 · 추세)
    collect.py     셀 = (문항, 반복). 무료 키라 느리게 — 호출 간격 · 백오프 · 실행당 상한 · 재개
    checks.py      셀 하나의 코드 검사. 최종 답의 안전 위반과 모델 원출력의 행동을 따로 본다
    report.py      합산 · 리포트 (md · json)

**판정기(LLM-as-judge)는 여기 없다.** 안전 규칙은 기계적으로 셀 수 있어서 코드가 정확하고, 판정기를
붙이면 잡음만 는다. 답변 품질 같은 부드러운 축은 따로 판정기로 잰다.
"""
