# 점령지 사진 판정 워커

이 문서는 워킹 스켈레톤의 사진 판정 경계를 기록한다. 사진은 산책 화면에서 촬영된
방문 증거이며, VLM은 **사진에 실제 강아지가 보이는지**만 판정한다. VLM 통과는
`VerifiedVisit`을 만든다. [게임 시도에 연결한 사진](territory-ownership-api.md)은
별도 점유 어댑터가 같은 트랜잭션에서 우선권·버전을 검사한다. 방문 인증 성공이 반드시
점유 성공을 뜻하지는 않는다. 연결하지 않은 기존 방문 인증은 소유권을 만들지 않는다.

## 처리 흐름

1. 앱이 `POST /app/territory/attempts`로 10m 위치 증거를 고정하고 create-only URL에
   사진을 직접 업로드한다.
2. confirm은 크기·MIME·object generation을 고정해 DB를 `VISION_PENDING`으로 먼저
   commit한다.
3. commit 뒤 `territory-vision` 큐에 attempt id만 발행하고 즉시 응답한다. 이미
   `VISION_PENDING`인 confirm 재호출도 다시 발행하므로 broker 장애 뒤 재시도가 가능하다.
4. 별도 worker가 고정된 generation의 바이트만 읽어 `TerritoryVisionPort`로 판정한다.
5. `dog_visible`은 `VERIFIED`, `no_dog_visible`과 `uncertain`은 `REJECTED`, 기술 실패는
   한 차례 재시도 뒤 `FAILED`로 기록한다. 앱은 기존 단건 GET을 polling한다.
6. 종결 상태와 모델/프롬프트 버전을 먼저 commit한 다음 원본을 0바이트 tombstone으로
   치환한다. 정리 실패는 다음 중복 전달에서 이어서 처리한다.

## 비동기·중복 정책

- 웹 요청 안에서 사진 다운로드나 모델 호출을 하지 않는다.
- Celery는 late ack, worker-lost 재큐잉, prefetch 1을 사용한다.
- 중복 전달은 허용한다. 이미 종결된 행은 결과를 바꾸지 않으며, 미완료 사진 정리만
  재개한다. 극히 좁은 동시 실행 창에서 모델 호출이 두 번 날 수 있는 비용 최적화는
  실제 트래픽을 본 뒤 lease/outbox와 함께 검토한다.
- broker 발행 실패 시 confirm은 503을 반환하지만 DB는 `VISION_PENDING`으로 남는다.
  앱은 같은 confirm을 재시도하면 된다. 장기적으로 앱 재시도에 의존하지 않아야 할
  규모가 되면 transactional outbox/reconciler를 붙인다.

## 판정 계약과 운영값

- 현재 adapter: Gemini (`google-genai`), 기본 모델 `gemini-3.1-flash-lite`
- 판정 계약: `territory-dog-presence-v1`
- provider timeout: 기본 12초
- worker retry: 2초 뒤 1회(총 2회 시도)
- 목표: 정상적인 provider 응답이면 confirm 이후 약 30초 안에 terminal status
- 원 공급자 오류/응답 본문은 DB·앱 응답에 남기지 않고 안정적인 reason code만 저장한다.

필수 배포값은 `GEMINI_API_KEY`, 저장소 설정(`GAIT_STORAGE` 및 GCS/LocalBridge 값),
DB·Redis 연결이다. 기동은 기본 compose의 `territory-vision-worker` 서비스가 담당한다.
배포 후 다음을 확인한다.

```bash
docker compose ps territory-vision-worker
docker compose logs --tail=100 territory-vision-worker
docker compose exec territory-vision-worker uv run --no-sync celery \
  -A daengs_backend.tasks.territory inspect ping
```
