# 대화 저장 트랜잭션 흐름

대화 원문은 제품 데이터로 저장하지만(D-043), 외부 오케스트레이터·Gemini 호출과 DB 작업은
한 트랜잭션으로 묶지 않습니다. 네트워크 대기 동안 row lock이나 `AsyncSession`을 들고 있으면
같은 반려견의 첫 활성화·5개 유지와 요약 재시도가 공급자 지연만큼 막히기 때문입니다.

## 턴 저장

1. **예약 TX** — 소유한 세션을 확인하고 `(session_id, client_message_id)`를 멱등 키로
   `chat_turns.processing` 한 행을 만든 뒤 commit합니다.
2. **DB 세션 종료** — 예약에 쓴 `AsyncSession`을 닫습니다.
3. **외부 호출** — 오케스트레이터를 호출합니다. DB 세션이나 row lock을 전달하지 않습니다.
4. **완료 TX** — `processing`인 행만 조건부로 `completed` 또는 `failed`로 바꿉니다. 성공적으로
   전달할 `AssistantResponse`가 있을 때만 세션의 `last_message_at`을 올립니다. 첫 활성화라면 이
   짧은 TX에서만 소유한 `pets` 행을 잠그고 활성 세션을 최근 5개로 정리합니다.

`CLARIFY`·`HANDOFF`·`REFUSED`도 사용자에게 전달된 `AssistantResponse`이므로 활성화합니다.
공급자·네트워크 실패는 turn을 실패로 닫을 뿐 draft를 활성화하지 않습니다. 완료 UPDATE가
0행이면 다른 작업이 먼저 완료/실패시킨 것이므로 덮어쓰지 않습니다.

## AI 요약

```text
예약 TX -> AsyncSession 닫기 -> Gemini 호출 -> 완료 TX
```

- 예약 TX는 완료된 turn만 읽어 `source_turn_count`를 고정하고 `processing` 요약을 commit합니다.
- 같은 `(source_session_id, source_turn_count)`의 processing/completed 행은 부분 UNIQUE로 한
  건만 허용합니다. completed면 그 summary ID를 담은 기존-요약 도메인 오류를 반환합니다.
- 5분을 넘긴 processing 행은 다음 예약 시 `STALE_PROCESSING` 실패로 바꾸며, failed 행은 같은
  원본 상태의 재시도를 막지 않습니다.
- Gemini에는 DB 객체가 아니라 예약 결과의 불변 transcript 문자열만 전달합니다.
- 완료 TX는 아직 `processing`인 summary만 구조화 결과로 채웁니다. 실패도 같은 조건부 UPDATE로
  닫아 늦게 도착한 응답이 이미 실패 처리된 예약을 되살리지 못하게 합니다.

## 무손실 제한

질문 2,000자, 답변 8,000자, 완료 turn 30개, transcript 320,000자, Gemini 입력 안전 상한
900,000 token입니다. 어느 경계에서도 조용히 자르지 않고 도메인 오류 또는 안전한 실패 코드로
끝냅니다. Gemini token 수는 tokenizer 의존성을 추가하지 않고 UTF-8 byte 수를 보수적인 상한으로
사용합니다.
