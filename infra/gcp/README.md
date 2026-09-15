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
   `processed/` 는 올리지 않는다 — 잡이 만든다. `seed_sources.yaml` 과 `daengs_life` 코드도 손으로
   올리지 않는다 — **`pipeline.sh`(4번)가 `code/` prefix 에 rsync 한다** (#427, 아래 「코드를 배포할 때」).
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

## 코드를 배포할 때 — **굽지 않는다** (2026-09-10 · #427)

🔴 **우리 코드는 이미지에 없다.** `daengs_life` 는 `gs://daengs-corpus/code/` 에 있고 잡이 뜰 때
entrypoint 가 `/app/src` 로 복사해 `PYTHONPATH` 로 잡는다. 그래서 코드 배포는 **rsync 몇 초**다:

```bash
PROJECT=daengs VM_INTERNAL_IP=<VM 내부 IP> bash infra/gcp/pipeline.sh
```

`pipeline.sh` 가 「코드·시드 업로드」 절에서 그것을 한다 — **따로 칠 명령이 없고, 그 자리에 있는
이유가 그것이다**(rsync 를 잊으면 옛 코드로 돈다. 옛 판에서 *굽기를 잊으면* 그랬던 것과 같은
실패 모양이다). 이유와 대가는 `docker/pipeline/Dockerfile` 머리말과 `RAG-086` ②.

**확인은 잡 로그 첫 줄이다** — `[entrypoint] 코드 <커밋 해시> <업로드 시각>`. `+dirty` 가 붙어
있으면 커밋 안 된 워킹 트리를 올린 것이다.

### 이미지를 다시 굽는 경우 — **의존성이 바뀔 때만**

태그(`cpu-<hash>`)는 git 커밋 sha 가 아니라 **이미지에 들어가는 파일들의 내용 해시**다:
`backend/pyproject.toml` · `backend/uv.lock` · `backend/README.md` · `docker/pipeline`.
**`backend/src` 와 시드는 이제 입력이 아니다** — 그래서 코드만 고친 배포에서는 빌드가 아예 안 돈다.
그 경로가 바뀐 뒤에만 새 태그가 나와 다시 굽고 잡 정의가 새 이미지로 update 된다 —
무관한 커밋에서 다시 돌리면 이미지는 그대로 재사용되고 **전체가 약 80초**에 끝난다
(2026-09-08 실측). 빌드가 실제로 도는 경우는 CPU 약 5분, CUDA 15~23분이다.

⚠ **태그가 코드 버전을 말해 주지 않는다.** 그 자리를 메우는 것이 `code/VERSION` 과 위의 로그
첫 줄이다. 어느 코드로 돌았는지 알고 싶으면 **태그가 아니라 실행 로그**를 본다.

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

**`pipeline.sh` 가 이것을 확인하고 경고한다** (`VM_NAME`·`VM_ZONE` 기본값, `SKIP_SCOPE_CHECK=1` 로 끔).
**고치지는 않는다** — 아래처럼 인스턴스를 멈춰야 해서, "여러 번 돌려도 안전한" 배포 스크립트가
말없이 할 일이 아니다.

#### 🟢 애초에 안 겪는 법 — **VM 을 만들 때 범위를 준다**

범위는 **만들 때는 자유롭게 정하고, 나중에 바꾸려면 멈춰야 한다.** 그러니 새로 세울 때 주면
아래 정지·재기동이 통째로 필요 없다:

```bash
gcloud compute instances create daengs --zone=asia-northeast3-c \
  --scopes=https://www.googleapis.com/auth/cloud-platform \
  ...나머지 옵션
```

⚠ **콘솔에서 만들면 기본 범위가 그대로 박힌다** — 지금 VM 이 그렇게 만들어졌고, 그래서 2026-09-09 에
운영을 멈춰야 했다. **VM 생성은 이 저장소에 없다**(사람이 콘솔에서 만들었고 `pipeline.sh` 는 그 VM 의
내부 IP 를 받아 쓸 뿐이다). `docs/deploy/roadmap.md` §8 로 GCP 를 지우고 다시 세우는 날,
**이 한 줄을 빠뜨리면 같은 일을 반복한다.**

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

## `realtime.sh` — 실시간 산책·날씨 서비스 (D-070)

코퍼스 파이프라인과 별도 스크립트다. `daengs-realtime` Cloud Run **서비스**(잡이 아니다 —
`min-instances=0` 이라도 리비전은 상시 존재하고, 리비전이 뜨려면 시크릿이 그 자리에서
해석돼야 한다)를 배포한다. 코드는 `docker/realtime/`, 설계는 `docs/deploy/realtime-cloudrun.md`
(있다면) 를 본다.

1. **사람** — 시크릿 셋에 값을 넣는다. `realtime.sh` 가 시크릿 자체(빈 값)는 만들지만 값은
   안 넣는다 — 히스토리에 안 남게 `printf | --data-file=-` 로:
   ```bash
   ssh -i ~/.ssh/google_compute_engine daengs@34.64.233.102 \
     "grep '^REDIS_PASSWORD=' /srv/daengs/.env | cut -d= -f2-"
   printf %s 'redis://:<위 암호>@10.178.0.2:6379/0' | gcloud secrets versions add realtime-redis-url --data-file=-
   printf %s '<KAKAO_REST_KEY>' | gcloud secrets versions add realtime-kakao-key --data-file=-
   printf %s '<KMA_HUB_KEY>'    | gcloud secrets versions add realtime-kma-hub-key --data-file=-
   ```
   `KAKAO_REST_KEY`·`KMA_HUB_KEY` 는 VM(또는 개발 PC) 의 `backend/.env` 에 이미 있는 것과 같다.
   `DATA_GO_KR_KEY` 는 새로 안 만든다 — 코퍼스 파이프라인의 `corpus-data-go-kr-key` 를
   그대로 재사용한다.
2. **사람** — 배포:
   ```bash
   PROJECT=daengs bash infra/gcp/realtime.sh
   ```
   값을 아직 안 넣은 시크릿이 있으면 스크립트가 배포로 안 넘어가고 안내만 찍은 뒤 `exit 0`
   한다(오류 아님) — 위 1번을 채우고 다시 돌리면 된다. `docker/realtime/` 를 고쳤으면
   `.gcloudignore` 에도 그 경로가 열려 있는지 먼저 확인한다(`gcloud meta list-files-for-upload`).
3. **검증** (2026-09-11 실측한 방법 그대로):

   ⚠ **`/healthz` 로 확인하지 마라.** Cloud Run 앞의 구글 프런트엔드가 **그 경로 하나를
   가로챈다** — 요청이 컨테이너에 안 닿고 구글의 일반 404 HTML 이 오며 **컨테이너 로그에
   요청 기록조차 안 남는다.** 리비전은 Ready 라서 「서비스가 죽었다」로 오인하기 딱 좋다
   (실제로 그렇게 오래 헤맸다). 엔드포인트는 `/health` 다.

   ⚠ **개발 PC 의 `gcloud auth print-identity-token` 으로는 200 이 안 나온다.** 사용자
   계정 토큰은 audience 가 이 서비스 URL 이 아니라서 **미인증으로 취급**된다. 인증된 호출은
   **VM 에서 메타데이터 서버로** 받은 토큰으로 한다 — 어차피 실제로 부르는 쪽이 VM 이다.

   ```powershell
   # 개발 PC — 인터넷에서 막히는지만 본다
   $U = gcloud run services describe daengs-realtime --region=asia-northeast3 --format="value(status.url)"
   curl.exe -s -o NUL -w "no-auth=%{http_code}`n" "$U/health"      # 403 이어야 한다
   ```
   ```bash
   # VM — 실제로 부르는 경로. 200 이어야 한다
   U=<위 URL>
   T=$(curl -s -H 'Metadata-Flavor: Google' \
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=$U")
   curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $T" "$U/health"
   # 판정까지 보려면 (실측 1.3초 — 캐시된 격자 / 7.2초 — 새 격자)
   curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' -H "Authorization: Bearer $T" \
        "$U/life/walk-conditions?lat=37.4979&lon=127.0276"
   ```

   **상태가 정말 공유되는지**는 VM 의 Redis 에서 본다 — 이것이 이 설계의 핵심 단언이다:
   ```bash
   cd ~/daengs && P=$(grep -m1 '^REDIS_PASSWORD=' .env | cut -d= -f2-)
   docker compose exec -T redis redis-cli -a "$P" --no-auth-warning --scan --pattern 'rt:*' | head
   docker compose exec -T redis redis-cli -a "$P" --no-auth-warning GET "rt:budget:datagokr-vilage-fcst:$(date +%Y%m%d)"
   ```
   새 좌표로 한 번 부른 **전후**로 그 카운터가 오르고 그 격자 키가 생기면, Cloud Run 이
   VM 의 Redis 를 쓰고 있는 것이다 (2026-09-11 실측: 부산 좌표로 6 → 8).
4. **VM 의 backend 를 갈림길 반대편으로 넘긴다** — 위 URL 을 VM 의 `backend/.env` 에
   `DAENGS_REALTIME_URL=…` 로 넣는다. ⚠ **그것만으로는 안 켜진다.** compose 는 `backend/.env`
   를 컨테이너에 마운트하지 않고 `env_file` 로 넣는데, 그 값은 **컨테이너를 만들 때** 굳는다.
   runbook §6 의 배포(`git merge --ff-only`)는 `fastapi dev` 의 reload 라 컨테이너를 다시
   만들지 않으므로 그 경로로는 절대 반영되지 않는다. 켜려면 VM 에서 아래를 **일부러** 돌려야
   한다(셸에 `GEMINI_API_KEY` 를 먼저 올릴 것 — 안 올리면 빈 키가 박혀 의미 라우터가 죽는다.
   CLAUDE.md 실측):
   ```bash
   export GEMINI_API_KEY=...
   docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d backend
   ```
5. **지울 때** — `PROJECT=daengs bash infra/gcp/realtime-teardown.sh`. 서비스·시크릿 셋(3개)·
   realtime 이미지 태그를 지운다. Artifact Registry 저장소(`daengs`)와 서비스 계정
   (`corpus-pipeline@...`)은 코퍼스 파이프라인과 공유하므로 지우지 않는다 — 정말 지우려면
   `pipeline-teardown.sh` 를 보고 사람이 판단한다.

## `cardgen.sh` — 도감 카드 생성 GPU 서비스 (D-078, #544)

모델마다 Cloud Run **서비스** 하나, 싱가포르 L4, `min 0 · max 1`. 코드는 `docker/cardgen/`, 가중치는 버킷
`daengs-cardgen-weights` 를 `/models` 로 마운트. **2026-09-16 기준 남아 있는 것:** 서비스 `daengs-cardgen-klein`과 잡
`cardgen-weights` 가 **같은 이미지 `07e7a55` 하나**를 쓴다(잡은 `python -m daengs_cardgen.fetch klein-4b`, 실행 안 함). 옛 이미지
`c917c96`·`90a42ef` 와 옛 리비전은 지웠다(#557). 버킷의 FLUX.2-klein-4B 가중치 14.88GiB. Qwen 서비스·가중치는 09-15 결과가 깨져
지웠고, 코드(`QwenModel`·`MODEL=qwen`)도 #557 에서 걷어냈다(D-078). `/generate` 는 `count`(1~4)로 한 요청에 여러 장을 뽑는다.

**실측 (09-15, #544 worklog):** 이미지 빌드 13~16분 · FLUX.2-klein-4B 가중치 받기 9분 · 서비스 기동→포트 10초 · 모델 로드 425~430초 ·
장당 18~26초 · 요청 뒤 유휴 약 10분 뒤 종료. 새 이미지를 싱가포르에서 처음 가져오면 5분이 붙는다.

**⚠ 09-15 에 실제로 물린 것 (배포 전에 읽을 것):**

| 무엇 | 증상 | 답 |
| --- | --- | --- |
| 가중치 잡 메모리 | 4CPU/16Gi 에서 "The configured memory limit was reached" — 큰 파일 여럿이 동시에 `.incomplete` | 한 파일씩(`max_workers=1`) + `HF_HUB_DISABLE_XET=1` + 8CPU/32Gi (Cloud Run 은 24GiB 넘으면 8 vCPU 필요) |
| Git Bash 경로 변환 | `--set-env-vars=HF_XET_CACHE=/tmp/xet` 가 `C:/Users/.../Temp/xet` 로 저장 | `MSYS2_ARG_CONV_EXCL` 에 `--set-env-vars` 까지. 배포 뒤 **`jobs describe`·`services describe` 로 저장값 확인** |
| PowerShell 쉼표 | `--add-volume=name=...,type=...` 가 배열로 쪼개져 "Key [type] required" | PowerShell 에서는 쉼표 든 인자(`--add-volume` · `--add-volume-mount` · `--set-env-vars` · `--args`)를 **따옴표로** |
| 이미지 태그 재계산 | `STEP=deploy` 가 파일 해시로 태그를 다시 계산 — 빌드 뒤 파일을 고치면 **없는 태그**를 배포 | 빌드한 태그를 확인해 직접 `gcloud run deploy --image=...:<태그>` 로 |
| `gcloud run services proxy` | `cloud-run-proxy` 컴포넌트가 필요한데 SDK 가 Program Files 라 일반 권한 설치 실패 | 관리자 권한 cmd 에서 `gcloud components install cloud-run-proxy` (한 번) |
| `hf download --include` | 값을 하나만 받는다. gcloud `--args` 는 목록 안 같은 플래그 두 번을 거부 | 잡 command 를 `/bin/sh -c "set -f; exec hf download ... --include a --include b"`(`^@^` 구분자) — 지금은 `fetch.py` 가 같은 조건이라 필요 없음 |
| 로그 조회 | PowerShell 에서 `labels."run.googleapis.com/execution_name"` 필터 따옴표가 깨짐 | `gcloud logging read` 는 Bash 로 |
| 새 리비전 전 호출 | 새 리비전 Ready 전에 `/health` 를 부르면 옛 리비전 인스턴스가 떠 로드가 헛돈다 | `latestReadyRevisionName` 이 새 이름이 된 뒤에 호출 |
| 이미지가 빌드마다 8GB | 서비스 `c917c96`·잡 `90a42ef` 가 레이어를 공유하지 않아 16GB(09-16 조회: 저장소 28.2GB vs 이미지 합 28.4GB). Dockerfile 앞쪽 `ENV` 가 바뀌면 뒤 설치 레이어가 전부 새로 생긴다 | 새로 빌드하면 서비스·잡을 **같은 태그로 함께** 배포하고, 서비스 확인 뒤 옛 태그 삭제(`gcloud artifacts docker images delete ...@sha256:... --delete-tags`). 09-16 에 `07e7a55` 하나로 맞췄다 |
| 배포하면 인스턴스가 바로 뜬다 | `gcloud run deploy`/`services update` 로 새 리비전을 만들면 요청이 없어도 인스턴스가 떠서(로그 `Starting new instance. Reason: DEPLOYMENT_ROLLOUT`) 모델을 올린다 — 09-16 실측 시작 17:23:16Z → ready 383초. **배포 한 번 = L4 약 7분 로드 + 유휴 약 10분 과금** | 배포를 몰아서 한다. 설정만 바꿔 보는 실험도 배포마다 이 비용이 붙는다 |

**Artifact Registry 정리 정책(cleanup policy)은 일부러 안 건다 (09-16).** 빌드가 드물어 손으로 지우는 것으로 충분하고,
저장소 `daengs` 는 cardgen·pipeline·realtime 이 같이 쓴다 — 특히 `pipeline` 은 `cpu-*`(매일 04:00 `corpus-refresh`)와
`cuda-*`(`corpus-embed-full`) **두 태그를 동시에** 쓰므로 "이미지마다 최근 N개" 같은 단순 규칙은 한쪽을 지워 다음 날
잡을 죽인다. 11-17 이후 GCP 를 유지하게 되면 그때 이미지·태그별 규칙을 짜고 **dry-run 으로 먼저** 확인한다.

- **돈이 나간다.** Cloud Build(CUDA 이미지), 가중치 받기 잡, 떠 있는 L4 시간. 요청 뒤에도 인스턴스가
  내려가기 전까지 과금된다(인스턴스 기반 과금 필수). 돌리기 전에 사람 승인.
- **부르는 법** — 개발 PC 의 `gcloud auth print-identity-token` 은 이 서비스에서 미인증으로 취급된다
  (realtime 절 3번). 대신 `gcloud run services proxy daengs-cardgen-klein --region=asia-southeast1 --port=8091`
  을 켜 두고 `http://127.0.0.1:8091` 을 부른다. `INVOKER` 로 준 계정에 `run.invoker` 가 걸려 있어야 한다.
- **VM backend 에 연결하지 않는다** — `DAENGS_CARDGEN_URL` 을 VM 에 넣으면 앱 경로가 GPU 서비스를 쓰는데,
  앱 경로 정리 기준이 콜드 스타트를 모른다(#544 남은 것).
- **지울 때** — `PROJECT=daengs bash infra/gcp/cardgen-teardown.sh`. 서비스(`daengs-cardgen-klein`)·잡·가중치 버킷·이미지 태그.

## 자주 걸리는 것

### Windows 에서 gcloud 에 인자를 넘기는 법 — 셋 다 2026-09-11 에 물렸다

컨테이너 경로(`/opt/venv/bin/python` 같은 것)나 따옴표가 든 인자를 넘길 때 **세 가지가 연달아
문다.** 하나를 피하면 다음 것에 걸리므로 같이 적어 둔다.

| 무엇 | 증상 | 답 |
| --- | --- | --- |
| ① Git Bash 의 경로 변환 | `--command=/bin/sh` 가 컨테이너에 **`C:/Program Files/Git/usr/bin/sh`** 로 들어간다. 컨테이너는 그런 파일이 없어 `Application exec likely failed` 로 죽는데, 그 메시지만 보면 이미지 문제로 읽힌다 | PowerShell 로 부른다 |
| ② `MSYS_NO_PATHCONV=1` | ①을 막으려고 켜면 **gcloud 자체가 깨진다** — `can't open file 'C:\c\Program Files...gcloud.py'`. gcloud 런처가 자기 경로를 만들 때 그 변수를 같이 맞기 때문이다 | 쓰지 마라. `MSYS2_ARG_CONV_EXCL` 로 **인자 이름만** 빼는 것은 괜찮다(아래 항목) |
| ③ PowerShell → 네이티브 exe | 큰따옴표가 **사라진다.** `python -c "import socket; s=socket.create_connection((\"10.0.0.1\",6379),5)"` 가 따옴표 없이 도착해 `SyntaxError` 가 난다 | **따옴표를 아예 안 쓰게** 짠다 — 값은 인자로 넘기고 `sys.argv` 로 받는다 |

③의 실제 해법 예 (Cloud Run 잡으로 VPC 연결을 확인할 때 쓴 것):

```powershell
gcloud run jobs update vpc-probe --region=asia-northeast3 --command=/opt/venv/bin/python `
  --args='^@^-c@import socket,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),5); s.sendall(bytes([80,73,78,71,13,10])); print(sys.argv[1], sys.argv[2], s.recv(80))@10.178.0.2@6379'
```

`^@^` 는 gcloud 의 **구분자 지정**이다(기본 구분자인 쉼표가 코드 안에 들어가므로 바꾼다).
⚠ 구분자로 `|` 를 고르면 셸 파이프와 겹쳐 인자가 잘린다 — 실제로 한 번 잘렸다.

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
