"""GPU 카드 생성 서비스(D-078)와 Nano Banana 2 를 같은 사진·틀·seed 로 비교한다 (#544).

    uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 \
        --photos ../cardimage/test/_03.jpg --months 4,9 --seeds 1,2 --out ../cardimage/out/_cardgen/klein
    uv run python tools/cardgen_compare.py --engine gemini --photos ... --out ../cardimage/out/_cardgen/gemini

결과: `<out>/<사진>_<달>_s<seed>.png` 와 `<out>/results.jsonl` 한 줄씩 — 닮음·글자·아바타(검수),
틀 밀림(`drift`), 제목판 어긋남(`plate_shift`, 못 재면 null), 걸린 시간(`seconds`, 서비스 쪽은 `service.seconds`).
재시도는 하지 않는다(`judge_min=1`) — 한 장 한 장이 비교 표본이다.
`--engine gemini` 에서 seed 는 파일 이름·반복 번호일 뿐이다(Gemini 는 seed 를 받지 않는다) — 같은 seed 끼리 짝지은 비교가 아니다.

⚠ 돈이 나간다: gemini 엔진 장당 약 $0.10, 검수 장당 몇 원, cardgen 은 Cloud Run L4 가 떠 있는 시간.
**실행 전에 사람에게 장수·순서를 설명하고 승인받는다** (docs/cardimage/README).
`cardgen` 의 `--url` 은 `gcloud run services proxy <서비스> --region=asia-southeast1 --port=8091` 로 연 주소다.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from PIL import Image

from daengs_backend.config import settings
from daengs_cardimage import catalog, generate_card
from daengs_cardimage.drift import frame_drift
from daengs_cardimage.engine import GeminiCardImageEngine, HttpCardImageEngine
from daengs_cardimage.judge import GeminiCardJudge

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=("cardgen", "gemini"), required=True)
    parser.add_argument("--url", default="", help="cardgen 서비스 주소 (proxy 로 연 로컬 포트)")
    parser.add_argument("--photos", required=True, help="쉼표로 구분한 사진 경로")
    parser.add_argument("--months", default="4,9")
    parser.add_argument("--seeds", default="1")
    parser.add_argument("--dog-name", default="테스트")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    key = settings.cardimage_gemini_api_key.get_secret_value().strip()
    if not key:
        print("DAENGS_CARDIMAGE_GEMINI_API_KEY 가 필요합니다 (검수·gemini 엔진)", file=sys.stderr)
        return 2
    if args.engine == "cardgen" and not args.url:
        print("--engine cardgen 에는 --url 이 필요합니다", file=sys.stderr)
        return 2

    from daengs_cardimage.title import plate_shift

    judge = GeminiCardJudge(api_key=key, model=settings.cardimage_judge_model,
                            timeout_ms=settings.cardimage_timeout_ms)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    months = sorted(int(m) for m in args.months.split(","))
    seeds = [int(s) for s in args.seeds.split(",")]

    for photo_path in [Path(p) for p in args.photos.split(",")]:
        photo = photo_path.read_bytes()
        for month in months:
            template = Image.open(catalog.template_path(month, settings.cardimage_dir)).convert("RGB")
            for seed in seeds:
                if args.engine == "cardgen":
                    engine = HttpCardImageEngine(base_url=args.url, timeout_s=settings.cardgen_timeout_s, seed=seed)
                else:
                    engine = GeminiCardImageEngine(api_key=key, model=settings.cardimage_model,
                                                   size=settings.cardimage_size,
                                                   timeout_ms=settings.cardimage_timeout_ms)
                started = time.monotonic()
                card = generate_card(
                    photo=photo, content_type=MIME[photo_path.suffix.lower()], month=month,
                    dog_name=args.dog_name, engine=engine, judge=judge, base_dir=settings.cardimage_dir,
                    open_months=frozenset(months), judge_min=1,
                )
                seconds = round(time.monotonic() - started, 1)
                name = f"{photo_path.stem}_{month}_s{seed}"
                (out / f"{name}.png").write_bytes(card.png)
                image = Image.open(io.BytesIO(card.png)).convert("RGB")
                row = {
                    "name": name, "engine": args.engine, "photo": photo_path.name, "month": month, "seed": seed,
                    "seconds": seconds, "service": getattr(engine, "last_meta", None),
                    "likeness": card.judge.likeness if card.judge else None,
                    "text_ok": card.judge.text_ok if card.judge else None,
                    "avatar_ok": card.judge.avatar_ok if card.judge else None,
                    "judge_note": card.judge.note if card.judge else None,
                    "drift": asdict(frame_drift(template, image)),
                    "plate_shift": plate_shift(image, catalog.get(month).plate),
                }
                with (out / "results.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
