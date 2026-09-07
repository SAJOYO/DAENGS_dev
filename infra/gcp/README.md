# infra/gcp — 코퍼스 파이프라인 (D-062)

설계 `docs/deploy/corpus-pipeline.md` · 운영 절차 `docs/deploy/runbook.md` §6 "코퍼스 파이프라인 (GCP)".
**11-17 실험 종료 때 `pipeline-teardown.sh`** (roadmap §8).

## 순서

1. **사람, 콘솔** — 결제 계정이 무료 체험이 아닌지 확인. `gcloud auth login` · `gcloud config set project`.
2. **사람** — Secret 값 넣기 (스크립트가 빈 Secret 을 만들어 두므로 먼저 스크립트를 한 번 돌려도 된다):
   ```bash
   printf '%s' '<VM pgvector 의 daengs 비밀번호>' | gcloud secrets versions add corpus-db-password --data-file=-
   printf '%s' '<LAW_OC>'          | gcloud secrets versions add corpus-law-oc --data-file=-
   printf '%s' '<DATA_GO_KR_KEY>'  | gcloud secrets versions add corpus-data-go-kr-key --data-file=-
   ```
3. **사람** — VM 내부 IP: `gcloud compute instances list --format='value(name,networkInterfaces[0].networkIP)'`
4. **사람** — `PROJECT=… VM_INTERNAL_IP=… bash infra/gcp/pipeline.sh` (저장소 루트에서. 이미지 두 장을 Cloud Build 가 굽는다, 20~40분).
5. **사람** — 초기 사본. 집 서버 `DAENGS_CORPUS_DIR` 의 `raw/` 와 `manifests/crawl_log.jsonl` 만:
   ```powershell
   gcloud storage rsync -r C:\deploy\daengs\corpus\raw gs://daengs-corpus/raw
   gcloud storage cp C:\deploy\daengs\corpus\manifests\crawl_log.jsonl gs://daengs-corpus/manifests/crawl_log.jsonl
   ```
   `processed/` 는 올리지 않는다 — 잡이 만든다. `seed_sources.yaml` 도 올리지 않는다 — 이미지가 넣는다.
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

코드가 바뀌면 `pipeline.sh` 를 다시 돌린다 — 태그가 `cpu-<sha>` 라 잡 정의가 새 이미지로 update 된다.

## 자주 걸리는 것

- **잡이 `코퍼스가 없습니다` 로 바로 죽는다** — 버킷에 `manifests/crawl_log.jsonl` 이 없다(5번).
- **`load` 가 connection refused** — 방화벽 규칙의 source range 가 잡이 쓰는 서브넷과 다르거나, VM 의 compose 가 5432 를 `0.0.0.0` 에 게시하지 않았다(`docker compose ps` 로 확인).
- **`embed` 가 모델을 받으려 한다** — 이미지 빌드의 가중치 굽기가 실패한 것. `HF_HOME=/models` 가 잡 env 에 있는지.
- **GPU 잡이 quota 에러** — 첫 실행에 자동 할당(3장)이 안 된 경우. 콘솔 할당량에서 `Cloud Run Admin API` × `NVIDIA L4` 를 1 로 요청.
- **`cuda` 이미지 빌드가 실패한다** — `pipeline.sh` 의 `build_image cuda` 는 이 스크립트로 처음
  굽는 것이다(CPU 판만 로컬에서 확인됐다). 실패하면 먼저 `docker/pipeline/Dockerfile` 의
  `torch-cuda` 스테이지 — `uv pip install ... cu126` 줄 — 을 본다 (버전 문자열에 `+` 뒤 로컬
  세그먼트가 남았는지, cu126 인덱스에 해당 버전이 있는지).
