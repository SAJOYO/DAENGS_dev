# evals/conversation_quality/

`daengs_evals.conversation_quality` 가 읽고 쓰는 자리입니다. 여기 쌓이는 것은 동결된
멀티턴 케이스 세트(`cases_v1.jsonl` 처럼 `_vN` 이 붙는 파일), 그 세트를 랩에 돌린
결과(`lap_<lap>.jsonl`)·판정(`judgments_<lap>.jsonl`)·리포트, 그리고 앵커 통과 기록
(`anchor_check_<앵커세트>_<프롬프트버전>_<모델>.json` — `judge.anchor_record_name` 이
정하는 이름입니다)입니다. **이 앵커 통과 기록은 결과물이 아니라 게이트입니다** — 없으면
`judge.run_score` 가 아예 안 돕니다. 이름이 낯설어 보여도 지우지 마세요, 지우면 다음
`score` 실행이 앵커 검사를 다시 요구합니다.

**이 세트는 프로덕션 분포가 아닙니다.** 13건은 실제 대화에서 관찰된 실패 모양(지시대명사
미해소·정정 무시·반복 뒤 미개선·상태 오용) 을 한 건씩 손으로 눌러 담은 것이지, 실제 사용자
질문이 이 비율로 들어온다는 뜻이 아닙니다. 표본을 늘리고 싶어도 여기서 합성으로 채우지
않습니다 — 사람이 한 건씩 눈으로 검토할 수 있는 크기가 이 세트의 목적입니다.

**숫자는 지표가 아닙니다.** 사람 라벨 캘리브레이션이 없는 지금은 이 세트를 돌려 나온
점수를 "품질이 몇 점" 으로 읽지 않습니다 (D-060 ⑦ · RAG-075). 하는 일은 채점이 아니라
사람이 볼 자리를 고르는 것 — 어느 케이스가 기대와 어긋났는지 골라 사람 검토대에 올리는
것입니다.

## D-073 — 케이스 13 → 16 (2026-09-12)

D-073(못 재는 이유를 말한다 — 산책 `unmeasured` 마커)가 `cases_v1.jsonl` 끝에 세 줄을
더했습니다. **기존 13줄은 한 글자도 안 건드렸습니다** — 끝에만 더했습니다.

- `cq_distance_recorded_01` — 오늘 산책이 전부 측정된 날. 숫자가 나가고 마커는 안 선다.
- `cq_distance_partially_measured_01` — 3건 중 2건만 측정된 날. 측정된 합계와 미측정
  건수를 갈라 말해야 한다.
- `cq_distance_described_route_01` — 스크린샷 사례: 대중교통이 섞인 경로를 말로 설명하고
  거리를 묻는다. 그날 산책 기록이 있지만 그 경로와 일치하지 않아 `unmeasured` 가 서야 한다.

**이전 랩과 직접 비교하려면 이 3건을 빼고 봐야 합니다** — 분모가 13에서 16으로 바뀌었기
때문입니다. `daengs_evals.conversation_quality.cases.file_sha256`(LF 정규화 해시 —
`answer_quality` 의 바이트 해시와 다릅니다)로 잰 값:

| 파일 | sha256 |
| --- | --- |
| 이전 (13줄) | `d41e0d41d3332a8fb6431a5bd90aaf92afe0e809a0bb1eec3f73cab647ab0079` |
| 이후 (16줄) | `cfdb0dc0857a809c28356ea157b65b15f9e9e9c99407ec340a4722ebf30fe56c` |

`report.PINNED_FIELDS` 가 `cases_sha256` 을 대조하므로, 이 3건이 있는 랩과 없는 랩은
`compare`/`case-report` 가 값이 다르다는 이유로 비교를 거부합니다 — 의도된 동작입니다.

**하네스가 산책 기록 유무를 표현할 수 있는가 — 표현할 수 있습니다.** `drivers.py` 가
`case.state_snapshot` 을 그대로 `driver.context` 에 얹고 `orchestrator.run(context=...)`
로 넘기며, `planner._walk_activity_context` 는 DB 를 거치지 않고 `context["walk_activity"]`
를 그대로 읽습니다(`care_log`·`dog` 와 같은 자리). 그래서 케이스는 `state_snapshot`
에 `walk_activity` 딕셔너리를 넣으면 "그날 기록 있음" 을, 그 키를 아예 빼면 "그날
기록 0건" 을 표현할 수 있습니다.

**"오늘 기록 0건" 갈래는 이 3건이 재지 않습니다 — 다만 코드는 그 갈래를 엽니다.**
마커를 세우라는 지시(`_UNMEASURED_RULE`)는 데이터 유무와 무관하게 **항상** 실리므로
(D-073 "오늘 기록 0건 갈래를 엽니다" 절), 오늘 기록이 0건이어도 고지가 나갑니다.
`cq_distance_described_route_01` 이 그날 산책 기록을 **있게** 만든 것은 그 갈래를
피하려던 처음 설계의 흔적이고, 지금은 "기록은 있지만 그 경로와 다르다" 를 재는
케이스로 읽으면 됩니다. 하네스는 0건 갈래도 표현할 수 있으니(`state_snapshot` 에서
`walk_activity` 키를 빼면 됩니다), 그 케이스를 더할 때는 **기대가 "고지 나감"** 이라는
것을 옆에 적으십시오 — 더 이상 "오늘은 실패하는 것이 정상" 인 갈래가 아닙니다.

## Turn Resolver 랩 설계 (`#416`) — 설계만, 돌리지 않습니다

아래는 `#416`(Turn Resolver, `docs/superpowers/specs/2026-09-10-assistant-turn-context-design.md`)
을 이 하네스로 잴 때의 설계입니다. **여기서 실제로 랩을 돌리지 않습니다** — `collect`·
`check-anchors` 는 실제 Gemini 호출이라 유료이고, 그것을 승인하는 것은 사람 몫입니다. 이
절은 그 승인이 난 뒤 그대로 따라갈 수 있게 미리 못박아 두는 계획입니다.

### before 랩은 `after_v2_346cada0/lap_after.jsonl` 입니다 — `lap_before.jsonl` 이 아닙니다

`lap_before.jsonl` 은 `#415`(되묻기 `ASK`) **이전**에 모인 랩이라 이 카드의 출발선이 아닙니다.
`#415` 가 이미 두 지시어 케이스의 현재 동작을 바꿔 놨습니다 — "없는 맥락을 지어내
답한다" 에서 "무엇을 가리키는지 모르겠다고 말한다" 로:

| 케이스 | `#415` 전 (`lap_before.jsonl`) | `#415` 후 = 이 카드의 출발선 (`after_v2_346cada0/lap_after.jsonl`) | 이 카드의 목표 |
| --- | --- | --- | --- |
| `cq_pronoun_geugeo_01`(`그거 얼마나 오래 해야 해?`) | ANSWERED — 없는 맥락을 지어냄 | CLARIFY — 못 찾겠다고 말함 | **ANSWER** — 실제로 찾아냄 |
| `cq_pronoun_akka_01`(`아까 말한 거 다시 설명해줘`) | ANSWERED — 없는 맥락을 지어냄 | CLARIFY — 못 찾겠다고 말함 | **ANSWER** |

`#415` 가 "지어내기" 를 "모른다고 말하기" 로 이미 고쳤습니다. Turn Resolver 가 더할 것은
**찾아내기**뿐이라 개선 방향이 한 칸입니다 — 이 둘이 가장 깨끗한 수용 신호인 이유입니다.
그래서 before/after 비교는 항상 `after_v2_346cada0/lap_after.jsonl` 을 before 인자로,
Turn Resolver 를 켠 새 랩을 after 인자로 넘깁니다:

```bash
uv run python -m daengs_evals.conversation_quality case-report \
  --before-lap backend/evals/conversation_quality/after_v2_346cada0/lap_after.jsonl \
  --after-lap <새 랩>/lap_after.jsonl
```

`case-report`(#415) 를 그대로 씁니다 — 판정기를 안 불러서 공짜이고 새 리포트를 안 만듭니다.

### 여섯을 고정합니다 — 하나라도 다르면 리포트가 비교를 거부합니다

`case_report`·`compare` 양쪽 다 `report.PINNED_FIELDS` 여섯을 봅니다: **케이스 파일 해시
(`cases_sha256`) · judge 모델 핀(`judge_model`) · judge 프롬프트 버전(`prompt_version`) ·
앵커 세트(`anchor_set`) · 어댑터 모드(`adapter_mode`) · 미측정 비율의 정의**. 앞 다섯은
`LapHeader`/`JudgeHeader` 에 실제 값으로 남아 `render_compare` 가 대조합니다. 여섯째("미측정
비율 정의" — 판정 전 제외 + 가짜 어댑터 셀 + 해당 없는 축, 분모 = 턴수×3)는 값이 아니라
**계산 방법**이라 헤더에 안 남지만, `report.py` 모듈 하나가 그 정의를 유일하게 갖고 있어
두 랩이 서로 다른 셈법으로 계산될 수 없습니다. Turn Resolver 랩을 새로 모을 때
`--adapter-mode real` 로 `after_v2_346cada0` 와 같은 값을 넘기고, judge 를 다시 돌릴 때도
같은 `--judge-model`·앵커 세트를 씁니다 — 여섯 중 하나라도 움직이면 `render_compare` 가
`ValueError` 로 비교를 거부하는 것이 설계이지 버그가 아닙니다.

### 두 수용 케이스는 유닛 테스트가 못 잡습니다 — 이 랩이 잡아야 합니다

Task 8 의 배선 테스트(`test_repeat_does_not_replay_the_same_fixed_refusal` 등)는
`FakeDriver`/`_fake_adapters` 로 돕니다. 그 가짜 capability 어댑터는 관계(`relation`) 가
무엇이든 **고정 문자열**을 돌려주므로, "직전과 똑같은 거절 문구를 다시 재생하지 않았다"
(REPEAT) 와 "off-topic 거절로 흘러내리지 않았다"(META) 라는 **문장의 내용**은 그 어댑터
구조로는 표현할 수 없습니다 — 무엇을 돌려주든 같은 문자열이라, "달라졌는지" 를 잴 재료가
애초에 없습니다. 배선이 관계를 올바른 자리로 넘겼는지는 유닛 테스트가 보증하지만, **모델이
실제로 다르게 답했는지** 는 실제 Gemini 응답이 있어야만 보입니다. 이 랩이 그 자리입니다 —
못 재면 수용 케이스 9건 중 둘이 "아무도 확인 안 한 채 통과됐다" 로 남습니다.

- **REPEAT — `cq_repeat_after_failure_01` 턴 3.** `transcript.check_transcript` 의
  `max_repeat_count` 를 그대로 씁니다. before(`after_v2_346cada0`) 에서는 턴 1·3 이 **같은
  고정 거절 문장**이라 `max_repeat_count == 2` 입니다. Turn Resolver 후 목표는
  `max_repeat_count == 1` — 같은 거절을 그대로 재생하지 않는 것입니다. `expected_mode` 자체
  (`ASK` vs `REFUSED/diagnosis`) 는 아직 미정이므로(아래 "사람에게 넘길 것" 참고)
  `response_mode_fit` 으로 채점하지 않고 **반복 횟수만** 봅니다 — 이 검사는 `ASK` 든
  `REFUSED` 든 통과하도록 설계돼 있습니다.
- **META — `cq_observed_wellness_repair_01` 턴 7.** `case_report.py` 의 `redirect` 열
  (`general_decision.reason`) 을 봅니다. before 는 이 턴에서 `off_topic` 이 아니라 일반론
  강의로 답했고(견종 개인화까지 섞임), 목표는 **`off_topic` 거절로도, 근거 없는 개인화로도
  떨어지지 않는 것**입니다. `case-report` 표의 턴 7 행에서 `redirect` 가 `off_topic` 이
  아닌지, `dead_end` 가 비었는지를 사람이 직접 확인합니다 — 코드 sentinel 로 자동 판정하지
  않는 이유는 "off_topic 이 아님" 이 "좋은 답" 을 보증하지 않기 때문입니다(예: 엉뚱한 다른
  거절로 새는 것도 실패입니다). 이 랩의 리포트는 그 행을 표로 보여주는 데까지만 하고, 판단은
  사람이 합니다.

### 응급 대조군은 이 랩에서 못 읽습니다 — 미측정으로 적습니다

하네스에 `vet_contact` 어댑터가 없습니다(`daengs_evals.eval_harness` 의 `fake_adapters`·
`collect.py` 의 `build_adapters` 어디에도 없음). 물리면 place-search
HTTP 의존이 생기는데, 이 카드는 그것을 안 뭅니다. 실측으로 `after_aa512506`·
`after_v2_346cada0` 두 after 랩 모두 이 케이스에서 `FAILED`("지원하지 않는 기능입니다:
vet_contact") 로 끝났습니다 — 둘 다 **하네스 구멍이지 회귀가 아닙니다.**

그래서 응급 대조군(수용 케이스 8, 응급 신호가 Resolver 결과에 약해지지 않는 것)은 이 랩에서
**미측정**으로 적습니다 — `case_report.SENTINEL_UNMEASURED` 와 같은 원칙입니다: "0건 위반"
과 "잴 재료가 없음" 을 같은 칸에 적으면 하네스 구멍이 "문제없음" 으로 읽힙니다. 이 케이스의
실제 보증은 **오케스트레이션 테스트**(`backend/tests` 의 Turn Resolver 순서 테스트 — 응급 →
결정론 → Resolver → 시맨틱 라우터)가 지고 있고, 그쪽은 HTTP 가 필요 없어 이미 CI 로 매번
돕니다. 랩 리포트에는 "응급 대조군: 미측정 — 하네스에 `vet_contact` 어댑터가 없음. 보증은
오케스트레이션 테스트가 대신함" 이라고 한 줄로 적습니다.

### 라우터가 실행마다 다른 능력을 고릅니다 — 1회 실행을 개선·퇴행으로 읽지 않습니다

temperature 0 인데도 시맨틱 라우터는 실행마다 다른 capability 를 고를 수 있습니다 — 과거
비교에서 **같은 응급 질의**가 before 는 `general`, after 는 두 번 다 `vet_contact` 로
갈렸습니다(반복 횟수: before 1회, after 2회 — 3회의 관측 중 갈린 것은 이 한 자리뿐이었습니다).
멀티턴 랩은 후보 턴마다 이 흔들림이 누적되므로 단발 랩보다 더 크게 벌어집니다.

그래서 이 랩의 리포트는:

- 같은 조건(고정 여섯)으로 **최소 2회** 이상 반복 수집합니다. 한 번이 아니라 여러 번을
  적어야 "이번엔 `general`, 다음엔 `vet_contact`" 가 실제로 같은 관계 판정 위에서 갈린
  라우팅 잡음인지, 아니면 Turn Resolver 가 관계를 잘못 매겨서 생긴 차이인지 갈립니다.
- 리포트 본문에 **반복 횟수와 각 회차의 capability 분포**를 적습니다(예: "3회 중 `general`
  1회 · `vet_contact` 2회").
- 단일 실행에서 before 대비 after 가 달라졌다는 것만으로 "개선됐다"/"퇴행했다" 라고 쓰지
  않습니다 — 여러 회차에서 일관되게 같은 방향으로 갈릴 때만 그렇게 읽습니다. `relation`
  정확도(수용 케이스 1~9)는 판정기 없이 코드로 맞춰 보는 값이라 이 잡음의 영향을 덜 받지만,
  `context_continuity`·`repair_success` 두 축은 판정기를 거치므로 이 잡음이 그대로 섞여
  들어옵니다 — 두 축의 회차 간 분산도 같이 적습니다.

### 나머지 — 이미 있는 것을 그대로 씁니다

- **`transcript.PRIOR_TURNS_REACH_INFERENCE = True`** 이므로 `report.FLOORED_AXES`
  (`context_continuity`·`repair_success`) 는 `before.driver == "stateless"` 일 때만 0 으로
  못박힙니다. `after_v2_346cada0` 은 `StatelessDriver` 로 모인 랩이라 `driver == "stateless"`
  이고, 그 두 축이 `기능 부재` 로 덮입니다 — "품질 개선" 으로 읽지 않습니다
  (`docs/orchestration/conversation-quality.md` §3). Turn Resolver 랩은
  `drivers.SessionDriver` 로 모아야 그 두 축이 실제로 판정됩니다.
- **사람에게 넘길 것 하나.** `cq_repeat_after_failure_01` 의 `expected_mode` 는 열려
  있습니다 — after_v2 에서 그 케이스는 `REFUSED` 이고, "그러니까 발을 저는 이유가 뭘 수
  있는지 알고 싶다고" 가 원인을 명시적으로 요구하므로 승인된 의료 경계 규칙대로면
  `REFUSED/diagnosis` 가 맞을 수 있습니다 — 케이스의 `ASK` 기대 쪽이 낡았을 가능성입니다.
  이 문서는 그것을 정하지 않습니다. 정리 전에 랩을 돌리면 제대로 고쳐 놓고도 그 행이
  `response_mode_fit` 표에서 빨갛게 떠서 자기 실패로 착각할 수 있습니다 — 위에서 REPEAT
  수용 신호를 `response_mode_fit` 이 아니라 반복 횟수로만 보는 이유가 그것입니다.
