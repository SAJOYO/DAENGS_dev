#!/bin/sh
# 코퍼스 파이프라인 잡의 진입점 (D-062 · #427).
#
# 🔴 **우리 코드는 이미지에 없다.** 버킷(`/data/code/`)에서 `/app/src` 로 복사한 뒤
# `PYTHONPATH`(Dockerfile 의 ENV)로 잡아 `python -m` 으로 부른다. 이유와 대가는
# `docker/pipeline/Dockerfile` 머리말에 있다 — 요약하면 14MB 짜리 코드가 바뀔 때마다
# 11GB 를 다시 굽지 않기 위해서다.
#
# 코드를 올리는 것은 `infra/gcp/pipeline.sh` 의 rsync 다. **그것을 잊으면 옛 코드로 돈다** —
# 옛 판에서 *이미지 굽기를 잊으면 옛 코드로 돌던 것*과 같은 실패 모양이고, 그래서 배포
# 스크립트가 rsync 를 자기 안에 들고 있다(사람이 치는 명령은 여전히 하나다).
set -eu

CODE_SRC=/data/code
CODE_DST=/app/src

# ── 가드 셋. 셋 다 "없으면 조용히 이상하게 도는" 자리라 시작 전에 크게 죽는다.

# ⓐ 코드. 없으면 `python -m` 이 ModuleNotFoundError 로 죽기는 하지만, 그 메시지는
#    "버킷에 코드가 없다"를 안 말해 준다.
if [ ! -d "$CODE_SRC/daengs_life" ]; then
  echo "코드가 없습니다: $CODE_SRC/daengs_life 이 없습니다." >&2
  echo "  올리는 것은 infra/gcp/pipeline.sh 의 rsync 단계입니다 (버킷 prefix: code/)." >&2
  exit 1
fi

# ⓑ 코퍼스 로그 (RAG-050): 로그가 없으면 전 소스가 due 로 잡혀 **다른 코퍼스**가 된다.
#    --dry-run 이나 crawl 없는 실행은 이 가드가 필요 없지만, 단순하게 항상 본다 —
#    버킷 마운트가 비어 있는 것 자체가 사고다.
if [ ! -f /data/manifests/crawl_log.jsonl ]; then
  echo "코퍼스가 없습니다: /data/manifests/crawl_log.jsonl 이 없습니다. 버킷 마운트를 확인하세요." >&2
  exit 1
fi

# ⓒ 시드. `--seeds-from` 이 이것을 `/data/manifests/` 에 덮어쓴다 — 시드는 코퍼스가 아니라
#    코드이고, 버킷의 낡은 시드가 이기면 안 된다 (corpus_refresh._install_seeds 의 docstring).
if [ ! -f "$CODE_SRC/seed_sources.yaml" ]; then
  echo "시드가 없습니다: $CODE_SRC/seed_sources.yaml 이 없습니다 (같은 rsync 가 올립니다)." >&2
  exit 1
fi

# FUSE 마운트에서 **직접 import 하지 않는다** — import 는 파일마다 stat 을 때리고 GCS FUSE 는
# 그 하나하나가 네트워크다. 3MB 를 로컬로 한 번 복사하는 것이 훨씬 싸다.
rm -rf "$CODE_DST"
mkdir -p "$CODE_DST"
cp -r "$CODE_SRC/daengs_life" "$CODE_DST/daengs_life"
cp "$CODE_SRC/seed_sources.yaml" /app/seed_sources.yaml

# 태그가 코드 버전을 안 말하게 된 대가를 여기서 메운다 (Dockerfile 머리말).
# 로그 첫 줄에 둬서 실행 하나만 열어도 "어느 코드였나"가 보이게 한다.
if [ -f "$CODE_SRC/VERSION" ]; then
  echo "[entrypoint] 코드 $(cat "$CODE_SRC/VERSION")"
else
  echo "[entrypoint] 코드 VERSION 없음 — 옛 rsync 이거나 손으로 올린 것입니다" >&2
fi

exec /opt/venv/bin/python -m daengs_life.jobs.corpus_refresh \
  --seeds-from /app/seed_sources.yaml "$@"
