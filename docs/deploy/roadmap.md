# GCP 이관 로드맵

> 정리일 2026-09-01. 배경 분석(Cloud Run vs VM · 비용 계산 · 대안 비교)은 노션
> "클라우드 이전 검토" 문서가 원본이고, 이 문서는 **회의 결정 이후의 실행 로드맵**입니다.
> 세부 명령어 수준의 절차서(runbook)는 Phase 별 구현 카드에서 이 폴더에 따로 만듭니다.

## 1. 목표와 제약

| 항목 | 내용 |
| --- | --- |
| 목표 ⓐ | **안드로이드 앱 출시 가능 상태** — HTTPS + 도메인 + 심사·테스트 기간 내내 살아 있는 API |
| 목표 ⓑ | 클라우드 배포 경험 (학습·취업) — "가장 효율적인 선택"만이 정답은 아님 |
| 1차 기한 | **발표 2026-09-21** — 그때까지 GCP 서빙 유지가 1차 목표 |
| 예산 제약 | GCP 크레딧 약 ₩435k, **2026-11-17 만료**. 계정이 일반 계정이라 만료·소진 후 **자동 실비 청구** — 예산 알림(50/80/100%) 필수 |
| 비용 전망 | e2-standard-4 기준 3주 약 ₩120k (크레딧의 약 27%) — 여유 있음 |

## 2. 확정된 결정 (2026-08-31 팀 회의 + 후속 논의)

1. **VM 1대에 지금 compose 를 통으로.** Cloud Run 부적합 사유(임베딩 상주 2.4GB ·
   celery 상주 · `data/` 바인드 마운트 · 비용 3배)는 노션 검토 참조. 최적화(celery →
   Cloud Scheduler 등)는 이관 후 별도로.
2. **사양 e2-standard-4** (4vCPU/16GB, 서울) + 100GB — gait(보행 분석) 데모를 켜기
   위한 선택. 영상 분석이 CPU 를 분 단위로 잡으므로 4vCPU 에서 CPU limit 로 API 를 보호.
3. **배포 소스는 `main` 브랜치.** 별도 배포 레포를 만들지 않는다 — 정본이 둘이 되면
   핫픽스가 갈라진다. `main` 은 릴리즈 스냅샷이라는 기존 규칙이 같은 목적을 이미 제공
   (첫 스냅샷: PR #114). DAENGS_APP 도 같은 모양(main=제출 스냅샷 / dev=작업)으로 맞춘다.
4. **서빙만 옮긴다.** 크롤러·코퍼스 정본은 로컬 서버 잔류 — 앱이 읽는 것은 적재가 끝난
   pgvector 뿐이고, 적재는 GPU 때문에 어차피 개발 PC. 컷오버 리스크를 발표 전에 지지
   않는다. GCP 유지 확정 시 2차로 이전 (§7).
5. **DB 는 dev/prod 로 갈라진다.** GCP 2대(pgvector·place-db, 덤프 복원)가 운영 정본,
   로컬 서버 DB 는 개발용으로 남는다. 따라서 GCP 는 5432/6379 를 인터넷에 열지 않는다.
   Training RAG 는 별도 DB 가 아니다 — #112 로 vectordb 안 `training_rag_*` 테이블로
   통합됐다 (전용 컨테이너·볼륨 삭제).

   > **그 대가 — GCP 의 DB 는 2026-09-02 덤프의 스냅샷이다.** 두 DB 사이에 복제는
   > 없다. 개발 PC 의 `rag load` 는 `POSTGRES_IP`(로컬 서버)를 보므로 **코퍼스를 다시
   > 적재해도 GCP 에는 아무 일도 일어나지 않는다.** 적재는 성공하고 스모크도 통과하는데
   > 앱에만 새 문서가 안 보이는 모양으로 만난다. 반영하려면 로컬 서버에서 `pg_dump` 한
   > 것을 VM 에서 다시 복원하는 수밖에 없다 — runbook §2(덤프)·§3(복원)의 명령 그대로다.
   > 재적재를 동반하는 생활 파트 카드(`docs/life/roadmap.md` 의 A1·A2·A7·F1·D3)에는
   > 이 손작업이 실제 비용으로 붙는다. 구조적 해소는 §7-1(9/21 이후).
   >
   > **`db/migrations/` 도 두 DB 에 각각 손으로 적용한다.** 버전 테이블이 없어 무엇이
   > 적용됐는지 DB 가 기억하지 않으므로(CLAUDE.md), 어디까지 적용했는지는 사람이 안다.
   > GCP 에 미적용인 것은 09-02 덤프 이후에 추가된 분, 즉 **`main` 에 아직 없는
   > `db/migrations/` 전부**이고 다음 dev→main 배포 때 적용한다 (runbook §6).
   >
   > **`verify_*.sql` 을 믿어도 된다 — 2026-09-06(#273)부터다.** 그전에는 아홉 장 중 여섯이
   > 출력 전용(SELECT 나열)이라 **스키마가 틀려도 종료 코드 0** 이었다(`psql … || exit 1` 이
   > 종료 코드를 본다). `tools/check_migration_verification.py` 가 일회용 Postgres 에 적용한 뒤
   > **일부러 망가뜨려 verify 가 잡는지** 확인한다. 규약은 `db/migrations/README.md` 에 있다.
   >
   > ⚠️ **"전부 단언형"은 아니다** — 2026-09-07(#288) 실측 정정. 19장 중 **8장이 아직 출력
   > 전용**이다(`chats`·`walk_pets`·`pet_farewell`·`walk_analyses`·`walk_point_chunks`·
   > `territory_visits`·`walk_capsules`·`admin_audit_log`). **그 여덟은 전부 이미 `main` 에
   > 있는 옛 장**이라 GCP 에 올릴 것과는 안 겹친다 — **다음 dev→main 에 올릴 열 장은 전부
   > 단언형이다.** 그래서 오늘의 위험은 0이고, 남은 여덟은 **별도 카드**다. #273 이
   > "아홉 장 중 여섯"을 고쳤을 때 세지 않은 장들이다.
   >
   > **점령 게임판 적재도 각각이다.** `territory-sites-ingest.yml` 은
   > `runs-on: [self-hosted]` 라 로컬 서버 place-db 만 채운다. GCP 는 runbook §6 의
   > "점령 게임판 적재 (GCP)" 절을 손으로 밟는다. 안 밟으면 `/territory/sites/nearby` 가
   > **어디서 불러도 빈 배열**이다 — 읽기 질의가 현행 세대(`...:hex-v1:140:`)만 거르는데
   > 덤프에 있는 건 옛 115u 행이기 때문이고, 그건 Alembic `0021` 의 의도다.
6. **TLS 는 certbot(Let's Encrypt, 무료).** DNS 는 가비아 유지. 인증서 구매 불필요.
7. **클라우드는 새 서브도메인을 쓴다** (2026-09-01 팀 회의) — 프런트
   `daengapp.weareithero.cloud` · 백엔드 `daengapi.weareithero.cloud`. 기존
   `daengs`·`daengback` 은 로컬(개발) 서버가 그대로 유지 → **DNS 컷오버가 없다.**
   앱에 박는 API 주소는 `https://daengapi.weareithero.cloud` (IP 금지 — 9/21 이후
   VM 을 지워도 DNS 회귀로 배포된 앱이 계속 살 수 있는 유일한 길).

> 결정의 `docs/decisions.md` **D-042** 기록은 Phase 1 구현 카드에 포함한다.

## 3. 브랜치 · PR 흐름

```
모든 작업(GCP 전용 설정·문서 포함) : dev 기준 토픽 브랜치 → PR → dev (로컬 서버 자동배포)
main 반영                          : 완성 단위마다 dev → main 스냅샷 PR (#114 가 첫 번째)
GCP VM                             : main 을 clone. 이후 배포는 git pull (수동, runbook §6)
로컬 서버                          : dev 자동배포(self-hosted 러너) — 개발 환경으로 계속
```

**흐름은 한 방향뿐이다 — 작업 브랜치 → dev → main.** `docs/collaboration.md` §브랜치
그대로이고 이 문서에 예외는 없다.

원래 이 절은 이관 산출물만 **main 직행**으로 뒀었다(2026-09-01). 소비자가 GCP VM(main
clone)뿐이고 dev 스냅샷 타이밍을 안 기다려도 된다는 이유였다. 그 예외를 2026-09-02 에
없앤다 — 대가가 예상보다 컸다.

- `main` 에만 있는 파일 5개(`docker-compose.gcp.yml` · `nginx/gcp.conf` ·
  `nginx/api-locations.inc` · 이 폴더의 두 문서)가 생겼고, **dev 에서 일하는 사람은 그것을
  볼 수 없었다.** 실제로 #133 이 `nginx/default.conf` 에 `/app/gait/` 블록(200m · 600s)을
  넣었는데 GCP 가 쓰는 `api-locations.inc` 에는 안 들어갔다. 로컬은 멀쩡하고 GCP 에서만
  영상 업로드가 413 이 되는, 배포 전에는 안 보이는 종류의 구멍이다.
- 두 갈래가 서로를 못 봐서 **결정 번호가 겹쳤다** — GCP 이관이 D-042, 같은 날 dev 의
  Walk 패키지 결정도 D-042. 나중 것을 D-045 로 renumber 했다(`docs/decisions.md`).

대신 치르는 비용은 하나다: **GCP 전용 수정도 이제 dev→main 스냅샷을 타므로 즉시 반영이
아니다.** 배포 직전 핫픽스는 스냅샷 PR 을 한 번 더 도는 것으로 처리한다 — main 에 직접
커밋하면 위 두 사고가 그대로 재발한다.

## 4. 단계 로드맵

2단계까지 끝나면 앱 출시 요건(HTTPS)이 충족된다. 각 단계가 끝날 때마다 시스템이
동작하는 상태라 중간에 시간이 없어져도 손해가 없다.

| Phase | 기간(안) | 작업 | 산출물 / 완료 기준 |
| --- | --- | --- | --- |
| **0. 사전 확인** | 9/1~9/2 | ① Play 개발자 계정 유형 확인(개인이면 정식 출시 전 "테스터 12명·14일" 요건 — 앱 담당자에게 전달) ② GCP 예산 알림 50/80/100% ③ 고정 IP 예약(서울) ④ 가비아에 새 A 레코드 `daengapp`·`daengapi` → 고정 IP (TTL 300, 기존 레코드는 안 건드림) | 새 레코드 생성 · 예산 알림 수신 확인 |
| **1. VM + compose** | 9/3~9/5 | ① dev→main 스냅샷 PR ② VM 생성(e2-standard-4, Ubuntu 24.04, 100GB), 방화벽 80/443 만 공개 ③ Docker·Node/PM2 설치, main clone ④ 상태 이전(§5) ⑤ 크롤러 제외 + gait 포함 기동, 프론트 PM2 | 고정 IP 로 전 API 스모크 테스트 통과 (아직 HTTP) |
| **2. TLS** | 9/5~9/6 | ① certbot 발급(daengapp·daengapi SAN 한 장 — A 레코드는 Phase 0 에 이미 생성) ② nginx 443 전환, 80 은 리다이렉트 ③ `DAENGS_CORS_ORIGINS` 를 https 도메인으로 | 자물쇠 뜨는 프론트 + `https://daengapi.~` 응답. **여기서 앱 담당자에게 주소 전달** → 앱 출시 요건 충족 |
| **3. 검증 + 운영 준비** | 9/6~9/8 | ① 전 경로 스모크(§6) ② VM 재부팅 자동 복구 확인 ③ 디스크 스냅샷 1회 ④ 수동 배포 절차 확인(git pull origin main) | 재부팅 후 무조치 복구 · 스냅샷 존재 |
| **발표 준비** | 9/8~9/21 | 개발은 dev 에서 계속. 완성 단위마다 dev→main → VM 에서 pull. **9/18 부터 main 프리즈** | 발표 당일 무배포 |
| **종료 결정** | 9/21 이후 | 유지(2차 이관, §7) vs 축소 vs 삭제+DNS 회귀. 어느 쪽이든 최종 pg_dump 백업 먼저 | §8 체크리스트 완료 |

역할: GCP 콘솔·가비아 DNS·결제 = **사람** / 절차서·설정 파일·검증 = Claude 지원 /
앱 주소 반영·출시 트랙 선택 = 앱 담당자.

## 5. 옮겨야 할 상태 (요약)

| 상태 | 원본 위치 | 비고 |
| --- | --- | --- |
| Postgres 2대 덤프 | 로컬 서버 (pgvector 의 vectordb · place-db) | `pg_dump` + **`pg_dumpall --globals-only`** — 손으로 만든 `daengs` 롤은 덤프에 안 담긴다. vectordb 에는 Training RAG 테이블(`training_rag_*`)도 들어 있다(#112). **덤프 전에 `db/migrations/` 최근분(특히 2026-09-01 training_rag 통합) 적용 여부 확인** |
| **업로드 원본(`gait-bridge` 볼륨)** | 로컬 서버의 도커 named volume | **D-052 로 사진·영상 원본이 여기 삽니다.** 안 옮기면 미디어만 집에 남고 GCP 는 빈 볼륨으로 뜹니다 — 조회가 404 나는데 DB 행은 멀쩡해서 원인이 안 보입니다. 뜨기: `docker run --rm -v gait-bridge:/d -v %CD%:/b alpine tar czf /b/bridge.tgz -C /d .` · 풀기: 같은 명령에 `tar xzf /b/bridge.tgz -C /d`. **백업이 이것뿐입니다**(D-052 가 감수) |
| 모델 가중치 4개 | 서버 디스크 (git 에 없음) | 스크리닝 2 + gait 2(best.pt·yolov8n.pt). 배포 폴더 밖에 두고 마운트 |
| `.env` 2개 | 최상단 + backend/ | CORS 를 https 도메인으로, RELEASE_DIR 경로들, **`DAENGS_CORPUS_DIR` 는 더미 경로 필요**(크롤러를 안 띄워도 compose 가 해석 시점에 `:?` 가드를 평가) |
| 암호화 키 3개 | 팀 채널 | **로컬과 같은 값** — 새로 만들면 덤프해 온 암호문을 못 연다 |
| API 키들 | `.env` 에 포함 | GEMINI · data.go.kr/KTO(Place) · TMAP(Journey) |
| 임베딩 모델(hf-cache) | 옮기지 않음 | 첫 기동 때 자동 다운로드(1.2GB). `EMBEDDING_MODEL_KEY` 를 코퍼스와 같게 — 어긋나면 차원이 같아 조용히 틀린다 |
| 코퍼스 | **옮기지 않음** | 정본은 로컬 서버 유지 (§2-4) |

## 6. 스모크 테스트 (완료 기준)

프론트 렌더(자물쇠) · 로그인(`/api/` 동일 오리진 경로) · `/life/ask`(첫 요청은 예열로 느림) ·
`/assistant/query`(오케스트레이션, 인증 필수 — #115) · `/v2/places/search` · `/journey` ·
`/screen/v1/screen`(사진) · `/gait/analyze`(영상, 분 단위) · 80→443 리다이렉트 ·
**앱 실기기에서 API 호출**(앱 담당자).

## 7. 2차 로드맵 — GCP 유지 확정 시 (9/21 이후)

우선순위 순. 노션 검토의 3~5단계에 대응한다.

1. **크롤러·코퍼스·적재 이전** — `DAENGS_CORPUS_DIR` 정본 컷오버(+로컬 워커 정지 **같은 날**),
   crawler-worker·beat 기동, 증분 적재는 VM 에서 CPU 로(`rag load`), 전체 재적재만
   스팟 GPU 또는 개발 PC. 이걸로 "코퍼스 갱신이 개발 PC 에 묶이는" 구조적 약점 해소.
   **그때까지의 임시 절차가 §2-5 의 수동 덤프→복원**이고, 이 항목이 없애는 것이 바로 그
   손작업이다
2. **이미지 굽기** — 지금은 기동 때마다 `uv sync` 하는 개발 편의 구조. 서비스별
   Dockerfile + Artifact Registry (멀티스테이지·레이어 캐시 학습)
3. **CI/CD** — dev→main 머지 시 빌드·푸시·SSH 배포, **Workload Identity Federation**(키 파일 없는 인증)
4. **운영 다듬기** — Tailscale(팀원 DB 접속·관리 평면 분리), celery → Cloud Scheduler 검토, 스냅샷 주기화

## 8. 종료 체크리스트 (삭제 시나리오)

⚠ **정지로는 과금이 계속된다** — 디스크·미연결 고정 IP 는 정지 중에도 청구되고,
크레딧 만료(11/17) 뒤에는 말없이 실비다.

1. 최종 `pg_dump` 2개(vectordb·place) 로컬 회수
2. 앱을 유지한다면 `daengapp`·`daengapi` A 레코드를 집 서버 IP 로 회귀 + 집에서 TLS 재구성 (앱에는 도메인이 박혀 있어 주소는 그대로 산다)
3. VM 삭제 → 디스크 삭제 확인 → 고정 IP **해제** → 스냅샷 정리 → 예산 화면 ₩0 확인
