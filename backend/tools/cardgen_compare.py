"""GPU 카드 생성 서비스(D-078)와 Nano Banana 2 를 같은 사진·틀·seed 로 비교한다 (#544, #557).

    uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 \
        --photos ../cardimage/test/_03.jpg --months 4,9 --seeds 1,2 --out ../cardimage/out/_cardgen/klein
    uv run python tools/cardgen_compare.py --engine gemini --photos ... --out ../cardimage/out/_cardgen/gemini

    # #557 E1 글씨 유지 — 아래 패널 문구를 프롬프트에 적고 / 생성 크기를 올린다
    uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 --photos ... \
        --months 4,9 --seeds 1 --panel-text --gen-size 1280x2048 --out ../cardimage/out/_cardgen/e1-both

    # #557 E2 — 한 요청에 4장: --batch 4 (순차 4회 비교는 --seeds 1,2,3,4)

결과: `<out>/<사진>_<달>_s<seed>.png` 와 `<out>/results.jsonl` 한 줄씩 — 닮음·글자·아바타(검수),
틀 밀림(`drift`), 제목판 어긋남(`plate_shift`, 못 재면 null), 걸린 시간(`seconds`, 서비스 쪽은 `service.seconds`),
조건(`gen_size`, `panel_text`).
재시도는 하지 않는다(`judge_min=1`) — 한 장 한 장이 비교 표본이다.
`--engine gemini` 에서 seed 는 파일 이름·반복 번호일 뿐이다(Gemini 는 seed 를 받지 않는다) — 같은 seed 끼리 짝지은 비교가 아니다.
`--gen-size` 는 cardgen 엔진에만 쓰인다. `--panel-text` 는 제품 프롬프트(`build_prompt`)를 바꾸지 않고
엔진을 감싸 끝에 문장을 붙인다 — 효과가 확인되면 그때 `catalog` 로 옮긴다.
강아지 이름 기본값은 영문 `MOMO` 다 — 검수가 한글 이름을 깨진 글자로 오판한다(09-15).

⚠ 돈이 나간다: gemini 엔진 장당 약 $0.10, 검수 장당 몇 원, cardgen 은 Cloud Run L4 가 떠 있는 시간.
**실행 전에 사람에게 장수·순서를 설명하고 승인받는다** (docs/cardimage/README).
`cardgen` 의 `--url` 은 `gcloud run services proxy <서비스> --region=asia-southeast1 --port=8091` 로 연 주소다.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

from PIL import Image

from daengs_cardimage import catalog, generate_card
from daengs_cardimage.drift import frame_drift
from daengs_cardimage.engine import GEN_SIZE, GeminiCardImageEngine, HttpCardImageEngine

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

#: 틀에 구워진 아래 패널 문구 — 제목 · 부제 · 왼쪽 칸 · 숫자 · 맨 아래 줄 (09-16 틀 이미지에서 읽음).
#: 09-15 FLUX.2-klein-4B 에서 4월 제목이 `PETL PPAUSE` 로 깨졌다(3/12, 전부 seed 1).
PANEL_TEXT: dict[int, tuple[str, ...]] = {
    4: ("PETAL PAUSE", "One petal. Perfect timing.", "SPRING", "920", "Bloomed right on schedule."),
    9: ("SONGPYEON SWEEP", "Full moon. Fuller snack tray.", "MOON LUCK", "925", "A warm Chuseok surprise."),
}


def parse_size(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)[xX](\d+)", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"크기는 WxH 형식입니다(예: 1280x2048): {text!r}")
    return int(m.group(1)), int(m.group(2))


def panel_sentence(month: int) -> str:
    quoted = ", ".join(f'"{t}"' for t in PANEL_TEXT[month])
    return (
        "The bottom panel text must stay exactly as in image 1, letter for letter, in the same font, size and "
        f"position: {quoted}. Do not misspell, merge, duplicate or drop any letter."
    )


class PromptSuffixEngine:
    """안쪽 엔진을 부르기 전에 프롬프트 끝에 문장을 붙인다. 비교 도구 전용 — 제품 프롬프트는 그대로 둔다."""

    def __init__(self, inner, suffix: str) -> None:
        self._inner, self._suffix = inner, suffix

    @property
    def last_meta(self):
        return getattr(self._inner, "last_meta", None)

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str,
                 seed: int | None = None) -> bytes:
        return self._inner.generate(template_png=template_png, photo_jpeg=photo_jpeg,
                                    prompt=f"{prompt}\n\n{self._suffix}", seed=seed)


class BatchReplayEngine:
    """서비스를 한 번 불러 N장을 받아 두고, `generate_card` 가 부를 때마다 다음 장을 준다 (#557 E2).
    `generate_card(judge_min=1)` 은 카드 한 장에 엔진을 한 번만 부르므로 N번 부르면 N장이 된다."""

    def __init__(self, inner, count: int) -> None:
        self._inner, self._count = inner, count
        self._cards: list[bytes] | None = None
        self._next = 0
        self.last_meta: dict | None = None

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str,
                 seed: int | None = None) -> bytes:
        # `seed` 는 서비스가 한 요청 안에서 스스로 정한다(장별 실제 값은 last_meta["seeds"]) —
        # 여기서는 프로토콜을 맞추려고 받되, 안쪽 `generate_batch` 는 seed 인자를 안 받는다.
        if self._cards is None:
            self._cards = self._inner.generate_batch(template_png=template_png, photo_jpeg=photo_jpeg,
                                                     prompt=prompt, count=self._count)
        if self._next >= len(self._cards):
            raise RuntimeError(f"{self._count} 장을 이미 다 줬습니다")
        meta = self._inner.last_meta or {}
        i = self._next
        self._next += 1
        self.last_meta = {"seed": (meta.get("seeds") or [None] * self._count)[i], "index": i,
                          "batch_seconds": meta.get("seconds"), "model": meta.get("model"),
                          "size": meta.get("size"), "count": self._count}
        return self._cards[i]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=("cardgen", "gemini"), required=True)
    parser.add_argument("--url", default="", help="cardgen 서비스 주소 (proxy 로 연 로컬 포트)")
    parser.add_argument("--photos", required=True, help="쉼표로 구분한 사진 경로")
    parser.add_argument("--months", default="4,9")
    parser.add_argument("--seeds", default="1")
    parser.add_argument("--dog-name", default="MOMO")
    parser.add_argument("--gen-size", type=parse_size, default=GEN_SIZE,
                        help="cardgen 생성 크기 WxH (기본 1024x1632, 서비스 상한 2048)")
    parser.add_argument("--panel-text", action="store_true", help="아래 패널 문구를 프롬프트 끝에 적는다 (#557 E1)")
    parser.add_argument("--batch", type=int, default=0, help="cardgen 한 요청에 N장(2~4) — 카드 이름에 _b<i> (#557 E2)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    from daengs_backend.config import settings
    from daengs_cardimage.judge import GeminiCardJudge
    from daengs_cardimage.title import plate_shift

    key = settings.cardimage_gemini_api_key.get_secret_value().strip()
    if not key:
        print("DAENGS_CARDIMAGE_GEMINI_API_KEY 가 필요합니다 (검수·gemini 엔진)", file=sys.stderr)
        return 2
    if args.engine == "cardgen" and not args.url:
        print("--engine cardgen 에는 --url 이 필요합니다", file=sys.stderr)
        return 2
    if args.batch and (args.engine != "cardgen" or not 2 <= args.batch <= 4):
        print("--batch 는 cardgen 엔진에서 2~4", file=sys.stderr)
        return 2

    judge = GeminiCardJudge(api_key=key, model=settings.cardimage_judge_model,
                            timeout_ms=settings.cardimage_timeout_ms)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    months = sorted(int(m) for m in args.months.split(","))
    seeds = [int(s) for s in args.seeds.split(",")]
    if args.panel_text:
        missing = [m for m in months if m not in PANEL_TEXT]
        if missing:
            print(f"--panel-text 문구가 없는 달: {missing}", file=sys.stderr)
            return 2
    gen_size = f"{args.gen_size[0]}x{args.gen_size[1]}"

    for photo_path in [Path(p) for p in args.photos.split(",")]:
        photo = photo_path.read_bytes()
        for month in months:
            template = Image.open(catalog.template_path(month, settings.cardimage_dir)).convert("RGB")
            for seed in seeds:
                if args.engine == "cardgen":
                    engine = HttpCardImageEngine(base_url=args.url, timeout_s=settings.cardgen_timeout_s, seed=seed,
                                                 gen_size=args.gen_size)
                    if args.batch:
                        engine = BatchReplayEngine(engine, args.batch)
                else:
                    engine = GeminiCardImageEngine(api_key=key, model=settings.cardimage_model,
                                                   size=settings.cardimage_size,
                                                   timeout_ms=settings.cardimage_timeout_ms)
                if args.panel_text:
                    engine = PromptSuffixEngine(engine, panel_sentence(month))
                for copy in range(args.batch or 1):
                    started = time.monotonic()
                    card = generate_card(
                        photo=photo, content_type=MIME[photo_path.suffix.lower()], month=month,
                        dog_name=args.dog_name, engine=engine, judge=judge, base_dir=settings.cardimage_dir,
                        open_months=frozenset(months), judge_min=1,
                        # 이 도구는 특정 seed 를 정확히 겨눠 비교한다 — pick_seeds 가 대신 고르면
                        # results.jsonl 의 "seed" 열이 실제로 만든 이미지와 어긋난다(#572 fix round 1 F1).
                        seed=seed,
                    )
                    seconds = round(time.monotonic() - started, 1)
                    name = f"{photo_path.stem}_{month}_s{seed}" + (f"_b{copy}" if args.batch else "")
                    (out / f"{name}.png").write_bytes(card.png)
                    image = Image.open(io.BytesIO(card.png)).convert("RGB")
                    row = {
                        "name": name, "engine": args.engine, "photo": photo_path.name, "month": month, "seed": seed,
                        "gen_size": gen_size if args.engine == "cardgen" else None, "panel_text": args.panel_text,
                        "batch": args.batch or None,
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
