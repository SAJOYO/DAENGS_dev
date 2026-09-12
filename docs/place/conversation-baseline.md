# 시설 개발 세트 예비 평가 — 2026-09-12

시설 실행과 Judge 모두 **`gemini-3.1-flash-lite`**로 개발 시나리오 7개·8턴을 한 번 실행했다. **2026-09-13 정정: 사용자가 확인한 운영 모델도 Flash-Lite다.** 이전에 코드에 별도로 넣은 Flash 기본값을 실제 운영값으로 단정해 이 측정을 운영 모델과 무관하게 설명한 것은 잘못이다. 이번 결과는 운영에 쓰는 모델로 수행한 합성 환경의 개발 측정이며 실제 배포 경로 전체를 관측한 결과는 아니다. 당시 실험은 운영 설정·프롬프트·실행 코드를 바꾸지 않았다.

[모델 기준 정정 기록](../../backend/evals/place_conversation/runs/20260912T135557Z-fd8609b-ea1461c50e/model-context-correction.json)에 잘못된 전제와 수정된 해석을 남겼다. 원본 계획·호출·관측·판정은 당시 기록으로 보존한다. Flash의 한도 소진 기록도 실제 시도한 모델에 관한 기록이지, Flash가 운영 모델이라는 근거가 아니다.

[입력·실제 행동·표시 문구·Judge 대조표](../../backend/evals/place_conversation/runs/20260912T135557Z-fd8609b-ea1461c50e/comparison.md)가 이번 결과의 읽기 입구다. [기계 대조 결과](../../backend/evals/place_conversation/runs/20260912T135557Z-fd8609b-ea1461c50e/comparison.json)와 원본 관측·호출·검토 파일도 같은 폴더에 보존했다.

## 실행 결과

| 항목 | 결과 |
| --- | --- |
| 개발 시나리오 | 7개·8턴, 반복 1회, 누락 0턴 |
| 코드로 확인한 행동 | 5턴 통과, 3턴 실패 |
| 시설 모델 호출 | 8회, 제공자 호출 오류 0회, 제안 계약 검증 실패 3턴 |
| Judge 대조 사례 | 12/12 기대 판정과 일치 |
| 실제 관측 Judge | 24개 축 모두 판정, 미측정·호출 오류 0건 |
| Judge 호출 | 앵커 12회 + 본 판정 24회 = 36회, 오류·재시도 0회 |
| Codex 검토와 Judge 비교 | 21개 축 일치, 결과 설명 축 3개 불일치 |

코드 검사를 통과한 5턴도 기존 최종 보고서에서는 `review_required`다. Codex 검토는 `codex-review.jsonl`에 별도로 두고 `reviews.jsonl`에 사람 판정처럼 넣지 않았다. 검토 파일의 시점·해시로 Judge 본 판정 전에 의견을 기록했음을 확인했다. 이는 두 에이전트 의견의 비교이며 사람과의 일치도나 일반 정확도는 아니다.

## 시설 기능에서 찾은 문제

다음 3턴은 명확한 요청인데도 실제 조건 변경이나 검색 없이 “원하는 장소나 바꿀 조건을 짧게 알려주세요.”로 끝났다.

| 사례 | 입력 | 관측된 실패 |
| --- | --- | --- |
| FJ-D01·1 | 주차되는 카페만 찾아줘 | 카페·주차 조건 누락, 검색 미실행 |
| FJ-D02·1 | 음식점은 빼줘 | 기존 음식점 조건을 제거하지 못함 |
| FJ-D07·2 | 카페만 보여줘 | 앞선 범위 밖 입력 뒤 정상 검색 요청을 처리하지 못함 |

세 원본 모델 제안 모두 `kind=facility_action`에 조회 전용 `state_subject=filters`를 함께 내고 변경 조건을 누락했다. 같은 원본 제안을 `ScopedInterpretation`으로 다시 검증하면 `filter state is read-only` 오류가 재현된다. 서버가 잘못된 제안의 실행을 막았지만 사용자 요청은 완료되지 않았다. 이는 제공자 HTTP 오류가 아니라 운영에 쓰는 Flash-Lite가 시설 계약을 만족시키지 못한 관측이다. 같은 모델이라는 이유만으로 실제 배포 환경에서 재현까지 확인했다고 볼 수는 없다.

나머지 관측에서는 범위 밖 수학·역할 변경 요청이 고정 강아지 문구로 끝났고, 현재 업종·반경 조회도 맞았다. `API라는 카페 찾아줘`는 상호명을 보존해 검색했으며 합성 데이터에 해당 장소가 없어 0건으로 안내했다. `주차란 뭐야?`는 모델이 조건 조회로 오분류했지만 서버 가드레일이 시설 밖 안내로 바로잡았다.

## Judge에서 찾은 문제

Judge는 위 3턴의 의도·범위 실패를 모두 잡았다. 하지만 **검색을 수행하지 못했다는 이유를 결과 설명 축에도 적용**했다.

현재 결과 설명 축의 기준은 실제 수행 사실과 표시 문구의 일치다. 세 문구는 되묻기만 했고 검색·변경 완료를 주장하지 않았다. Codex 검토에서는 의도·범위는 실패, 결과 설명은 통과로 기록했으나 Judge는 세 축 모두 실패로 판정했다. 불일치 3건의 원문 사유와 근거 경로는 대조표에 남겼다.

이번 12개 앵커 통과만으로 축 구분이 충분하다고 볼 수 없다. 다음 Judge 수정에서는 동일한 미실행 상태를 두고 아래 대조를 추가할 필요가 있다.

- 명확한 요청을 수행하지 못하고 되묻기만 함: 의도는 실패지만, 완료 사실을 꾸미지 않았다면 결과 설명은 통과.
- 같은 미실행 상태에서 “찾아뒀어요”라고 말함: 의도와 결과 설명 모두 실패.

최초 기준선 작업(#489)은 관측과 대조 검토까지였고 당시에는 루브릭·앵커나 시설 모델 프롬프트를 고치지 않았다. 이후 안내 문구와 Judge를 보완한 결과는 아래 후속 검증에 구분해 기록한다.

## 재현

`DAENGS_dev/backend/`에서 실행한다. 기존 `GEMINI_API_KEY` 또는 이름 있는 Gemini 키 필드가 있는 `--key-file`을 사용한다. 키 자체를 명령행 인자로 넣지 않는다. 아래 모델 호출은 새 관측을 만들며 저장된 결과와 같다고 보장하지 않는다.

```powershell
$env:PYTHONUTF8='1'

uv run --no-sync python -m daengs_evals.place_conversation.runner --live --cases evals/place_conversation/judge/cases.dev.v1.jsonl --model gemini-3.1-flash-lite --repeat 1 --interval 8

# 위 명령에서 출력한 실행 경로를 사용한다.
uv run --no-sync python -m daengs_evals.place_conversation.judge check-anchors --run evals/place_conversation/runs/<실행-ID> --judge-id lite-v1 --judge-model gemini-3.1-flash-lite --max-calls 50 --interval 8

# 앵커가 통과한 경우에만 실행한다.
uv run --no-sync python -m daengs_evals.place_conversation.judge score --run evals/place_conversation/runs/<실행-ID> --judge-id lite-v1 --judge-model gemini-3.1-flash-lite --max-calls 50 --interval 8

uv run --no-sync python -m daengs_evals.place_conversation.report evals/place_conversation/runs/<실행-ID> --judge-id lite-v1
```

이번 실행 ID는 `20260912T135557Z-fd8609b-ea1461c50e`이며 코드·사례·관측 해시는 해당 폴더의 `metadata.json`과 `evaluation-plan.json`에 있다. 기본 `report` 명령은 원본 코드 검사와 Judge 보고서를 재생성한다. `comparison.md`의 Codex 대조는 이번에 별도로 기록한 검토 의견이며 자동 사람 리뷰 생성 기능이 아니다.

현재 코드의 기본 Judge는 v3다. 위 명령을 현재 코드에서 새 폴더로 실행하면 최신 기준을 사용한다. 최초 v1의 설정까지 재현하려면 기록된 코드 버전과 앵커를 사용해야 하며, 기존 결과 폴더를 덮어쓰지 않는다.

## 산출물 검증

[검증 기록](../../backend/evals/place_conversation/runs/20260912T135557Z-fd8609b-ea1461c50e/validation.json)에 7개 사례·8턴의 누락 없음, 원본 사례·관측·Judge 입력 해시, 24개 판정의 근거 경로를 확인한 결과를 남겼다. 모델 재호출 없이 저장된 상태로 코드 검사를 재생해 전부 같은 결과를 얻었고, 표시 문구 8개도 다시 렌더링한 결과와 일치했다. 제안 계약 오류 3건도 원본 응답으로 재현했다.

`uv run check`와 문서 링크·diff·키 미포함 검사를 확인했다. 실행 코드를 바꾸지 않았으므로 전체 앱 테스트를 반복하지 않고 실제 산출물과 재생 가능한 검사에 검증 범위를 맞췄다.

## 한계와 후속 순서

합성 검색과 서버 prepare/answer만 관측했다. 공통 라우터의 시설 진입, APP 반영, 실제 찜 저장, PostGIS 검색 정확도는 이번 결과로 확인되지 않는다. 선택·확인 대기도 비어 있는 초기 상태이므로 값이 있는 상태의 보존을 입증하지 않는다. 개발 세트 한 번의 결과이며 별도 평가 세트는 이번에 사용하지 않았다.

Judge 결과 설명 축을 분리한 후속 검증은 아래에 있다. 다음 기능 과제는 Flash-Lite에서 발생한 제안 계약 실패 3턴의 원인을 고치고 같은 입력·별도 표현으로 검증하는 것이다. 운영 모델을 Flash로 바꿔 재측정해야 한다는 이전 제안은 철회한다. 실제 배포 설정과 전체 APP 경로의 재현은 별도로 확인한다.

## 안내 문구·Judge 분리 후속 검증

원본 Lite 관측 8턴과 Judge 전에 기록한 Codex 검토는 그대로 유지했다. [v2 대조](../../backend/evals/place_conversation/runs/20260912T135557Z-fd8609b-ea1461c50e/judges/lite-v2-axis/comparison.md)는 루브릭·대조 사례만 바꿨으며 판정 일치가 21/24에서 22/24로 늘었지만 중복 감점 2건이 남았다. [v3 대조](../../backend/evals/place_conversation/runs/20260912T135557Z-fd8609b-ea1461c50e/judges/lite-v3-facts/comparison.md)는 결과 설명 축의 요청 수행 정책과 사용자 요청을 제외해 24/24가 일치했다. 의도·범위 입력 16개는 이전과 같고 결과 설명 입력 8개는 `query`만 제외했다. 같은 원본 관측의 비교이며 모든 Judge 입력을 고정한 비교는 아니다.

v2·v3 각각 앵커 22/22와 본 판정 24개 축을 완료했다. 각 46회, 합계 92회의 실제 Lite Judge 호출에서 제공자 오류는 없었다. 개발 사례를 보고 보완한 결과이므로 독립 정확도로 보고하지 않는다. 특히 v3 사유 두 건의 “검색 결과를 제시한 후”는 기존 목록과 이번 턴의 미실행을 모호하게 표현한다. 판정 일치와 사유 정확성은 별개이며 원문은 보존했다.

실행 오류 원본 제안 3건을 현재 서버에 [재생한 기록](../../backend/evals/place_conversation/runs/20260912T135557Z-fd8609b-ea1461c50e/replays/failure-feedback-v2/observations.jsonl)도 별도 보존했다. 실제 제공자 호출 없이 같은 계약 검증 실패를 재현하고, 준비 상태와 코드 검사 결과가 이전과 동일한 상태에서 안내만 “앗, 요청을 처리하지 못했어요. 다시 시도해 주세요 🐾”로 바뀜을 확인했다. 세 요청의 기능 실패는 계속 `fail`이고 나머지 5턴도 `review_required`다. Lite의 잘못된 제안을 정상 실행으로 고친 실험은 아니다.

서버·직접 호출 API 회귀 검사 276개가 통과했고 기존 opt-in live 검사 1개는 건너뛰었다. 최종 v3 입력 분리 뒤 Judge 관련 검사 28개, Ruff, `uv run check`도 통과했다. 값이 있는 선택·이력·유효한 확인 대기의 보존은 별도 회귀 검사로 확인했다. APP의 같은 오류 코드 표시 보완은 [APP #352](https://github.com/SAJOYO/DAENGS_APP/pull/352)와 연결한다.
