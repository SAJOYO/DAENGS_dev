# 시설 함수 호출 실험

사용자 대신 시설 필터·검색·카드 선택을 조작하는 첫 번째 실행 경로다.
실제 Gemini가 저장소의 함수 호출 대화 모듈을 사용하고, 검색은 기존 합성 A–F 장소를 사용한다.
운영 v2 라우트, 회원 DB, Redis, Android 화면은 연결하지 않았다.
설계와 남은 연결은 [native-tool-conversation.md](../../../../docs/place/native-tool-conversation.md)를 본다.

backend 디렉터리에서 실행한다. 환경은 Python 3.12와 `uv sync --frozen --extra place`로 준비한다.

```powershell
uv run --no-sync python -m daengs_evals.facility_tools.playground --key-file C:\path\to\.env
```

기본 주소는 `http://127.0.0.1:8769/`다. `--port`, `--model`로 변경할 수 있다.
키 파일은 `GEMINI_API_KEY=...` 또는 `gemini: ...` 항목만 읽는다. 키와 인증 헤더는 브라우저와 실행 기록에 포함하지 않는다.
페이지는 127.0.0.1에서만 열리고, 세션은 쿠키별 메모리에 저장된다. 재시작하면 실험 세션은 사라진다.

실험 순서:

1. `주차되는 카페만 보여줘` → 카페·주차 필수, 장소 A.
2. `음식점도 추가해줘` → 주차 조건 유지, 장소 A·E.
3. `주차는 상관없어` → 두 업종 유지, 장소 A–F.
4. `두 번째는 주차 돼?` → B의 등록된 주차 불가 사실 조회, 화면 상태 보존.
5. `두 번째 장소 선택해줘` → B 카드 선택. 검색 조건 변경과 별개다.
6. 수동 업종·주차 선택 또는 카드 버튼으로 동일 명령 계층을 비교한다.

조건 칩·카드·선택·변경 표시는 서버 상태에서 만든다. 모델의 문장으로 UI를 구성하지 않는다.
개발자 보기를 펼치면 시스템 지침, 도구 정의, 실제 함수 인자/결과, 모델 요청 원문을 확인할 수 있다.
일반 검색과 카드 선택은 즉시 실행한다. 지원하지 않는 조건이나 모델이 제안한 조건 변경만 별도 제안으로 남는다.
제안이 생긴 턴은 추가 행동을 실행할 수 없으며, 다음 사용자 입력이나 제안 버튼으로 처리한다.

모델 요청 실패 시 이미 변경된 카드와 조건은 유지된다. `응답 이어받기`는 같은 요청 ID로
기록된 함수 결과 이후부터 재개한다. 새 요청으로 재검색하는 동작과 다르다.

검증은 새 명령·대화·실험 HTTP 경계만 선택한다.

```powershell
uv run --no-sync pytest -q tests/place/commands
uv run --no-sync ruff check src/daengs_place/place/commands src/daengs_backend/services/facility_tools src/daengs_evals/facility_tools tests/place/commands
```

이 검사는 합성 검색기·대역 모델·메모리 상태를 사용한다. 실제 Gemini의 조건 누락과 답변 사실성,
운영 PostGIS·회원 DB·Redis 저장, Android 통합의 성공을 보장하지 않는다.

실제 모델 평가는 다음 명령으로 실행한다. Gemini 요청 비용이 발생한다.
`--scenario 0`은 기본 연속 조작 7턴, `1`은 미지원 조건 제안·취소·수락·감사 5턴,
`2`는 아는 장소 기록 1턴이다. 생략하면 13턴을 모두 실행한다.
출력 파일의 상위 디렉터리는 먼저 준비해야 한다. 자동 판정은 화면 상태와 도구 실행이며,
말풍선의 사실성·자연스러움은 기록을 별도로 읽고 판단해야 한다.

```powershell
uv run --no-sync python -m daengs_evals.facility_tools.evaluate --key-file C:\path\to\.env --scenario 0 --output evals/facility-tools-basic.json
```
