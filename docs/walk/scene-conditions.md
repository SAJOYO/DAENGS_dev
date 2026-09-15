# 장면 카드 기온 readmodel

기존 SGIS 주소는 `place_reference.facts`를 그대로 사용한다. 새 선택 필드 `temperature`는
`walk-diary-board-v1`의 각 장면에만 붙는다. 값이 없으면 JSON 필드 자체를 생략하여 과거
발행본의 해시·읽기를 유지한다. APP PR #421과 대응한다.

```json
{
  "temperature": {
    "temperature_c": 22.5,
    "unit": "celsius",
    "provider": "kma-vilage-fcst:ncst",
    "observed_at": "2026-09-14T10:00:00Z",
    "scene_at": "2026-09-14T10:01:00Z",
    "grid": [61, 125],
    "evidence_id": "slot:example"
  }
}
```

숫자는 계약 설명용 예시다. APP은 기온만 카드 주소 옆에 표시하고 관측 메타데이터도 보존한다.

## 데이터 경로

기존 weather-observation 수집·저장 → 기존 temperature_candidate의 좌표·요청 시각·관측 나이
검사 → 충돌 해소 → PartStamp.temperature_reference → publish_board → JSONB 발행본 → APP 파서.
시간 적합성이 확인된 격자 실황만 지원한다. 예보·regional-weather의 유효 구간값을 격자
실황으로 변환하지 않는다. 이미 수집된 유효 기온이 없으면 생략한다.

카드 표시용 참조는 글쓰기 용량 적용 전에 선택한다. 여러 유효 관측은 기존 rank를 따라
최신 관측부터 선택한다. 기존 근거 선택·LLM 프롬프트·호출 횟수는 변경하지 않는다.
전체 산책 기온이나 현재 날씨를 장면 기온으로 대체하지 않는다.

## 저장과 호환

준비 시 입력·계획 버전이 같은 슬롯만 결합하고, 완료 검사에서 준비된 장면 기온과 반환된
기온을 대조한다. 저장 결과의 bundle hash가 기온도 묶는다. GET 시 새 날씨를 조회하지 않는다.
기본 발행과 작성 실패 발행에도 같은 스냅샷을 쓴다. DB 스키마·SQL·APP Room 변경은 없다.
관계 일기 작업 브랜치는 변경하지 않았으며 별도 새 응답 형식에서도 이 DTO/투영을 재사용할 수 있다.

## 검증 범위

실제 준비·작성 조립·저장·재조회 함수를 사용한다. 작성 응답만 주입하고 외부 API/LLM/DB에는
접속하지 않는다. 환경 용량 0, 장면 결합, 변조, 과거 JSON 호환, 실패 발행을 확인한다.
실제 서버 배포와 폰 설치는 별도 단계다.

실행 결과: test_scene_conditions.py 3개, 기존 test_diary_temperature.py와
test_diary_card_writing.py 30개 통과. 변경 파일 Ruff 통과. APP 대응 검사 19개 및 APK 빌드 통과.
