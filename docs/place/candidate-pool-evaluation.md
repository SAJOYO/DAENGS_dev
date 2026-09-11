# 새 후보 출력 평가와 구조 연구

2026-09-11, 서버 #455 / 앱 #326. [구현 계약](candidate-pools.md).
결정적 실행 검증과 실제 모델 출력 평가를 분리한다. 자연어 누락을 회귀 테스트 통과로 가리지 않는다.

## 실험과 관측

[candidate-pool-cases.json](../../backend/evals/place_conversation/candidate-pool-cases.json)에
11문장 × 현재 집합 조합 21건을 고정했다. 저장 금지+주차/새 후보, 찜 제외,
정정 단독/조건 변경/다음 후보, 지칭 누락, 욕설 포함 정보 이의, 부정, 평가, 명시적 제외다.
모델은 `gemini-3.1-flash-lite`. 실제 운영 해석 경로를 1회 호출하며 회원·DB 없이
합성 카페 46곳과 B=앞 20곳으로 기존 ConversationService를 실행한다.
찜 화면은 plan_saved까지 검증하며 실제 앱 전환을 호출하지 않는다.

| 실행 ID (UTC) | 범위 | 자동 동작 통과 / 실패 / 미완료 |
| --- | --- | --- |
| [113042](../../backend/evals/place_conversation/candidate-pool-runs/20260911T113042Z/) | 21건, 초안 | 11 / 6 / 4 |
| [113430](../../backend/evals/place_conversation/candidate-pool-runs/20260911T113430Z/) | 21건, 정정 계약 보완 | 17 / 2 / 2 |
| [114238](../../backend/evals/place_conversation/candidate-pool-runs/20260911T114238Z/) | N01/N02/N08/N10/N11, 11건 | 9 / 2 / 0 |
| [114440](../../backend/evals/place_conversation/candidate-pool-runs/20260911T114440Z/) | 나머지 10건 | 10 / 0 / 0 |

앞 두 실행의 미완료는 HTTP 429다. 이후 호출 시작 간격을 3초에서 8초로 늘렸다.
마지막 두 실행은 같은 의미의 코드에서 서로 겹치지 않는 21건이며 **동작 19 통과 / 2 실패**다.
자동 의미 필드 검사는 17/21이지만 scope·저장 금지·feedback enum의 제한된 비교다.
모든 자연어 의미·답변 품질을 검증한 점수는 아니다. 두 실행 사이의 Python 포맷 정리로 파일 해시는 다를 수 있다.

초안의 평가기가 유효하지 않은 최신 계획 대신 이전 유효 계획을 표기하는 오류를 발견했다.
113042의 `intent/meaning`을 그대로 비교하지 않는다. 원본 `provider_calls`는 보존했고
현재 실행기는 해당 요청의 원본 function arguments를 사용한다.
113430 N01/new_candidates는 주차 조건이 OR 안에 적용됐는데 최상위 AND만 보는 평가기가 실패로 표시했다.
별도 review.json에 재판정(동작 18 통과 / 1 실패 / 2 미완료)을 남겼다.
마지막 두 실행은 AND/OR의 참·거짓·미상 의미를 비교하고 후보에도 주차 가능/불가/미상을 섞었다.
앞뒤 통과율을 엄밀한 A/B 개선율로 제시하지 않는다. 원본 관측은 덮어쓰지 않는다.

## 무엇을 바꿨고 왜인가

1. 모델이 정정 객체를 정확히 출력해도 중복 `feedback=familiarity`를 빠뜨리면 거절되던 계약을 고쳤다.
   정정 객체에서 라벨을 파생하며 서로 모순된 feedback은 거절한다.
2. 집합 전환의 근거 누락은 불필요한 재질문으로 이어졌고, 정보 이의는 검색 절과 함께일 때 생략됐다.
   도구 스키마에서 근거 구절과 feedback을 항상 출력하게 하고 현재 탭과 무관한 새 후보 요청을 지침에 명시했다.
   원문 근거 검사를 없애거나 저장 금지만 보고 범위를 바꾸는 우회는 추가하지 않았다.
3. K 확정과 조회 성공을 분리했다. 실패·제시 한도에서도 정정이 사라지지 않으며 다음 요청이 K를 따른다.
   특정 카드 교체가 남겨 둔 카드의 사실 시각을 계속 연장하지 않도록 했다.
4. B/K/E/P 계산·SQL 페이지 제한·세션 소유자·revision은 기존 결정적 실행 계층이 맡는다.

마지막 출력에서 N04/N05/N06의 정정·조건 조합·다음 후보는 동작 검사를 통과했다.
N08은 정보 한계를 인정하면서 주차 검색을 실행했다. N07은 지칭이 없어 질문했고 N09는 K를 만들지 않았다.
N05/N06/N11 일부는 `keep` 대신 이미 활성화된 `new_candidates`를 반복 출력해 의미 필드가 실패로 표시된다.
실제 집합은 바뀌지 않는다. N11의 과도한 next 출력은 기존 원문 기반 browse 검사가 current로 제한했다.

## 남은 실제 실패

N01 “찜하지 말고 주차 되는 카페만 찾아줘”의 전체 장소·찜 화면에서 모델이 주차 변경을 아예 누락했다.
집합 유지와 저장 금지는 지켜졌지만 필수 주차가 적용되지 않았으므로 **작업 미완료**다.
찜 화면에서는 잘못된 all_places 출력도 있었고, 저장 부정 근거 검사가 집합 유지로 제한했다.
현재 규칙만으로 누락 조건을 복원할 수 없다. 예문별 정규식을 늘리는 방법은 채택하지 않았다.
다음 연구는 필터 변경 누락 탐지와 의미 추출 계약의 비교가 적절하다.

21건은 작은 탐색 표본이다. 프롬프트에 넣은 문장이 포함되며 별도 미사용 표현 검증을 완료하지 않았다.
모델의 안정적 정확도·정보 이의 대응 전체·운영 데이터 품질·실제 회원 API 성능을 보증하지 않는다.
찜 화면에서 정보 이의와 검색이 함께인 경우의 답변 인정 문구 연결도 이번 표본의 검증 범위 밖이다.

## 반복 실행

backend에서 사용자가 허용한 기존 키 파일의 경로만 전달한다. 키·인증 헤더는 기록하지 않는다.

```powershell
uv run --no-sync python -m daengs_evals.place_conversation.candidates --key-file '<기존 키 파일>' --interval 8
# 수정한 경계만
uv run --no-sync python -m daengs_evals.place_conversation.candidates --key-file '<기존 키 파일>' --only N01,N02,N08 --interval 8
```

실행기 [candidates.py](../../backend/src/daengs_evals/place_conversation/candidates.py)는
요청·원본 계획·확정 상태·영수증·렌더 답변·개별 판정·제공자 지연을 새 폴더에 저장한다.
metadata 소스 해시와 케이스/후보 조건을 함께 본다. 답변은 영수증에서 렌더하며 두 번째 LLM을 호출하지 않는다.

## 결정적 검증

서버 고유 179개는 아래 범위를 변경 시점에 나누어 실행한 합집합이다. 재실행을 중복 합산하지 않았다.
후보·정책·컴파일·기존 찜 명령, 회원 HTTP·재시도·소유자 복원, 실제 SQL 제한 전 제외를 포함한다.

```powershell
uv run --no-sync pytest -q tests/place/conversation/test_candidates.py tests/place/conversation/test_candidate_evaluation.py tests/place/conversation/test_exploration.py tests/place/conversation/test_policy.py tests/place/conversation/test_service.py tests/place/conversation/test_search_policy.py tests/place/conversation/test_saved_search.py tests/place/conversation/test_bookmark_commands.py
uv run --no-sync pytest -q tests/place/api/test_candidates.py tests/place/api/test_conversation.py tests/place/api/test_conversation_filters.py tests/place/api/test_conversation_pending.py tests/place/api/test_conversation_bookmarks.py tests/place_bookmarks/test_interpret.py
# 별도 로컬 PostGIS에 기존 Alembic 마이그레이션 적용 후 DAENGS_PLACE_DATABASE_URL 지정
uv run --no-sync pytest -q -rs tests/place/integration/test_candidate_pools.py tests/place/integration/test_exploration_search.py tests/place/search/test_bookmark_lookup.py
```

처음 DB 미설정으로 건너뛴 2건도 임시 PostGIS 18에서 실행했다. 신규 SQL 테스트의 키 타입 fixture를
수정한 뒤 해당 검사를 다시 실행했다. 운영 DB 마이그레이션은 실행하지 않았다.
변경 Python 25파일의 Ruff·포맷과 `uv run check`도 통과했다. Windows 검사에는 실행 프로세스에만
`PSExecutionPolicyPreference=Bypass`, `PYTHONUTF8=1`을 설정했다. 시스템 정책은 변경하지 않았다.

앱은 13클래스 고유 62개와 `:app:assembleDebug`를 통과했다.
최초 61개 후 E가 남은 찜 목록의 조건 표시 1건을 추가하고 해당 클래스만 다시 확인했다.
CandidatePoolsTest, CandidatePoolUiTest, SearchPolicyTest, FacilityConversationTest,
ConversationUndoTest, ConversationFiltersTest, ConversationBookmarkTest, ConversationConnectedTest,
SavedSearchConversationTest, PlaceBookmarkApiTest, PlaceBrowseSessionTest, PlaceBookmarkControllerTest,
ConnectedPlaceBookmarkUiTest가 범위다. 전체 스위트·기기 설치·운영 종단 검증은 실행하지 않았다.

