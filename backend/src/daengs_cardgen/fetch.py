"""가중치를 `HF_HOME` 에 받아 둔다 — Cloud Run 잡 `cardgen-weights` 가 버킷을 /models 로 마운트하고 부른다.

서비스는 기동마다 인터넷에서 받지 않는다(`HF_HUB_OFFLINE=1`). 컨테이너 파일 시스템은 메모리라
20GB 넘는 가중치를 거기 받으면 인스턴스가 죽는다 — 그래서 버킷에 한 번만 받는다.
"""

from __future__ import annotations

import sys

from daengs_cardgen.diffusion import MODEL_REPOS


def main(argv: list[str] | None = None) -> int:
    names = list(argv if argv is not None else sys.argv[1:]) or sorted(MODEL_REPOS)
    unknown = [n for n in names if n not in MODEL_REPOS]
    if unknown:
        print(f"모르는 모델 {unknown} — 가능: {sorted(MODEL_REPOS)}", file=sys.stderr)
        return 2
    from huggingface_hub import snapshot_download

    for name in names:
        path = snapshot_download(MODEL_REPOS[name])
        print(f"fetched {name} -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
