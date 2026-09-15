# 관계 계산과 모델 전달 정책

2026-09-15. 두 지점 비교의 새 어휘는 운영 작성기와 숏메모리에 연결했다
(`single-writing-brief-v3`). 기존 v1/v2 발행본은 각각의 입력 정책으로 검증한다.
구간 거리열을 만드는 공급자 연결과 구간 관계 선정은 여전히 실험 어댑터 범위다.
기존 발행본이나 원자료를 변경하지 않는다.

## 책임

| 모듈 (`daengs_walk.diary.relational`) | 입력과 출력 | 책임 밖 |
| --- | --- | --- |
| `relation_flow_contracts.py` | 거리 관측열·관계 원장·선택 결과의 내부 계약 | 모델 요청으로 직접 직렬화 |
| `relation_flow_analysis.py` | 동일 대상의 거리 관측열 / 기존 동선 판정 → 구간 관계 | 표현 어휘·중심 주제 선정 |
| `relation_injection.py` | 현재 장면의 시각·대상 식별자·관계 후보 → 명시적 허용 목록 | 어휘 변경·문체·필수 표현 지정 |
| `relation_delivery.py` | 선택 결과 → 모델 요청 | 관계 재판정·임의 재료 탈락 |
| `relation_vocabulary.py` | 확정된 판정명 → 모델용 어휘 | 계산·주입 선택·문체 지시 |

운영 경로는 `write_brief_task → brief_writer_view → writer_view → relation_view`이며
`memory_view`도 같은 `relation_view`를 사용한다. 별도 기능 플래그 없이 신규 생성에 적용한다.
`nearer/farther/same_distance`는 `closer_at_this_scene/farther_at_this_scene/comparable_distance`로,
배경의 동일/차이는 `shared_background/contrasting_background`로 보낸다.
내부 판정과 근거 ID는 유지한다. `drawing_closer`나 `leaving_behind` 같은 **구간** 어휘는
아래 거리열 근거가 있을 때의 실험 경로용이며, 두 끝점만 가진 운영 입력에 끼워 넣지 않는다.

실험 연결은 `backend/tools/run_relation_policy_experiment.py`에 있다. 공원 시험 자료의 명시적인 객체 ID에 등록 좌표를 연결하고, 계산된 유효 GPS 위치들로 그 점과의 거리열을 만든다. 임의 이름 매칭이나 모델의 위치 추론을 사용하지 않는다. 실제 공급자 객체·형상 연결은 운영 통합 때 별도 어댑터로 제공해야 한다.

## 계산과 어휘는 별도다

| 계산 결과 | 전달 어휘 | 성립 조건 |
| --- | --- | --- |
| `distance_decrease` | `drawing_closer` | 유효 구간의 거리 감소, 허용 오차 이상의 역방향 변화 없음 |
| `distance_increase` | `leaving_behind` | 유효 구간의 거리 증가, 허용 오차 이상의 역방향 변화 없음 |
| `distance_valley` | `drawing_closer_then_away` | 앞선 접근 이후 현재 구간에서 최솟값을 지나 충분히 멀어짐 |
| `passing` | `passing_by` | 실제 대상 범위, 근접 범위, 대상 축 기준 위치가 반대편으로 연속 진행한 근거까지 있음 |
| `distance_stable` | `keeping_a_similar_distance` | 허용 오차 내 거리 유지 |
| `alongside` | `staying_alongside` | 등록점이 아닌 공간 범위, 가까운 관계 유지, 누적 경로상 이동까지 있음 |
| `route_retrace` | `retracing` | 기존 동선 분석의 동일 경로 되짚기 |
| `route_return` | `coming_back` | 출발·종료점 근접과 실제로 벗어났다가 이어진 복귀 동선 |
| `route_turn` | `turning_back` | 기존 분석의 방향 반전 |
| `route_straight` | `carrying_on_straight` | 기존 분석의 직선 이동 |

등록점 접근 후 멀어짐을 `passing_by`로 바꾸지 않는다. 일정 거리만으로 `alongside`를 만들지 않는다. `target_axis_offset_m`는 대상에 대해 정의된 일관된 공간 축의 측정값이어야 하며 단순 GPS 순번이 아니다. `path_offset_m`는 유효 경로의 누적 거리다.

계산 정책 v1의 실험 기본값: 변화 20m 이상, 비교값은 보고 정확도 최대값의 2배 이상, 관측 간격 최대 30초, 최소 구간 10초, 실제 공간 범위의 곁 판정 최대 50m. 정책 객체로 분리했고 운영 기준으로 실측 확정한 수치는 아니다. 두 끝점만 있거나 공백·연속성 단절을 가로지르면 구간 관계를 만들지 않는다. 미래 거리 관측으로 과거 장면의 관계를 바꾸지 않는다.

## 선택 정책

- 현재 장면과 이전 장면 사이에 해당하는 관계를 선택한다. 구간 관계는 근거의 끝 시각, 회전 같은 사건은 별도의 대표 사건 시각을 사용한다. 사건 검출에 사용한 넓은 분석 범위는 따로 보존한다.
- 접근 이력이 앞 장면까지 이어지더라도 가장 가까운 시점이 현재 구간에 있으면 접근 후 멀어짐 관계를 구성할 수 있다. 앞선 구간에서 이미 끝난 최솟값을 다시 새 관계로 만들지 않는다.
- 같은 대상의 구간 관계가 선택되면 이에 중복되는 두 점의 거리 비교만 대체한다. 대체된 ID는 선택 결과에 보존한다. 다른 대상이나 도로·피복·넓은 지역의 비교는 유지한다.
- 모든 선택된 장면에 공간 호출을 만든다. `central`, `writing_focus`, 필수 관계 ID는 없다. 제공된 관계를 문장에 사용하는 것은 모델의 선택이다.

## 전달 정책

- 계산용 case를 그대로 노출하지 않고 정해진 어휘로 변환한다. 대상 이름·공간 범위, 구간 시각, 거리 변화의 대표 관측, 필요한 정확도와 관측 수를 묶어 전달한다.
- 등록점은 `reference_point`, 실제 경계는 `object_boundary`, 선형 대상은 `linear_feature`로 구별한다.
- 내부 원자료 ID·판정 정책·소스 버전은 원장에 남긴다. 모델에는 모델용 인용 ID와 뜻을 이해하는 데 필요한 정보만 보인다.
- 구간 근거가 없어 남은 두 점 비교는 `closer_at_this_scene` / `farther_at_this_scene` 등 지점 비교로 전달한다. 풍부한 어휘를 위해 이동 사실로 승격하지 않는다.
- 메모에는 이미 이 경계를 거친 선택 자료만 넣는다. 생성문을 사실로 재사용하지 않는다. 행동 입력은 현재 강아지 행동과 현재 맥락 경로를 유지한다.
- 프롬프트에 문학적 문체 지시는 추가하지 않는다. 제거된 `end_near_start` 용어 설명과 자료 컨테이너 이름만 새 요청에 맞춘다.

## 확인

`tests/walk/diary/test_relation_flow_policy.py`는 케이스, 필요한 공간 근거, 시간 절단, 공백, 대상별 중복 대체, 전달 경계, 사건 시각과 분석 범위 분리를 검사한다. 실제 출력·시나리오·원장·선택 결과·요청은 별도 실험 산출물에 보존한다. 인용 ID 통과는 자연어 의미 정확성 통과를 뜻하지 않는다.

운영 어휘 연결 재현: `uv run python tools/run_writer_vocabulary_smoke.py --env <환경 파일> --output <결과 JSON>`.
합성 스냅샷 두 개를 실제 `write_brief_task`와 운영 프롬프트·Gemini 전송 함수로 보내고,
발행 결과 검증 함수를 거친다. 10초 간격, 최대 2회, 재시도 없음. DB·APP·운영 서버 호출은 아니다.
결과는 `backend/evals/walk_diary/writer_policy_20260915/production_vocabulary.json`에 보존한다.
