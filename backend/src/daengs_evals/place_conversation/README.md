# 시설 검색 평가기와 LLM Judge

이 폴더는 시설 검색의 **개발용 평가 도구**다. 시설 서비스가 실행한 조건·선택·응답을 기록하고, 코드 검사와 LLM Judge로 사람이 검토할 사례를 찾는다. APP 요청 중에는 실행되지 않는다.

Judge가 묻는 것은 “요청대로 움직이고, 실제로 한 일만 짧게 말했나”다. 일반 챗봇처럼 시설 밖 질문에 자세히 답하면 좋은 평가를 받는 도구가 아니다.

## 어디서 무엇을 하나

| 파일 | 역할 |
| --- | --- |
| [runner.py](runner.py) | 실제 Gemini와 시설 prepare/answer를 실행해 원본 관측 수집. 검색은 합성 fixture |
| [checks.py](checks.py) | 기대 필터·실행·상태 유지 등 코드로 검증 가능한 조건 검사 |
| [judge.py](judge.py) | 공통 Gemini 클라이언트로 Judge 호출, 앵커 검사, 호출 상한, 오류 기록, 재개 CLI |
| [judge_contract.py](judge_contract.py) | 입력·판정·근거 경로·파일 형식 |
| [judge_rubric.py](judge_rubric.py) | 세 평가 축, 입력 구성, 버전이 있는 프롬프트 |
| [judge_anchors.py](judge_anchors.py) | 명백한 성공/실패 대조 사례로 Judge 자체 검사 |
| [report.py](report.py) | 코드 결과·Judge 의견·사람/Codex 리뷰를 구분해 표시 |

설계·판정 기준은 [시설 Judge 문서](../../../../docs/place/conversation-judge.md),
데이터 배치는 [평가 데이터 안내](../../../evals/place_conversation/judge/README.md)를 참고한다.
다른 기존 모듈은 후보 탐색·찜·검색 정책·맥락 실험용이며, 이번 Judge가 모든 형태의 실험 파일을 자동 변환하지는 않는다.

## 빠르게 실행하기

명령은 **`DAENGS_dev/backend/`에서** 실행한다. 기존 `uv` 환경에 Place 추가 의존성이 필요하다.

```powershell
# API 호출 없이 사례 목록만 확인
uv run python -m daengs_evals.place_conversation.runner --cases evals/place_conversation/judge/cases.dev.v1.jsonl

# Gemini로 시설 기능을 실행하고 새 runs/<실행 ID> 생성
uv run python -m daengs_evals.place_conversation.runner --live --cases evals/place_conversation/judge/cases.dev.v1.jsonl --repeat 1

# 위에서 출력된 경로를 입력. 같은 Judge ID의 기존 폴더는 덮어쓰지 않는다.
uv run python -m daengs_evals.place_conversation.judge check-anchors --run evals/place_conversation/runs/<실행-ID> --judge-id first --max-calls 100

# 같은 설정의 앵커가 통과해야 실행된다.
uv run python -m daengs_evals.place_conversation.judge score --run evals/place_conversation/runs/<실행-ID> --judge-id first --max-calls 100

# 모델 호출 없이 보고서 생성
uv run python -m daengs_evals.place_conversation.report evals/place_conversation/runs/<실행-ID> --judge-id first
```

수집기와 Judge 모두 기존 `GEMINI_API_KEY`를 사용한다. Judge 클라이언트와 타임아웃은 오케스트레이션도 사용하는 [공통 Gemini 생성 코드](../../daengs_backend/core/gemini.py)를 따른다. 타임아웃은 `GEMINI_TIMEOUT_MS`(밀리초, 기본 30000)다. DB·암호화 키 설정 없이 오프라인 평가를 실행할 수 있다.

시설 모델은 `FACILITY_CONVERSATION_MODEL`, Judge 모델은 `FACILITY_JUDGE_MODEL`(기본 `gemini-3-flash-preview`)이다. `--model`, `--judge-model`로 각각 명시할 수도 있다. Judge는 `backend/.env`에서도 설정을 읽는다. `--key-file <파일 또는 .env가 있는 폴더>`는 `GEMINI_API_KEY` 또는 `gemini:` 필드를 읽어 현재 CLI 프로세스에만 전달한다. 키를 복사·출력하지 않는다. 키 자체를 명령행 인자로 넣지 않는다. OpenAI 키는 사용하지 않는다.

`runner --live`, `judge check-anchors`, `judge score`는 Gemini 모델 호출을 한다. 보고서·사례 목록·가짜 제공자를 쓰는 단위 테스트는 모델을 호출하지 않는다. 기존 `answer_quality/gemini.py`의 스키마 구성·검증 함수를 재사용하며 [Gemini 구조화 출력](https://ai.google.dev/gemini-api/docs/structured-output)을 받는다. 차단·불완전 응답은 통과가 아닌 호출 오류로 남긴다. 사고 토큰도 사용량에 포함한다.

시설 생성과 Judge는 같은 모델 계열이다. 이를 실행 메타데이터에 명시하며 계열이 독립된 교차검증이라고 보고하지 않는다. 세 축별 모델 의견은 기존대로 검토 보조다.

Windows 콘솔에서 한글이 깨지면 실행 전에 `$env:PYTHONUTF8='1'`로 현재 프로세스의 Python 입출력 인코딩을 맞춘다. 결과 파일은 항상 UTF-8이다.

## 결과는 어디에 생기나

```text
evals/place_conversation/runs/<실행-ID>/
├─ metadata.json, cases.json, fixtures.json
├─ observations.jsonl              # 원본 실행 기록
├─ reviews.jsonl                   # 별도 사람/Codex 리뷰가 있는 경우
└─ judges/first/
   ├─ metadata.json                # 판정 설정·원본·앵커·코드 해시
   ├─ inputs.jsonl                  # 축별로 실제 보낸 입력
   ├─ anchor-check.json             # Judge 자체 검사
   ├─ calls.jsonl                   # 시작/종료·오류·사용량·시간
   ├─ judgments.jsonl               # 자동 판정·사유·근거
   ├─ summary.json                  # report 명령으로 생성
   └─ report.md                     # 사람이 읽을 검토 목록
```

`code_status`는 실제 코드 검사, `judge_axes`는 모델 의견, `review_status`는 기존 리뷰다. Judge가 전부 pass여도 별도 의미 리뷰가 없으면 최종 상태는 `review_required`다. 코드 검사 실패는 Judge 의견으로 바뀌지 않는다. `uncertain`, `unmeasured`, `judge_error`, `budget_exhausted`, `not_run`을 통과로 세지 않는다.

수집기가 남긴 API 오류와 Judge API 오류는 서로 다른 문제다. 재시도 성공 후에도 최초 호출 오류는 `calls.jsonl`에 남는다. 숫자는 검토 목록의 건수이며 사람과 일치도를 검증한 품질 점수나 출시 승인 기준이 아니다.

## 재개와 버전 변경

중단한 `score`는 같은 명령에 `--resume`을 붙인다. 완료된 축은 재호출하지 않고 호출 오류·상한으로 멈춘 축만 이어간다. `--max-calls`는 해당 Judge 실행 전체의 상한으로 **앵커와 재시도, 완료 기록이 없는 시작 호출까지** 포함한다. 같은 값을 주면 상한은 초기화되지 않는다. 늘릴 경우 명시적으로 더 큰 값을 준다. SDK의 숨은 자동 재시도는 끈다.

`--interval`은 재시도를 포함한 호출 시작 사이의 최소 간격이며 기본 8초다. 분당 요청 제한에는 간격을 늘릴 수 있지만, 하루 요청 한도를 소진한 429는 간격을 늘려도 해결되지 않는다. 이때는 한도 초기화 후 새 Judge ID로 실행한다. 다른 모델을 명시적으로 선택할 때도 앵커부터 새로 검사한다. 최초 오류 기록을 유지하며, Google SDK의 자동 함수 호출은 비활성화한다.

모델·프롬프트·앵커·입력·평가 코드가 바뀌면 새로운 `--judge-id`로 앵커부터 다시 실행한다. 앵커 실패도 새 ID로 재검사하고 이전 결과를 남긴다. 기존 `observations.jsonl`과 `reviews.jsonl`은 Judge가 덮어쓰지 않는다.

401 인증 오류 등 재시도해도 해결되지 않는 4xx 응답은 같은 실행에서 재호출하지 않고 남은 축도 호출 오류로 표시한다. 408·409·429와 일시적 오류는 `--retries`(기본 1, 최대 2) 범위에서 재시도한다. 키 문제를 해결한 후에는 새 Judge ID로 앵커부터 검사한다.

동일 Judge 폴더의 동시 실행은 `.lock`으로 막는다. 정상 오류 종료에서는 잠금을 해제한다. 프로세스를 강제 종료해 파일이 남았다면 해당 실행이 종료됐는지 확인한 뒤 그 Judge 폴더의 `.lock` 파일만 정리하고 재개한다. JSONL이 쓰는 도중 잘렸으면 무시해서 넘어가지 않고 오류로 멈춘다. 이 경우 원본을 보존하고 새 Judge ID를 사용한다.

## 검증 범위

첫 버전의 관측은 **합성 검색 + 서버 prepare/answer**다. 실제 PostGIS 검색 정확도, 공통 오케스트레이션의 라우터 선택, APP 표시·선택 상태, 회원 찜 저장 완료, Redis 동시성은 이 평가만으로 확인되지 않는다. 특히 찜 명령 준비를 저장 완료로 설명하면 실패 후보로 찾는다.

```powershell
uv run pytest -q tests/place/conversation/test_judge.py tests/place/conversation/test_evaluation.py
```
