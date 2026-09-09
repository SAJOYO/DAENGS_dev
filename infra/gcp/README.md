# infra/gcp — 코퍼스 파이프라인 (D-062)

설계 `docs/deploy/corpus-pipeline.md` · 운영 절차 `docs/deploy/runbook.md` §6 "코퍼스 파이프라인 (GCP)".
**11-17 실험 종료 때 `pipeline-teardown.sh`** (roadmap §8).

## 순서

1. **사람, 콘솔** — 결제 계정이 무료 체험이 아닌지 확인. `gcloud auth login` · `gcloud config set project`.
2. **사람** — Secret 만들고 값 넣기. `pipeline.sh`(4번)도 같은 이름으로 만들려 하지만
   `describe || create` 라 이미 있으면 건너뛰므로, 순서를 바꿔 여기서 먼저 만들어도 안전하다:
   ```bash
   gcloud secrets create corpus-db-password --replication-policy=automatic
   gcloud secrets create corpus-law-oc --replication-policy=automatic
   gcloud secrets create corpus-data-go-kr-key --replication-policy=automatic
   gcloud secrets create corpus-seoul-open-data-key --replication-policy=automatic
   printf '%s' '<VM pgvector 의 daengs 비밀번호>' | gcloud secrets versions add corpus-db-password --data-file=-
   printf '%s' '<LAW_OC>'              | gcloud secrets versions add corpus-law-oc --data-file=-
   printf '%s' '<DATA_GO_KR_KEY>'      | gcloud secrets versions add corpus-data-go-kr-key --data-file=-
   printf '%s' '<SEOUL_OPEN_DATA_KEY>' | gcloud secrets versions add corpus-seoul-open-data-key --data-file=-
   ```
   `create` 를 4번 뒤에 다시 하면 "이미 있음" 에러가 난다 — 그때는 `versions add` 네 줄만
   돌리면 된다. 값(버전)이 없는 채로 잡을 배포해도 배포 자체는 성공한다 — Secret Manager
   참조는 **첫 실행 시점**에 읽히므로, 값을 안 넣으면 배포가 아니라 실행이 실패한다.
   네 값 다 `backend/.env` 에 이미 있는 것과 같다 — 특히 `SEOUL_OPEN_DATA_KEY` 는 서울 열린데이터
   광장 키다 (`seoul-notice-api` 소스가 이 값 없이는 수집을 건너뛴다).
3. **사람** — VM 내부 IP: `gcloud compute instances list --format='value(name,networkInterfaces[0].networkIP)'`
4. **사람** — `PROJECT=… VM_INTERNAL_IP=… bash infra/gcp/pipeline.sh` (저장소 루트에서. 이미지 두 장을 Cloud Build 가 굽는다, 20~40분).
5. **사람** — 초기 사본. **개발 PC 의 `DAENGS_DATA_DIR`**(메인 체크아웃의 `data/`) 에서
   `raw/` 와 `manifests/crawl_log.jsonl` 만 올린다:
   ```powershell
   gcloud storage rsync -r <메인 체크아웃>\data\raw gs://daengs-corpus/raw
   gcloud storage cp <메인 체크아웃>\data\manifests\crawl_log.jsonl gs://daengs-corpus/manifests/crawl_log.jsonl
   ```
   ⚠ **집 서버(`C:/deploy/daengs/corpus`)가 아니라 개발 PC 인 것이 맞다** (D-062 ①).
   ⓐ 집 서버에는 SMB 도 SSH 도 없어 개발 PC 에서 그 폴더에 못 닿고, ⓑ 더 중요하게는 GCP DB 의
   `documents` 가 개발 PC 의 `processed/`(= 개발 PC 의 `raw/`) 에서 나온 것이라 **개발 PC raw ↔
   GCP DB 가 이미 한 줄**이다. 2026-09-08 에 올린 실물은 raw **673개** · 로그 **373줄**(마지막
   수집 09-06)이고, 그 위에서 돌린 parse 가 청크 **10,304** — 개발 PC 와 같은 수 — 를 냈다.
   `processed/` 는 올리지 않는다 — 잡이 만든다. `seed_sources.yaml` 도 올리지 않는다 — 이미지가 넣는다.
   `pipeline.sh` 의 `BUCKET=` 을 다른 이름으로 바꿨다면(버킷 이름 충돌 시) 위 두 줄의
   `gs://daengs-corpus` 도 그 이름으로 바꿔야 한다.
6. **검증** — `docs/deploy/corpus-pipeline.md` §6 의 2~6. 잡 수동 실행:
   ```bash
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,parse,chunk" --wait
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,embed,--dry-run" --wait
   gcloud run jobs execute corpus-embed-full --region=asia-southeast1 --wait          # 전체 임베딩 (GPU)
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,load,--dry-run" --wait
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --args="--stages,load" --wait
   gcloud run jobs execute corpus-refresh --region=asia-northeast3 --wait                 # 전체
   ```
   로그: `gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="corpus-refresh"' --limit=200 --format='value(textPayload)'`
7. 다음 날 `crawl_runs` 에 `trigger='due'` 행이 있으면 끝.

## 이미지를 다시 구울 때

코드가 바뀌면 `pipeline.sh` 를 다시 돌린다 — 태그(`cpu-<hash>`)는 git 커밋 sha 가 아니라
**이미지에 들어가는 파일들의 내용 해시**다: `backend/pyproject.toml` · `backend/uv.lock` ·
`backend/README.md` · `backend/src` · `data/manifests/seed_sources.yaml` · `docker/pipeline`.
그 경로가 바뀐 뒤에만 새 태그가 나와 다시 굽고 잡 정의가 새 이미지로 update 된다 —
무관한 커밋(문서·`infra/` 스크립트만)에서 다시 돌리면 이미지는 그대로 재사용되고
**전체가 약 80초**에 끝난다 (2026-09-08 실측). 빌드가 실제로 도는 경우는 CPU 약 5분,
CUDA 15~23분이다.

`gcloud builds submit .` 이 올리는 파일은 루트 `.gcloudignore` 가 정한다 — 이미지에 안 들어가는
것을 새로 넣으면 거기도 열어야 한다.

## 관리자 트리거 (#326)

관리자 콘솔의 크롤 버튼은 집 서버에서는 Celery 를 부르지만, GCP 에서는 `services/crawl.py`
가 이 잡(`corpus-refresh`)을 직접 실행한다 — 크롤만이 아니라 적재까지 간다. 인증은 VM 의
기본 컴퓨트 서비스 계정 + 메타데이터 서버다(키 파일 없음).

**`pipeline.sh` 가 그 계정에 잡 실행 권한을 이미 준다** (Scheduler 바인딩 바로 다음, "관리자
콘솔 트리거" 절) — 처음에는 이 README 산문에만 있어서 teardown 뒤 재배포하면 조용히
빠졌다(#326 최종 리뷰). 손으로 다시 줄 일은 없어야 하지만, 스크립트가 하는 일은 이렇다:

```bash
PROJECT_NUMBER="$(gcloud projects describe daengs --format='value(projectNumber)')"
VM_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"   # = pipeline.sh 의 BUILD_SA
gcloud run jobs add-iam-policy-binding corpus-refresh --region=asia-northeast3 \
  --member="serviceAccount:${VM_SA}" --role=roles/run.invoker
gcloud run jobs add-iam-policy-binding corpus-refresh --region=asia-northeast3 \
  --member="serviceAccount:${VM_SA}" --role=roles/run.viewer
```

`run.invoker` 가 실행을, `run.viewer` 가 "끝나지 않은 실행이 있나" 조회를 위한 것이다 —
버튼을 다시 눌러도 새로 안 띄우고 그 실행 이름을 돌려주는 것과, 상태 페이지의 "크롤" 항목이
둘 다 이 조회를 쓴다.

### 🔴 IAM 만으로는 안 된다 — VM 의 **액세스 범위**도 봐야 한다 (2026-09-09 실측)

역할을 맞게 줘도 상태 페이지가 이렇게 죽는다:

```
PermissionDenied: 403 Request had insufficient authentication scopes.
reason: "ACCESS_TOKEN_SCOPE_INSUFFICIENT"
service: run.googleapis.com  method: google.cloud.run.v2.Jobs.GetJob
```

**메타데이터 서버가 내주는 토큰의 범위(OAuth scope)가 인스턴스에 박혀 있기 때문이다.** GCE 기본값은
`devstorage.read_only`·`logging.write`·`monitoring.write`·`service.management.readonly`·
`servicecontrol`·`trace.append` 뿐이라 `run.googleapis.com` 이 아예 안 들어간다. **범위에 없으면
IAM 을 아무리 줘도 못 부른다** — 역할과 범위는 다른 층이고, 위의 `add-iam-policy-binding` 은
범위를 안 건드린다.

확인:

```bash
gcloud compute instances describe daengs --zone=asia-northeast3-c \
  --format='value(serviceAccounts[0].scopes)'
```

⚠ **고치려면 인스턴스를 멈춰야 한다** — `set-service-account` 는 `TERMINATED` 상태에서만 먹는다.
즉 **운영 전체가 몇 분 내려간다.**

```bash
gcloud compute instances stop  daengs --zone=asia-northeast3-c
gcloud compute instances set-service-account daengs --zone=asia-northeast3-c \
  --service-account=584617819762-compute@developer.gserviceaccount.com \
  --scopes=https://www.googleapis.com/auth/cloud-platform
gcloud compute instances start daengs --zone=asia-northeast3-c
```

- **외부 IP 는 안 바뀐다** — `daengs-ip` 로 예약된 고정 주소다 (`gcloud compute addresses list`).
  임시 IP 였다면 정지만으로 주소를 잃고 DNS 가 끊긴다. 멈추기 전에 반드시 확인할 것.
- **되살아나는 것은 자동이다** — docker 가 systemd `enabled` 이고 컨테이너가 `restart: unless-stopped`,
  `pm2-daengs` 도 `enabled` 다. 다만 backend 가 뜨며 **임베딩 모델을 다시 올리는 데 75초**쯤 걸리고
  그동안 `/life/ask` 는 503 이다.

VM 의 `backend/.env` 에 네 줄을 더한다 (집 서버는 그대로 비워 둔다 — 기본이 `celery`):

```
DAENGS_CRAWL_BACKEND=cloudrun
DAENGS_GCP_PROJECT=daengs
DAENGS_GCP_REGION=asia-northeast3
DAENGS_CORPUS_JOB=corpus-refresh
```

(`GCP_REGION`·`CORPUS_JOB` 은 기본값이 위와 같아 사실 안 적어도 되지만, 적어 두면 리전이나
잡 이름을 바꿀 때 여기부터 보게 된다.)

`backend/.env` 를 고쳤으면 `docker compose restart` 로는 반영되지 않는다 — `env_file` 은
컨테이너를 만들 때 굳는다. `docker compose up -d backend` 로 다시 만들되, 그 전에 셸에
`GEMINI_API_KEY` 를 올려야 한다(CLAUDE.md) — 빈 셸에서 `up -d` 를 치면 빈 키가 박혀 의미
라우터가 죽는다.

## 자주 걸리는 것

- **`mount_path: should be a valid unix absolute path`** — MSYS 경로 변환. Windows Git Bash 가
  `/data` 같은 인자를 네이티브 exe(gcloud) 에 넘길 때 `C:/Program Files/Git/data` 로 바꿔 버린다.
  스크립트가 `MSYS2_ARG_CONV_EXCL="--add-volume-mount"` 를 export 하지만, 명령을 손으로 칠
  때는 앞에 `MSYS2_ARG_CONV_EXCL="--add-volume-mount"` 를 붙일 것. `MSYS_NO_PATHCONV=1` 로
  변환을 전부 끄면 안 된다 — gcloud 런처(bash 스크립트) 자체가 python 에 넘기는 경로까지
  안 바뀌어 gcloud 가 안 뜬다.
- **잡이 `코퍼스가 없습니다` 로 바로 죽는다** — 버킷에 `manifests/crawl_log.jsonl` 이 없다(5번).
- **`load` 가 connection refused** — 방화벽 규칙의 source range 가 잡이 쓰는 서브넷과 다르거나, VM 의 compose 가 5432 를 `0.0.0.0` 에 게시하지 않았다(`docker compose ps` 로 확인).
- **`embed` 가 모델을 받으려 한다** — 이미지 빌드의 가중치 굽기가 실패한 것이다. `HF_HOME=/models` 는
  잡 env 가 아니라 **이미지에 굽혀 있다** (`docker/pipeline/Dockerfile` 의 `ENV`) — 잡 설정이 아니라
  Cloud Build 로그의 모델 굽기 단계(`SentenceTransformer(...)` 호출)가 성공했는지를 본다.
- **GPU 잡이 quota 에러** — 첫 실행에 자동 할당(3장)이 안 된 경우. 콘솔 할당량에서 `Cloud Run Admin API` × `NVIDIA L4` 를 1 로 요청.
  (2026-09-08 에는 **아무 신청 없이** 잡이 만들어졌다 — 자동 할당이 실제로 됐다.)
- **GPU 잡 생성이 타임아웃 때문에 거부된다** — Cloud Run **GPU 잡의 `--task-timeout` 상한은 1h**
  다. `corpus-embed-full` 이 3h 가 아닌 이유가 그것이고, 전체 임베딩은 L4 에서 약 11분이라 여유가
  있다. 코퍼스가 커져 1h 를 넘기게 되면 소스를 나눠 여러 번 돌리는 수밖에 없다.
- **Cloud Build 가 권한 부족으로 실패한다** — 2024년 중반 이후 만든 프로젝트엔 legacy Cloud Build
  SA 가 없어 빌드가 기본 컴퓨트 SA(`<프로젝트번호>-compute@developer.gserviceaccount.com`)로 돈다.
  `pipeline.sh` 가 APIs 단계 바로 뒤에 그 SA 에 `roles/cloudbuild.builds.builder` ·
  `roles/artifactregistry.writer` · `roles/logging.logWriter` 를 준다 — 그래도 실패하면 IAM 에서
  이 SA 가 그 역할들을 가졌는지 확인한다.
- **크로스 리전 비용** — GPU 잡 `corpus-embed-full` 은 싱가포르(`asia-southeast1`)에서 서울
  (`asia-northeast3`) 버킷을 읽고 쓴다. 코퍼스가 ~200MB 라 크로스 리전 egress 비용은 작지만 0 은 아니다.
- **`cuda` 이미지 빌드가 실패한다** — 실패하면 먼저 `docker/pipeline/Dockerfile` 의 `torch-cuda`
  스테이지 — `uv pip install ... cu126` 줄 — 을 본다 (버전 문자열에 `+` 뒤 로컬 세그먼트가
  남았는지, cu126 인덱스에 해당 버전이 있는지). 2026-09-08 에 여기서 걸린 것 셋:
  torchvision 은 `ml` 이 아니라 `gait` 그룹이라 같이 적으면 안 되고, `torch==X` 는 이미 깔린
  `X+cpu` 로 충족돼 **조용히 CPU 이미지가 나오며**(그래서 `+cu126` 핀 + `--reinstall-package`
  + 빌드 끝의 `torch.version.cuda` 검사가 있다), Triton JIT 이 `gcc`·`libc6-dev` 를 요구한다.
  ⚠ 그 검사를 빼면 실패가 **빌드가 아니라 GPU 잡 로그**에서만 보인다 (`torch 2.13.0+cpu`).
