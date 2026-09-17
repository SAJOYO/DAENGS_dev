"""보행 변화 관찰 해설 에이전트(D-080)를 **실제 Gemini 로** 잰다 (#575).

    uv run python -m daengs_evals.gait_change.collect --label gc_v1
    uv run python -m daengs_evals.gait_change.collect --label gc_v1 --resume
    uv run python -m daengs_evals.gait_change.report --label gc_v1

#568 의 단위 테스트 99건은 가짜 LLM 이라 "모델이 이렇게 내면 코드가 이렇게 처리한다" 까지만
고정한다. 이 패키지는 그 반대를 잰다 — **실제 모델이 얼마나 자주 규칙을 지키는가**, 지키지
않을 때 코드 가드가 몇 번 막았는가, 그리고 **가드가 못 잡고 사용자에게 나간 것이 있는가**.

    questions.py   비교 갈래 4종 × 20문항. 갈래별 `GaitCompareContext` 도 여기서 만든다
    collect.py     셀 = (문항, 반복). 무료 키라 느리게 — 간격 · 백오프 · 실행당 상한 · 재개
    checks.py      셀 하나의 코드 검사. 최종 답과 모델 원출력을 **따로** 본다
    report.py      합산 · 리포트 (md · json)

**판정기(LLM-as-judge)는 여기 없다.** 안전 규칙은 기계적으로 셀 수 있어서 코드가 정확하고,
판정기를 붙이면 잡음만 는다. `skin_guidance`(#558)와 같은 판단이다.

## 왜 지금인가

**D-081** 이 자유 질문을 해설로 넘기면 모델이 받는 입력이 지금(칩의 고정 문장 하나)보다
넓어진다. 피부 쪽에서 앞 대화를 열었을 때 `농피증` 이 출력 가드를 그대로 통과한 전례가 있다 —
가드 어휘 목록에 없는 낱말이었다. **라우팅을 열기 전에 현재 구멍을 먼저 잰다.**

## 이 패키지는 운영을 바꾸지 않는다

운영 어댑터 `GaitCapabilityAdapter` 를 **그대로** 부르고 모델 호출만 `generate=` 로 감싼다.
생성 설정도 운영의 `gait_generation_config()` 그 객체다 — 숫자를 다시 적으면 운영과 평가가
조용히 갈린다.
"""

from __future__ import annotations
