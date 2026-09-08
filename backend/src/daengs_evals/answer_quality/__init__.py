"""답변률 · 답변 품질 측정 (#277). 사람 라벨 없이 돈다.

라우팅 골드(`evals/orchestration_router/`)는 "정책대로 골랐나" 를 채점하고 그 정책이 거절을
포함한다. 이 패키지는 다른 잣대다 — **답변률**(FAILED · CLARIFY 가 아닌 비율)과 **판정기가 매긴
답변 품질**, 그리고 폴백(#279) 전/후의 차이. 두 잣대를 한 파일에 섞지 않는다.

    strata.py             계층 = 주제 × 문체. 코드가 정의하고 생성기·리포트가 읽는다
    questions.py          동결 질문 파일의 스키마 · 로더 · 중복 제거
    generate_questions.py Gemini 로 계층마다 질문을 만들어 `questions_v1.jsonl` 로 동결
    collect.py            질문을 오케스트레이터에 먹여 상태 · 문구 · 결과를 JSONL 로
    anchors.py            코드로 만든 답변과 기대 점수 — 판정기의 자동 검증
    judge.py              루브릭 판정기 (절대 2변형 · 쌍대 위치 교환) 와 앵커 검사
    report.py             `report_v1.md` · `summary_v1.json` — 파일에서 결정론으로

모델을 부르는 명령은 전부 `usage_metadata` 로 토큰을 세고, 한 단계가 예산(기본 400,000)을 넘으면
멈춘다. 자산은 `evals/answer_quality/` 에 둔다.
"""
