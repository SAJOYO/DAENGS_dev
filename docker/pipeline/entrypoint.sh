#!/bin/sh
# 코퍼스 폴더 가드 (RAG-050): 로그가 없으면 전 소스가 due 로 잡혀 다른 코퍼스가 된다.
# --dry-run 이나 --stages 에 crawl 이 없는 실행은 이 가드가 필요 없지만, 단순하게 항상 본다 —
# 버킷 마운트가 비어 있는 것 자체가 사고다.
set -eu
if [ ! -f /data/manifests/crawl_log.jsonl ]; then
  echo "코퍼스가 없습니다: /data/manifests/crawl_log.jsonl 이 없습니다. 버킷 마운트를 확인하세요." >&2
  exit 1
fi
exec uv run --no-sync corpus-refresh --seeds-from /app/seed_sources.yaml "$@"
