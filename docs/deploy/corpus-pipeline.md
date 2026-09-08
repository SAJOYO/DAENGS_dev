# GCP 코퍼스 파이프라인 — 설계

**작성 2026-09-08 · 실험 종료 2026-11-17(크레딧 만료) · 종료 후 이 문서와 GCP 리소스는 삭제 대상.**

무엇을/왜의 상위는 [roadmap.md](roadmap.md) §7-1, 명령어 절차는 [runbook.md](runbook.md) §6
"코퍼스 파이프라인 (GCP)" 이다. 여기에는 **설계와 결정 근거**만 둔다. 결정 번호는 D-062.

## 0. 한 줄

집 서버에 묶여 있던 크롤 → 파싱 → 청킹 → 임베딩 → 적재를 **GCP 에서 사람 없이** 돌린다.
크레딧 안에서 GCP 관리형 서비스를 실제로 써 보는 실험이고, 집 서버의 정본과 프로세스는
**하나도 건드리지 않는다.**

## 1. 확정한 전제 (2026-09-08)

| 항목 | 결정 | 이유 |
| --- | --- | --- |
| 수명 | 11-17 까지 실험. 끝나면 GCP 쪽 전부 삭제 | 크레딧 만료 후 자동 실비 청구 (roadmap §1) |
| 코퍼스 정본 | **집 서버 그대로.** GCP 는 초기 사본에서 갈라진 별도 코퍼스 | 삭제 때 잃을 것도 되돌릴 것도 없게. 개정되는 원문이라 두 코퍼스는 합칠 수 없다 (RAG-008·RAG-017) |
| 집 서버 크롤러 | 워커·Beat 그대로 | 위와 같다 |
| GCP DB `documents` | **파이프라인만 쓴다.** runbook §6 "Life 코퍼스만 동기화" 는 실험 기간 사용 금지 | 손으로 갈아끼우면 파이프라인 결과를 덮어쓴다 |
| 적재 게이트 | 사람 승인 없음. 기계 가드만 | §3 의 가드 세 가지 |
| 전체 재임베딩 | Cloud Run Jobs + GPU(L4) 1순위. 보조안 순서: CPU 잡 → 개발 PC parquet 업로드 → Spot GPU VM | GPU 잡이 어떤 이유로든 안 될 때만 내려간다. 셋 다 코드 차이 없음 — **2026-09-08 L4 로 실측 11분, 확정.** 보조안은 기록으로만 남긴다. |
| 초기 사본 | `raw/` + `manifests/crawl_log.jsonl` 만. `processed/` 는 GCP 가 만든다 | 버킷 안 모든 산출물이 GCP 산이 되어 "로컬 작업 없음" 이 처음부터 성립 |
| 초기 사본의 **출처** | **개발 PC 의 `data/`** (2026-09-08 실측 — raw 673개 · 로그 373줄, 마지막 수집 09-06). 집 서버가 아니다 | ⓐ 개발 PC 에서 집 서버 폴더에 못 닿는다 — SMB 도 SSH 도 없다(루트 `README.md`). ⓑ 더 중요한 이유: GCP 의 `documents` 는 #289 에서 **개발 PC 의 `processed/`** 를 실은 것이고 그것은 **개발 PC 의 `raw/`** 를 파싱한 것이라, 개발 PC raw ↔ GCP DB 가 이미 한 줄이었다. 집 서버 raw 는 GCP 에 간 적이 없다 |
| 관리자 트리거 | 범위에 포함. GCP 에서는 Celery 대신 Cloud Run Jobs API | 버튼 → 몇십 분 뒤 앱에 새 문서, 가 이 카드의 목적 |
| 접근 | A(잡 하나, Scheduler 직결). Workflows 는 2차 | 두 달 실험에 배관이 파이프라인보다 커지지 않게 |

## 2. 구성과 데이터 흐름

```
Cloud Scheduler  corpus-refresh-daily   (0 4 * * *  Asia/Seoul)
   └─▶ Cloud Run Job  corpus-refresh    [asia-northeast3, 4vCPU/16GB, CPU]
          │  /data  ← GCS 버킷 daengs-corpus 볼륨 마운트
          │  crawl(due) → parse → chunk → embed(CPU 증분) → guard → load
          └─▶ VM 내부 IP :5432 (pgvector, VPC 내부 이그레스만)

사람 실행 ─▶ Cloud Run Job  corpus-embed-full  [asia-southeast1, L4 1장]
          │  /data ← 같은 버킷
          └─  embed --full 만. parquet 을 버킷에 쓰고 끝 (DB 안 봄)
                 └─ 다음 refresh 가 parquet 지문 변화를 보고 전부 upsert

관리자 콘솔 ─▶ backend services/crawl.py ─▶ Cloud Run Jobs API ─▶ corpus-refresh 실행
                                            (집 서버에서는 종전대로 Celery 큐)

Artifact Registry  daengs/pipeline:cpu-<sha> · cuda-<sha>
   = backend 프로젝트 + `ml` 그룹 + qwen3-embedding-0.6b 가중치
```

**버킷 안 배치는 `data/` 와 같다** — `raw/` · `manifests/` · `processed/{parsed,chunks,embeddings}`.
크롤러 config 가 이미 `DAENGS_DATA_DIR=/data` 를 컨테이너용으로 예정해 두었고 rag 도 같은
설정을 쓴다. **경로 코드는 안 고친다.** 각 단계의 상류 해시 증분 판단이 그대로 산다.

**VM 에는 Beat 도 크롤 워커도 없고 이번에도 안 띄운다.** 시계는 Scheduler, 실행은 잡이다.
VM 에 남는 Celery 는 gait-worker(요청 구동)뿐이다. 집 서버는 Beat·워커 둘 다 그대로.

**DB 는 VM 에 그대로.** Cloud SQL 이관은 운영 테이블 31개가 같은 DB 라 서빙 backend 까지
옮기는 일이므로 범위 밖.

## 3. 잡 안의 단계와 코드

**진입점** `daengs_life/jobs/corpus_refresh.py` → `[project.scripts] corpus-refresh`.
`crawler`(브로커 없는 순수 CLI)와 `rag`(단계 CLI)를 순서대로 부르는 얇은 조립층.
`tasks/`(Celery 래퍼)의 형제이고 Celery 를 안 쓴다. `test_import_direction_packages.py` 의
`ALLOWED["jobs"]` 에 `crawler.{core.config, core.cadence, core.registry, run}` 을 더한다.
`jobs → rag` 는 그 검사의 대상이 아니다(검사는 crawler import 만 본다).

| 순서 | 하는 일 | 재사용 | 실패하면 |
| --- | --- | --- | --- |
| 0 | Cloud Run API 로 같은 잡의 다른 실행이 있으면 "건너뜀" 찍고 종료 코드 0 | 새로 | — |
| 1 | `cadence.due_sources` → `crawler.run`. `--sources` 인자가 있으면 그것만 | 있음 | 소스 하나 실패는 `crawl_runs` 에 남기고 계속. 전부 실패면 중단 |
| 2 | `rag parse` 증분 | 있음 | 파서 예외 상한 초과 시 중단 |
| 3 | `rag chunk` 증분 | 있음 | 예외 시 중단 |
| 4 | `rag embed --model <EMBEDDING_MODEL_KEY>` 청크 해시 증분, CPU | 있음 | 예외 시 중단 |
| 5 | **가드** — 적재 계획을 만들고 검사 | 새로 | 걸리면 DB 안 건드리고 종료 코드 1 |
| 6 | `rag load` upsert + stale prune, 한 트랜잭션 | 있음 | 롤백 |

**가드 세 가지** (행 수 한계는 `--max-drop` 인자, 기본 0.2 — 환경 변수가 아니다):
① 적재 뒤 행 수가 적재 전 대비 비율 이상 줄어드는 계획이면 중단 (20%).
② 파서 예외는 **가드까지 못 온다** — `rag parse` 가 예외 1건이면 종료 코드 1 이라 거기서 멈춘다.
   그래서 이 자리에 따로 셀 것이 없다.
③ 기존 `metadata_loss` 검사에 걸리는 행이 하나라도 있으면 중단.
전체 재임베딩 뒤의 1만 행 upsert 는 "줄어드는 계획" 이 아니라 ① 을 통과한다.

**`--stages`** 로 단계 부분 실행 (`crawl` · `parse,chunk` · `embed` · `load`), **`--full`** 은 embed 전체.
`corpus-embed-full` 잡은 같은 진입점에 `--stages embed --full`.

**`crawl_runs` 기록**은 `tasks/crawl_runs.py` 가 Celery 와 무관한 psycopg 코드라 그대로 부른다.
위치를 옮길지는 구현 때.

**이미지** `docker/pipeline/Dockerfile` 하나, 빌드 인자로 CPU/CUDA. 공통은
`uv sync --frozen --group ml --group pipeline --group pdf` 와 가중치 다운로드(`HF_HOME=/models`, 빌드 때).
`pdf`(PyMuPDF)가 들어가는 것은 AGPL 격리(RAG-032 ②)가 말하는 **오프라인 파이프라인이 이 잡이기
때문**이다 — 서빙 이미지에는 계속 안 넣는다. CUDA 변형은 lock 이 리눅스에서 CPU torch 를
고정하므로 sync 뒤 **torch 만** cu126 인덱스로 덮어쓴다 (torchvision 은 `ml` 이 아니라 `gait` 그룹
것이라 건드리지 않는다). ⚠ **`torch==X` 로 적으면 이미 깔린 `X+cpu` 가 그것을 만족시켜 조용히
아무 일도 안 한다** — `X+cu126` 까지 핀하고 `--reinstall-package` 를 주며, 빌드 끝에
`torch.version.cuda` 를 확인해 아니면 빌드를 실패시킨다. Triton JIT 이 C 컴파일러를 부르므로
CUDA 스테이지에 `gcc`·`libc6-dev` 도 넣는다. Artifact Registry 실측(2026-09-09, 압축 저장 크기)은 **CPU 2.4GB · CUDA 9.7GB, 합 12.1GB** — CUDA 판은 nvidia-* 런타임 휠 때문에 네 배다.
roadmap §7-2 "이미지 굽기" 가 파이프라인에 한해 여기서 먼저 간다.

**설정은 환경 변수로만** — `DAENGS_DATA_DIR=/data`, `POSTGRES_IP=<VM 내부 IP>` 와 계정 조각,
`EMBEDDING_MODEL_KEY`. 비밀번호는 Secret Manager. `.env` 는 이미지에 안 들어간다.
가드 한계는 잡 인자(`--max-drop`)다 — `runbook.md` §6 참고.

**관리자 트리거** (#326) — `services/crawl.py` 가 `settings.crawl_backend`(`DAENGS_CRAWL_BACKEND`,
기본 `celery`)로 갈린다. GCP VM 의 `backend/.env` 에만 `cloudrun` 을 두고, 같이 `DAENGS_GCP_PROJECT`
· `DAENGS_GCP_REGION`(기본 `asia-northeast3`) · `DAENGS_CORPUS_JOB`(기본 `corpus-refresh`) 을 준다.
cloudrun 이면 Jobs API 로 `corpus-refresh` 실행(소스 id 는 컨테이너 인자 `--sources a b`, 없으면
due 판정 그대로). **끝나지 않은 실행이 있으면**(pending 포함) 새로 안 띄우고 `AlreadyRunning`
을 던져 라우터가 202 + `note: "실행 중인 것이 있어 새로 띄우지 않았습니다: <실행 이름>"` 로
그 실행을 알린다 — 실패가 아니다. Cloud Run API 자체가 죽으면 옛 "브로커 다운"과 같은 503.
인증은 VM 기본 컴퓨트 서비스 계정 + 메타데이터 서버다(키 파일 없음) — `run.invoker`·`run.viewer`
가 필요하고 절차는 `infra/gcp/README.md`.

상태 페이지의 "크롤" 항목은 GCP 에서 잡이 있으면 `Cloud Run 잡 corpus-refresh@asia-northeast3`
로 뜬다. API 가 안 답하면 **`absent` 가 아니라 `down`** 이다 — "여기엔 원래 크롤러가 없다"가
아니라 "있어야 하는데 안 답한다"이기 때문이다("Cloud Run 잡에 묻지 못했습니다"). 설정 자체가
비어 있으면 여전히 `absent`. 진행 표시는 `crawl_runs` 폴링 그대로. 관리자 화면에는 "전체"
하나만 노출한다 (집 서버는 크롤에서 멈추지만 GCP 는 적재까지).

**안 바꾸는 것** — 크롤러·rag 각 단계 내부, 경로 규약, Celery 태스크, 집 서버 compose.

## 4. GCP 리소스와 역할

**전부 2026-09-08 에 실제로 만들었다** (프로젝트 `daengs`). 아래는 그때의 실물이다.

| 리소스 | 이름 | 비고 |
| --- | --- | --- |
| Cloud Storage | `daengs-corpus` | 서울 단일 리전. **버전 관리 켬** (잘못된 적재를 되돌리는 유일한 길) |
| Artifact Registry | `daengs` | `pipeline:cpu-<hash>` · `pipeline:cuda-<hash>`. **`<hash>` 는 git 커밋이 아니라 이미지 입력의 내용 해시**다 — `backend/pyproject.toml`·`uv.lock`·`README.md`·`backend/src`·`data/manifests/seed_sources.yaml`·`docker/pipeline`. 태그가 이미 있으면 빌드를 건너뛰므로, 문서만 바뀐 커밋에서 스크립트를 다시 돌려도 **약 80초**에 끝난다 |
| Cloud Run Job | `corpus-refresh` | 서울(`asia-northeast3`), 4vCPU/16Gi, 타임아웃 3h, **재시도 0** (가드가 막은 것은 사람이 봐야 한다). Direct VPC 이그레스 · 버킷 볼륨 `daengs-corpus` 를 `/data` 로 |
| Cloud Run Job | `corpus-embed-full` | **asia-southeast1(싱가포르)** — 잡의 L4 지원 리전에 서울·도쿄가 없다. L4 1장 · 8vCPU/32Gi · 인자 `--stages embed --full`. **타임아웃 1h — 이건 GPU 잡의 상한이지 선택이 아니다** (3h 로 만들면 생성이 거부된다). 서울 버킷을 리전 간 마운트(200MB, 비용 무시) |
| Cloud Scheduler | `corpus-refresh-daily` | `0 4 * * *` Asia/Seoul → `corpus-refresh`. 밀리지 않는다(관리형 cron). 콜드 스타트 1~2분은 상관없음. **첫 자동 실행 2026-09-09 04:00 KST 확인** |
| Secret Manager | `corpus-db-password` · `corpus-law-oc` · `corpus-data-go-kr-key` · `corpus-seoul-open-data-key` | 잡 환경 변수로 주입 |
| 서비스 계정 | `corpus-pipeline` | 버킷 RW · Secret 읽기 · VPC 이그레스 · **Run 실행 조회**(동시 실행 확인) |
| VPC 방화벽 | `allow-pg-from-run` | 서울 서브넷 범위 → VM tcp:5432. 인터넷에는 여전히 안 연다 |
| IAM | VM 기본 서비스 계정에 잡 실행 권한 | #326 — VM 기본 SA 에 잡 `run.invoker`·`run.viewer` |
| Monitoring | 잡 실패 → 이메일 | **아직 안 만들었다** — 알림 채널이 없어 `pipeline.sh` 가 건너뛴다. 선택이고, 붙일 때는 `CHANNEL=<알림 채널 id> bash infra/gcp/pipeline.sh` 로 다시 돌리면 정책이 선다 |
| 예산 알림 | 기존 것 확인 | GPU 잡 폭주 안전판 |

**설정 방식** — `gcloud` 명령을 `infra/gcp/pipeline.sh` 에 순서대로. 삭제 스크립트
`infra/gcp/pipeline-teardown.sh` 를 같이 두고 roadmap §8 종료 체크리스트에 건다. Terraform 은
두 달 실험에 상태 파일 관리까지 얹는 것이라 안 쓴다.

**역할** — 콘솔에서 API 켜기 · 계정 유형(무료 체험 아님) 확인 · 스크립트 실행 · 초기 사본 업로드 · VM 내부 IP 확인
= 사람 / Dockerfile · 진입점 · 가드 · 트리거 갈래 · 스크립트 · 문서 · 검증 절차 = Claude
(roadmap §4 의 기존 규칙과 같다).

**비용 어림** (전부 크레딧 차감) — 아래는 2026-09-08 실측 시간에서 뽑은 것이다.

| 항목 | 실측 (2026-09-08) | 월 어림 |
| --- | --- | --- |
| `corpus-refresh` 매일 1회 | 17분 × 4vCPU/16Gi | 약 ₩5,500 |
| `corpus-embed-full` 1회 | 11분 L4 (8vCPU/32Gi) | 회당 약 ₩250 |
| 이미지 빌드 (이미지 입력이 바뀔 때만) | CPU 약 5분 + CUDA 15~23분 | 쌍당 약 ₩350 |
| Artifact Registry | 이미지 2장 12.1GB (CPU 2.4 · CUDA 9.7, 2026-09-09 실측) | 약 ₩1,600 (GB당 월 $0.10) |
| 버킷 | 약 300MB + 버전 | 수백 원 |
| Scheduler · Secret | | 무료 구간 |
| **첫날 세팅 (1회성)** | 빌드 15회 약 110분 + GPU 57분(그중 43분은 CPU 로 헛돈 것) | 약 ₩5,000 |

**L4 쿼터는 신청이 필요 없었다** — 리전에서 첫 GPU 잡을 만들 때 3장(영역 중복 없음)이 자동
할당된다. **아무 신청 없이 잡이 만들어진 것으로 실제 확인했다.** 무료 체험 계정만 GPU 가 막힌다.

## 5. 오류 처리와 되돌리기

- **실패** — 종료 코드 ≠ 0 → Cloud Run 실행 실패 → Monitoring 이메일. 자동 재시도 없음. 크롤 실패는
  다음 날 due 로 다시 잡힌다. 단계 시작·끝마다 `[refresh] parse 12 docs 3 changed` 꼴 한 줄.
- **중간에 죽음** — 상류 해시 증분이라 재실행하면 끝난 단계는 건너뛴다. 버킷 마운트는 파일 단위
  쓰기라 반쯤 쓰인 파일은 안 남는다. `crawl_log.jsonl` 은 건별 append 로 지금과 같다.
- **잘못된 적재** — DB 는 upsert 라 지워지는 것은 stale prune 뿐이고 가드 ① 이 막는다. 파일은 버킷
  버전에서 parquet 이전 판을 꺼내 `--stages load` 로 다시 적재. 절차는 runbook §6.
- **동시 실행** — 잡 시작 시 Cloud Run API 로 확인해 건너뛴다(§3 의 0번). Job 자체에는 "동시 1개"
  설정이 없다. 버킷 잠금 파일은 죽은 잡의 잠금 만료 처리가 더 길어서 안 쓴다.

## 6. 검증

1. 로컬 CPU 이미지 빌드 → `docker run … corpus-refresh --dry-run` 이 임시 `data/` 사본에서 끝까지. DB 안 봄
   **실측 ✅ (통과했으나 값이 낮았다)** — 이미 처리된 코퍼스를 재사용해 parse 가 "할 일 없음" 으로
   지나갔고, 그래서 `pdf` 그룹 누락을 여기서 못 잡았다. **다음에 이 검사를 할 사람은 `processed/`
   가 빈 사본에서 돌릴 것.**
2. Registry 에 push, 잡 생성, `--stages parse,chunk` 로 버킷 마운트와 산출물 확인
   **실측 ✅** — `parsed 25, same 302, skipped 6, failed 0` · 청크 **10,304** (개발 PC 와 같은 수).
3. `--stages embed` CPU 로 청크 몇 개(`--limit`) — 벡터가 나오고 메모리 상한 안
   **실측 ✅** — 뒤의 전체 실행에서 증분 369청크를 CPU 로 처리.
4. GPU 잡 전체 임베딩. 시간과 parquet 행 수(약 1만) 기록
   **실측 ✅** — 10,304행 · parquet **43.5MB** · **약 11분** · `cuda_available=True torch=2.13.0+cu126`.
   (그 전 43분짜리 한 판은 `+cpu` 였다 — §3 의 cu126 함정.)
5. `--stages load --dry-run` 으로 VM DB 접속(방화벽·비밀번호) → 실제 적재 → `documents` 행 수·카테고리 분포를 집 서버와 대조
   **실측 ✅** — 9,838 → 9,838 (upsert 9,838 · prune 0), VM 에서 전 행 갱신 · 1024차원 ·
   `content_tsv` 채워짐 확인.
6. 전체 잡 수동 1회 → 다음 날 04:00 Scheduler 실행을 `crawl_runs` 와 Logging 에서 확인
   **실측: 수동 1회 ✅ (17분)** — crawl due 11개 중 10개 성공(1개는 서울 열린데이터 키가 없어
   건너뜀 → 네 번째 Secret 을 넣고 다시 돌려 **2건 수집**), parse 48 · chunk 48 · embed 369(CPU) ·
   load 9,838 → **9,885**. **Scheduler 자동 실행 ✅ — 2026-09-09 04:00:00 KST 정각에 발사,
   실행 `corpus-refresh-nrfk7` 3분 40초 성공.** due 소스 0개(전날 16:40 수동 전체 실행이 다 받아서),
   parse·chunk·embed 전부 same, load 9,885 → 9,885. 그래서 `crawl_runs` 에 `trigger='due'` 행은
   없다 — 주기가 돌아오는 날 생긴다. 완료 기준("Scheduler 가 매일 돈다")은 여기서 닫혔다.
7. (#326, 실제 확인은 dev→main 뒤 VM 에서) 관리자 콘솔 트리거 → 잡이 뜨고 화면 폴링이 진행을 보여 준다
8. 앱 `/life/ask` 로 새 문서가 근거에 잡힌다

**자동 테스트** — 진입점의 단계 조립과 가드 판정은 DB·파일 없이(계획 객체를 주고 중단 여부만).
트리거 갈래는 Celery·Cloud Run 클라이언트를 가짜로 바꿔 어느 쪽이 불리는지만. 의존 방향
테스트에 새 간선. 실제 GCP 를 부르는 테스트는 없다.

## 7. PR 과 순서

**PR 둘.**

- **#325 (본문 재작성, 브랜치 유지)** — 파이프라인 잡. 진입점 · 가드 · 동시 실행 확인 · Dockerfile ·
  gcloud 스크립트 · D-062 · 문서. **완료 기준: Scheduler 가 매일 돈다.** backend 는 안 건드리므로
  dev 머지로 끝나고 VM 재배포 없음. 잡은 이미지 push 로 배포.
- **#326** — 관리자 트리거 갈래. `services/crawl.py` 분기 · 상태 페이지 분기 · Google 인증 의존성
  (`uv add`) · 관리자 화면 문구 · `docs/console/` 한 절. backend 를 바꾸므로 dev→main 스냅샷과
  VM 재배포를 지난다. #325 가 끝나 잡이 있어야 검증되므로 뒤.

**순서** ① 이 문서 커밋 · #325 본문 재작성 · 새 PR 빈 커밋으로 열기 ② 코드(진입점 → 가드 →
Dockerfile → 스크립트), 로컬 `--dry-run` ③ 사람: API 켜기, 스크립트,
초기 사본 ④ 검증 1~6 ⑤ 문서 마무리, #325 머지 ⑥ 새 PR, 검증 7~8.

**고치는 문서** — `runbook.md` §6 신설 절 + "Life 코퍼스만 동기화" 머리에 사용 금지 표시 ·
`roadmap.md` §7-1 교체 · §7-2 표기 · §8 삭제 스크립트 · `docs/life/roadmap.md` §0·§4 ·
`docker-compose.gcp.yml` 머리 주석 · `CLAUDE.md` GCP 한 문단(집 서버 절은 그대로) · 루트 `README.md`
한 줄 · `infra/gcp/README.md` 신설.
