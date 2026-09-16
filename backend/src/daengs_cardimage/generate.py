"""사진 한 장 → 달 카드 한 장. 틀 읽기 → 사진 축소 → 엔진(강아지 교체) → 제목 얹기 → 검수(유사도 1~5) → 유사도가 기준 미만이면 한 번 더 → 둘 중 점수 높은 쪽."""

from __future__ import annotations

import io
import logging
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from daengs_cardimage import catalog
from daengs_cardimage import photo as photo_mod
from daengs_cardimage import title as title_mod
from daengs_cardimage.engine import (
    CardImageEngine,
    EngineError,
    build_prompt,
)
from daengs_cardimage.judge import (
    CardJudge,
    JudgeError,
    JudgeResult,
)

log = logging.getLogger(__name__)


class CardImageUnavailable(Exception):
    """키가 없거나 틀 파일이 없다 — 설정 문제라 503."""


@dataclass
class GeneratedCard:
    png: bytes
    judge: JudgeResult | None
    attempts: int
    month: int
    title: str
    #: 이 카드를 만들 때 쓴 seed. `catalog.pick_seeds` 가 뽑은 값이다 (#572 Task 3a).
    seed: int | None = None


def _load_template(month: int, base_dir: Path) -> bytes:
    p = catalog.template_path(month, base_dir)
    if not p.exists():
        raise CardImageUnavailable(f"틀 파일이 없습니다: {p}")
    buf = io.BytesIO()
    Image.open(p).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def _attempt(engine: CardImageEngine, judge: CardJudge | None, *, template: bytes, photo_jpeg: bytes,
             prompt: str, text: str, font: Path, plate: title_mod.Plate,
             seed: int | None) -> tuple[bytes, JudgeResult | None]:
    """한 번의 생성 시도: 엔진 호출 → 제목 얹기 → (있으면) 검수. 검수가 없거나 실패해도
    카드 자체는 만들어 돌려준다 — 점수는 `None` 이 될 뿐 이 함수가 실패하지는 않는다."""
    try:
        raw = engine.generate(template_png=template, photo_jpeg=photo_jpeg, prompt=prompt, seed=seed)
    except EngineError as exc:
        if exc.code == "no_key":
            raise CardImageUnavailable(exc.detail) from exc
        raise
    card = title_mod.draw_title(Image.open(io.BytesIO(raw)).convert("RGB"), text, font, plate=plate)
    out = io.BytesIO()
    card.save(out, "PNG")
    png = out.getvalue()
    if judge is None:
        return png, None
    try:
        return png, judge.judge(photo_jpeg=photo_jpeg, card_png=png)
    except JudgeError as exc:
        # 검수 실패는 치명적이지 않다 — 이번 한 장을 점수 없이 그대로 돌려준다(재시도하지 않는다).
        log.warning("cardimage judge failed, accepting card without score: %s", exc)
        return png, None


def generate_card(*, photo: bytes, content_type: str, month: int, dog_name: str, engine: CardImageEngine,
                  judge: CardJudge | None, base_dir: Path, open_months: frozenset[int], judge_min: int,
                  rng: random.Random | None = None, seed: int | None = None) -> GeneratedCard:
    """사진 한 장으로 달 카드 한 장을 만든다. 검수 점수가 `judge_min` 미만이면 한 번 더 만들어
    보고 둘 중 점수 높은 쪽을 돌려준다(동점이면 첫 번째). 검수가 없거나 실패하면 재시도 없이
    그 한 장을 그대로 돌려준다.

    `seed` 를 주면(비교 도구가 특정 seed 를 재도록 쓴다, #572 fix round 1 F1) **그 값을 그대로
    쓰고 재시도하지 않는다** — cardgen 엔진은 seed·프롬프트·입력이 같으면 바이트까지 같은
    이미지를 내놓으므로(FLUX.2-klein-4B 는 결정적이다) 같은 seed 로 다시 만들면 판정이 절대
    못 바뀌면서 돈만 한 번 더 나간다. `attempts` 는 이때 늘 1이다.

    `seed` 가 없으면(운영 경로) `rng` 로 **서로 다른 두 seed**(`catalog.pick_seeds(month, 2, ...)`)
    를 미리 뽑아 두고, 첫 시도가 기준 미만이면 두 번째 seed 로 재시도한다 — 같은 seed 로
    재시도하면 이 경로도 위와 같은 무의미한 재생성이 된다(#572 fix round 1 F2). `rng` 는
    테스트에서 `random.Random(0)` 을 넘기면 고정된다."""
    card_meta = catalog.require_open(month, open_months)
    photo_jpeg = photo_mod.prepare_photo(photo, content_type)
    template = _load_template(month, base_dir)
    font = catalog.font_path(base_dir)
    if not font.exists():
        raise CardImageUnavailable(f"글꼴이 없습니다: {font}")
    if not card_meta.subtitle:
        # require_open 이 scene 없는 달을 먼저 막지만, 부제만 빠진 채 열리면 여기서 소리 내어 실패한다.
        raise CardImageUnavailable(f"부제가 없는 달: {month}")
    prompt = build_prompt(scene=card_meta.scene, badge=card_meta.badge, subtitle=card_meta.subtitle,
                          outfit=card_meta.outfit)
    text = title_mod.title_text(card_meta.card_name, dog_name)
    plate = card_meta.plate

    if seed is not None:
        # 호출자가 seed 를 못박았다 — pick_seeds 를 아예 부르지 않고 그 값 그대로, 한 번만.
        png1, j1 = _attempt(engine, judge, template=template, photo_jpeg=photo_jpeg, prompt=prompt, text=text,
                            font=font, plate=plate, seed=seed)
        return GeneratedCard(png=png1, judge=j1, attempts=1, month=month, title=text, seed=seed)

    seed1, seed2 = catalog.pick_seeds(month, 2, rng or random.Random())
    png1, j1 = _attempt(engine, judge, template=template, photo_jpeg=photo_jpeg, prompt=prompt, text=text,
                        font=font, plate=plate, seed=seed1)
    if j1 is None or j1.likeness >= judge_min:
        return GeneratedCard(png=png1, judge=j1, attempts=1, month=month, title=text, seed=seed1)
    png2, j2 = _attempt(engine, judge, template=template, photo_jpeg=photo_jpeg, prompt=prompt, text=text,
                        font=font, plate=plate, seed=seed2)
    if j2 is not None and j2.likeness > j1.likeness:
        return GeneratedCard(png=png2, judge=j2, attempts=2, month=month, title=text, seed=seed2)
    return GeneratedCard(png=png1, judge=j1, attempts=2, month=month, title=text, seed=seed1)
