# 테스트 재점검 — 지도와 진행

구조 재편(결정 #67)이 끝났다. 그 다섯 PR 은 전부 **"테스트 본문 수정 0"** 을 완료 조건으로
삼았으므로, 테스트는 새 구조를 한 번도 따라오지 못한 채 import 경로만 갱신됐다. 이 문서는
그 격차를 재고 pass 별로 좁힌 기록이다.

한 번에 다 돌리면 꼼꼼해지지 않는다. 나눠서, 각 pass 마다 세 가지를 묻는다.

    이 테스트가 무엇을 지키나
    어디 있어야 하나
    **무엇을 못 보나**          ← 이번 구조 시리즈에서 네 번 물린 질문이다

## 규모 — Pass 0 스냅샷

**기준 `2331ced` (2026-08-27).** 이 문서의 숫자와 배정은 전부 이 시점의 것이다. 이후
pass 가 진행되고 다른 갈래가 머지되면 총계는 달라진다 — 감사 문서는 현재 상태가 아니라
**시점별 기록**이므로 다시 세지 않고 pass 결과에 변화를 적는다.

397 개 / 49 파일. 디렉토리별로는 `walk` 129 · `discovery` 102 · `facility` 60 ·
`place` 31 · `providers` 32 · `api` 14 · `usage` 9 · 최상위 20.

## 자리 불일치 — 디렉토리 이름이 옛 구조의 화석이다

| 디렉토리 | 실제 검증 대상 | 처분 |
|---|---|---|
| `search/` → `discovery/` | import 1위가 `app.discovery`(38건). `search` 라는 패키지는 없다 | **Pass 1 완료** |
| `walk/` | walk 7파일 + **territory 6** + scene 1 — 코드는 셋으로 갈랐는데 테스트는 한 방 | Pass 2 |
| `facility/` | `geo`(hours·pet_axes·icons) + `ingest` + `place` 혼재. `facility` 는 #65 가 내부 용어로 격하한 어휘 | Pass 3 |
| `place/` `providers/` `usage/` `api/` | 대상과 대체로 일치 | Pass 4 |

배치 원칙은 **primary owner** 다 — 그 테스트가 주로 무엇을 지키는지를 보고 그 패키지 밑에
둔다. 그래야 "이 테스트 어디 두지"에 구조가 답한다.

**`app/` 트리의 1:1 복제가 목표가 아니다.** 둘 이상의 패키지 **사이 경계 자체가 계약**인
테스트가 있다 — 생산자와 소비자를 한 픽스처로 관통해야 의미가 있는 것들이다. 그런 파일은
주 경계 쪽에 두고 **예외 이유를 파일 docstring 과 이 문서에 명시한다.** 억지로 쪼개면
경계 테스트가 반쪽 두 개가 되어 지키던 것을 잃는다.

통합 테스트가 여러 패키지를 관통할 때의 임시 배치도 마찬가지다 — 최종 소유권을 지금
정하지 않고, 그 패키지를 다루는 pass 에서 함께 본다.

## 커버리지 공백

직접 import 가 없는 모듈 24개를 세었으나, TestClient·함수 호출 경유를 추적하니 대부분
간접 검증이었다. **산책 업로드는 공백이 아니다** — `test_walk_store`(멱등) ·
`test_walk_bundle`(변환) · Android `WalkSessionExporterTest` 셋이 분담하고 그 분담을
docstring 이 적어놨다.

진짜로 아무도 안 보는 것:

| 대상 | 무엇이 안 지켜지나 | 배정 |
|---|---|---|
| `GET /places` (legacy) | 응답 계약 | Pass 4 |
| `/anchor/search` | `truncated` 경계 | Pass 3 |
| `providers.naver` · `kakao` | 파싱·인증 헤더. fake 경유만 있다 (tmap 은 truthfulness 가 커버) | Pass 4 |
| `providers.registry.build` | 키 유무별 제공사 선택 | Pass 4 |
| `journey.handoff` | 딥링크 생성 | Pass 4 |
| `usage.http` | `UsageDenied` → HTTP 매핑. **gate 는 촘촘한데 오류 매핑층이 0 이다** | Pass 4 |
| `core.clock` | `FixedClock` 결정론 계약 | Pass 4 |

## Pass 진행

- [x] **Pass 0** — 지도 (이 문서)
- [x] **Pass 1** — `search/` → `discovery/`. 아래 참고
- [x] **Pass 2** — `walk/` 를 가른다. 아래 참고
- [ ] **Pass 3** — `facility/` 해체 + `place/` 정합, `/anchor` 공백
- [ ] **Pass 4** — providers·usage·api 정합 + 남은 공백 전부

### Pass 2 결과

`tests/walk/` 14파일을 검증 대상으로 갈랐다.

    tests/geo/        test_cells_golden                                   geo.cells 만 본다
    tests/territory/  paint · region · layers · multichain · sheet_cache
                      · region_visit_rate · experience · evidence         8파일
    tests/walk/       bundle · contract · curve · facts · store
                      · encounter                                         6파일 잔류

**`test_walk_encounter` 는 일부러 안 가른다.** scene 판정 테스트 3개가 섞여 있지만, 파일
docstring 이 주제를 "관측(사실)과 판정의 **경계** 고정"으로 선언한다 — 생산자와 소비자를
한 픽스처로 관통하는 것이 이 파일의 존재 이유다. 쪼개면 경계 테스트가 죽는다. 두 진입
경로의 합의를 지키는 `test_ranking` 과 같은 종류다.

**`test_territory_experience` · `test_territory_evidence` 도 함께 옮겼다.** 처음엔 #108(E3a)
이 그 파일을 편집 중이라 보류했는데, #108 이 머지되면서 보류 근거가 사라졌다. 둘 다 주
검증 대상이 `territory.experience` · `territory.evidence` 이고 `walk` 쪽은 픽스처용
`facts`·`models` 만 쓴다. 남겨뒀으면 Pass 2 의 `[x]` 가 거짓이 됐다 — **보류는 근거가
사라지는 순간 재검토해야 하고, 그 재검토를 안 하면 체크박스가 사실과 어긋난다.**

경로 문자열 4건 갱신 — `hex-grid-golden.json` 의 generator 주석 포함. 전부 "그 테스트가
지금 어디 있나"를 가리키는 살아있는 포인터라 갱신했다. 사건 서술(결정문의 옛 경로)과
구분한다.

### Pass 1 결과

`tests/search/` → `tests/discovery/`. 함께 옮긴 것:

    test_personas.py        → tests/profile/     검증 대상이 profile.source · journey.advice
    test_facility_ranking.py → tests/facility/    검증 대상이 api.facility · ingest

**테스트 품질 자체는 깨끗했다.** 비공개 심볼을 찌르는 곳 0, `Companion = str` 시절 가정
잔재 0, `inspect.signature` 세 곳은 전부 의도적 경계 고정이다(계획을 나눈 목적이 "엔진이
남의 축을 못 본다"이므로 시그니처가 곧 계약).

`refine.labels` 만 직접 테스트가 없었는데, `diff` 와 `features/hospital/actions` 가
문장 전체를 비교하므로 간접으로는 덮여 있다. 다만 두 소비자가 대표값 하나씩만 쓰기 때문에
**m/km 경계는 아무도 안 본다** — `test_labels.py` 로 그 경계만 고정했다.

`tests/search` 문자열 참조 3건(`.github/workflows/ci.yml` 주석 포함)도 갱신했다. pytest 가
못 보는 자리라 grep 으로만 잡힌다 — 구조 PR 에서 두 번 물린 것과 같은 종류다.
