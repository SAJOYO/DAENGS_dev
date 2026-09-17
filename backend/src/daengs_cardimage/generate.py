"""사진 한 장 → 카드 한 장(달 1~12 또는 딸기·상추). 틀 읽기 → 사진 축소 → 엔진(강아지 교체) → 제목 얹기 → 검수(유사도 1~5) → 유사도가 기준 미만이면 한 번 더 → 둘 중 점수 높은 쪽."""

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
    #: 달 카드의 달. 달이 아닌 카드(딸기·상추)는 0 이다 — 무엇을 만들었는지는 `card_key` 로 본다.
    month: int
    #: 무엇을 만들었나 — 달은 `"4"`, 종류는 `"strawberry"` (#592). 저장·로그가 이 값을 쓴다.
    card_key: str
    title: str
    #: 이 카드를 만들 때 쓴 seed. `catalog.pick_seeds` 가 뽑은 값이다 (#572 Task 3a).
    seed: int | None = None


def _load_template(card: catalog.CardSelector, base_dir: Path) -> bytes:
    p = catalog.template_path(card, base_dir)
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


def _setup(*, card: catalog.CardSelector, dog_name: str, photo: bytes, content_type: str, base_dir: Path,
           open_months: frozenset[int]) -> tuple[bytes, bytes, Path, str, str, catalog.MonthCard]:
    """카드마다 달라지지 않는 것들을 한 번만 준비한다 — 틀·사진·프롬프트·제목은 장을 몇 장을
    만들든 같으므로, 여러 장을 만들 때 이걸 장마다 다시 하지 않는다.

    잠금(`open_months`)은 **달일 때만** 본다 — 달이 아닌 카드(딸기·상추)는 콘솔 전용이라 잠글
    대상이 아니다(설계 ①)."""
    card_meta = catalog.require_open(card, open_months) if isinstance(card, int) else catalog.resolve(card)
    photo_jpeg = photo_mod.prepare_photo(photo, content_type)
    template = _load_template(card, base_dir)
    font = catalog.font_path(base_dir)
    if not font.exists():
        raise CardImageUnavailable(f"글꼴이 없습니다: {font}")
    if not card_meta.subtitle:
        # require_open 이 scene 없는 달을 먼저 막지만, 부제만 빠진 채 열리면 여기서 소리 내어 실패한다.
        raise CardImageUnavailable(f"부제가 없는 카드: {card}")
    prompt = build_prompt(scene=card_meta.scene, badge=card_meta.badge, subtitle=card_meta.subtitle,
                          outfit=card_meta.outfit, face_hidden=card_meta.face_hidden,
                          face_only=card_meta.face_only)
    text = title_mod.title_text(card_meta.card_name, dog_name)
    return template, photo_jpeg, font, prompt, text, card_meta


def plan_seeds(card: catalog.CardSelector, count: int, rng: random.Random) -> list[int]:
    """`count` 장을 만들 seed 를 **한 번에** 뽑는다 — 서로 다른 seed 가 있는 만큼만.

    이 카드의 검증된 seed(비어 있으면 `catalog.DEFAULT_SEEDS`)가 겹치지 않는 값이 `count` 보다
    적으면 있는 만큼만 돌려준다 — 같은 seed 를 두 번 쓰면(엔진이 결정적이다) 완전히 같은 이미지
    두 장에 돈을 두 번 내는 것이기 때문이다(#572 Task 4 fix round 1 Important 2 — 실측: 4월 풀
    {3,4} 에서 `pick_seeds(4,3,rng)` → `[3,4,3]`, `pick_seeds(4,4,rng)` → `[3,4,3,4]`). 그 경우
    로그로 경고한다 — 나중에 그 카드의 seed 를 더 채우는 일의 단서가 되도록.

    `count == 1` 이면 그대로 `catalog.pick_seeds(card, 1, rng)` 를 부른다 — 풀이 하나뿐이어도
    한 장은 항상 만들 수 있다."""
    pool = list(dict.fromkeys(catalog.resolve(card).seeds or catalog.DEFAULT_SEEDS))  # 순서를 지키며 중복만 제거
    effective = min(count, len(pool))
    if effective < count:
        log.warning(
            "cardimage card %s has only %d distinct seed(s) — requested %d card(s), making %d instead",
            card, len(pool), count, effective,
        )
    return catalog.pick_seeds(card, effective, rng)


def generate_cards(*, count: int, photo: bytes, content_type: str, card: catalog.CardSelector, dog_name: str,
                   engine: CardImageEngine, judge: CardJudge | None, base_dir: Path,
                   open_months: frozenset[int], judge_min: int,
                   rng: random.Random | None = None, seed: int | None = None) -> list[GeneratedCard]:
    """사진 한 장으로 카드 `count` 장을 **순차로** 만든다. `card` 는 달 정수 또는 종류 문자열이다.

    한 번에 여러 장(`num_images_per_prompt`)은 L4 에서 2장부터 CUDA OOM 이다 (#557 E2 실측).
    장마다 다른 seed 를 쓰고, 한 장이 실패해도 나머지는 돌려준다 — 고를 게 하나라도 남는 편이 낫다.
    전부 실패하면 마지막 예외를 올린다.

    `seed` 를 주면(비교 도구가 특정 seed 를 재도록 쓴다, #572 fix round 1 F1) 카드 한 장을 그
    값 그대로, 재시도 없이 만든다 — `generate_card` 가 이 경로로 위임한다. **`count` 가 1이
    아니면 `ValueError` 를 올린다** — seed 하나로 여러 장을 만들면 전부 똑같은 이미지가 되는데,
    그것을 조용히 한 장으로 줄여 버리면 호출자가 부탁한 것과 다른 결과를 아무 신호 없이 받는다
    (#572 Task 4 fix round 1 controller ruling C — 이 계열 결함을 세 번째로 봐주지 않는다).

    `seed` 가 없고 `count == 1` 이면(관리자 콘솔 등 카드가 한 장뿐인 경로) 옛 `generate_card` 와
    똑같이 동작한다: `rng` 로 **서로 다른 두 seed**를 미리 뽑아 두고, 첫 시도의 검수 점수가
    `judge_min` 미만이면 두 번째 seed 로 한 번 더 만들어 보고 둘 중 나은 쪽을 쓴다.

    `count > 1` 이면(앱 경로, #572 Task 4) 재시도를 하지 않는다 — 카드 하나마다 재시도까지
    넣으면 최악 2×`count` 번 돈이 나가는데, 이미 고를 카드를 여러 장 만드는 것 자체가 재시도의
    대안이기 때문이다. 대신 `plan_seeds(card, count, rng)` 를 **한 번**만 불러 서로 다른 seed
    를 미리 뽑는다 — 카드마다 따로 뽑으면(`rng` 상태가 이어지므로) 검증된 seed 가 둘뿐인
    카드에서 서로 다른 장이 같은 seed 를 뽑을 수 있다(실측: 4월 풀 {3,4}, 두 카드를 각각
    `pick_seeds(4, 2, rng)` 로 뽑으면 rng 값에 따라 둘 다 첫 seed 가 3이 될 수 있다). 그 카드의
    겹치지 않는 seed 가 `count` 보다 적으면 `plan_seeds` 가 있는 만큼만 돌려주므로, 실제로
    만드는 카드 수가 `count` 보다 **적을 수 있다**(#572 Task 4 fix round 1 Important 2 — 같은
    seed 로 두 번 만들면 완전히 같은 이미지 두 장에 돈을 두 번 낸다)."""
    template, photo_jpeg, font, prompt, text, card_meta = _setup(
        card=card, dog_name=dog_name, photo=photo, content_type=content_type,
        base_dir=base_dir, open_months=open_months,
    )
    plate = card_meta.plate
    # 달이 아닌 카드는 `month` 가 0 이다 — 무엇을 만들었는지는 `card_key` 가 갖는다.
    common = {"month": card_meta.month, "card_key": catalog.card_key(card), "title": text}

    if seed is not None:
        if count != 1:
            raise ValueError(f"seed 를 지정했으면 count 는 1이어야 합니다 (count={count})")
        # 호출자가 seed 를 못박았다 — pick_seeds 를 아예 부르지 않고 그 값 그대로, 한 번만.
        png1, j1 = _attempt(engine, judge, template=template, photo_jpeg=photo_jpeg, prompt=prompt, text=text,
                            font=font, plate=plate, seed=seed)
        return [GeneratedCard(png=png1, judge=j1, attempts=1, seed=seed, **common)]

    rng = rng or random.Random()

    if count == 1:
        seed1, seed2 = catalog.pick_seeds(card, 2, rng)
        png1, j1 = _attempt(engine, judge, template=template, photo_jpeg=photo_jpeg, prompt=prompt, text=text,
                            font=font, plate=plate, seed=seed1)
        if j1 is None or j1.likeness >= judge_min:
            return [GeneratedCard(png=png1, judge=j1, attempts=1, seed=seed1, **common)]
        png2, j2 = _attempt(engine, judge, template=template, photo_jpeg=photo_jpeg, prompt=prompt, text=text,
                            font=font, plate=plate, seed=seed2)
        if j2 is not None and j2.likeness > j1.likeness:
            return [GeneratedCard(png=png2, judge=j2, attempts=2, seed=seed2, **common)]
        return [GeneratedCard(png=png1, judge=j1, attempts=2, seed=seed1, **common)]

    seeds = plan_seeds(card, count, rng)
    cards: list[GeneratedCard] = []
    last_exc: Exception | None = None
    for card_seed in seeds:
        try:
            png, j = _attempt(engine, judge, template=template, photo_jpeg=photo_jpeg, prompt=prompt, text=text,
                              font=font, plate=plate, seed=card_seed)
        except Exception as exc:  # noqa: BLE001 — 한 장 실패해도 나머지는 돌려준다.
            last_exc = exc
            log.warning("cardimage 여러 장 중 한 장 생성 실패, 계속 진행합니다 (seed=%s): %s", card_seed, exc)
            continue
        cards.append(GeneratedCard(png=png, judge=j, attempts=1, seed=card_seed, **common))
    if not cards:
        assert last_exc is not None  # count >= 1 이므로 seeds 도 최소 1개 — 여기 오면 전부 실패한 것이다.
        raise last_exc
    return cards


def generate_card(*, photo: bytes, content_type: str, card: catalog.CardSelector, dog_name: str,
                  engine: CardImageEngine, judge: CardJudge | None, base_dir: Path,
                  open_months: frozenset[int], judge_min: int,
                  rng: random.Random | None = None, seed: int | None = None) -> GeneratedCard:
    """사진 한 장으로 카드 한 장을 만든다. `generate_cards(count=1, ...)` 의 얇은 위임이다 —
    관리자 콘솔(`/admin/cardimage/generate`)이 그대로 쓴다. 동작은 `generate_cards` 의 문서를 본다."""
    return generate_cards(
        count=1, photo=photo, content_type=content_type, card=card, dog_name=dog_name, engine=engine,
        judge=judge, base_dir=base_dir, open_months=open_months, judge_min=judge_min, rng=rng, seed=seed,
    )[0]
