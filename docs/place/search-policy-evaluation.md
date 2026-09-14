# 공통 검색 정책 실제 출력 평가

2026-09-11. 모델: gemini-3.1-flash-lite. [구현 계약](search-policy.md).
실제 Gemini 해석과 공통 정책·컴파일까지만 실행했다. 회원 API·실제 찜 쓰기·DB 검색은 없다.
9문장을 일반 검색/찜 검색에 각각 넣은 18건이며, 기준은 카페·반경 3km·반려견 없음이다.

## 관측

| 실행 | 원래 기록 | 해석 |
| --- | --- | --- |
| [103943](../../backend/evals/place_conversation/search-policy-runs/20260911T103943Z/observations.json) | pass 10 / fail 7 / incomplete 1 | 아래 평가기 오류가 있어 성공률로 사용하지 않음 |
| [104503](../../backend/evals/place_conversation/search-policy-runs/20260911T104503Z/observations.json) | 의미 표현 17/18, 실행 효과 18/18 | S07 양쪽 통과, C08 찜 상태에서 keep 대신 bookmarks |

총 36회 호출 중 첫 실행 C08/bookmarks 1건은 429로 미완료였다.
두 번째 실행은 18건 모두 응답 완료했다. 전체 문장을 무한 재시도하지 않았으며,
각 실행의 cases·모델·소스 지문과 원문 요청/응답은 해당 디렉터리에 남겼다.

### 첫 실행에서 구분한 오류

- S07/bookmarks: forbid_save=true를 맞혔지만 search_scope=all_places였다.
  당시 새 일반 검색 연결을 그대로 타면 원치 않는 집합 전환이 될 수 있었다.
- C05: 새 후보를 all_places로 해석했다. 새 후보·찜 제외를 별도 의미 enum으로 인식하고,
  아직 구현하지 않은 집합은 미지원으로 보존하도록 바꿨다.
- 평가기: 같은 카페 조건을 유지하기 위해 kinds 변경을 생략한 정상 출력을 오답 처리했다.
- 평가기: BookmarkFilters의 dogs 리스트와 FilterState의 dogs 튜플을 직접 비교해,
  둘 다 비어 있는 경우도 원본 보존 실패로 처리했다.

원래 observations의 실패 표기를 지워 통과로 바꾸지 않았다. 두 평가기 문제를 수정한 뒤
전체 쌍을 다시 실행했다. 첫 실행 수치와 두 번째 실행 수치를 단순한 품질 향상률로
비교해서는 안 된다. 원시 모델 출력에서 의미 오류와 평가 오류를 각각 확인했다.

### 변경과 남은 차이

저장 금지·화면 복원을 독립적으로 표현하고, 집합을 바꿀 때 원문 구절을 받는다.
공통 정책은 실제 원문의 집합 지칭을 확인한다. 저장 금지만 있고 집합 지칭이 없으면
과도한 집합 변경을 유지로 처리한다. 이 잘못된 출력을 고정 입력으로 넣은 정책 테스트도
일반·찜 양쪽에 있다.

두 번째 S07은 모델 자체가 keep + forbid_save=true + 주차 필수를 출력했다.
따라서 실제 실행에서 검증 장치가 매번 모델 오해를 보정했다고 주장하지 않는다.
서버 검증의 효과는 고정 오답 입력 테스트로 별도 확인했다.

C08/bookmarks의 “찜 여부는 그대로 두고 식당도 같이 볼래”는 모델이 keep 대신 현재 집합인
bookmarks를 출력했다. 의미 표현의 차이는 fail로 보존했지만 실제 집합·조건은 기대와 같았다.
이번 18건은 한 번씩의 관측이며 모든 표현·재시도에서 정답이라는 보장은 아니다.
원문 근거 검사는 제한된 한국어 집합 지칭을 다루므로 표현 범위 밖의 명확화도 후속 관찰 대상이다.

## 반복

```powershell
cd backend
uv run --no-sync python -m daengs_evals.place_conversation.search_policy --key-file <기존 키 파일 경로>
# 특정 의미만 일반·찜 양쪽에서
uv run --no-sync python -m daengs_evals.place_conversation.search_policy --key-file <기존 키 파일 경로> --only S07,C02,C04
```

[search-policy-cases.json](../../backend/evals/place_conversation/search-policy-cases.json)이 입력이다.
status는 모델 의미 표현, execution_status는 공통 정책·컴파일에서 얻은 실행 효과를 평가한다.
둘 다 실제 회원 저장 완료나 DB 검색 품질의 점수가 아니다. 종료 코드만으로 통과를 판단하지 말고
observations의 fail/incomplete와 검사 항목을 확인한다. 인증 헤더·키 값은 기록하지 않는다.
