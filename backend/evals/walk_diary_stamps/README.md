# 일기 스탬프 이관 예제

전부 합성 자료(`evidence_origin=mock`)이며 실제 동선/시설 조회나 LLM 결과가 아니다.
entry/photo/context는 저장 형식을 입력 어댑터로 변환했다. 관측은 검증된 입력 계약 형태의
합성 후보를 별도로 제공했다. 동선에서 후보를 추출하는 정확도를 평가하지 않는다.

모든 예제의 목표 장수는 5다.

[Geo 선택 정책 비교 결과](selection-parity.json): 같은 이벤트 시각과 관측 구간을 기존
`scene_core.choose_supplements`에 전달했을 때 4개 예제 모두 선택/제외 사유가 일치했다.
원자료 추출·배경 투영·전체 Geo 파이프라인의 동등성 검사는 아니다.

| 예제 | 사용자 기록 | 관측 보충 | 결과 장면 | 남은 부족분 | 확인하는 동작 |
| --- | --- | --- | --- | --- | --- |
| [mixed](results/mixed/preview.md) | 3 | 2 | 5 | 0 | 행동·메모·사진 사이에 관측 보충, 행동 시점 근처·중복 관측 제외 |
| [records-enough](results/records-enough/preview.md) | 6 | 0 | 6 | 0 | 목표보다 많아도 기록 보존 |
| [observations-only](results/observations-only/preview.md) | 0 | 2 | 2 | 3 | 근거가 부족하면 장수를 억지로 채우지 않음 |
| [missing-route](results/missing-route/preview.md) | 1 | 0 | 1 | 4 | 위치·동선·사진 정보가 없어도 원문 기록 보존 |

`inputs/*.json`은 재실행 입력이다. 각 `results/`에는 입력 사본, 선택 정책과 사유가 있는
`prepared.json`, 시스템 기본 제목/미생성 서술의 `bundle.json`, 읽기용 `preview.md`를 담았다.

```powershell
# backend에서 실행. 테스트 DB/키나 Gemini 키가 필요하지 않다.
uv run python tools/preview_diary_stamps.py --input evals/walk_diary_stamps/inputs/mixed.json --target 5 --out ../diary-preview
```

Place 샘플에서 이름이 나온 것은 원본 등록 위치와의 거리 재료다. 시설 방문이나 촬영 대상,
사용자의 의도에 대한 서술은 없다. 사진 파일은 예제에 포함되지 않는다.
배경이 없는 장면은 원본 액션/관측만 남으며 LLM을 호출한 것처럼 표시하지 않는다.

구현 경계와 남은 연결은 [선택·스탬프 문서](../../../docs/walk/diary-stamps.md)에 있다.
