# 시설 검색 대화 평가 데이터

## 파일과 역할

| 파일 | 역할 |
| --- | --- |
| [cases.v1.jsonl](cases.v1.jsonl) | 고유 ID, 초기 상태, 입력/이벤트 순서, 통과 기준의 원본 |
| [fixtures.v1.json](fixtures.v1.json) | 고정된 합성 장소 후보와 경계 데이터 |
| [run-record.example.json](run-record.example.json) | 실행 기록 형식 예시. 실제 실행 결과가 아님 |
| `runs/<UTC 시각>-<코드 SHA>-<실행 ID>/` | 향후 각 실행의 metadata.json, observations.jsonl, report.md |

[설계·판정 원칙](../../../docs/place/conversation-evaluation.md)을 먼저 읽는다.
`cases.v1.jsonl`은 시나리오 **명세**다. `setup`은 HTTP 요청이나 FilterState의 직접 직렬화가 아니며,
이 값을 실제 상태/fixture로 바꾸는 평가 어댑터는 아직 연결하지 않았다.
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

## 현재 실행 가능한 관련 테스트

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
