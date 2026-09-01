# 결정: 환경 축(ContextFactor) 폐기 — 2026-08-24

## 결정

`docs/schema_contextfactor_v1.md`에서 1차 확정했던 `absence`/`numeric` 두 dimension을
**폐기한다.** `extraction-prompt-v3` 작성, Neo4j 반영, 코퍼스 재추출 어느 것도 진행하지
않는다. 스키마 문서(`schema_contextfactor_v1.md`)는 삭제하지 않고 그대로 남긴다 —
왜 이 설계가 근거 부족으로 폐기됐는지의 증거이기 때문이다(견종 축 v1 가드레일 코드를
남겨둔 것과 같은 이유).

## 근거

`reports/research_graph_viability_0824/02_env_axis_projection_dev.md`가 재실측한 결과,
`schema_contextfactor_v1.md`가 확정 근거로 든 "혼자→분리불안 260건 비순환 교차"를
분해하니 **83.1%(216/260건)가 이미 기각된 `space_setup`(agenda_0825.md #18)과 동일한
순환 매칭 구조**(환경어 사전과 `TOPICS` 사전이 "혼자 있"/"혼자 두" 문자열을 공유)로만
성립했다. 진짜 독립 신호는 28건(10.8%)뿐이다.

`schema_contextfactor_v1.md` 자체가 이미 "260건은 공기(共起) 횟수이지 추출된 관계가
아니다"라고 한계를 명시해 뒀었는데, 실제로 분해해보니 그 공기 대부분이 사전 설계에서
비롯된 순환 아티팩트였다는 뜻이다. `space_setup`을 기각한 잣대(비순환 근거 0건에 가까움,
사전이 자기 자신을 근거로 삼는 구조)를 그대로 적용하면 absence도 같은 결론에 수렴한다.

부수적으로 numeric dimension도 표본 n=14(1.38%)로, 보류 처리됐던 `trigger_location`
(n=47)보다도 작아 별도로 승격할 근거가 없다.

## 이번에 하지 않은 것

- `schema_contextfactor_v1.md` 삭제 — 하지 않음(위 이유)
- `혼자` 외 다른 환경어(외출→산책 45건, 현관→배변/짖음 47건)의 순환/비순환 재분해 —
  이번 결정은 absence(혼자→분리불안)에만 적용된다. 나머지 환경어 교차가 같은 오염을
  가졌는지는 미확인이며(`02_env_axis_projection_dev.md` §3부 (c)), 이 결정 문서가
  환경 축 전체(8,145건)를 다루는 것은 아니다.

## 다음

그래프 축 실험은 절차/순서 축과 연령대 축으로 전환한다. 상세는
`reports/research_graph_viability_0824/00_SYNTHESIS.md` 질문3, 다음 실측 작업 3·6번.

## 참조

- `reports/research_graph_viability_0824/00_SYNTHESIS.md` (종합 판정, 질문2)
- `reports/research_graph_viability_0824/02_env_axis_projection_dev.md` (재실측 전문)
- `docs/schema_contextfactor_v1.md` (폐기 대상 원 설계, 삭제하지 않음)
- `docs/agenda_0825.md` #18 (space_setup 기각 — 같은 잣대의 선례)
