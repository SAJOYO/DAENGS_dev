# 시설 대화의 다음 후보와 명시적 제외

PR #430은 #425 문맥 대조 이후 합의한 첫 구현 단위다. 현재 필터·장소 참조를 해석하며,
무관한 클릭에서 취향을 추정하지 않는다. UI와 facility-conversation-v2 기본 응답은 유지한다.

## 상태와 실행

- FilterState는 검색 조건이다. ExplorationState는 세션의 명시적 제외(이름/원천 키, 최대 120개),
  현재 조건에서 제시한 키(최대 1,200개), 그 조건 fingerprint를 갖는다.
- browse=current는 일반 검색이다. 명시적 제외만 적용하며, 이미 제시한 장소도 다시 나올 수 있다.
- browse=next는 같은 조건의 제시 기록까지 SQL에서 제외하고 다음 묶음으로 결과 목록을 교체한다.
  조건이 바뀌면 제시 기록을 새 조건 기준으로 시작한다. 필수 조건을 자동 완화하지 않는다.
- browse=restart는 현재 검색 조건을 유지하며 제외·제시 기록을 초기화한다.
  별도 새 세션(manual/restore)도 빈 탐색 기록으로 시작한다.
- place_edit.exclude는 현재 표시 목록, restore는 제외 목록을 참조한다. 모델이 원천 ID를 생성하지 않는다.
  화면 밖/범위 밖 참조는 변경 없이 되묻는다. 제외는 수동 카테고리·반경 변경에도 같은 세션에서 유지된다.
- A 제외와 다른 곳 표시는 current, A 제외와 **더/다음 후보** 표시는 next다.
  다음 후보의 중복 제거를 모든 일반 검색에 적용하지 않는다.
  제외·복구와 next를 함께 제안한 경우 서버가 추가 전진 표현(더/다음 등)을 확인한다.
  그 표현이 없으면 current로 남은 목록을 조회한다. 이름으로 지칭한 대상의 상호명은 전진 지시에서 제외한다.
- 후보의 자료상 일치/불일치/정보 부족은 기존 3값 판정을 사용한다. 이 PR에서 정보 이의나
  미확인 후보를 새로 일치로 취급하지 않는다.

시설 SQL은 canonical 병합·조건 평가 후 제외를 적용하고 순위·개수 제한을 수행한다.
의료 SQL도 원천을 고정한 뒤 source_ref 제외를 LIMIT 이전에 적용한다.
표시 상한을 올려 전체 후보를 메모리에 가져오는 방식은 사용하지 않는다.
키는 source+ref다. 같은 물리 장소의 다른 원천 레코드까지 자동 제외한다는 보장은 없다.

캐시 재사용은 조건 fingerprint와 명시적 제외를 확인한다. 일반 show는 실제 조회에서 뺀 키까지
같아야 하므로, 더 보기 소진의 빈 페이지를 전체 검색의 빈 결과로 재사용하지 않는다.
현재 페이지에서 하나 고르기·설명은 해당 페이지를 참조한다.
next/restart는 실제 조회한다. 새 결과와 탐색 기록은 기존 gateway의 소유자·revision·CAS로 함께 확정한다.
조회 실패나 늦은 요청의 CAS 실패는 페이지 소비·제외를 확정하지 않는다.
기록 한도는 후보 소진으로 설명하지 않고 탐색 범위를 좁히거나 초기화하도록 안내한다.

## 입력과 답변

현재 표시 장소의 순서·이름·키·업종, 선택 장소, 명시적 제외 목록을 입력한다.
기존 사용자 발화 history는 유지하지만 수동 클릭 이력·취향 추정은 추가하지 않는다.
최신 query는 입력 마지막에 배치한다. #425의 제한된 관측을 바탕으로 한 선택이며 보편적인 정확도 보장은 아니다.

receipt는 browse, new_places, excluded_places, restored_places, remaining을 추가한다.
remaining=more/exhausted는 해당 조회의 현재 조건·제외 범위에서 얻은 lookahead이며,
정확한 총수나 현실 세계의 장소 부재를 뜻하지 않는다. 실패·미실행·기록 한도는 unknown이다.
기존 응답 소비자는 추가 필드를 무시할 수 있다. 새 페이지가 0개이면 목록은 빈 결과로 교체된다.
답변은 실제 확정된 제외와 새로 제시한 개수만 서버에서 렌더링한다.

미지원 조건과 탐색 동작이 섞이거나 확인 대기안을 수정하며 탐색 동작까지 요구하면,
아직 전체 동작을 저장할 제안 계약이 없으므로 일부만 적용하지 않고 되묻는다.
기존 PendingChange를 여러 탐색 동작의 만능 제안으로 사용하지 않는다.

## 검증 계획과 실행 기록

합성 연속 시나리오는 [exploration.v1.jsonl](../../backend/evals/place_conversation/exploration.v1.jsonl)에 있다.
10개 시나리오, 21단계(채팅 18·수동 3)를 3회 반복한다. 실제 이전 결과를 다음 턴에 전달한다.
기존 자료나 원본 관측을 바꾸지 않는다. variant는 production-exploration-v1이다.

EX01/02/06/09/10은 다음 조회·제외·참조·복구, EX03/04/05는 같은 조건 요청의 욕설·수동 클릭 대조다.
EX07/08은 익숙함/정보 이의만으로 제외하지 않는 경계 검사이며 **유용한 정보 이의 답변의 완료 검사가 아니다**.
행동 완료, 답변의 근거 충실도, 다음 응답의 유용성은 구분해 기록한다.
SQL·HTTP 검사와 합성 모델 평가는 서로 대체하지 않는다.

backend에서 실행:

~~~powershell
uv run --no-sync python -m daengs_evals.place_conversation.runner --live --cases evals/place_conversation/exploration.v1.jsonl --fixtures evals/place_conversation/fixtures.policy.v1.json --repeat 3 --key-file C:\path\to\.env
~~~

첫 실행(9a46dc2)은 54회 실제 모델 호출에서 EX07의 임의 제외와 EX10의 잘못된 대상 제외가
각각 3회 재현됐다. 원본 관측과 별도 의미 평가는
[첫 실행 기록](../../backend/evals/place_conversation/runs/20260910T095337Z-9a46dc2-c47a1416b4/reviewed-summary.json)에 보존했다.
답변이 실제 실행을 정확히 설명해도, 그 실행이 사용자 의도와 어긋날 수 있었다.

이에 place_edit은 indices 대신 operation_quote와 targets(kind/text)를 받는다.
최신 발화의 실제 지시 인용과 대상 인용을 검사하고, 서버가 이름·현재 선택·명시 순번·전체 표현으로 키를 정한다.
같은 이름이 여럿이거나 현재 범위에 없는 이름이면 임의로 정하지 않는다.
탐색 초기화도 명시적 초기화 발화를 검사한다. 실패하면 조건·목록·탐색 상태를 함께 보존한다.

현재 검증은 제한적인 한국어 표현을 허용하는 보수적 방어선이다. 완전한 자연어 의미 검증이 아니다.
이름은 공백·대소문자 정규화 후 전체 이름이 일치해야 한다. 약칭, 인용부호를 포함한 요청,
부정/가정/정정이 섞인 일부 요청은 의도가 분명해도 되물을 수 있다. 한글 순번은 열 번째까지,
숫자 순번은 표시 범위 안에서 지원한다. 이 범위를 넓힐 때는 오실행과 불필요한 질문을 함께 평가한다.
정보 이의나 불만을 이 규칙으로 감정 분류하지 않는다.

첫 보완 뒤 실행(5634f9f)은 EX07/10의 임의 제외를 막았으나, EX02의 'A 빼고 다른 카페'를
next로 해석해 이미 표시한 B/C도 건너뛰는 실패가 3회 재현됐다.
[두 번째 실행 기록](../../backend/evals/place_conversation/runs/20260910T100819Z-5634f9f-e99817be7d/reviewed-summary.json)을 보존했다.
이 관측에 따라 제외·복구와 추가 전진을 결합하는 위 서버 규칙을 추가했다.
최종 코드(adcf019)의 3회 반복에서는 완료된 채팅 37턴의 상태·대상 기준이 모두 맞았다.
그러나 HTTP 429가 10회 발생했고, 이어지는 7턴은 미실행했다.
[원본 실행](../../backend/evals/place_conversation/runs/20260910T101455Z-adcf019-de94bda7ca/reviewed-summary.json)의
오류를 성공률 분모에서 조용히 빼거나 통과로 올리지 않는다. 실패한 대화 묶음 전체를
15초 간격으로 한 차례 다시 실행했다. 재시도 결과는 별도 run으로 보존한다.

| 실행 | 실제 호출 | HTTP 429 | 완료된 채팅 | 상태·대상 기준 실패 | 대화 작업 완료 실패 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 최초 구현 9a46dc2 | 54 | 0 | 54 | 6 | 9 |
| 대상 검증 5634f9f | 54 | 0 | 54 | 3 | 9 |
| 범위 검증 adcf019 | 47 | 10 | 37 | 0 | 3 |
| 반복 2의 실패 묶음 재실행 | 3 | 1 | 2 | 0 | 1 |
| 반복 3의 실패 묶음 재실행 | 7 | 6 | 1 | 0 | 0 |

[비교 원본](../../backend/evals/place_conversation/exploration-comparison.v1.json)에는 각 run,
전체 대화 단위의 재실행 대응과 최종 미완료 슬롯을 기록했다.
최종 코드에서 계획한 30개 시나리오 반복 중 23개는 완주했고, 7개는 429로 미완료다.
완주한 23개 중 19개는 정의한 작업 완료 기준을 통과했고, 4개(EX07/08 각각 2회)는
불만/정보 이의에 유용한 응답을 주지 못했다. 단순 상태 보존이나 질문 생성을 성공으로 올리지 않았다.
미완료는 EX01/02/07/08/10의 반복 3, EX09의 반복 2·3이다.

두 재실행은
[반복 2 재실행](../../backend/evals/place_conversation/runs/20260910T102130Z-f6d9782-9418a701f9/report.md),
[반복 3 재실행](../../backend/evals/place_conversation/runs/20260910T102327Z-38eb856-45aa0ead9f/report.md)에 있다.
최종 코드와 두 재실행의 Python source hash가 같고, 모든 실행의 케이스·fixture hash도 같다.
각 run의 audit.json은 실행 후 source hash, 실제 이전 상태 전달, 원본 관측 hash,
모델 입력에 평가 정답 필드가 없음을 확인했다. 단일 반복 run은 반복 간 일관성의 근거가 아니다.
평가는 Codex가 실제 출력·receipt를 검토한 것이며 독립적인 사람 검토가 아니다.

이 실험은 모델 정확도 개선과 실행 정책 개선을 구분하게 했다. 최종 EX02의 원본 계획은
여전히 next였지만, 서버는 추가 전진 표현이 없는 장소 제외를 current로 실행해 B/C를 반환했다.
모델의 의미 제안을 곧바로 집합 연산으로 실행하지 않는 것이 이번 구조 변경의 핵심이다.
다만 같은 고정 입력을 보며 수정한 평가이므로 일반 정확도나 새로운 발화에 대한 보장으로 해석하지 않는다.
제공자 오류·재시도와 대화 유용성 미완료를 남긴 상태로 PR은 draft다.

### 로컬 검증

2026-09-10, Python 3.12/uv 환경에서 변경 경계의 타겟 검사 164개를 통과했다(중복 실행 제외).
각 실행은 아래 파일로 한정했고 skip은 없었다. 전체 저장소 pytest를 실행한 결과가 아니다.

| 경계 | 실행 파일 (backend/tests/ 기준) | 결과 |
| --- | --- | --- |
| 최종 탐색·검증·HTTP | place/conversation/{test_exploration_grounding,test_exploration,test_service,test_policy}.py, place/api/test_conversation.py | 69 passed |
| 기존 평가·확인 대기·필터 계약 | place/conversation/{test_evaluation,test_correction_evaluation,test_context_ablation,test_context_followup}.py, place/api/test_conversation_pending.py, place/filters/test_contract.py | 72 passed |
| 실제 SQL | place/integration/{test_exploration_search,test_condition_filters}.py | 23 passed |

모든 pytest 명령은 `uv run --no-sync pytest -q -rs <명시 파일들>` 형태다.
SQL은 별도 임시 PostGIS 18-3.6을 loopback 55435에 띄우고 기존 Place Alembic head를 적용해 실행했다.
시설/의료의 LIMIT 이전 제외, 원천 키 구분, 20개 밖 후보, 소진, unknown lookahead를 확인했다.
사용한 컨테이너는 종료·제거했다. 운영 DB나 서버 compose를 실행하지 않았다.
HTTP 경합은 실제 ASGI 경로와 메모리 세션 더블을 사용했다. 실제 Redis 원자성·앱 화면 검증은 아니다.

변경한 Place/eval 코드와 테스트의 ruff, git diff --check 및 자격 증명 유입 검사는 통과했다.
`uv run --no-sync check`는 마이그레이션 이름·짝 검사 후 **Windows 바이트 하네스에서 실패**했다.
Windows PowerShell 실행 정책이 임시 validate.ps1을 거부했으며 기기 정책을 바꾸거나 우회하지 않았다.
전체 pytest·앱 빌드·compose/nginx 검사는 이번 범위에서 실행하지 않았다. 머지 전 전체 게이트 통과로 표현하지 않는다.

## 후속 범위

정보 이의 표시·피드백과 요청의 조합, 미확인 후보 공개 응답과 UI, 시험 조회로 만든 제안과 제안 ID 적용은
다음 작업 단위다. 범용 행동 복원이나 장기 취향 기억은 이번에 추가하지 않는다.
