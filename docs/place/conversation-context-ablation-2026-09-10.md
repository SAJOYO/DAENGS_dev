# 시설 대화의 화면·직전 행동 문맥 대조

PR #421의 관측을 해석할 때 문맥 부족과 모델/도구의 한계를 분리하기 위한 실험이다.
사용자 지시대로 운영 프롬프트·출력 스키마·정책·답변 렌더러·UI는 고정하고,
모델의 JSON 입력에 현재 화면과 완료된 직전 행동 단서만 추가한다.

## 실행 전에 정한 방법

- [명세](../../backend/evals/place_conversation/context-ablation.v1.json)의 14개 독립 턴을 A/B 각 3회 비교한다.
  A=current-input, B=context-input. 이전 실험의 첫 반복에서 13개 턴 직전 상태를 고정하고,
  수동 취소의 정답이 반대인 반사실 1개를 더한다.
- 두 arm은 동일 query, 이전 필터·스냅샷·선택·시계에서 시작한다.
  앞선 arm의 결과를 다음 arm이나 다음 턴에 이어 주지 않는다.
  이는 한 턴에 대한 문맥 효과를 보는 실험이며 전체 대화의 최종 성공률은 아니다.
- 같은 모델(gemini-3.1-flash-lite), temperature=0, tools/system_instruction/generation_config를 유지한다.
  A/B 실행 순서는 대상 번호와 반복에 따라 번갈아 바꾸고 호출 간격 5초를 공유한다.
- B의 유일한 변경은 input.context다. snapshot의 표시 순서와 대응 장소명/업종/거리/저장 사실,
  선택 장소, 결과 잘림 여부, snapshot 나이와 현재 필터 일치 여부,
  완료된 최근 6개 이벤트의 사용자 문장·수동/채팅 구분·필터 전후·실제 제공 답변을 넣는다.
- 이 추가 정보는 평가 도구가 기존 관측에서 수집하는 실험용 가설이다.
  운영에서 이미 수집·보존·전달되고 있다는 뜻이 아니다. 특히 수동 이벤트 before/after와 제공 답변은
  운영의 최근 사용자 발화 history에 없다.
- 기대값·이전 의미 검토·미래 쿼리·결과 목록 밖 후보는 넣지 않는다. 26개 fixture의 21~26번째 이름/ID도
  주입하지 않는다. 잘림 여부만 현재 스냅샷에서 알 수 있다.

## 비교 대상과 판정

| ID | 고정한 원래 턴 | 역할 |
| --- | --- | --- |
| X01 | C01 t2 | 명확한 주차 해제 대조군 |
| X02 | C04 t2 | 기존 채팅 문맥만으로 가능한 undo 대조군 |
| X03 | C05 t2 | 수동 추가 취소 |
| X04 | U01 t2 | 빈 목록 직후 불만 |
| X05 | U02 t1 | 재미 요청: 제품 정책에 민감한 사례 |
| X06 | U03 t1 | 목록이 있는 상태의 불만 |
| X07 | U04 t1 | 이미 아는 장소 |
| X08 | U05 t1 | 선택 장소에 대한 “거기 없던데?” |
| X09 | U06 t1 | 다음 후보 요청 |
| X10 | U07 t1 | 명시적 상호 제외 |
| X11 | U08 t1 | 상호를 붙인 존재 제보 |
| X12 | U08 t2 | 직전 실제 응답 뒤 폐업 확인 |
| X13 | U09 t2 | 전체 풀의 주차 카페로 명시적 복구 대조군 |
| X14 | C05 반사실 | restaurant에서 cafe를 수동 추가한 뒤 취소. X03과 같은 현재 조건·다른 정답 |

원래 케이스와 기대값은 [이전 관측](../../backend/evals/place_conversation/runs/20260910T075627Z-7996e8c-6de532d692/cases.json)을
따르고, 고정한 턴 기대값을 새 실행 cases.json에 복사한다. X14의 목표만 원래 restaurant 복원이다.
“재미”를 되물을지 업종으로 넓힐지는 이전 보수적 기준을 유지하되 제품 정책 판단 항목으로 별도 표기한다.

다음 축을 따로 읽는다: 의미/대상 해석의 변화, 실제 적용 조건, 새 장소/제외/취소 완료,
답변의 관련성, 근거 없는 사실 유무, 모델 원본 계약 위반.
잘 이해했어도 실행 수단이 없어 못한 경우와, 잘못 해석한 경우를 구분한다.
명확화 응답이 모두 성공이거나 모두 실패라고 보지 않고 현재 단서에서 풀 수 있는 구체적인 질문인지 판단한다.
동작이 같으면 개선으로 세지 않는다. 3회 반복은 일반 자연어 정확도 추정이 아니다.

## 검증 경계와 재실행

backend에서:

~~~powershell
uv run --no-sync pytest -q -rs tests/place/conversation/test_context_ablation.py tests/place/conversation/test_evaluation.py
uv run --no-sync python -m daengs_evals.place_conversation.context_ablation
uv run --no-sync python -m daengs_evals.place_conversation.context_ablation --live --repeat 3 --key-file C:\path\to\.env
~~~

실제 모델 호출 전 타겟 테스트 **14통과, skip 0**(4.57초), 새 Python 두 파일 ruff 통과.
테스트는 wire에서 context 외 프롬프트/도구/쿼리 동일, arm 사이 상태 비전파,
미래/기대값·미표시 후보 누출 없음, 반대 수동 이력의 구분, 잘못된 원본 계획 보존을 확인한다.
DB/Redis/HTTP 경합/실제 앱 사용성은 이 실험에 포함하지 않는다.
