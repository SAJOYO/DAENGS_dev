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

## 관측 중 정한 제한적 후속 비교

첫 두 반복에서 X03의 수동 undo는 문맥 보강으로 성공했지만, X13은 새 주차 요구를 적용하지 않고
직전 “더 가져와”처럼 refresh만 하는 퇴행이 반복됐다. 이 결과를 보고 작성한 후속 비교이며 사전 A/B와 섞지 않는다.
[후속 명세](../../backend/evals/place_conversation/context-followup.v1.json)는 X03/X13/X14만 각각 3회씩 두 조건으로 본다.

- query-last: A/B의 보강 입력과 정보·프롬프트·스키마는 같고, JSON query 키만 맨 끝으로 옮긴다.
- no-duplicate-past-query: 기존 배치는 유지하고 context.recent_interactions의 user_query 필드만 뺀다.
  원래 top-level history의 사용자 발화와 직전 실제 답변·필터 전후·화면 단서는 그대로 둔다.

이는 문맥 전체를 빼는 비교가 아니라, 주입 블록이 최신 쿼리와 경쟁한다는 가설을 분해하기 위한 비교다.
운영 도입이나 일반적인 개선을 전제하지 않는다. 주차 퇴행 복구와 반대 방향 undo 둘의 보존을 같이 본다.
실행: `uv run --no-sync python -m daengs_evals.place_conversation.context_followup --live --repeat 3 --key-file C:\path\to\.env`.

## 완료한 결과

본 비교는 `e9cfa82`, 후속은 `482d8ef`의 평가 코드로 실행했다. 두 실행 모두 시작 시 clean이며,
원본 metadata의 코드/입력/fixture 해시를 보존했다.
총 **102회 실제 Gemini 호출**, 제공자/통신 오류 0회다. 아래 점수는 의도적으로 어려운 고정 턴의 판정이며
일반 자연어 정확도나 서비스 전체 대화 성공률이 아니다.

| 비교 | 현재 입력 A | 문맥 보강 B |
| --- | --- | --- |
| 대상 | 동일 14턴 × 3회 | 동일 14턴 × 3회 |
| 작업 완료 | 9통과 / 33실패 | 12통과 / 30실패 |
| 원본 reference_index=0 계약 위반 | 3회 | 0회 |
| 입력 토큰 중앙값 | 2,208 | 2,949 |
| 모델 호출 지연 중앙값(평가 간격 제외) | 2,476ms | 3,075ms |

42쌍 중 완료 개선 6쌍(X03/X14), 퇴행 3쌍(X13), 완료 판정 동일 33쌍이다.
X08은 그 동일 판정 중 참조 오류만 개선된 3쌍이다. 재미 X05의 양쪽 6회는 제품 정책에 민감한 판정이다.
각 대상/arm에서 3회 계획·제공 문구·결과·판정이 동일했다.

| 사례 | 관측 | 해석 |
| --- | --- | --- |
| 수동 추가 취소, 반대 방향 반사실 | A는 모두 무변경, B는 각각 cafe/restaurant 복원 | 이 두 경우는 **문맥만 보강해 기존 실행 도구로 해결**됐다 |
| “거기 없던데?” | A는 index=0 차단, B는 index=1로 대상 식별 | 단서는 참조에 도움이 됐지만 존재 제보를 selection_reason으로 답하는 문제는 남음 |
| 새 주차 요청 | A는 cafe-26 반환, B는 parking=keep/refresh=true로 같은 20개 표시 | 최신 요청을 누락하는 문맥 주입 퇴행 |
| 더 보기 | 양쪽 같은 20개 | 잘림 여부를 알려줘도 다음 후보를 실행하는 수단은 생기지 않음 |
| A 제외 | 양쪽 A를 그대로 표시 | 이름/ID 대응을 알려줘도 현 스키마에 개별 장소 제외 동작은 없음 |
| 불만·폐업 확인 | 실제 직전 답변/장소 단서를 줘도 같은 일반 안내·선택 이유 | 현재 의미 표현과 답변 경계에서 다음 행동으로 연결되지 않음 |

후속에서 같은 정보의 **query 키만 마지막으로 옮긴 조건은 9/9 통과**했다.
주차 cafe-26을 다시 찾고 양방향 수동 취소도 유지했다.
중복된 과거 user_query만 제거한 조건은 **6통과/3실패**로 주차 퇴행이 남았다.
과거 발화가 원래 history에도 있기 때문에 이를 “과거 문맥 전체 제거”로 해석하면 안 된다.
이는 X03/X13/X14 세 요청의 사후 대조 결과이며 query-last로 14개 전부를 재평가한 결과가 아니다.
특정 입력 배치가 모델 내부에서 작동한 원리까지 입증한 것은 아니다.

## 원본과 독립 확인

- [본 A/B 실행](../../backend/evals/place_conversation/runs/20260910T083600Z-e9cfa82-194ff74278/):
  [원본 관측](../../backend/evals/place_conversation/runs/20260910T083600Z-e9cfa82-194ff74278/observations.jsonl),
  [개별 의미 검토](../../backend/evals/place_conversation/runs/20260910T083600Z-e9cfa82-194ff74278/reviews.jsonl),
  [비교 집계](../../backend/evals/place_conversation/runs/20260910T083600Z-e9cfa82-194ff74278/comparison.json).
- [입력 배치 후속 실행](../../backend/evals/place_conversation/runs/20260910T084740Z-482d8ef-08ec65d41b/):
  [원본 관측](../../backend/evals/place_conversation/runs/20260910T084740Z-482d8ef-08ec65d41b/observations.jsonl),
  [개별 의미 검토](../../backend/evals/place_conversation/runs/20260910T084740Z-482d8ef-08ec65d41b/reviews.jsonl),
  [비교·사용량·검증](../../backend/evals/place_conversation/runs/20260910T084740Z-482d8ef-08ec65d41b/comparison.json).

Codex가 실제 출력의 의미를 검토했으며 사용자 직접 채점은 아니다. 관측 자체는 덮어쓰지 않았다.
본 비교의 [wire 감사](../../backend/evals/place_conversation/runs/20260910T083600Z-e9cfa82-194ff74278/intervention-audit.json)는
42개 A 입력이 과거 production 입력과 같고, 각 A/B 쌍의 직전 상태가 같으며 input.context만 다름을 실제 요청으로 확인한다.
후속 [wire 감사](../../backend/evals/place_conversation/runs/20260910T084740Z-482d8ef-08ec65d41b/intervention-audit.json)는
18개 입력에서 키 순서 또는 중복 과거 user_query만 달라짐을 확인한다.
기록한 source 해시는 각 실행 이후 그대로다.

후속 배치 함수 검증:
`uv run --no-sync pytest -q -rs tests/place/conversation/test_context_followup.py` **2통과, skip 0**(10.85초).
본 비교의 14개와 합쳐 **고유 테스트 16개**이며 전체 서비스 테스트가 아니다.
추가한 Python 4파일 ruff 통과. 전체 pytest, uv run check, DB/Redis/HTTP 경합/Android/compose 검증은 실행하지 않았다.
운영 코드는 변경하지 않았고 머지/배포하지 않는다. PR CI는 현재 docs/ci로 이동되어 자동 실행되지 않는다.

## 앞선 판단에서 수정할 점과 다음 구조

“문맥이 없어도 실행 기능부터 추가해야 한다”는 식으로 묶을 근거는 부족했다.
**이번 카테고리 수동 취소는 변경 전후 단서를 주면 현재 스키마와 실행기로 가능했다.**
반면 범용 undo가 모든 변경을 정확히 역전하고 경합을 처리한다는 검증은 아니므로,
운영에 옮길 때는 실제 확정 변경 전후를 revision과 함께 보존하고 사용 범위를 정해야 한다.

다음 구현 후보는 필요한 현재 화면/직전 확정 변경을 명시적인 문맥으로 만들고,
사용자의 최신 쿼리를 문맥 뒤에 분명하게 배치하는 것이다.
정보를 많이 복제할수록 좋다고 보지 않는다. 이번 묶음은 선택 장소·전체 표시 목록·이력을 함께 넣었으므로
필드별 최소 필요량과 처음 보는 표현에 대한 효과는 후속 검증이 필요하다.

이와 별개로 다음 후보/개별 장소 제외/정보 오류 제보는 현 Interpretation의 동작·속성으로 표현하기 어렵다.
불만을 이해했는지를 제한된 스키마 출력만으로 단정할 수는 없다.
다만 현재 출력과 서버 응답이 실제 다음 행동으로 이어지지 않는다는 관측은 분명하다.
문맥 전달 개선을 먼저 작게 적용할 후보로 삼고, 남은 실패 유형별로 필요한 의미/실행/응답 계약을 추가하는 순서를 제안한다.
