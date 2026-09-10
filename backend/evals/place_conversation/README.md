# 시설 검색 대화 평가 데이터

## 파일과 역할

| 파일 | 역할 |
| --- | --- |
| [cases.v1.jsonl](cases.v1.jsonl) | 고유 ID, 초기 상태, 입력/이벤트 순서, 통과 기준의 원본 |
| [fixtures.v1.json](fixtures.v1.json) | 고정된 합성 장소 후보와 경계 데이터 |
| [run-record.example.json](run-record.example.json) | 실행 기록 형식 예시. 실제 실행 결과가 아님 |
| [transfer.v1.jsonl](transfer.v1.jsonl) | 첫 관측 뒤 작성한 별도 전이 검사 4개. 원래 23개와 합산하지 않음 |
| [policy.v1.jsonl](policy.v1.jsonl) / [fixtures.policy.v1.json](fixtures.policy.v1.json) | 확인 후속 발화 10개와 불투명 ID fixture |
| [corrections.v1.jsonl](corrections.v1.jsonl) | 교정 5개와 사용자 불만·재탐색 원문/확장 9개. 27단계 중 수동 이벤트 2개 |
| [context-ablation.v1.json](context-ablation.v1.json) | 동일 턴 직전 상태에서 현재 입력/화면 문맥 보강을 비교할 14개 대상 |
| [context-followup.v1.json](context-followup.v1.json) | 주입 후 관측한 최신 요청 누락을 입력 키 순서/중복 과거 발화로 나눈 3개 대상 |
| `runs/<UTC 시각>-<코드 SHA>-<실행 ID>/` | metadata.json, observations.jsonl, 별도 reviews.jsonl과 연구 기록 |

[설계·판정 원칙](../../../docs/place/conversation-evaluation.md)을 먼저 읽는다.
[1차 연구 결과](../../../docs/place/conversation-research-2026-09-10.md)에 실행별 링크와 구조 제안을 정리했다.
[채택한 실행 정책](../../../docs/place/conversation-policy.md)은 PR #417의 해석·확인·사실 응답 경계를 설명한다.
[교정·재탐색 연구](../../../docs/place/conversation-corrections-2026-09-10.md)는 그 정책을 고정한 후속 실험이다.
[문맥 주입 대조](../../../docs/place/conversation-context-ablation-2026-09-10.md)는 동일 query/상태에
현재 화면과 완료된 직전 행동만 추가한다. 실행: `uv run python -m daengs_evals.place_conversation.context_ablation --live --repeat 3 --key-file C:\path\to\.env`.
이 대조의 cases.json은 원래 케이스 전체가 아니라 고정 턴/단서 snapshot이고,
report의 case_repetitions는 독립 턴 대상의 반복이다. 전체 대화 완료로 집계하지 않는다.
제한적 배치 후속은 `uv run python -m daengs_evals.place_conversation.context_followup --live --repeat 3 --key-file C:\path\to\.env`로 실행한다.
두 모듈은 기존 run의 고정 입력을 읽으며 새 고유 run을 생성한다. 기본 spec의 source_run은 수정하지 않고,
다른 근거로 비교하려면 spec을 새 버전으로 만든다.
`cases.v1.jsonl`은 시나리오 **명세**다. `setup`은 HTTP 요청이나 FilterState의 직접 직렬화가 아니며,
이 값을 실제 상태/fixture로 바꾸는 어댑터는 `src/daengs_evals/place_conversation/`에 있다.
예를 들어 `parking=required_true`는 원본의 의미를 나타내며 production enum이 아니다.

## 원본 읽기

저장소 루트에서 PowerShell로 필요한 케이스를 바로 꺼낼 수 있다. 모델·DB 호출은 없다.

~~~powershell
$cases = Get-Content backend/evals/place_conversation/cases.v1.jsonl | ForEach-Object { $_ | ConvertFrom-Json }
$cases | Select-Object id, title, layer, status
$cases | Where-Object id -eq 'PC-E08' | ConvertTo-Json -Depth 20
~~~

각 케이스의 기본 후보는 `fixtures.v1.json`의 standard다. setup에 fixture가 있으면 해당 변형을 사용한다.
이름이 생략된 변형 후보는 `테스트 <ref>`, 좌표는 setup.origin, source/주소는 fixture defaults를 쓴다.
명시하지 않은 사실은 미상이다. 이 합성 후보는 LLM/상태 검증용으로, 실제 공간 SQL 정확도의 증거가 아니다.
스냅샷은 해당 초기 조건을 적용한 결과로 만들고, source+ref를 유지하며 distance_m 오름차순으로 표시한다.

## 반복 평가 실행

backend 디렉터리, Python 3.12 / `uv sync --frozen --extra place` 환경에서 실행한다.
명령마다 고유한 실행 폴더를 생성하며 이전 관측을 덮어쓰지 않는다.

~~~powershell
# 목록만 확인. 모델 호출 없음
uv run python -m daengs_evals.place_conversation.runner
# 실제 Gemini + 현재 서버 prepare/answer + 합성 검색. 키 파일은 저장소 밖에 둔다
uv run python -m daengs_evals.place_conversation.runner --live --repeat 3 --key-file C:\path\to\.env
# 실패 범위만 재검사
uv run python -m daengs_evals.place_conversation.runner --live --ids PC-E05,PC-E08 --repeat 3 --key-file C:\path\to\.env
# 별도 전이 입력. 원래 평가셋 점수에 섞지 않는다
uv run python -m daengs_evals.place_conversation.runner --live --cases evals/place_conversation/transfer.v1.jsonl --key-file C:\path\to\.env
# 확인 후속 발화와 불투명 ID의 사실 설명
uv run python -m daengs_evals.place_conversation.runner --live --cases evals/place_conversation/policy.v1.jsonl --fixtures evals/place_conversation/fixtures.policy.v1.json --key-file C:\path\to\.env
# 교정·불만 뒤 재탐색: 사용자 원문 연속/단독, 순차 수동 조작
uv run python -m daengs_evals.place_conversation.runner --live --cases evals/place_conversation/corrections.v1.jsonl --fixtures evals/place_conversation/fixtures.policy.v1.json --repeat 3 --key-file C:\path\to\.env
# false와 unknown이 답변 입력에서 구별되는지
uv run python -m daengs_evals.place_conversation.diagnose evals/place_conversation/runs/<run> --evidence-gap
# 실제 제안에 대한 구조화된 동의/거절 재생 (policy-context E08 실행 폴더)
uv run python -m daengs_evals.place_conversation.pending_replay evals/place_conversation/runs/<run>
# reviews.jsonl 작성 후 보고서만 재생성. 원본 관측은 보존
uv run python -m daengs_evals.place_conversation.report evals/place_conversation/runs/<run>
~~~

`--model` 기본값은 기존 live 스모크와 같은 `gemini-3.1-flash-lite`다.
`--interval 5`는 호출 사이 최소 간격이며 모델 자체 지연과 구별한다. 오류는 조용히 재시도하지
않고 기록한다. 키 파일은 `GEMINI_API_KEY=...` 또는 기존 `gemini: ...` 형식을 읽으며,
키 경로/값·HTTP 헤더·예외 본문을 결과에 저장하지 않는다.

대화형 20개는 모델+엔진으로 실행한다. PC-E16은 계획을 거친 경로와 답변층 직접 호출을 별도로
남긴다. HTTP 경합 3개는 이 CLI에서 `not_run`으로 표기하고 다음 통제 테스트로 검증한다.

~~~powershell
uv run pytest -q tests/place/api/test_conversation_evaluation.py --junitxml=evals/place_conversation/runs/<run>/controlled.xml -o junit_family=xunit1
uv run pytest -q tests/place/conversation/test_evaluation.py
~~~

JUnit의 scenario_id/trace 속성에 경합별 확정·복구 상태를 남긴다. 이는 in-process HTTP와
메모리 세션 저장소 검증이며 실제 Redis 원자성, 네트워크, 앱 화면 검증을 대신하지 않는다.

자동 기준이 모두 맞아도 전체 `pass`로 만들지 않는다. 사람이 실제 확인 질문, 원본/제공 답변,
확정 필터를 읽고 `reviews.jsonl`에 근거 충실도와 작업 완료 여부를 따로 기록한다.
관측의 상태는 변경하지 않고, 리포트에서 관측과 검토를 합친다.
현재 runner는 `production-policy-v1`만 실행하며 답변은 커밋된 receipt에서 서버가 렌더링한다.
순차 `manual` 이벤트는 production prepare에 실제 PlaceSearchRequest를 전달하며 모델을 호출하지 않는다.
관측의 event는 원래 입력, result_delta는 직전 목록 대비 추가/제거 ref다. 동일 ref가 다른 source에
존재하는 fixture에서는 이 지표를 사용하지 않는다. 이번 fixture는 ref가 모두 유일하다.
`min_new_refs`는 검색 호출 성공과 별개로 실제 새 후보가 있는지 검사한다. HTTP 경합 이벤트는
여전히 별도 하네스 대상이며 이 확장은 Redis CAS나 앱 클릭 검증이 아니다.
과거 `production-baseline`, `prompt-only`, `policy-context`와 원본 계획 재생 진단은 **ac2b062 체크아웃**에서 실행한다.
원본 관측은 보존하며, 현재 코드로 과거 variant 이름의 결과를 생성하지 않는다.
`pending_replay`의 저장된 계획·동의 재생은 당시 구조 연구용이며 운영 정책은 conversation/policy.py에 있다.

## 기존 관련 테스트

2026-09-10 코드에서 확인한 기존 테스트다. **아래 명령은 새 23개 시나리오 전체 실행 명령이 아니다.**
케이스의 existing_tests는 관련 범위 연결이며, coverage_note를 함께 읽는다.

backend 디렉터리에서 실행한다. 환경 준비는 저장소 README와 tests/README.md를 따른다.

~~~powershell
# PC-E13: 저장한 상위 20개 밖의 후보도 새 조건으로 검색하는지. 고정 계획 사용.
uv run pytest -q tests/place/conversation/test_service.py::test_changed_filter_does_not_refilter_only_cached_top_twenty
# PC-R01: 새 수동 요청과 늦은 AI 확정의 경합. HTTP 테스트 더블 사용.
uv run pytest -q tests/place/api/test_conversation.py::test_newer_manual_request_prevents_old_ai_from_committing_server_state
~~~

기존 실제 Gemini 스모크는 선택·설명·주차·업종 변경의 4턴이다. 합성 후보를 사용한다.
`DAENGS_CONVERSATION_LIVE_ENV`에 사용자가 지정한 키 파일 경로를 설정한 환경에서만 실행되며,
환경 변수가 없으면 skip된다. 아래 반복은 해당 **4턴 스모크**만 실행한다.

~~~powershell
# backend 디렉터리. 모델/키 경로 설정은 기존 test_live.py의 계약을 따른다.
1..3 | ForEach-Object {
    uv run pytest -q -s tests/place/conversation/test_live.py --tb=short
    if ($LASTEXITCODE -ne 0) { throw "Gemini smoke failed on repetition $_" }
}
~~~

새 카탈로그의 자동 실행 연결 시 실제 LLM에는 query와 현재 상태만 전달한다.
expect/invariants를 모델 입력에 섞지 않는다. 조건 ID 표현이 달라도 의미가 같으면 허용하되
개수·중복·타입 제한과 언급하지 않은 조건의 보존은 별도로 검사한다.
API 경합은 모델의 임의 지연에 기대지 않고 제어 가능한 대기점에서 이벤트 순서를 재현한다.
재사용 평가 코드를 추가한다면 저장소 규칙에 따라 `src/daengs_evals/place_conversation/`에 두고,
그 코드의 단위/회귀 검증은 `tests/`에 둔다. 데이터 폴더에 Python 패키지를 만들지 않는다.

## 실행 기록

run_id, UTC 시각, 코드 SHA와 dirty 여부, 모델 식별자, 프롬프트/케이스/fixture SHA256,
실행 경계(model/engine/answer/api), seed 또는 제공자 설정, 반복 번호를 metadata에 기록한다.
각 케이스/variant/반복/turn마다 다음을 observations.jsonl에 저장한다.

- 원래 입력과 초기 상태, 실제 이전 확인 질문, 표시 순서/선택
- 모델의 원본 계획, 서버 검증 결과, 확정 전후 필터와 revision
- execution/receipt, 실제 검색 호출 수, 반환 장소 ID와 근거
- 원본 모델 답변, 최종 제공 답변, fallback 여부, 오류 코드
- 기준별 pass/fail/review_required/not_run/blocked, 검토자와 구체적인 사유

원본 관측은 덮어쓰지 않고 검토/재판정은 별도 파일로 남긴다. 키·Bearer 토큰·실사용자 개인정보는
산출물에 저장하지 않는다. 재현에 필요한 합성 입력과 모델 출력만 보존한다.
설계된 케이스 수, 실행한 수, 자동 검사 통과, 의미 검토 완료, 실패, 미실행/차단을 따로 집계한다.
일부 기준이 미검토인 케이스를 전체 pass로 만들지 않는다.

실제 실행을 마치면 해당 run 폴더를 검토해 PR에 함께 보존한다. 예시 기록은 결과 집계에서 제외한다.
