# 관계 기반 산책 일기 v5

기획 기준: `docs/walk/relational-diary-contract.md`.

공간은 관계 중심으로, 행동은 현재 핀에 묶어 별도로 작성한다. 원자료·메모·사진은 생략 여부와 무관하게 보존한다. GPS 연결과 공간 경계·도로 통과는 다른 주장이다.

## 진입점

- `generate_relational_skeleton(base, ...)`: 기존 준비 계층부터 실행한다.
- `generate_prepared_relational_diary(prepared, ...)`: 저장된 v5 준비본에서 같은 작성·검수·조립·저장 경로를 실행한다.
- `write_relational_diary`: 독립 작업 작성기. `review=False`는 검수 없는 명시적 실험이며 의미 성공으로 표시하지 않는다.

기본 opt-in 경로는 숏메모리를 사용한다. 소개 실패 후 다음 동일 맥락의 소개를 복구하되, 성공한 소개에서 생략한 선택적 사실을 전부 나열하도록 강제하지 않는다. 생성문은 기억의 근거가 아니다.

## 오프라인 검사

backend 디렉터리에서 기존 프로젝트 환경으로 실행한다.

```sh
uv run pytest tests/walk/diary/test_relational_takeover.py -q
uv run python tools/run_relational_takeover.py --prepared evals/relational_takeover/prepared_v5.json.gz --output outputs/relational-inspect-new
```

두 번째 명령은 기본적으로 모델을 호출하지 않는다. 저장 준비본이 v5인지와 계획을 확인한다.

## 명시적 실제 호출

```sh
uv run python tools/run_relational_takeover.py --prepared evals/relational_takeover/prepared_v5.json.gz --output outputs/relational-live-new --live --env /path/to/project.env --max-calls 16 --minimum-interval 10
```

`--live`는 실제 provider 호출을 발생시킨다. 설정된 배포 모델을 사용하며 다른 모델로 자동 교체하지 않는다. 공간 3개·행동 1개·제목의 정상 경로는 작성 5회와 검수 5회다. 429는 그 실행을 중단하며 자동 재시도는 없다.

## 자료와 판정

`evals/relational_takeover`의 source와 v4 호출·발행본은 사용자 제공 묶음의 `app-gps-skeleton-08`에서 가져온 독립 합성 GPS·공간 자료다. 실측 산책이나 새 API 결과가 아니다. v5 준비본은 이 저장 프레임을 새 계획기로 재계획했다. 원래 이동 관측은 보존했으며 측정 커널을 다시 실행한 것으로 표시하지 않는다.

회귀 테스트의 writer/reviewer는 명시적 테스트 더블이다. 테스트 통과는 Gemini의 문장 품질이나 의미 검수 정확도가 확인됐다는 뜻이 아니다. 각 결과의 실패 단계·원문·실제 요청·검수 응답을 함께 본다.

운영 API 기본 전환, DB 마이그레이션, APP 반영은 이 opt-in 변경에 포함하지 않는다. 이전 v2/v3/v4 발행본은 읽기 호환을 유지한다.
