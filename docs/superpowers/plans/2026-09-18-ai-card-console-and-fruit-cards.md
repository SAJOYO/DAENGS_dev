# 콘솔 카드 테스트·과일 채소 카드 구현 계획 (#592)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 콘솔에서 1~12월과 딸기·상추 카드를 엔진을 골라 뽑고, 그 결과를 콘솔 전용 표에 저장해 다시 보고 지울 수 있게 한다. 같은 PR 에서 제목이 제목판에 들어가게 고친다.

**Architecture:** `daengs_cardimage` 의 카드 키를 달 정수에서 `int | str`(종류)로 넓히고, 얼굴만 보이는 틀을 위한 프롬프트 갈래(`face_only`)를 더한다. 콘솔은 앱 경로(`ai_cards`·한도·큐)를 건드리지 않고 새 표 `admin_ai_cards` 와 새 엔드포인트를 쓴다. 제목 축소는 판정 기준을 바로잡고 장평(가로 축소)을 더한다.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 async · Pillow 12.3 · Next.js 16 (App Router, TypeScript) · Postgres

**Spec:** `docs/superpowers/specs/2026-09-18-ai-card-console-and-fruit-cards-design.md`

## Global Constraints

- 브랜치는 **`fix/ai-card-title-condense`** (PR #592). 브랜치를 새로 파지 않는다. **push 는 컨트롤러가 한다 — 구현자는 커밋까지만.**
- 커밋 메시지 트레일러는 **정확히** `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` — 자기 모델 이름으로 바꾸지 않는다.
- `git add -A` 금지. 그 태스크가 만든 파일만 `git add <경로>`.
- 파일 읽기·쓰기·수정은 Read/Edit/Write 도구로 한다(heredoc·sed 금지). 검색은 Grep/Glob.
- Python 은 `backend/` 에서 `uv run --no-sync ...`(테스트·ruff)로 돌린다. 한글 출력이 필요하면 `PYTHONUTF8=1`.
- 태스크 끝에 **`uv run --no-sync ruff check <건드린 파일>`** 과 그 태스크의 테스트가 통과해야 한다. 커밋 전에 `uv run check`(3초).
- 코드 식별자에 `neo`/`NEO` 를 쓰지 않는다. 데이터 값(배지 문자열 `NEO-S0824`)은 예외다.
- 주석·문서는 한국어, 기존 파일의 말투를 따른다. 프롬프트 문자열은 영어.
- **하지 않는 것:** `dev` 머지, DB 마이그레이션을 서버에 적용, 운영 설정 변경, GPU(FLUX.2-klein-4B) 호출.
- 유료 호출은 Task 7 에서만. Nano Banana 2 **최대 10회**(이 밤 전체 상한), 같은 원인으로 두 번 실패하면 중단하고 보고.
- 이미 끝난 것(고치지 말 것): 10월 `face_hidden` 프롬프트(`4aab9373`), 10월 seed 주석(`09c931b8`), 딸기·상추 틀 webp 와 `backend/tools/cardimage_fruit_templates.py`(`0284c41f`).

## 파일 구조

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `backend/src/daengs_cardimage/catalog.py` | 카드 정의(달 12 + 종류 2), `resolve`, 틀 경로 | 1 |
| `backend/src/daengs_cardimage/engine.py` | 프롬프트 갈래(`face_only`) | 1 |
| `backend/src/daengs_cardimage/generate.py` | `card: int \| str` 로 생성 | 1 |
| `backend/src/daengs_cardimage/title.py` | 제목 축소 판정·장평 | 2 |
| `backend/src/daengs_backend/services/ai_card_engine.py` | 카드 키 전달, `ready_check` | 1·5 |
| `db/init/41_admin_ai_cards.sql` · `db/migrations/2026-09-18_admin_ai_cards.sql` · `verify_…` | 콘솔 표 스키마 | 3 |
| `backend/src/daengs_backend/models/admin_ai_card.py` | 그 표의 모델 | 3 |
| `backend/src/daengs_backend/repositories/admin_ai_card.py` | 그 표 DAO | 4 |
| `backend/src/daengs_backend/services/admin_card_store.py` | 저장·조회·삭제(스토리지 포함) | 4 |
| `backend/src/daengs_backend/routers/admin_cardimage.py` · `schemas/cardimage.py` | 콘솔 API | 5 |
| `frontend/app/components/cardimage-inspect.tsx` | 콘솔 화면 | 6 |
| `docs/cardimage/README.md` · `worklog.md` · `roadmap.md` | 문서 | 7 |

---

### Task 1: 카드 키를 넓히고 딸기·상추를 넣는다

**Files:**
- Modify: `backend/src/daengs_cardimage/catalog.py`, `engine.py`, `generate.py`, `__init__.py`
- Modify: `backend/src/daengs_backend/services/ai_card_engine.py`
- Test: `backend/tests/test_cardimage_catalog.py`, `test_cardimage_engine.py`, `test_cardimage_generate.py`

**Interfaces:**
- Produces: `catalog.CardSelector = int | str`; `catalog.resolve(selector: CardSelector) -> MonthCard`(없으면 `MonthNotOpenError`); `catalog.KINDS: tuple[str, ...] = ("strawberry", "lettuce")`; `MonthCard.face_only: bool`, `MonthCard.kind: str`; `catalog.template_path(selector, base)`; `catalog.card_key(selector) -> str`("4" · "strawberry"); `engine.build_prompt(..., face_only: bool = False)`; `generate.generate_cards(*, card: CardSelector, ...)`, `generate.generate_card(*, card: CardSelector, ...)`, `GeneratedCard.card_key: str`(`month` 는 달이 아니면 0); `ai_card_engine.generate(*, card: CardSelector, ...)`, `ai_card_engine.ready_check(card: CardSelector)`.
- Consumes: 없음.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_cardimage_catalog.py` 에 추가:

```python
def test_resolve_accepts_month_and_kind():
    assert catalog.resolve(4).card_name == "BLOSSOM"
    assert catalog.resolve("strawberry").card_name == "BERRY"
    assert catalog.resolve("lettuce").card_name == "LETTUCE"
    with pytest.raises(catalog.MonthNotOpenError):
        catalog.resolve("banana")
    with pytest.raises(catalog.MonthNotOpenError):
        catalog.resolve(13)


def test_kind_cards_are_face_only_with_their_own_template_and_plate():
    base = Path(__file__).resolve().parents[2] / "cardimage"
    for kind, name, badge in (("strawberry", "BERRY", "NEO-S0824"), ("lettuce", "LETTUCE", "NEO-0824")):
        c = catalog.resolve(kind)
        assert c.kind == kind and c.month == 0 and c.face_only and not c.face_hidden
        assert c.card_name == name and c.badge == badge and c.scene.strip() and c.subtitle.strip()
        assert catalog.template_path(kind, base).name == f"{kind}_template.webp"
        assert catalog.template_path(kind, base).exists()
    assert catalog.resolve("strawberry").plate == catalog.STRAWBERRY_PLATE
    assert catalog.resolve("lettuce").plate == catalog.LETTUCE_PLATE


def test_card_key_strings():
    assert catalog.card_key(4) == "4" and catalog.card_key("lettuce") == "lettuce"
```

`backend/tests/test_cardimage_engine.py` 에 추가:

```python
def test_face_only_prompt_replaces_face_and_keeps_the_fruit_body():
    p = engine.build_prompt(scene="the leaf parachute", badge="NEO-S0824", subtitle="FRUIT DOG",
                            outfit="", face_only=True)
    assert "in the main illustration is replaced by the dog from image 2" not in p
    assert "only the dog's face is visible" in p and "do not draw the dog's body" in p
    assert "the leaf parachute" in p and "NEO-S0824" in p and "FRUIT DOG" in p
    assert "no collar" in p
```

`backend/tests/test_cardimage_generate.py` 에 추가:

```python
def test_strawberry_uses_its_template_and_face_only_prompt():
    eng, jd = FakeEngine(), FakeJudge([5])
    out = generate.generate_card(
        photo=_photo(), content_type="image/jpeg", card="strawberry", dog_name="네오",
        engine=eng, judge=jd, base_dir=CARDIMAGE, open_months=frozenset({4}), judge_min=3,
    )
    assert out.card_key == "strawberry" and out.month == 0 and out.title == "BERRY 네오"
    prompt = eng.calls[0]["prompt"]
    assert "only the dog's face is visible" in prompt and "NEO-S0824" in prompt
    sent = Image.open(io.BytesIO(eng.calls[0]["template"]))
    ref = Image.open(CARDIMAGE / "strawberry_template.webp").convert("RGB")
    assert sent.size == ref.size == (994, 1582)


def test_kind_card_ignores_open_months():
    """종류 카드는 콘솔 전용이라 DAENGS_CARDIMAGE_MONTHS 로 잠그지 않는다."""
    eng = FakeEngine()
    generate.generate_card(photo=_photo(), content_type="image/jpeg", card="lettuce", dog_name="네오",
                           engine=eng, judge=None, base_dir=CARDIMAGE, open_months=frozenset(), judge_min=3)
    assert eng.calls
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `cd backend && uv run --no-sync pytest -q tests/test_cardimage_catalog.py tests/test_cardimage_engine.py tests/test_cardimage_generate.py`
Expected: 새 테스트 5개가 FAIL (`AttributeError: resolve`, `TypeError: unexpected keyword 'card'` 등)

- [ ] **Step 3: `catalog.py` 에 종류 카드를 넣는다**

`MonthCard` 에 필드 둘을 더한다(기존 필드 순서는 그대로, 새 필드는 맨 뒤에 기본값과 함께):

```python
    #: 본문에 강아지 **얼굴만** 보이는 틀(딸기·상추). 10월 `face_hidden` 의 반대다 — `engine.build_prompt` 가 앞부분을 가른다.
    face_only: bool = False
    #: 달이 아닌 카드의 키("strawberry"·"lettuce"). 달 카드는 빈 문자열이고 `month` 로 식별한다.
    kind: str = ""
```

제목판 상수(09-18 실측, `docs/cardimage/worklog.md` 참고)와 카드 둘을 `_CARDS` 아래에 추가한다:

```python
#: 딸기 틀의 제목판 — 09-18 실측(판 y 59~152). `top_y` 는 측정값 59 가 아니라 62 다:
#: 윗선을 다시 재는 네 열의 중앙값이 62 라, 59 로 두면 제목이 3px 내려간다(사용자 결정 09-18).
STRAWBERRY_PLATE = Plate(center_y=105, edge=((62, 749), (149, 676)), top_y=62)

#: 상추 틀의 제목판 — 09-18 실측(판 y 47~141). 달 카드보다 넓다.
LETTUCE_PLATE = Plate(center_y=94, edge=((50, 790), (138, 717)), top_y=47)

#: 얼굴만 보이는 틀의 소품 금지 문장 — 몸이 없으니 "아무것도 안 입는다"(NO_OUTFIT)가 맞지 않는다.
FACE_ONLY_NO_ACCESSORY = (
    "Do not carry over any accessories from image 2 — no collar, no leash, no harness, no clothing."
)

#: 달이 아닌 카드(콘솔 전용, #592). 앱 경로는 이것을 쓰지 않는다.
_KIND_CARDS: dict[str, MonthCard] = {
    "strawberry": MonthCard(
        0, "strawberry", "BERRY", "NEO-S0824",
        "the giant leaf parachute with its golden rigging lines, the heart-shaped strawberry body with its seeds "
        "and the round hole in its middle, the pink and golden motion streaks, the floating golden seeds, the "
        "pastel blue-violet starry sky and the pink clouds",
        "FRUIT DOG",
        FACE_ONLY_NO_ACCESSORY,
        STRAWBERRY_PLATE,
        face_only=True,
        kind="strawberry",
    ),
    "lettuce": MonthCard(
        0, "lettuce", "LETTUCE", "NEO-0824",
        "the ruffled lettuce leaves with water droplets that form the body, the two crossed lettuce stems below, "
        "the loose leaves floating around, the radiating rainbow holographic rays and the soft reflective floor",
        "VEGGIE DOG",
        FACE_ONLY_NO_ACCESSORY,
        LETTUCE_PLATE,
        face_only=True,
        kind="lettuce",
    ),
}

KINDS: tuple[str, ...] = tuple(_KIND_CARDS)

CardSelector = int | str
```

`get`/`template_path` 아래에 더한다:

```python
def resolve(selector: CardSelector) -> MonthCard:
    """달 정수 또는 종류 문자열로 카드 정의를 찾는다. 없으면 `MonthNotOpenError`.

    잠금(`DAENGS_CARDIMAGE_MONTHS`)은 보지 않는다 — 그건 앱 경로의 `require_open` 몫이다."""
    if isinstance(selector, bool):  # bool 은 int 의 하위형이다 — 달로 오인하지 않게 먼저 막는다
        raise MonthNotOpenError(f"card {selector!r} is not a card")
    if isinstance(selector, int):
        card = _CARDS.get(selector)
    else:
        card = _KIND_CARDS.get(selector)
    if card is None:
        raise MonthNotOpenError(f"card {selector!r} is not a card")
    return card


def card_key(selector: CardSelector) -> str:
    """저장·로그에 쓰는 문자열 키 — 달은 `"4"`, 종류는 `"strawberry"`."""
    return str(selector)
```

`template_path` 는 `get(month)` 대신 `resolve(selector)` 를 쓰도록 바꾼다(시그니처는 `template_path(selector: CardSelector, base: Path)`). `pick_seeds` 도 `get(month)` → `resolve(selector)` 로 바꾸고 인자 이름을 `selector` 로 바꾼다(로그 문구는 `card %s` 로).

- [ ] **Step 4: `engine.build_prompt` 에 `face_only` 갈래를 넣는다**

시그니처에 `face_only: bool = False` 를 더하고, `face_hidden` 분기 앞에 넣는다:

```python
    if face_only:
        lead = (
            "In image 1 only the dog's face is visible, framed by the hole in the produce body; the dog has no "
            "visible body, legs or paws there. Edit image 1 so that this visible face becomes the face of the dog "
            "from image 2 — same breed, same fur color, fur length and texture, same ear shape and color, same "
            "muzzle length, same eye color and facial markings, with the same happy open-mouth expression and the "
            "same head angle and size as in image 1. Do not draw the dog's body, legs, paws or tail anywhere. Keep "
            "the produce body, its hole and everything attached to it exactly as in image 1. Also replace the small "
            "circular portrait in the top-left badge with the face of the same dog from image 2."
        )
    elif face_hidden:
        ...
```

세 갈래가 배타임을 문서화한다(docstring 한 줄). `face_only` 는 `outfit`(=`FACE_ONLY_NO_ACCESSORY`)을 지금처럼 뒤에 이어 붙인다.

- [ ] **Step 5: `generate.py` 를 카드 키로 바꾼다**

- `_load_template(card, base_dir)` · `_setup(card=..., ...)` · `plan_seeds(card, count, rng)` · `generate_cards(*, card: CardSelector, ...)` · `generate_card(*, card: CardSelector, ...)` 로 인자 이름을 바꾼다(`month` 라는 이름을 남기지 않는다).
- `_setup` 안에서 잠금은 **달일 때만** 본다:

```python
    card_meta = catalog.require_open(card, open_months) if isinstance(card, int) else catalog.resolve(card)
```

- `build_prompt(...)` 에 `face_hidden=card_meta.face_hidden, face_only=card_meta.face_only` 를 넘긴다.
- `GeneratedCard` 는 `month: int` 를 유지하되(달이 아니면 0) `card_key: str` 을 더한다. 값은 `card_meta.month` 와 `catalog.card_key(card)`.
- `daengs_cardimage/__init__.py` 의 재수출 목록에 바뀐 이름이 맞는지 확인한다.

- [ ] **Step 6: `ai_card_engine.py` 호출부를 맞춘다**

- `generate(*, card: CardSelector, ...)`, `plan_seeds(card, ...)`, `plan_request_seeds(card, ...)`, `ready_check(card: CardSelector)` 로 바꾼다.
- `ready_check` 는 달이면 지금처럼 `catalog.require_open(card, settings.cardimage_months)`, 종류면 `catalog.resolve(card)` 를 쓰고, 키·틀·글꼴 검사는 그대로 둔다.
- 호출부를 전부 고친다: `grep -rn "month=month\|ready_check(\|plan_request_seeds(\|plan_seeds(" backend/src backend/tests backend/tools`. 앱 경로(`services/ai_card.py`, `routers/ai_card.py`)는 **달 정수를 그대로 넘긴다** — 앱 동작은 바뀌지 않는다.

- [ ] **Step 7: 테스트를 돌려 통과시킨다**

Run: `cd backend && uv run --no-sync pytest -q tests/test_cardimage_catalog.py tests/test_cardimage_engine.py tests/test_cardimage_generate.py tests/test_ai_card_engine.py tests/test_cardimage_admin_api.py tests/test_ai_cards_api.py`
Expected: 전부 PASS. 이름이 바뀐 인자 때문에 기존 테스트가 깨지면 **테스트를 새 이름으로 고친다**(동작 기대값은 바꾸지 않는다).

- [ ] **Step 8: 달 카드 프롬프트 회귀를 확인한다**

`backend/tests/test_cardimage_engine.py` 에 추가하고 통과시킨다:

```python
def test_month_prompts_are_unchanged_by_the_new_branches():
    """달 12장은 face_only/face_hidden 도입 전과 글자까지 같아야 한다(10월만 09-18 에 의도적으로 바뀜)."""
    from daengs_cardimage import catalog
    for m in range(1, 13):
        c = catalog.resolve(m)
        p = engine.build_prompt(scene=c.scene, badge=c.badge, subtitle=c.subtitle, outfit=c.outfit,
                                face_hidden=c.face_hidden, face_only=c.face_only)
        assert ("do not draw the dog's face" in p) == (m == 10)
        assert "only the dog's face is visible" not in p
```

- [ ] **Step 9: ruff·check 후 커밋**

```bash
cd backend && uv run --no-sync ruff check src/daengs_cardimage src/daengs_backend/services/ai_card_engine.py tests && uv run check
```

커밋 메시지(파일로 작성해 `git commit -F`):

```
feat: 카드 키를 달 정수에서 종류까지 넓히고 딸기·상추를 넣는다

콘솔 전용 카드 둘(BERRY·LETTUCE)을 catalog 에 넣고, 얼굴만 보이는 틀을 위한
face_only 프롬프트 갈래를 더한다. 달 12장 프롬프트는 그대로다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

---

### Task 2: 제목이 제목판에 들어가게 — 앞말·판정·장평

**Files:**
- Modify: `backend/src/daengs_cardimage/title.py`, `catalog.py`(앞말 3개)
- Test: `backend/tests/test_cardimage_title.py`

**Interfaces:**
- Consumes: Task 1 의 `catalog.resolve`.
- Produces: `title.MIN_CAP = 20`, `title.CONDENSE_MIN = 0.70`, `title.draw_title(...)` 동작 변경(외부 시그니처는 그대로).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_cardimage_title.py` 에 추가:

```python
def test_short_name_is_not_condensed_and_fills_more_of_the_plate():
    """흔한 이름은 장평 100% 그대로 — 예전과 같은 모양이어야 한다."""
    tpl = _template()
    out = title.draw_title(tpl, "BLOSSOM 네오", FONT)
    assert out.size == (994, 1582)


def test_long_name_never_covers_the_badge_for_every_card():
    """12달 + 딸기·상추에서, 20+70% 안에 들어가는 긴 이름은 사선 경계를 넘지 않는다."""
    from daengs_cardimage import catalog
    base = CARDIMAGE
    for selector in list(range(1, 13)) + list(catalog.KINDS):
        c = catalog.resolve(selector)
        tpl = Image.open(catalog.template_path(selector, base)).convert("RGB")
        text = title.title_text(c.card_name, "BUTTERCUP")
        out = title.draw_title(tpl, text, FONT, plate=c.plate)
        a, ref = np.asarray(out).astype(int), np.asarray(tpl).astype(int)
        band = slice(c.plate.top_y, c.plate.top_y + 95)
        bright = (a[band, 250:900].min(axis=2) > 200) & (ref[band, 250:900].min(axis=2) <= 200)
        cols = np.where(bright.any(axis=0))[0] + 250
        limit = title._plate_right_edge(c.plate.center_y, c.plate.edge)
        assert cols.max() <= limit, (selector, cols.max(), limit)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `cd backend && uv run --no-sync pytest -q tests/test_cardimage_title.py`
Expected: `test_long_name_never_covers_the_badge_for_every_card` 가 FAIL(1·3·5월·딸기에서 배지를 덮는다).

- [ ] **Step 3: 판정을 바로잡고 장평을 넣는다**

`title.py` 를 이렇게 고친다.

1. 상수: `CONDENSE_MIN = 0.70`(가로 축소 하한), `CONDENSE_CAP = 26`(여기까지 줄인 뒤 장평을 쓴다).
2. `_layout` 의 한계 계산을 **글자 잉크 맨 아래**에서 잰다. 지금은 `_plate_right_edge(baseline + 10, ...)` 인데, 이것은 09-14 에 4월(사선이 완만) 기준으로 잡은 값이라 사선이 가파른 달에서 필요 이상 깎는다. 새 규칙:

```python
def _fits(text: str, font: ImageFont.FreeTypeFont, plate: Plate, cap: float, ratio: float = 1.0) -> tuple[bool, float]:
    """(들어가나, 잉크 오른끝) — 사선은 **잉크 맨 아래 높이**에서 재고 여백은 RIGHT_MARGIN 하나뿐이다."""
    ascent, _ = font.getmetrics()
    baseline = plate.center_y + cap / 2
    y = baseline - ascent
    _, t, r, b = font.getbbox(text)
    y += plate.center_y - (y + t + y + b) / 2
    bottom = y + b + STROKE_W
    right = plate.left_x + (r + STROKE_W) * ratio
    return right <= _plate_right_edge(bottom, plate.edge) - RIGHT_MARGIN, right
```

3. 크기는 1px 계단 대신 **소수 대문자 높이**로 고른다(Pillow 12.3 은 소수 글꼴 크기를 받는다). 순서는 사용자 결정 그대로:
   - ① `CAP_HEIGHT`(48) 에서 `CONDENSE_CAP`(26)까지 이분 탐색으로 가장 큰 크기를 찾는다. 들어가면 장평 100% 로 끝.
   - ② 26 에서도 넘치면 장평을 1.0 → `CONDENSE_MIN`(0.70)까지 이분 탐색.
   - ③ 26·70% 로도 넘치면 대문자 높이를 26 → `MIN_CAP`(20)까지 더 줄인다(장평 0.70 유지).
   - ④ 20·70% 로도 넘치면 **그대로 그린다**(사용자 결정 09-18 — 더 줄이지 않는다).
4. `_Run` 에 `ratio: float = 1.0` 을 더하고, `draw_title` 은 `ratio < 1.0` 일 때 **넓은 캔버스**(폭 ×3)에 글자·외곽선·그림자·그라데이션을 그린 뒤 `left_x` 기준으로 가로만 `Image.LANCZOS` 로 줄여 카드에 합성한다. 좁은 캔버스에 그리면 줄이기 전에 오른쪽이 잘린다(09-18 실측: 높이 32 에서 글자 폭이 약 900px).
5. `plate_shift`·`_plate_shift`·`title_text`·`Plate` 는 그대로 둔다.

- [ ] **Step 4: 1·3·5월 앞말을 바꾼다**

`catalog.py` 의 `card_name` 세 개만 바꾸고 각각 한 줄 주석을 단다(9월 `CHUSEOK` 과 같은 처리):

```python
        1, "1_new_year", "SEBAE", "26JAN",      # 원본 제목은 NEW YEAR — 앞말이 판을 거의 채워 사용자가 SEBAE 로 (09-18)
        3, "3_first_day", "SCHOOL", "26MAR",    # 원본 제목은 FIRST DAY — 같은 이유로 SCHOOL (09-18)
        5, "5_home_team", "HOME", "26MAY",      # 원본 제목은 HOME TEAM — 앞말만으로 판을 넘겨 HOME (09-18)
```

- [ ] **Step 5: 테스트를 돌린다**

Run: `cd backend && uv run --no-sync pytest -q tests/test_cardimage_title.py tests/test_cardimage_catalog.py tests/test_cardimage_generate.py`
Expected: 전부 PASS. 기존 `test_long_name_stays_left_of_badge_boundary`(`758 + 20` 기준)는 새 규칙에 맞게 고친다 — 사선 안이면 통과.

- [ ] **Step 6: 눈으로 볼 격자를 만든다**

`cardimage/out/_title_check/` 에 14장(12달 + 딸기·상추) × 이름 3종(`MOMO` · `보리` · `PRINCESS BUTTERCUP`)의 상단 띠 격자를 만든다(파일 하나당 ≤1400×900, 여러 장으로 나눈다). 스크립트는 그 폴더에 둔다(미추적). 각 장에 대문자 높이와 장평을 적는다. 보고에 경로를 남긴다.

- [ ] **Step 7: ruff·check 후 커밋**

```
fix: 제목이 제목판 안에 들어가게 — 판정 기준·장평·1·3·5월 앞말

사선을 잉크 맨 아래에서 재고 소수 크기로 줄인다. 26 에서 넘치면 장평 70% 까지,
그래도 넘치면 높이 20 까지 줄이고 그 뒤로는 그대로 둔다(사용자 결정 09-18).
1·3·5월 앞말을 SEBAE·SCHOOL·HOME 으로 줄인다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

---

### Task 3: 콘솔 표 스키마와 모델

**Files:**
- Create: `db/init/41_admin_ai_cards.sql`, `db/migrations/2026-09-18_admin_ai_cards.sql`, `db/migrations/verify_2026-09-18_admin_ai_cards.sql`, `backend/src/daengs_backend/models/admin_ai_card.py`
- Test: `backend/tests/test_admin_ai_card_model.py`

**Interfaces:**
- Produces: 표 `admin_ai_cards`(spec ⑤ 그대로), 모델 `AdminAiCard`.

- [ ] **Step 1: 기존 짝을 읽는다**

`db/init/38_ai_cards.sql`, `db/migrations/2026-09-14_ai_cards.sql`, `db/migrations/verify_2026-09-14_ai_cards.sql`, `tools/check_migration_verification.py`, `backend/src/daengs_backend/models/ai_card.py` 를 먼저 읽고 **같은 관례**(주석 머리말, `IF NOT EXISTS`, verify 의 실패 조건 출력 방식)를 따른다.

- [ ] **Step 2: 실패하는 모델 테스트를 쓴다**

```python
def test_admin_ai_card_table_matches_sql():
    from daengs_backend.models.admin_ai_card import AdminAiCard
    t = AdminAiCard.__table__
    assert t.name == "admin_ai_cards"
    assert {c.name for c in t.columns} == {
        "id", "admin_user_id", "card_key", "dog_name", "title", "engine", "seed", "attempts",
        "likeness", "judge_note", "storage_key", "size_bytes", "width", "height", "elapsed_ms", "created_at",
    }
    assert not t.c.storage_key.nullable and not t.c.admin_user_id.nullable
```

- [ ] **Step 3: SQL 셋을 쓴다**

`db/init/41_admin_ai_cards.sql` 은 spec ⑤ 의 DDL 그대로. 여기에 CHECK 를 더한다:
`engine IN ('gemini','cardgen')`, `length(btrim(card_key)) > 0`, `likeness IS NULL OR likeness BETWEEN 1 AND 5`, `size_bytes > 0`.
`db/migrations/2026-09-18_admin_ai_cards.sql` 은 같은 DDL 을 `IF NOT EXISTS` 로 감싸 **여러 번 돌려도 안전하게**.
`verify_2026-09-18_admin_ai_cards.sql` 은 표·칸·인덱스·CHECK 존재를 확인하고 없으면 `RAISE EXCEPTION`.

- [ ] **Step 4: 모델을 쓴다**

`models/ai_card.py` 의 관례를 따르되 CHECK 는 SQL 과 같은 이름으로. `admin_user_id` 는 `ForeignKey("admin_users.id", ondelete="CASCADE")`.

- [ ] **Step 5: 검사와 커밋**

Run: `cd backend && uv run --no-sync pytest -q tests/test_admin_ai_card_model.py && uv run check`
(`uv run check` 가 마이그레이션 짝·이름을 본다. 실패하면 파일 이름을 규칙에 맞춘다.)

커밋: `feat: 콘솔 카드 저장 표(admin_ai_cards) 스키마와 모델`

---

### Task 4: 저장·조회·삭제 서비스

**Files:**
- Create: `backend/src/daengs_backend/repositories/admin_ai_card.py`, `backend/src/daengs_backend/services/admin_card_store.py`
- Modify: `backend/src/daengs_backend/core/storage.py`(키 빌더 한 줄)
- Test: `backend/tests/test_admin_card_store.py`

**Interfaces:**
- Consumes: Task 3 의 `AdminAiCard`; Task 1 의 `catalog.card_key`.
- Produces:
  - `core/storage.build_admin_ai_card_key(admin_user_id: uuid.UUID, card_id: uuid.UUID) -> str` → `admin-ai-cards/{admin_user_id}/{card_id}.png`
  - `admin_card_store.save(session, *, admin_user_id, card_key, dog_name, title, engine, seed, attempts, judge, png, elapsed_ms) -> AdminAiCard | None`(저장소 미설정이면 `None`)
  - `admin_card_store.recent(session, limit: int = 50) -> list[AdminAiCard]`
  - `admin_card_store.load_png(session, card_id) -> tuple[AdminAiCard, bytes] | None`
  - `admin_card_store.remove(session, card_id) -> bool`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_admin_card_store.py` — 기존 테스트가 DB 를 어떻게 다루는지 먼저 본다(`backend/tests/fakes.py` 의 세션·스토리지 가짜, `test_ai_card_service.py`). 같은 가짜를 재사용해:

- 저장하면 행이 생기고 `storage_key` 가 `admin-ai-cards/<admin>/<id>.png` 이며 PNG 가 저장소에 쓰인다
- `GAIT_STORAGE=none`(`NotConfiguredStorage`)이면 `save` 가 `None` 을 돌려주고 행도 안 생긴다
- `recent` 가 만든 사람과 무관하게 최신순으로 준다
- `load_png` 가 행+바이트를, 없는 id 면 `None` 을
- `remove` 가 행과 저장 객체를 지우고 두 번째 호출은 `False`

- [ ] **Step 2: 실패 확인 → Step 3: 구현 → Step 4: 통과 확인**

`services/ai_card.py` 의 `_store_png`(스토리지 종류별 분기)와 같은 방식을 쓴다. 이 서비스는 **한도·세마포어·큐를 쓰지 않는다.** `save` 는 저장 실패(`StorageNotConfiguredError`)를 예외로 올리지 말고 `None` 으로 돌려준다 — 라우터가 `stored=false` 로 응답한다.

- [ ] **Step 5: ruff·check 후 커밋**

커밋: `feat: 콘솔 카드 저장·조회·삭제 서비스`

---

### Task 5: 콘솔 API

**Files:**
- Modify: `backend/src/daengs_backend/routers/admin_cardimage.py`, `backend/src/daengs_backend/schemas/cardimage.py`, `backend/src/daengs_backend/services/ai_card_engine.py`
- Test: `backend/tests/test_cardimage_admin_api.py`

**Interfaces:**
- Consumes: Task 1(`catalog`·`ai_card_engine.generate(card=...)`), Task 4(`admin_card_store`).
- Produces: spec ④ 의 다섯 엔드포인트와 `CardImageResponse.{id,card,stored,engine,seed}`, `CardImageOptions{cards:[{key,label}], engines:[{key,label,available,reason}], photo_guidance}`.

- [ ] **Step 1: 실패하는 테스트를 쓴다** (`tests/test_cardimage_admin_api.py` 의 기존 방식(dependency_overrides)을 따른다)

- 권한 없으면 403
- `card=strawberry` 로 생성하면 200 이고 응답의 `card == "strawberry"`, 가짜 엔진이 받은 프롬프트에 `only the dog's face is visible` 이 있다
- `card=13` · `card=banana` → 404 `card_closed`
- `engine=cardgen` 인데 `DAENGS_CARDGEN_URL` 이 비면 503 `cardgen_disabled`
- `engine=cardgen` + `seed=7` 이면 엔진에 seed 7 이 그대로 간다
- `engine=gemini` + `seed=7` → 400 `seed_not_supported`
- 저장소 미설정이면 200 + `stored=false`, 목록은 빈 채로
- 생성 → `GET /cards` 에 보이고 → `GET /cards/{id}/image` 가 `image/png` 바이트를 → `DELETE` 뒤 404
- `GET /options` 가 14개 카드와 엔진 둘, `photo_guidance`(=`catalog.PHOTO_GUIDANCE`)를 준다

- [ ] **Step 2: 실패 확인 → Step 3: 구현**

- `generate` 의 `month: int = 4` 쿼리를 `card: str = "4"` 로 바꾸고, 숫자면 `int(card)` 로 변환해 `catalog.resolve` 로 검증한다(`MonthNotOpenError` → 404 `card_closed`). 옛 `month` 쿼리는 남기지 않는다(콘솔만 쓰는 경로다 — Task 6 이 같이 나간다).
- 엔진 선택: `engine=gemini|cardgen`. `cardgen` 이면 `ai_card_engine.gpu_path_active()` 가 참이어야 하고, 거짓이면 503. 엔진 객체는 `ai_card_engine` 에 `engine_by_name(name) -> CardImageEngine` 를 더해 만든다(`default_engine()` 은 그대로 두고 그것을 재사용).
- 생성 뒤 `admin_card_store.save(...)` 로 저장하고(세션은 `core.database.get_session` 의존성), 결과로 `id`·`stored` 를 응답에 싣는다. 저장 실패는 `log.warning` + `stored=False`.
- 목록·이미지·삭제 엔드포인트를 더한다. 이미지는 `Response(content=png, media_type="image/png")`.

- [ ] **Step 4: 통과 확인 → Step 5: ruff·check 후 커밋**

커밋: `feat: 콘솔에서 카드·엔진을 골라 뽑고 저장한 것을 보고 지운다`

---

### Task 6: 콘솔 화면

**Files:**
- Modify: `frontend/app/components/cardimage-inspect.tsx`, `frontend/app/components/inspect-tabs.tsx`(힌트 문구)
- Test: 수동 — `npm run lint` + 로컬 실행

**Interfaces:**
- Consumes: Task 5 의 엔드포인트.

- [ ] **Step 1: 지금 파일을 읽고 `MONTHS` 하드코딩과 `PHOTO_GUIDANCE` 복제를 `GET /admin/cardimage/options` 응답으로 바꾼다.**
- [ ] **Step 2: 카드 선택(14개) · 엔진 선택(둘, 불가면 비활성 + 이유) · seed 입력(엔진이 `cardgen` 일 때만) 을 넣는다.**
- [ ] **Step 3: 생성 결과 아래에 저장 목록을 만든다** — 만든 사람·카드·엔진·seed·닮음·시각, 미리보기(이미지 엔드포인트를 `apiFetch` 로 받아 `URL.createObjectURL`, 언마운트 때 `revokeObjectURL`), 삭제 버튼(확인 후 DELETE → 목록 갱신).
- [ ] **Step 4: 화면 문구에서 "사진은 저장하지 않습니다"를 실제 동작에 맞게 고친다** — 사진 원본은 저장하지 않고 **만들어진 카드만** 저장한다는 뜻으로.
- [ ] **Step 5: `npm run lint` 통과 → 커밋** (`feat: 콘솔에서 14종 카드와 엔진을 고르고 저장 목록을 본다`)

---

### Task 7: 유료 스모크 · 문서 · PR 본문

**Files:**
- Modify: `docs/cardimage/README.md`, `docs/cardimage/worklog.md`, `docs/cardimage/roadmap.md`
- Test: 전체 `uv run pytest`, 마이그레이션 하네스

- [ ] **Step 1: 전체 테스트**

`Start-Process` 로 백그라운드에서 `uv run pytest` 를 돌리고 로그를 지켜본다(9분, Bash 백그라운드는 10분에 죽는다). 실패는 원인을 확인/추정으로 갈라 적는다.

- [ ] **Step 2: 마이그레이션 하네스**

`docs/ci/README.md` ②(버리는 Postgres + `verify_` 변조 하네스)를 그대로 돌린다. `PYTHONUTF8=1` 을 잊지 않는다(cp949 로 교착한 전례).

- [ ] **Step 3: 유료 스모크 (Nano Banana 2, 최대 4회)**

`cardimage/test/치와와_test1.jpg` 로 딸기·상추 각 2장. 엔진은 `gemini`. 도구는 `backend/tools/cardgen_compare.py --engine gemini`(카드 인자가 달 전용이면 이 태스크에서 `--card` 를 받게 넓힌다). 결과 격자(≤1400×900)를 `cardimage/out/_cardgen/fruit-smoke/` 에 만들고 **눈으로** 본다: 얼굴이 사진 강아지인가 · 딸기·상추 몸통과 구멍이 그대로인가 · 몸·다리가 새로 그려지지 않았나 · 배지 `NEO-…` 와 아래 패널 글씨가 그대로인가 · 제목이 판 안에 들어갔나.

- [ ] **Step 4: 문서**

- `README.md` 「정해진 것」에 콘솔 전용 카드 둘과 저장 표, 엔진 선택을 더한다.
- `worklog.md` 맨 위에 이 세션 절을 쓰고 **첫 소절을 「아침에 볼 것」** 으로: 한 일, 비용(호출 수 × 단가), 잠정 판정, 격자 경로, 사람이 정할 것.
- `roadmap.md` 4번 절을 고친다 — 딸기·상추 틀 채택과 콘솔 경로를 반영하고, 남은 21종과 앱 노출은 열린 항목으로.
- 식별자·숫자는 **grep 해서 확인한 것만** 적는다(지어내지 않는다).

- [ ] **Step 5: 커밋**

커밋: `docs: 콘솔 카드 테스트와 과일·채소 카드 결과를 적는다`

---

## Self-Review

- **spec 대응:** ① Task 1 · ② Task 1 · ③ Task 1(정의)·Task 7(스모크) · ④ Task 5 · ⑤ Task 3·4 · ⑥ Task 6 · 제목 작업 Task 2 · 시험 Task 7. 빠진 절 없음.
- **타입 일관성:** `card`(=`CardSelector`)는 Task 1·5·7 에서 같은 이름, 저장 칸은 `card_key`(문자열)로 Task 1·3·4·5 가 같다. `stored` 플래그는 Task 4(`save` 가 `None`)와 Task 5(응답 필드)에서 짝이 맞는다.
- **미정 없음:** 모든 상수(제목판, 장평 0.70, 높이 26·20, 앞말)가 값으로 적혀 있다.
