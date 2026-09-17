# 도감 카드 12달 열기 + 2장 생성·고르기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 4·9월만 열려 있는 AI 도감 카드를 12달로 넓히고, 달마다 글씨가 안 깨지는 seed 에서 2장을 뽑아 사용자가 고르게 한다.

**Architecture:** 달마다 다른 값(무대 문장·의상 문장·제목판 기하·검증된 seed)은 전부 `daengs_cardimage/catalog.py` 의 `MonthCard` 한 곳에 모은다 — 코드 분기를 늘리지 않고 표만 채운다. 생성은 지금처럼 `generate_card` 한 함수가 하되 **N장 순차**로 넓히고(L4 는 한 번에 2장부터 OOM), 쓴 seed 를 DB 에 남긴다. 한도는 "카드 장수"에서 "뽑기 횟수"로 바꾸고, 사라진 작업 판정 기준에 GPU 콜드 스타트를 넣는다.

**Tech Stack:** Python 3.12 / uv · FastAPI · SQLAlchemy 2.0 async + asyncpg · Pillow · pytest · Next.js 16(콘솔)

**Spec:** PR [#572](https://github.com/SAJOYO/DAENGS_dev/pull/572) 본문 + `docs/cardimage/roadmap.md` 「3번」 + `docs/cardimage/compare-2026-09-16-klein-e1.md` · `compare-2026-09-16-klein-e2-e3.md`

## Global Constraints

- Python 은 **3.12 고정**. 실행은 반드시 `uv run` 을 거친다. 의존성은 `uv add` / `uv remove` 로만 (`pyproject.toml` 직접 수정 금지, `uv.lock` 커밋).
- 기본 DB 는 **Alembic 을 쓰지 않는다.** 스키마 원본은 `db/init/*.sql`, 이미 도는 DB 용 변경은 `db/migrations/` 에 파일로. **버전 테이블이 없으므로 여러 번 돌려도 안전하게** (`IF NOT EXISTS`). `models/` 는 SQL 을 손으로 따라간다.
- 머지 전 게이트는 전부 로컬이다: `uv run check`(3초, 무조건) · `uv run pytest`(약 9분) · `npm run lint`. `db/` 를 건드리면 `docs/ci/README.md` 「마이그레이션 변조 하네스」를 **손으로** 돌린다 — `uv run pytest` 가 조용히 건너뛴다.
  - Windows 에서 그 하네스는 `PYTHONUTF8=1` 없이는 **에러 없이 교착**한다.
- `daengs_cardimage` 는 `daengs_backend`·DB·웹을 **import 하지 않는다.** backend 쪽 접점은 `services/ai_card_engine.py` 한 곳뿐이다.
- 문서·주석에 모델 이름은 **풀네임**으로 (`FLUX.2-klein-4B`, `Nano Banana 2`). 코드 식별자(`klein-4b`, `daengs-cardgen-klein`)는 그대로.
- **합성 방식(art 모드 · 배지 합성)은 2026-09-14 에 두 번 거부됐다. 어떤 태스크에서도 다시 꺼내지 않는다.**
- GPU 를 쓰는 실행은 **돌리기 전에 장수·순서·예상 비용을 사람에게 설명하고 승인받는다.** 실패는 매 시도 원인을 확인/추정으로 갈라 적는다.
- 커밋은 이 계획의 태스크 하나 = 커밋 하나. 브랜치는 `feat/ai-card-multi-generate` (PR #572). ⚠ 그 브랜치가 orca 워크트리 `dev-2` 에 잡혀 있으면 메인 체크아웃에서 체크아웃이 막힌다 — `git branch` 의 `+` 표시를 먼저 본다.

---

## File Structure

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `backend/src/daengs_cardimage/catalog.py` | 달별 표 하나 — 무대·의상·제목판·seed 목록 | 2, 3 |
| `backend/src/daengs_cardimage/title.py` | 제목판 기하·그리기 (변경 없음, 값만 catalog 가 준다) | — |
| `backend/tools/cardimage_plate_probe.py` (신규) | 틀에서 제목판을 재어 `Plate(...)` 한 줄을 찍는다 | 2 |
| `backend/src/daengs_cardimage/generate.py` | 한 장 → **N장 순차**. 장마다 쓴 seed 를 함께 돌려준다 | 3, 4 |
| `backend/src/daengs_cardimage/engine.py` | `CardImageEngine` 프로토콜에 seed 를 얹는다 | 3 |
| `backend/src/daengs_backend/services/ai_card.py` | 백그라운드 생성·저장. 장별 행·진행률 | 4 |
| `backend/src/daengs_backend/services/ai_card_quota.py` | 한도와 사라진 작업 판정 | 5 |
| `backend/src/daengs_backend/models/ai_card.py` | `seed`·`pick_group` 컬럼 | 3, 4 |
| `db/init/*.sql` · `db/migrations/2026-09-16_*.sql` | 위 컬럼의 SQL 원본과 적용본 | 3, 4, 5 |
| `frontend/app/console/search/…` | 콘솔의 사진 안내 문구 | 6 |
| `docs/cardimage/worklog.md` · `roadmap.md` | 기록 | 1, 7 |

---

## Task 1: 착수 기록

**Files:**
- Modify: `docs/cardimage/roadmap.md:12` (한눈에 보기 표 3번 행)
- Modify: PR #572 본문 `## 착수 절차` 체크박스

**Interfaces:**
- Consumes: 없음
- Produces: 없음 (기록만)

- [ ] **Step 1: 브랜치로 이동하고 dev 를 병합한다**

```bash
git branch -a --list "*ai-card-multi-generate*"   # '+' 면 다른 워크트리가 잡고 있다
git fetch origin
git checkout feat/ai-card-multi-generate
git merge origin/dev
```

- [ ] **Step 2: 로드맵 3번 행을 진행 중으로 고친다**

`docs/cardimage/roadmap.md` 한눈에 보기 표의 3번 행을 이렇게 바꾼다:

```markdown
| 3 | #572 12달 열기 + 2장 생성·고르기 | 🟢 진행 중 | ▱▱▱▱▱ | Task 1~7 |
```

- [ ] **Step 3: D 번호를 예약한다**

한도 규칙을 바꾸므로 D-077 을 개정하는 새 번호가 필요하다. 남의 브랜치까지 본다:

```bash
git fetch origin && git log --all --oneline --grep="예약"
```

비어 있는 다음 번호를 `docs/decisions.md` 에 「예약 중」으로 한 줄 남긴다.

- [ ] **Step 4: Project 카드 Status 를 In progress 로**

```bash
gh project item-list 3 --owner SAJOYO --format json | python -c "import sys,json;print([i['id'] for i in json.load(sys.stdin)['items'] if i.get('content',{}).get('number')==572])"
```

나온 item id 로:

```bash
gh project item-edit --id <ITEM_ID> --project-id PVT_kwDOEnOr-c4BhG3g \
  --field-id PVTSSF_lADOEnOr-c4BhG3gzhgD6Wk --single-select-option-id 47fc9ee4
```

- [ ] **Step 5: PR 본문의 `## 착수 절차` 를 `[x]` 로 갱신하고 커밋**

```bash
git add docs/cardimage/roadmap.md docs/decisions.md
git commit -m "chore: 착수 — 12달 열기 + 2장 생성·고르기 (#572)"
git push
gh pr edit 572 --body-file <갱신한 본문 파일>
```

---

## Task 2: 12달 열기 — 무대·의상·제목판

**Files:**
- Create: `backend/tools/cardimage_plate_probe.py`
- Modify: `backend/src/daengs_cardimage/catalog.py:41-70`
- Modify: `backend/src/daengs_backend/config.py:256` (`cardimage_months` 기본값)
- Test: `backend/tests/test_cardimage_catalog.py`
- Test: `backend/tests/test_cardimage_settings.py`

**Interfaces:**
- Consumes: `title.Plate(center_y, edge, top_y, left_x)` · `title.APRIL_PLATE` · `catalog.NO_OUTFIT`
- Produces: `catalog._CARDS` 의 12달 전부가 `scene` 비어 있지 않고 자기 `plate` 를 가진다. Task 3·4 는 `catalog.get(m).plate` 와 `catalog.require_open(m, months)` 를 그대로 쓴다.

**왜 필요한가:** 지금 10달은 `plate` 가 `APRIL_PLATE` 기본값이다. `README.md` 에 "달마다 제목판 오른쪽 끝이 713~769 로 다르다"고 적혀 있으니 **그대로 열면 10달 다 제목이 어긋난다.**

- [ ] **Step 1: 제목판 계측 도구의 실패 테스트를 쓴다**

`backend/tests/test_cardimage_plate_probe.py` (신규):

```python
from pathlib import Path

from PIL import Image

from daengs_cardimage.title import APRIL_PLATE
from tools_shim import plate_probe   # 아래 Step 3 에서 import 경로를 맞춘다

CARDIMAGE = Path(__file__).resolve().parents[2] / "cardimage"


def test_probe_reproduces_measured_april_plate():
    """4월 틀은 이미 손으로 쟀다(center_y=99, top_y=52, edge y65→791). 도구가 그것을 재현해야 한다."""
    img = Image.open(CARDIMAGE / "4_blossom_template.webp").convert("RGB")
    got = plate_probe.measure(img)
    assert abs(got.top_y - APRIL_PLATE.top_y) <= 2
    assert abs(got.center_y - APRIL_PLATE.center_y) <= 2
    assert abs(got.edge[0][1] - 791) <= 6


def test_probe_reports_september_plate_higher_and_narrower_than_april():
    img = Image.open(CARDIMAGE / "9_harvest_moon_template.webp").convert("RGB")
    got = plate_probe.measure(img)
    assert got.center_y < APRIL_PLATE.center_y          # 9월은 11px 위
    assert got.edge[0][1] < APRIL_PLATE.edge[0][1]      # 그리고 약 40px 좁다
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd backend && uv run pytest tests/test_cardimage_plate_probe.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'tools_shim'`

- [ ] **Step 3: 계측 도구를 만든다**

`backend/tools/` 는 단일 파일 스크립트 자리이고 테스트에서 import 하기 어렵다. **계측 로직은 패키지에 두고 도구는 얇게 감싼다.**

`backend/src/daengs_cardimage/plate_probe.py` (신규):

```python
"""틀 이미지에서 검은 제목판을 재어 `title.Plate` 를 만든다.

달마다 판의 높이·위치·오른쪽 경계가 다르다(09-14 실측: 4월 y 53~145 · 9월 y 40~135).
손으로 재면 9월처럼 두 번 틀리므로 도구로 재고 눈으로 확인한다.
"""

from __future__ import annotations

from PIL import Image

from daengs_cardimage.title import PLATE_DARK, PLATE_PROBE_XS, Plate

MIN_PLATE_RUN = 40      # 제목판은 최소 이만큼 세로로 이어진다 (테두리 선과 가른다)
SEARCH_BOTTOM = 220     # 판은 카드 위쪽에만 있다


def _dark(px, x: int, y: int) -> bool:
    return max(px[x, y][:3]) < PLATE_DARK


def _longest_dark_run(px, x: int, height: int) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    start: int | None = None
    for y in range(min(height, SEARCH_BOTTOM)):
        if _dark(px, x, y):
            start = y if start is None else start
        elif start is not None:
            if y - start >= MIN_PLATE_RUN and (best is None or y - start > best[1] - best[0]):
                best = (start, y - 1)
            start = None
    return best


def _right_edge(px, y: int, width: int, from_x: int) -> int:
    x = from_x
    while x + 1 < width and _dark(px, x + 1, y):
        x += 1
    return x


def measure(img: Image.Image) -> Plate:
    rgb = img.convert("RGB")
    px = rgb.load()
    runs = [r for x in PLATE_PROBE_XS if (r := _longest_dark_run(px, x, rgb.height)) is not None]
    if len(runs) < 2:
        raise ValueError("제목판을 못 찾았습니다 — 틀 이미지를 확인하세요")
    runs.sort(key=lambda r: r[0])
    top, bottom = runs[len(runs) // 2]
    y_hi, y_lo = top + 12, bottom - 10
    edge = ((y_hi, _right_edge(px, y_hi, rgb.width, PLATE_PROBE_XS[-1])),
            (y_lo, _right_edge(px, y_lo, rgb.width, PLATE_PROBE_XS[-1])))
    return Plate(center_y=(top + bottom) // 2, edge=edge, top_y=top)
```

`backend/tools/cardimage_plate_probe.py` (신규, 얇은 CLI):

```python
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
```

테스트의 import 를 `from daengs_cardimage import plate_probe` 로 고치고 `plate_probe.measure(...)` 를 부른다 (Step 1 의 `tools_shim` 은 지운다).

- [ ] **Step 4: 테스트가 통과하는지 본다**

```bash
cd backend && uv run pytest tests/test_cardimage_plate_probe.py -v
```

Expected: PASS 2건. 실패하면 `MIN_PLATE_RUN`·`SEARCH_BOTTOM` 을 그 틀에 맞게 조정한다 — **4월·9월의 손으로 잰 값이 정답이다.**

- [ ] **Step 5: 12달을 재고 눈으로 확인한다**

```bash
cd backend && uv run python tools/cardimage_plate_probe.py
```

나온 10달(1·2·3·5·6·7·8·10·11·12) 값을 catalog 에 넣기 전에 각각:

```bash
cd backend && uv run python tools/cardimage_title.py --month 7 "BEACH MOMO"
```

`cardimage/out/_title_test/` 의 결과에서 제목이 판 안에 들어가고 세로 중심이 맞는지 본다. **9월 때 두 번 틀렸던 자리다 — 눈으로 보지 않고 넘어가지 않는다.**

- [ ] **Step 6: 달별 무대·의상·제목판을 catalog 에 채운다**

`catalog.py` 의 `_CARDS` 에서 `scene` 이 `""` 인 10달을 채운다. 각 달의 틀(`cardimage/N_*_template.webp`)과 원본(`cardimage/N_*.webp`)을 열어 보고 쓴다. 형식은 4·9월과 같다 — **포즈 · 무대 소품 · 고정할 색**을 영어로, 변주는 강아지 쪽에만.

예시(12월 산타 — 실제 틀을 보고 고칠 것):

```python
    12: MonthCard(
        12, "12_santa", "SANTA", "26DEC",
        "the pose (sitting beside the decorated Christmas tree, front paws on a wrapped gift box), "
        "the warm string lights, the red-and-green wrapped gifts, the knitted stocking",
        "DECEMBER SPECIAL",
        SANTA_OUTFIT,
        DECEMBER_PLATE,
    ),
```

**의상 문장** — 틀의 강아지가 옷을 입고 있는 달은 9월 한복처럼 "이미지 1 의 옷 그대로" 로 쓴다. 안 입은 달은 `outfit` 인자를 생략해 `NO_OUTFIT` 기본값을 쓴다:

```python
#: 12월 틀의 강아지는 산타 옷을 입고 있다 — 9월 한복과 같은 처리.
SANTA_OUTFIT = (
    "The dog must wear exactly the same santa outfit as the dog in image 1. "
    "Do not carry over any accessories from image 2 — no collar, no leash, no harness."
)
```

- [ ] **Step 7: catalog 테스트를 넓힌다**

`backend/tests/test_cardimage_catalog.py` 에 더한다:

```python
def test_every_month_is_openable():
    """12달 전부 무대 문장과 부제를 갖는다 — 하나라도 비면 require_open 이 404 로 막는다."""
    for m in range(1, 13):
        c = catalog.require_open(m, frozenset(range(1, 13)))
        assert c.scene.strip(), m
        assert c.subtitle.strip(), m


def test_every_month_has_its_own_measured_plate():
    """제목판은 달마다 다르다. 4월 말고 다른 달이 APRIL_PLATE 를 그대로 쓰면 제목이 어긋난다."""
    others = [catalog.get(m).plate for m in range(1, 13) if m != 4]
    assert all(p != catalog.APRIL_PLATE for p in others)
```

`test_month_with_empty_scene_is_not_open_even_if_listed` 는 1월이 열리면서 깨진다 — **그 테스트는 지우지 말고** 존재하지 않는 달 대신 빈 `MonthCard` 를 직접 만들어 검사하도록 고친다:

```python
def test_month_with_empty_scene_is_not_open_even_if_listed(monkeypatch):
    blank = catalog.MonthCard(1, "1_new_year", "NEW YEAR", "26JAN", "", "JANUARY SPECIAL")
    monkeypatch.setitem(catalog._CARDS, 1, blank)
    with pytest.raises(catalog.MonthNotOpenError):
        catalog.require_open(1, frozenset({1}))
```

- [ ] **Step 8: 기본으로 열리는 달을 12로 넓힌다**

`backend/src/daengs_backend/config.py:256` 의 `cardimage_months` 기본값을 `"4,9"` 에서 `"1,2,3,4,5,6,7,8,9,10,11,12"` 로 바꾸고, `backend/.env.example` 의 설명도 같이 고친다.

`backend/tests/test_cardimage_settings.py` 에 더한다:

```python
def test_default_months_open_all_twelve():
    assert settings.cardimage_months == frozenset(range(1, 13))
```

- [ ] **Step 9: 전체 테스트와 저장소 검사**

```bash
cd backend && uv run check && uv run pytest tests/test_cardimage_catalog.py tests/test_cardimage_plate_probe.py tests/test_cardimage_settings.py -v
```

Expected: 전부 PASS

- [ ] **Step 10: 커밋**

```bash
git add backend/src/daengs_cardimage/catalog.py backend/src/daengs_cardimage/plate_probe.py \
        backend/tools/cardimage_plate_probe.py backend/src/daengs_backend/config.py \
        backend/.env.example backend/tests/test_cardimage_catalog.py \
        backend/tests/test_cardimage_plate_probe.py backend/tests/test_cardimage_settings.py
git commit -m "feat: 달마다 무대·의상·제목판을 채워 12달을 연다"
git push
```

---

## Task 3: 검증된 seed 목록과 seed 기록

**Files:**
- Modify: `backend/src/daengs_cardimage/catalog.py` (`MonthCard.seeds`)
- Modify: `backend/src/daengs_cardimage/engine.py:29-33` (프로토콜), `:187` (`generate`)
- Modify: `backend/src/daengs_cardimage/generate.py:51-102`
- Modify: `backend/src/daengs_backend/models/ai_card.py`, `db/init/`, `db/migrations/`
- Test: `backend/tests/test_cardimage_catalog.py`, `test_cardimage_generate.py`, `test_ai_card_model.py`

**Interfaces:**
- Consumes: Task 2 의 `catalog.MonthCard`
- Produces:
  - `MonthCard.seeds: tuple[int, ...]` — 그 달에서 글씨가 안 깨진다고 확인된 seed
  - `catalog.pick_seeds(month: int, count: int, rng: random.Random) -> list[int]`
  - `CardImageEngine.generate(*, template_png, photo_jpeg, prompt, seed: int | None = None) -> bytes`
  - `GeneratedCard.seed: int | None`
  - `AiCard.seed: int | None` (SmallInteger 아님 — `Integer`)

**왜 이 순서인가:** Task 2 가 끝나야 12달 틀로 seed 를 잴 수 있다. GPU 는 여기서 **한 번만** 켠다.

- [ ] **Step 1: 실험 전에 사람에게 설명하고 승인받는다**

돌릴 것을 그대로 적어 보여 준다:

- 조건: 12달 × seed 1~6 × 사진 1장 = **72장**, 순차 장당 약 16초 → GPU 약 19분 + 콜드 스타트 약 7분 + 유휴 10분
- 예상 비용: 장당 ₩43 **추정**(E4 미실측, 가중치 잡 단가를 옮겨 쓴 값) → 약 ₩3,100
- 판정: 제목과 아래 패널 문구가 안 깨진 seed. 격자 이미지를 **눈으로** 본다

**승인 전에는 돌리지 않는다.**

- [ ] **Step 2: 실험을 돌린다**

사진은 메인 체크아웃에 있다(`cardimage/test/`, 34장). 정면·앉은 사진 한 장을 고른다.

```bash
cd backend && uv run python tools/cardgen_compare.py --engine cardgen \
  --url http://127.0.0.1:<proxy 포트> \
  --photos "C:/Users/403/Documents/workspace/DAENGS_dev/cardimage/test/<정면사진>.jpg" \
  --months 1,2,3,4,5,6,7,8,9,10,11,12 --seeds 1,2,3,4,5,6 --dog-name MOMO \
  --out "C:/Users/403/Documents/workspace/DAENGS_dev/cardimage/out/_cardgen/months"
```

⚠ **강아지 이름은 영문(`MOMO`)** 으로 둔다 — 검수가 한글 이름을 깨진 글자로 오판한다.

```bash
cd backend && uv run python tools/cardgen_grid.py --dir "…/out/_cardgen/months" --out "…/months_grid.png"
```

격자를 열어 달마다 **제목·아래 패널이 깨지지 않은 seed** 를 적는다. 4월 `{3,4}` · 9월 `{1,4}` 는 #557 에서 이미 확인됐으니 재현되는지 같이 본다.

- [ ] **Step 3: seed 목록의 실패 테스트를 쓴다**

`backend/tests/test_cardimage_catalog.py`:

```python
import random


def test_every_month_has_verified_seeds():
    """글씨 깨짐은 (틀, seed, 크기) 로 정해진다(#557 E1) — 달마다 깨끗한 seed 를 적어 둔다."""
    for m in range(1, 13):
        assert catalog.get(m).seeds, m


def test_pick_seeds_returns_distinct_values_from_the_month_list():
    rng = random.Random(0)
    picked = catalog.pick_seeds(4, 2, rng)
    assert len(picked) == len(set(picked)) == 2
    assert set(picked) <= set(catalog.get(4).seeds)


def test_pick_seeds_repeats_when_asked_for_more_than_the_list_has():
    """목록이 2개인데 4장을 뽑으면 되풀이한다 — 장수가 seed 수에 갇히지 않게."""
    rng = random.Random(0)
    assert len(catalog.pick_seeds(4, 4, rng)) == 4
```

- [ ] **Step 4: 실패를 확인한다**

```bash
cd backend && uv run pytest tests/test_cardimage_catalog.py -k seed -v
```

Expected: FAIL — `AttributeError: 'MonthCard' object has no attribute 'seeds'`

- [ ] **Step 5: catalog 에 seed 목록과 뽑기를 넣는다**

`catalog.py`:

```python
import random

@dataclass(frozen=True)
class MonthCard:
    ...
    #: 이 달 틀에서 제목·아래 패널 글씨가 안 깨진다고 실험으로 확인된 seed (#557 E1·E2, #572 Task 3).
    #: 글씨 깨짐은 사진이 아니라 (틀, seed, 크기) 로 정해지므로 여기서 뽑으면 깨진 장이 안 나온다.
    seeds: tuple[int, ...] = ()


def pick_seeds(month: int, count: int, rng: random.Random) -> list[int]:
    """그 달의 검증된 seed 에서 `count` 개를 겹치지 않게 뽑는다. 목록이 모자라면 되풀이한다."""
    pool = list(get(month).seeds)
    if not pool:
        raise MonthNotOpenError(f"month {month} has no verified seeds")
    picked: list[int] = []
    while len(picked) < count:
        rng.shuffle(pool)
        picked.extend(pool[: count - len(picked)])
    return picked
```

`_CARDS` 의 각 달에 Step 2 에서 얻은 값을 넣는다 (4·9월은 확인된 값):

```python
    4: MonthCard(..., seeds=(3, 4)),
    9: MonthCard(..., seeds=(1, 4)),
```

- [ ] **Step 6: 엔진 프로토콜에 seed 를 얹는다**

`engine.py`:

```python
class CardImageEngine(Protocol):
    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str,
                 seed: int | None = None) -> bytes:
        ...
```

`HttpCardImageEngine.generate` 는 이미 `__init__(seed=...)` 를 받는다 — **호출 인자가 있으면 그것을 쓰고, 없으면 생성자 값을 쓴다.** `GeminiCardImageEngine.generate` 는 seed 를 받되 **무시한다**(Nano Banana 2 는 seed 를 안 받는다). 무시한다는 것을 주석으로 남긴다:

```python
    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str,
                 seed: int | None = None) -> bytes:
        # Nano Banana 2 는 seed 를 받지 않는다. 인자를 받되 버린다 — 프로토콜을 하나로 두기 위해서다.
```

- [ ] **Step 7: 생성이 쓴 seed 를 돌려주게 한다**

`generate.py` 의 `GeneratedCard` 에 `seed: int | None = None` 을 더하고, `_attempt` 에 `seed: int | None` 인자를 넘긴다. `generate_card` 는 `catalog.pick_seeds(month, 1, rng)[0]` 로 뽑아 쓴다 (`rng` 는 인자로 받아 테스트에서 고정 가능하게).

`backend/tests/test_cardimage_generate.py` 에 더한다:

```python
def test_generated_card_records_the_seed_it_used(fake_engine, fake_judge, tmp_cardimage):
    card = generate_card(..., rng=random.Random(0))
    assert card.seed in catalog.get(4).seeds
    assert fake_engine.calls[0]["seed"] == card.seed
```

- [ ] **Step 8: DB 에 seed 를 남긴다**

`models/ai_card.py` 의 `AiCard` 에:

```python
    #: 이 카드를 만든 seed. 같은 seed 가 같은 자리를 깨뜨리므로(#557 E1) 기록해 둔다.
    #: 09-15 에 `PETL PPAUSE` 3건이 전부 같은 seed 였는데 기록이 없어 나중에야 알았다.
    seed: Mapped[int | None] = mapped_column(Integer)
```

`db/init/` 의 해당 테이블 정의에 같은 컬럼을 더하고, `db/migrations/2026-09-16_ai_card_seed.sql` 을 만든다:

```sql
-- #572 Task 3 — 카드를 만든 seed 를 남긴다. 여러 번 돌려도 안전하다.
ALTER TABLE ai_cards ADD COLUMN IF NOT EXISTS seed INTEGER;
```

`services/ai_card.py` 의 `_finish_ready` 에서 `card.seed = generated.seed` 를 채운다.

- [ ] **Step 9: 테스트와 마이그레이션 검사**

```bash
cd backend && uv run check && uv run pytest tests/test_cardimage_catalog.py tests/test_cardimage_generate.py tests/test_cardimage_engine.py tests/test_ai_card_model.py -v
```

`db/` 를 건드렸으므로 마이그레이션 하네스를 **손으로** 돌린다 (Windows 는 `PYTHONUTF8=1` 필수):

```bash
PYTHONUTF8=1 PGCLIENTENCODING=UTF8 PGHOST=127.0.0.1 PGPORT=55432 PGUSER=postgres \
  PGPASSWORD=test-password PGDATABASE=migration_test python tools/check_migration_verification.py sql
```

- [ ] **Step 10: 실험 결과를 문서로 남기고 커밋**

`docs/cardimage/compare-2026-09-16-months-seeds.md` 에 달별 seed 표(깨끗/깨짐)와 격자 이미지 경로를 적는다.

```bash
git add backend/src/daengs_cardimage/ backend/src/daengs_backend/models/ai_card.py \
        backend/src/daengs_backend/services/ai_card.py db/ backend/tests/ docs/cardimage/
git commit -m "feat: 달마다 검증된 seed 에서 뽑고 쓴 값을 남긴다"
git push
```

---

## Task 4: 한 요청에 2장, 고른 한 장만 저장

**Files:**
- Modify: `backend/src/daengs_cardimage/generate.py`
- Modify: `backend/src/daengs_backend/services/ai_card.py`
- Modify: `backend/src/daengs_backend/routers/ai_card.py`, `schemas/`
- Modify: `backend/src/daengs_backend/models/ai_card.py`, `db/init/`, `db/migrations/`
- Test: `backend/tests/test_cardimage_generate.py`, `test_ai_card_service.py`, `test_ai_cards_api.py`

**Interfaces:**
- Consumes: Task 3 의 `catalog.pick_seeds`, `GeneratedCard.seed`
- Produces:
  - `generate_cards(*, count: int, …) -> list[GeneratedCard]` (기존 `generate_card` 는 `count=1` 로 위임)
  - `AiCard.pick_group: uuid.UUID | None` — 같은 요청에서 나온 장들을 묶는 값
  - `POST /app/ai-cards` 응답에 `pick_group`, `GET /app/ai-cards/{id}` 응답에 `done`·`total`
  - `POST /app/ai-cards/{id}/choose` — 고른 장만 남기고 형제를 지운다

- [ ] **Step 1: N장 순차 생성의 실패 테스트**

`backend/tests/test_cardimage_generate.py`:

```python
def test_generate_cards_makes_each_card_with_a_different_seed(fake_engine, tmp_cardimage):
    """L4 는 한 번에 2장부터 CUDA OOM 이다(#557 E2) — 반드시 순차 호출이어야 한다."""
    cards = generate_cards(count=2, month=4, rng=random.Random(0), ...)
    assert len(cards) == 2
    assert cards[0].seed != cards[1].seed
    assert len(fake_engine.calls) == 2      # 한 번에 두 장이 아니라 두 번 부른다


def test_generate_cards_keeps_going_when_one_attempt_fails(failing_once_engine, tmp_cardimage):
    """두 장 중 한 장이 실패해도 나머지 한 장은 돌려준다 — 사용자가 고를 게 남는다."""
    cards = generate_cards(count=2, month=4, rng=random.Random(0), ...)
    assert len(cards) == 1
```

- [ ] **Step 2: 실패를 확인한다**

```bash
cd backend && uv run pytest tests/test_cardimage_generate.py -k generate_cards -v
```

Expected: FAIL — `ImportError: cannot import name 'generate_cards'`

- [ ] **Step 3: `generate_cards` 를 만든다**

```python
def generate_cards(*, count: int, photo: bytes, content_type: str, month: int, dog_name: str,
                   engine: CardImageEngine, judge: CardJudge | None, base_dir: Path,
                   open_months: frozenset[int], judge_min: int,
                   rng: random.Random) -> list[GeneratedCard]:
    """사진 한 장으로 카드 `count` 장을 **순차로** 만든다.

    한 번에 여러 장(`num_images_per_prompt`)은 L4 에서 2장부터 CUDA OOM 이다 (#557 E2 실측).
    장마다 다른 seed 를 쓰고, 한 장이 실패해도 나머지는 돌려준다 — 고를 게 하나라도 남는 편이 낫다.
    전부 실패하면 마지막 예외를 올린다.
    """
```

기존 `generate_card` 는 남겨 두고 `generate_cards(count=1, …)[0]` 로 위임한다 — 콘솔(`/admin/cardimage/generate`)이 그대로 쓴다.

- [ ] **Step 4: 테스트 통과 확인**

```bash
cd backend && uv run pytest tests/test_cardimage_generate.py -v
```

Expected: PASS

- [ ] **Step 5: 장을 묶는 컬럼과 마이그레이션**

`models/ai_card.py`:

```python
    #: 같은 요청에서 나온 장들을 묶는다. 사용자가 하나를 고르면 나머지 형제 행은 지운다 (#572).
    pick_group: Mapped[uuid.UUID | None] = mapped_column(Uuid)
```

`db/migrations/2026-09-16_ai_card_pick_group.sql`:

```sql
-- #572 Task 4 — 한 요청에서 나온 카드들을 묶는다. 여러 번 돌려도 안전하다.
ALTER TABLE ai_cards ADD COLUMN IF NOT EXISTS pick_group UUID;
CREATE INDEX IF NOT EXISTS ix_ai_cards_pick_group ON ai_cards (pick_group);
```

`db/init/` 의 테이블 정의에도 같은 컬럼·인덱스를 더한다.

- [ ] **Step 6: 서비스가 2장을 만들고 진행률을 준다**

`services/ai_card.py` 의 `_run` 이 `generate_cards(count=settings.cardimage_pick_count, …)` 를 부르고, 장마다 행을 만들어 같은 `pick_group` 을 준다. `GET /app/ai-cards/{id}` 응답에 `done`(완료 장수)·`total` 을 싣는다.

`config.py` 에 더한다:

```python
    #: 한 요청에 만들 장수. 사용자가 고른다. 4장은 서로 차이가 작아 2장으로 정했다 (#557 E2, 사용자 09-16).
    cardimage_pick_count: int = Field(default=2, ge=1, le=4,
                                      validation_alias=AliasChoices("DAENGS_CARDIMAGE_PICK_COUNT"))
```

- [ ] **Step 7: 고르기 API 의 실패 테스트**

`backend/tests/test_ai_cards_api.py`:

```python
async def test_choose_keeps_the_picked_card_and_deletes_its_siblings(client, app_user):
    ...
    resp = await client.post(f"/app/ai-cards/{picked_id}/choose")
    assert resp.status_code == 200
    remaining = await list_cards(app_user)
    assert [c["id"] for c in remaining] == [str(picked_id)]


async def test_choose_rejects_a_card_that_belongs_to_another_user(client, other_user):
    resp = await client.post(f"/app/ai-cards/{other_users_card_id}/choose")
    assert resp.status_code == 404
```

- [ ] **Step 8: 실패 확인 → 구현 → 통과 확인**

```bash
cd backend && uv run pytest tests/test_ai_cards_api.py -k choose -v   # FAIL: 404 (라우트 없음)
# 라우터·서비스 구현 후
cd backend && uv run pytest tests/test_ai_cards_api.py tests/test_ai_card_service.py -v
```

Expected: PASS

- [ ] **Step 9: 저장소 검사와 마이그레이션 하네스**

```bash
cd backend && uv run check && uv run pytest -v
PYTHONUTF8=1 PGCLIENTENCODING=UTF8 PGHOST=127.0.0.1 PGPORT=55432 PGUSER=postgres \
  PGPASSWORD=test-password PGDATABASE=migration_test python tools/check_migration_verification.py sql
```

- [ ] **Step 10: 커밋**

```bash
git add backend/ db/
git commit -m "feat: 한 요청에 2장을 순차로 만들고 고른 한 장만 남긴다"
git push
```

---

## Task 5: 한도 재설계와 콜드 스타트 대기 상한

**Files:**
- Modify: `backend/src/daengs_backend/services/ai_card_quota.py:49-93`
- Modify: `backend/src/daengs_backend/services/ai_card.py:258-280`
- Modify: `docs/decisions.md` (D-077 개정)
- Test: `backend/tests/test_ai_card_quota.py`

**Interfaces:**
- Consumes: Task 4 의 `pick_group`, Task 3 의 `AiCard.seed`
- Produces: `stale_after()` 가 GPU 경로를 알게 되고, `_finish_ready` 가 닮음 기준 미달이면 `ai_card_usage` 를 남기지 않는다

**🔴 왜 이것이 "오류만 안 나면" 의 전제인가:** `stale_after()` 는 지금 `4 × cardimage_timeout_ms + 60초` = **9분**이다(기본 120초 기준). FLUX.2-klein-4B 의 콜드 스타트만 **6~7분**이라 2장 생성·검수까지 가면 9분을 넘길 수 있고, 그러면 **정상 진행 중인 작업이 사라진 것으로 정리된다.** `config.py:271` 에 "`cardimage_timeout_ms` 만 보므로 콜드 스타트를 모른다"고 이미 적혀 있다.

- [ ] **Step 1: 실패 테스트 — 콜드 스타트를 예산에 넣는다**

`backend/tests/test_ai_card_quota.py`:

```python
def test_stale_after_covers_gpu_cold_start_when_cardgen_is_configured(monkeypatch):
    """FLUX.2-klein-4B 는 콜드 스타트만 6~7분이다(#557 E3). 그보다 짧으면 진행 중인 작업을 죽인다."""
    monkeypatch.setattr(settings, "cardgen_url", "http://cardgen.example")
    monkeypatch.setattr(settings, "cardgen_timeout_s", 900.0)
    assert stale_after() >= timedelta(seconds=900)


def test_stale_after_stays_short_when_cardgen_is_not_configured(monkeypatch):
    """Nano Banana 2 만 쓰는 지금 운영에서는 기준이 늘어나면 안 된다 — 죽은 작업이 오래 남는다."""
    monkeypatch.setattr(settings, "cardgen_url", "")
    assert stale_after() == timedelta(milliseconds=4 * settings.cardimage_timeout_ms) + timedelta(seconds=60)
```

- [ ] **Step 2: 실패 확인**

```bash
cd backend && uv run pytest tests/test_ai_card_quota.py -k stale_after -v
```

Expected: FAIL — 첫 테스트가 9분(540초) < 900초 로 실패

- [ ] **Step 3: `stale_after` 를 고친다**

```python
def stale_after() -> timedelta:
    """`generating` 을 사라진 작업으로 볼 기준. ...(기존 주석 유지)...

    **GPU 경로(`cardgen_url`)가 켜져 있으면 콜드 스타트를 더한다** — FLUX.2-klein-4B 는 가중치
    로드에만 6~7분이 걸리고(#557 E3, 설정으로 못 줄였다), `cardimage_timeout_ms` 는 그것을 모른다.
    이 값이 콜드 스타트보다 짧으면 정상 진행 중인 카드가 사라진 것으로 정리된다.
    """
    budget = timedelta(milliseconds=4 * settings.cardimage_timeout_ms) + timedelta(seconds=60)
    if settings.cardgen_url:
        budget += timedelta(seconds=settings.cardgen_timeout_s)
    return budget
```

- [ ] **Step 4: 통과 확인**

```bash
cd backend && uv run pytest tests/test_ai_card_quota.py -k stale_after -v
```

Expected: PASS 2건

- [ ] **Step 5: 실패 테스트 — 안 닮은 카드는 하루치를 쓰지 않는다**

```python
async def test_low_likeness_card_does_not_consume_the_daily_quota(session, app_user):
    """두 번 시도해도 기준 미달이면 그 카드는 돌려주되 하루치는 안 쓴다 — 사진 각도가 나쁘면
    재시도로 안 구해지므로(#557 E5 · `_08` 사진 2/2 실패) 사용자가 그날을 잃는다."""
    await _finish_ready(card_id, key, stored, generated_with_likeness(2))
    assert await ai_card_repo.count_usage_since(session, app_user.id, day_start) == 0


async def test_card_at_the_threshold_does_consume_the_quota(session, app_user):
    await _finish_ready(card_id, key, stored, generated_with_likeness(settings.cardimage_judge_min))
    assert await ai_card_repo.count_usage_since(session, app_user.id, day_start) == 1
```

- [ ] **Step 6: 실패 확인 → `_finish_ready` 를 고친다 → 통과 확인**

`services/ai_card.py:278` 부근:

```python
        # 닮음이 기준 미만인 카드는 하루치를 쓰지 않는다 (#572). 점수는 이미 재어 두고도
        # 안 쓰고 있었다 — 사진 각도가 나쁘면 재시도로 안 구해지므로 사용자가 그날을 잃었다.
        # 점수가 없는 경우(검수 실패)는 세어 준다 — 카드는 멀쩡할 수 있고, 검수 장애가 공짜 무한 생성이 되면 안 된다.
        likeness = generated.judge.likeness if generated.judge else None
        if likeness is None or likeness >= settings.cardimage_judge_min:
            ai_card_repo.add_usage(session, AiCardUsage(...))
```

```bash
cd backend && uv run pytest tests/test_ai_card_quota.py tests/test_ai_card_service.py -v
```

Expected: PASS

- [ ] **Step 7: 한도를 「뽑기 횟수」로 고쳐 적는다**

`ai_card_quota.py` 머리말 docstring 의 규칙을 고친다 — 하루 한도가 세는 것은 **카드 장수가 아니라 뽑기 한 번**(`pick_group` 하나)이다. `_finish_ready` 는 그 그룹의 **첫 장이 ready 가 될 때만** `ai_card_usage` 를 남긴다.

```python
async def test_two_cards_from_one_request_consume_one_quota(session, app_user):
    """2장은 한 번 뽑은 것이다 — 두 장이 완성돼도 하루치는 하나만 쓴다."""
    ...
    assert await ai_card_repo.count_usage_since(session, app_user.id, day_start) == 1
```

- [ ] **Step 8: 결정을 `docs/decisions.md` 에 적는다**

Task 1 Step 3 에서 예약한 번호로 D-077 을 개정한다. 적을 것: 뽑기 횟수 기준 · 닮음 미달은 안 셈 · GPU 콜드 스타트를 정리 기준에 넣음 · 근거(#557 E3·E5).

- [ ] **Step 9: 전체 검사**

```bash
cd backend && uv run check && uv run pytest -v
```

- [ ] **Step 10: 커밋**

```bash
git add backend/ docs/decisions.md
git commit -m "feat: 뽑기 횟수로 한도를 세고 콜드 스타트를 정리 기준에 넣는다"
git push
```

---

## Task 6: 사진 안내 문구

**Files:**
- Create/Modify: `backend/src/daengs_cardimage/catalog.py` (`PHOTO_GUIDANCE`)
- Modify: `backend/src/daengs_backend/routers/ai_card.py`, `schemas/`
- Modify: `frontend/app/console/search/…` (도감 카드 생성 탭)
- Test: `backend/tests/test_ai_cards_api.py`

**Interfaces:**
- Consumes: 없음
- Produces: `GET /app/ai-cards` 응답의 `photo_guidance: str` — 콘솔과 `SAJOYO/DAENGS_APP` 이 같은 문구를 쓴다

**왜 서버가 주는가:** 앱(`DAENGS_APP#414`)은 다른 저장소다. 문구를 앱에 박으면 고칠 때마다 앱 배포가 필요하다. 응답에 실어 두면 앱은 그리기만 한다.

- [ ] **Step 1: 실패 테스트**

```python
async def test_card_list_carries_photo_guidance(client, app_user):
    """엎드린 옆모습 사진은 몇 장을 뽑아도 쓸 게 없다(#557 E2) — 앞에서 막는 유일한 수단이 안내다."""
    resp = await client.get("/app/ai-cards")
    assert "정면" in resp.json()["photo_guidance"]
```

- [ ] **Step 2: 실패 확인**

```bash
cd backend && uv run pytest tests/test_ai_cards_api.py -k guidance -v
```

Expected: FAIL — `KeyError: 'photo_guidance'`

- [ ] **Step 3: 문구를 정하고 넣는다**

`catalog.py`:

```python
#: 업로드 화면에 띄우는 안내. 사진 각도가 닮음을 좌우한다 — 엎드린 옆모습은 4장을 뽑아도
#: 쓸 게 없었다(#557 E2). 각도 약점을 모델로 고치는 대신(E5 안 함) 앞에서 막는다.
PHOTO_GUIDANCE = "얼굴이 정면으로 보이고 앉아 있는 사진이 가장 잘 나와요. 엎드려 있거나 옆을 보는 사진은 닮지 않게 나올 수 있어요."
```

목록 응답 스키마에 `photo_guidance: str` 을 더하고 이 값을 싣는다.

- [ ] **Step 4: 통과 확인**

```bash
cd backend && uv run pytest tests/test_ai_cards_api.py -v
```

Expected: PASS

- [ ] **Step 5: 콘솔 화면에 같은 문구를 띄운다**

`/console/search` 의 「도감 카드 생성」 탭에서 사진 고르는 자리 위에 안내를 띄운다. 콘솔은 관리자 경로라 `/admin/cardimage/generate` 를 쓰므로, 문구는 프런트 상수로 두되 **위 문자열과 같은 글자**로 맞춘다.

```bash
cd frontend && npm run lint
```

- [ ] **Step 6: 커밋**

```bash
git add backend/ frontend/
git commit -m "feat: 정면·앉은 사진 안내를 응답과 콘솔에 넣는다"
git push
```

---

## Task 7: 문서 마무리

**Files:**
- Modify: `docs/cardimage/worklog.md`, `docs/cardimage/roadmap.md`, `docs/cardimage/README.md`
- Modify: PR #572 본문

**Interfaces:**
- Consumes: Task 2~6 의 결과
- Produces: 없음

- [ ] **Step 1: worklog 에 이 세션 절을 더한다**

`docs/cardimage/worklog.md` 맨 위에 `## 2026-09-NN — 12달 열기 + 2장 뽑기 (#572)` 절. 적을 것: 달별 제목판 실측값과 어긋났던 달, seed 실험 결과(장수·시간·비용, 확인/추정 구분), 2장 계약, 한도 변경.

- [ ] **Step 2: roadmap 을 갱신한다**

3번 행을 ✅ 머지됨으로. 4번(과일·채소)의 「다음 할 일」에 **틀이 없어 한 단계가 더 있다**(원본 고르기 → 글자 없는 틀 만들기 → plate 재기 → scene/outfit → seed)를 적는다.

- [ ] **Step 3: 카드 종류별 엔진 선택을 적는다**

`docs/cardimage/README.md` 「정해진 것」 표에 한 줄: 월 스페셜은 Nano Banana 2 한 장, 개인화 카드는 FLUX.2-klein-4B 여러 장. `DAENGS_CARDGEN_URL` 은 아직 운영에 안 넣는다는 것도.

- [ ] **Step 4: 머지 전 게이트를 전부 돌린다**

```bash
cd backend && uv run check
cd backend && uv run pytest
cd frontend && npm run lint
docker compose config > /dev/null && docker run --rm -v "$PWD/nginx:/etc/nginx/conf.d:ro" nginx nginx -t
PYTHONUTF8=1 PGCLIENTENCODING=UTF8 PGHOST=127.0.0.1 PGPORT=55432 PGUSER=postgres \
  PGPASSWORD=test-password PGDATABASE=migration_test python backend/tools/check_migration_verification.py sql
```

⚠ **`git status --short` 가 비었는지 보고 나서 돌린다** — #566 에서 워킹트리만 초록이고 커밋에는 수정이 빠져 머지 뒤 `dev` 가 실패했다.

- [ ] **Step 5: PR 본문의 「확인한 것」을 채우고 draft 를 푼다**

```bash
gh pr edit 572 --body-file <갱신한 본문 파일>
gh pr ready 572
```

- [ ] **Step 6: 커밋**

```bash
git add docs/
git commit -m "docs: 12달 열기·2장 뽑기 결과와 로드맵 갱신"
git push
```

---

## 배포 절차 (머지 후, 사람이 한다)

`dev` 머지가 곧 배포다. **DB 를 먼저, 코드를 나중에.**

1. 개발서버·GCP DB 에 마이그레이션을 손으로 적용한다 (순서대로, 여러 번 돌려도 안전):
   - `db/migrations/2026-09-16_ai_card_seed.sql`
   - `db/migrations/2026-09-16_ai_card_pick_group.sql`
2. `DAENGS_CARDIMAGE_MONTHS` 를 서버 `.env` 에서 따로 지정하고 있으면 지우거나 12달로 고친다.
3. `backend/.env` 를 고쳤으면 `docker compose restart` 로는 안 된다 — **셸에 `GEMINI_API_KEY` 를 올린 뒤** `docker compose up -d backend` 로 재생성한다.
4. `DAENGS_CARDGEN_URL` 은 **이 카드에서 넣지 않는다.** Task 5 가 대기 상한을 고쳤으므로 넣을 준비는 됐지만, 켜는 판단은 별도다.

---

## Self-Review

**Spec 대비 빠진 것:** PR #572 「작업 목록」 12항목 중 — seed 목록(Task 3) · 쓴 seed 저장(3) · N장 순차(4) · 진행률·폐기(4) · 한도 재설계(5) · 닮음 미달 한도(5) · 안내 문구(6) · 대기 상한(5) · 12달 catalog(2) · 엔진 선택 문서(7) · roadmap·worklog(1,7) 전부 태스크가 있다. **「콜드 스타트 대응 — 최소 인스턴스」는 사용자가 "그냥 냅두자"로 정해 태스크를 두지 않았다** — Task 5 의 대기 상한이 그 결정의 전제를 지킨다. **「앱 고르기 화면 계약 조율」은 사용자가 "앱은 나중에"로 빼서 PR 「남은 것」에 있다.**

**미정으로 남은 값 둘 (실행 중에 채운다, 지금 지어내지 않는다):**
- Task 2 Step 6 의 10달 `scene`·`outfit` 문장 — 틀을 눈으로 보고 쓴다. 12월 예시는 형식을 보이려는 것이고 실제 틀을 보고 고친다.
- Task 3 Step 5 의 달별 `seeds` — Step 2 실험 결과. 4월 `(3, 4)` · 9월 `(1, 4)` 만 #557 에서 확정됐다.

**타입 일관성:** `Plate(center_y, edge, top_y, left_x)` 는 Task 2·3에서 같은 이름. `pick_seeds(month, count, rng)` 는 Task 3 에서 정의하고 Task 4 가 그대로 부른다. `GeneratedCard.seed` 는 Task 3 에서 더해 Task 4·5 가 읽는다. `pick_group` 은 Task 4 에서 만들어 Task 5 가 읽는다. `AiCard.seed` 는 `Integer`(`SmallInteger` 아님 — seed 가 32767 을 넘을 수 있다).
