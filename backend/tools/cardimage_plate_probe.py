"""틀 12장의 제목판을 재어 catalog 에 붙일 `Plate(...)` 줄을 찍는다.

    uv run python tools/cardimage_plate_probe.py            # 12달 전부
    uv run python tools/cardimage_plate_probe.py --month 7  # 한 달만

찍힌 값을 그대로 믿지 말고 `uv run python tools/cardimage_title.py --month N "TITLE 이름"` 으로
제목을 얹어 눈으로 확인한 뒤 catalog 에 넣는다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from daengs_cardimage import catalog
from daengs_cardimage.plate_probe import measure

CARDIMAGE = Path(__file__).resolve().parents[2] / "cardimage"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--month", type=int, default=0, help="0 이면 12달 전부")
    args = ap.parse_args()
    months = [args.month] if args.month else list(range(1, 13))
    for m in months:
        path = catalog.template_path(m, CARDIMAGE)
        plate = measure(Image.open(path))
        print(f"{m:2d} {catalog.get(m).stem:<18} "
              f"Plate(center_y={plate.center_y}, edge={plate.edge}, top_y={plate.top_y})")


if __name__ == "__main__":
    main()
