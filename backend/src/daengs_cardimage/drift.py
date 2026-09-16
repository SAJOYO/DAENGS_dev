"""모델 출력이 카드 틀을 얼마나 밀었는지 잰다 (D-078 비교 판정).

카드 바깥 테두리 띠만 본다 — 강아지·제목은 원래 달라야 하는 자리라 빼고, 테두리는 틀과 같아야 한다.
출력을 (dx, dy) 만큼 옮겨 봤을 때 틀과 가장 잘 맞는 이동량이 밀림이다. 반 해상도로 찾고 2배로 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageStat

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Drift:
    dx: int
    dy: int
    mad_at_zero: float   # 옮기지 않았을 때 테두리 평균 절대 차이(0~255)
    mad_at_best: float   # 가장 잘 맞는 이동에서의 차이


def _band_boxes(w: int, h: int, band: int, margin: int) -> list[Box]:
    return [
        (margin, margin, w - margin, margin + band),
        (margin, h - margin - band, w - margin, h - margin),
        (margin, margin + band, margin + band, h - margin - band),
        (w - margin - band, margin + band, w - margin, h - margin - band),
    ]


def _mad(ref: Image.Image, img: Image.Image, dx: int, dy: int, boxes: list[Box]) -> float:
    total, area = 0.0, 0
    for x0, y0, x1, y1 in boxes:
        a = ref.crop((x0, y0, x1, y1))
        b = img.crop((x0 + dx, y0 + dy, x1 + dx, y1 + dy))
        n = (x1 - x0) * (y1 - y0)
        total += ImageStat.Stat(ImageChops.difference(a, b)).mean[0] * n
        area += n
    return total / area


def frame_drift(template: Image.Image, card: Image.Image, *, band: int = 24, max_shift: int = 8,
                scale: int = 2) -> Drift:
    size = (template.width // scale, template.height // scale)
    ref = template.convert("L").resize(size, Image.BILINEAR)
    img = card.convert("L").resize(size, Image.BILINEAR)
    reach = max_shift // scale
    boxes = _band_boxes(size[0], size[1], max(1, band // scale), reach + 1)
    zero = _mad(ref, img, 0, 0, boxes)
    candidates = (
        (_mad(ref, img, dx, dy, boxes), abs(dx) + abs(dy), dx, dy)
        for dy in range(-reach, reach + 1)
        for dx in range(-reach, reach + 1)
    )
    best_mad, _, dx, dy = min(candidates)
    return Drift(dx=dx * scale, dy=dy * scale, mad_at_zero=round(zero, 2), mad_at_best=round(best_mad, 2))
