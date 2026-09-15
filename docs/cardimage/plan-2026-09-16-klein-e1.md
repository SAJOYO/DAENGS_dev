# FLUX.2-klein-4B E1 글씨 유지 실험 구현 계획 (#557)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FLUX.2-klein-4B 카드의 아래 패널 문구 깨짐(09-15 `PETAL PAUSE` → `PETL PPAUSE`, 3/12)이 ① 프롬프트에 문구 명시 ② 생성 크기 1280×2048 ③ 둘 다 중 무엇으로 줄어드는지, 같은 사진·틀·seed 로 18장을 만들어 눈으로 판정한다.

**Architecture:** 서비스(`daengs_cardgen`)와 제품 코드(앱 경로·`generate_card`·`catalog`)는 바꾸지 않는다. 클라이언트 쪽만 넓힌다 — `HttpCardImageEngine` 에 생성 크기 인자(기본값 그대로), 비교 도구 `tools/cardgen_compare.py` 에 `--gen-size`·`--panel-text`(엔진을 감싸 프롬프트 끝에 문구 문장을 붙인다)·영문 이름 기본값, 조건별 결과를 한 장에 모으는 `tools/cardgen_grid.py`. 유료 실행과 기록은 컨트롤러가 사람 승인 뒤 한다.

**Tech Stack:** Python 3.12 · httpx(MockTransport) · Pillow 12.3 · pytest · Cloud Run 서비스 `daengs-cardgen-klein`(asia-southeast1 L4) · `gcloud run services proxy`

**Spec:** `docs/cardimage/roadmap.md` 「2번 → E1. 글씨 유지」와 PR #557 본문 「작업 목록」·「컨텍스트 메모」. 기준 결과는 `docs/cardimage/compare-2026-09-15-cardgen.md` 와 `cardimage/out/_cardgen/task8-klein/`(미추적). 결정 근거 D-078.

## Global Constraints

- **돈이 나가는 명령(서비스 호출 · Gemini 검수 · 이미지 빌드 · 배포)은 실행 전에 사람에게 조건·장수·예상 비용을 설명하고 승인받는다.** 하위 에이전트는 이런 명령을 실행하지 않는다 — Task 4 는 컨트롤러가 사람과 함께 한다. 실패하면 매 시도 원인을 확인/추정으로 갈라 보고한 뒤 다음 시도.
- 서비스(`backend/src/daengs_cardgen/`)·Dockerfile·`infra/gcp/`·`daengs_cardimage/generate.py`·`catalog.py`·`services/ai_card_engine.py` 는 이 계획에서 **고치지 않는다**. 이미지 재빌드·재배포 없음.
- `GEN_SIZE = (1024, 1632)` 기본값과 기존 호출(`HttpCardImageEngine(base_url=…, timeout_s=…, seed=…, auth=…, transport=…)`)은 **글자 그대로 같이 동작**해야 한다.
- 서비스 크기 검증은 `256 ≤ width, height ≤ 2048`, 서비스가 16의 배수로 맞춘다(`snap`). E1 크기는 **1280×2048**.
- `daengs_cardimage` 는 `daengs_backend`·`fastapi`·`sqlalchemy`·`starlette`·`pydantic_settings` 를 import 하지 않는다.
- 비교에서 seed 는 **1 고정**. 강아지 이름은 영문 **`MOMO`** — 검수가 한글 이름을 깨진 글자로 오판한다.
- 사진은 09-15 와 같은 셋: `../cardimage/test/KakaoTalk_20260913_220514335.jpg`, `…_03.jpg`, `…_08.jpg`. `cardimage/test/`·`cardimage/out/` 은 **커밋하지 않는다**(`git add -A` 금지, 파일을 이름으로 stage).
- 합성 방식(그림만 잘라 붙이기·배지 합성)을 만들거나 제안하지 않는다. 제목 글자는 지금처럼 Pillow(`title.draw_title`)가 얹는다.
- 문서의 모델 이름은 풀네임 **FLUX.2-klein-4B**. 식별자(`daengs-cardgen-klein`·`klein-4b`)는 그대로.
- 파일 읽기·쓰기는 전용 도구로(heredoc·sed 금지). 검색은 Grep 도구로.
- **하위 에이전트는 커밋·stage·stash·push 를 하지 않는다.** 커밋은 컨트롤러가 한다. 에이전트는 자기 태스크 테스트만 돌린다(전체 `uv run pytest` 는 컨트롤러가 마지막에 한 번).
- 커밋 메시지는 한글 서술형, 접두사 없음, 끝에 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- 명령은 `backend/` 에서 `uv run …` 으로 부른다.

## 파일 구조

| 파일 | 책임 | 태스크 |
| --- | --- | --- |
| `backend/src/daengs_cardimage/engine.py` | `HttpCardImageEngine(gen_size=…)` — 서비스에 보내는 width·height | 1 |
| `backend/tests/test_cardimage_engine_http.py` | 위 인자 테스트 | 1 |
| `backend/tools/cardgen_compare.py` | `--gen-size` · `--panel-text` · 이름 기본값 `MOMO` · 결과 행에 조건 기록 | 2 |
| `backend/tests/test_cardgen_compare_tool.py` (새) | 크기 파싱 · 문구 문장 · 프롬프트 감싸기 | 2 |
| `backend/tools/cardgen_grid.py` (새) | 조건 폴더들 × 카드 이름들 → 라벨 붙은 격자 PNG | 3 |
| `backend/tests/test_cardgen_grid_tool.py` (새) | 격자 크기·빈칸 | 3 |
| `docs/cardimage/compare-2026-09-16-klein-e1.md` (새) · `worklog.md` · `roadmap.md` | 결과 기록 | 5 |

---

### Task 1: `HttpCardImageEngine` 생성 크기 인자

**Files:**
- Modify: `backend/src/daengs_cardimage/engine.py` (클래스 `HttpCardImageEngine`, 147~181행 부근)
- Test: `backend/tests/test_cardimage_engine_http.py`

**Interfaces:**
- Consumes: 없음
- Produces: `HttpCardImageEngine(*, base_url: str, timeout_s: float, seed: int | None = None, gen_size: tuple[int, int] = GEN_SIZE, auth: Callable[[str], str] | None = None, transport: httpx.BaseTransport | None = None)`. `last_meta` 에 `"size": "<w>x<h>"`(보낸 크기) 키가 더해진다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_cardimage_engine_http.py` 끝에 더한다:

```python
def test_gen_size_is_sent_and_result_still_fits_card() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=png(1280, 2048))

    engine = _engine(handler, gen_size=(1280, 2048))
    out = engine.generate(template_png=png(), photo_jpeg=b"j", prompt="P")

    assert (seen["body"]["width"], seen["body"]["height"]) == (1280, 2048)
    assert Image.open(io.BytesIO(out)).size == (994, 1582)
    assert engine.last_meta["size"] == "1280x2048"
```

그리고 기존 `test_posts_template_then_photo_and_fits_card_size` 의 마지막 단언을 새 키에 맞춘다:

```python
    assert engine.last_meta == {"seed": 11, "seconds": "3.2", "model": "fake", "size": "1024x1632"}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_cardimage_engine_http.py -v`
Expected: `test_gen_size_is_sent_and_result_still_fits_card` FAIL — `TypeError: ... unexpected keyword argument 'gen_size'`; 기존 테스트는 `last_meta` 에 `size` 가 없어 FAIL.

- [ ] **Step 3: 구현한다**

`engine.py` 의 `HttpCardImageEngine` 을 이렇게 바꾼다(docstring 첫 문단 뒤에 한 줄 더하고, `__init__`·`generate` 의 해당 줄만):

```python
    """GPU 카드 생성 서비스(`daengs_cardgen`, D-078)를 부르는 엔진.

    인증은 `auth(audience) -> ID 토큰` 을 주입받는다 — 이 패키지는 backend 를 import 하지 않으므로
    토큰 발급(메타데이터 서버)은 backend 가 넘긴다. `auth=None` 이면 헤더 없이 부른다
    (`gcloud run services proxy` 로 연 로컬 포트 — 비교 도구가 쓴다).
    `seed=None` 이면 호출마다 무작위. 마지막 호출의 seed·서비스 시간·모델·보낸 크기는 `last_meta` 에 남긴다.
    `gen_size` 는 서비스에 요청하는 생성 크기다(기본 `GEN_SIZE`). 결과는 크기와 상관없이 `CARD_SIZE` 로 줄인다 —
    #557 E1 이 1280×2048 을 비교한다.
    """

    def __init__(self, *, base_url: str, timeout_s: float, seed: int | None = None,
                 gen_size: tuple[int, int] = GEN_SIZE,
                 auth: Callable[[str], str] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._base = base_url.strip().rstrip("/")
        self._timeout_s, self._seed, self._auth, self._transport = timeout_s, seed, auth, transport
        self._gen_size = gen_size
        self.last_meta: dict | None = None
```

`generate` 안의 body 와 `last_meta`:

```python
        body = {
            "images_b64": [base64.b64encode(template_png).decode(), base64.b64encode(photo_jpeg).decode()],
            "prompt": prompt, "seed": seed, "width": self._gen_size[0], "height": self._gen_size[1],
        }
```

```python
        self.last_meta = {"seed": seed, "seconds": resp.headers.get("X-Cardgen-Seconds"),
                          "model": resp.headers.get("X-Cardgen-Model"),
                          "size": f"{self._gen_size[0]}x{self._gen_size[1]}"}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_cardimage_engine_http.py tests/test_ai_card_engine.py -v`
Expected: 전부 PASS (`test_ai_card_engine.py` 는 `default_engine` 이 기존 인자로 만드는지 — 바뀌면 안 된다).

- [ ] **Step 5: 컨트롤러가 커밋한다**

```bash
git add backend/src/daengs_cardimage/engine.py backend/tests/test_cardimage_engine_http.py
git commit -m "GPU 카드 엔진이 생성 크기를 인자로 받는다 — 기본값은 그대로 1024×1632 (#557 E1)"
```

---

### Task 2: 비교 도구 — `--gen-size` · `--panel-text` · 이름 `MOMO`

**Files:**
- Modify: `backend/tools/cardgen_compare.py`
- Create: `backend/tests/test_cardgen_compare_tool.py`

**Interfaces:**
- Consumes: Task 1 의 `HttpCardImageEngine(gen_size=…)`, `last_meta["size"]`
- Produces (Task 4 가 명령줄로 쓴다):
  - `parse_size(text: str) -> tuple[int, int]` — `"1280x2048"` → `(1280, 2048)`, 형식이 틀리면 `argparse.ArgumentTypeError`
  - `PANEL_TEXT: dict[int, tuple[str, ...]]` — 4월·9월 아래 패널 문구
  - `panel_sentence(month: int) -> str`
  - `class PromptSuffixEngine` — `generate(*, template_png, photo_jpeg, prompt)` 가 `prompt + "\n\n" + suffix` 로 안쪽 엔진을 부르고, `last_meta` 는 안쪽 엔진 것을 그대로 보인다
  - 명령줄: `--gen-size WxH`(기본 `1024x1632`, cardgen 엔진만), `--panel-text`(플래그), `--dog-name`(기본 `MOMO`)
  - `results.jsonl` 행에 `"gen_size": "WxH"`, `"panel_text": true|false` 추가

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_cardgen_compare_tool.py`:

```python
"""`tools/cardgen_compare.py` 의 순수 부분 — 크기 파싱 · 아래 패널 문구 문장 · 프롬프트 감싸기 (#557 E1)."""

import argparse

import pytest

from tools.cardgen_compare import PANEL_TEXT, PromptSuffixEngine, panel_sentence, parse_size


def test_parse_size() -> None:
    assert parse_size("1280x2048") == (1280, 2048)
    assert parse_size("1024X1632") == (1024, 1632)
    for bad in ("1280", "x2048", "1280x", "axb", "1280x2048x1"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_size(bad)


def test_panel_text_covers_open_months_exactly() -> None:
    assert PANEL_TEXT[4] == ("PETAL PAUSE", "One petal. Perfect timing.", "SPRING", "920",
                             "Bloomed right on schedule.")
    assert PANEL_TEXT[9] == ("SONGPYEON SWEEP", "Full moon. Fuller snack tray.", "MOON LUCK", "925",
                             "A warm Chuseok surprise.")


def test_panel_sentence_quotes_every_text() -> None:
    sentence = panel_sentence(4)
    for text in PANEL_TEXT[4]:
        assert f'"{text}"' in sentence
    assert "letter for letter" in sentence


def test_panel_sentence_unknown_month_fails_loudly() -> None:
    with pytest.raises(KeyError):
        panel_sentence(1)


class _Inner:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.last_meta = {"seed": 1}

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        self.prompts.append(prompt)
        return b"card"


def test_prompt_suffix_engine_appends_and_exposes_meta() -> None:
    inner = _Inner()
    engine = PromptSuffixEngine(inner, "EXTRA")
    assert engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="BASE") == b"card"
    assert inner.prompts == ["BASE\n\nEXTRA"]
    assert engine.last_meta == {"seed": 1}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_cardgen_compare_tool.py -v`
Expected: FAIL — `ImportError: cannot import name 'PANEL_TEXT'`.

- [ ] **Step 3: 구현한다**

`backend/tools/cardgen_compare.py` 를 아래로 바꾼다. 달라지는 점: 모듈 docstring 의 E1 예시 · `settings`/`GeminiCardJudge`/`plate_shift` import 를 `main()` 안으로(테스트가 도구를 import 할 때 설정을 읽지 않게) · `parse_size` · `PANEL_TEXT` · `panel_sentence` · `PromptSuffixEngine` · 새 인자 셋 · 결과 행 두 키.

```python
"""GPU 카드 생성 서비스(D-078)와 Nano Banana 2 를 같은 사진·틀·seed 로 비교한다 (#544, #557).

    uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 \
        --photos ../cardimage/test/_03.jpg --months 4,9 --seeds 1,2 --out ../cardimage/out/_cardgen/klein
    uv run python tools/cardgen_compare.py --engine gemini --photos ... --out ../cardimage/out/_cardgen/gemini

    # #557 E1 글씨 유지 — 아래 패널 문구를 프롬프트에 적고 / 생성 크기를 올린다
    uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 --photos ... \
        --months 4,9 --seeds 1 --panel-text --gen-size 1280x2048 --out ../cardimage/out/_cardgen/e1-both

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

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        return self._inner.generate(template_png=template_png, photo_jpeg=photo_jpeg,
                                    prompt=f"{prompt}\n\n{self._suffix}")


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
                else:
                    engine = GeminiCardImageEngine(api_key=key, model=settings.cardimage_model,
                                                   size=settings.cardimage_size,
                                                   timeout_ms=settings.cardimage_timeout_ms)
                if args.panel_text:
                    engine = PromptSuffixEngine(engine, panel_sentence(month))
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
                    "gen_size": gen_size if args.engine == "cardgen" else None, "panel_text": args.panel_text,
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
```

- [ ] **Step 4: 통과와 명령줄을 확인한다**

Run: `uv run pytest tests/test_cardgen_compare_tool.py tests/test_cardimage_engine_http.py -v`
Expected: 전부 PASS

Run: `uv run python tools/cardgen_compare.py --help`
Expected: `--gen-size`·`--panel-text` 가 보이고 종료 코드 0 (네트워크·키를 쓰지 않는다)

Run: `uv run python tools/cardgen_compare.py --engine cardgen --photos x.jpg --gen-size 1280 --out _`
Expected: `argument --gen-size: 크기는 WxH 형식입니다` 로 종료 코드 2 (호출 전 실패 — 돈이 안 나간다)

- [ ] **Step 5: 컨트롤러가 커밋한다**

```bash
git add backend/tools/cardgen_compare.py backend/tests/test_cardgen_compare_tool.py
git commit -m "비교 도구에 생성 크기와 아래 패널 문구 명시 조건을 더하고 이름 기본값을 MOMO 로 둔다 (#557 E1)"
```

---

### Task 3: 조건 격자 도구 `tools/cardgen_grid.py`

**Files:**
- Create: `backend/tools/cardgen_grid.py`
- Create: `backend/tests/test_cardgen_grid_tool.py`

**Interfaces:**
- Consumes: 없음 (Task 2 가 만드는 `<out>/<사진>_<달>_s<seed>.png` 파일 이름 규칙만)
- Produces:
  - `build_grid(columns: list[tuple[str, Path]], names: list[str], *, thumb_width: int = 360) -> PIL.Image.Image` — 열 = (라벨, 폴더), 행 = 카드 이름(확장자 없음). 없는 파일 칸은 회색. 맨 위 라벨 줄, 각 행 왼쪽에 이름 줄.
  - 명령줄: `--col 라벨=폴더`(여러 번) · `--names a,b,c` · `--out grid.png` · `--thumb-width`(기본 360)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_cardgen_grid_tool.py`:

```python
"""`tools/cardgen_grid.py` — 조건 폴더 × 카드 이름 격자 (#557 E1)."""

from pathlib import Path

from PIL import Image

from tools.cardgen_grid import HEADER_H, LABEL_W, build_grid, main


def _card(path: Path, color) -> None:
    Image.new("RGB", (994, 1582), color).save(path)


def test_grid_layout_and_missing_cell(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _card(a / "p_4_s1.png", (255, 0, 0))
    _card(a / "p_9_s1.png", (255, 0, 0))
    _card(b / "p_4_s1.png", (0, 0, 255))   # b 에는 9월이 없다

    grid = build_grid([("A", a), ("B", b)], ["p_4_s1", "p_9_s1"], thumb_width=100)

    thumb_h = round(1582 * 100 / 994)
    assert grid.size == (LABEL_W + 2 * 100, HEADER_H + 2 * thumb_h)
    assert grid.getpixel((LABEL_W + 50, HEADER_H + thumb_h // 2)) == (255, 0, 0)
    assert grid.getpixel((LABEL_W + 150, HEADER_H + thumb_h // 2)) == (0, 0, 255)
    assert grid.getpixel((LABEL_W + 150, HEADER_H + thumb_h + thumb_h // 2)) == (128, 128, 128)


def test_main_writes_png(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.mkdir()
    _card(a / "p_4_s1.png", (0, 255, 0))
    out = tmp_path / "grid.png"

    assert main(["--col", f"A={a}", "--names", "p_4_s1", "--out", str(out), "--thumb-width", "50"]) == 0
    assert Image.open(out).size[0] == LABEL_W + 50
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_cardgen_grid_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.cardgen_grid'`

- [ ] **Step 3: 구현한다**

`backend/tools/cardgen_grid.py`:

```python
"""비교 조건 폴더들을 한 장의 격자로 모은다 — 열 = 조건, 행 = 카드 이름 (#557).

    uv run python tools/cardgen_grid.py \
        --col 기준=../cardimage/out/_cardgen/task8-klein --col 문구=../cardimage/out/_cardgen/e1-panel \
        --col 2048=../cardimage/out/_cardgen/e1-2048 --col 둘다=../cardimage/out/_cardgen/e1-both \
        --names KakaoTalk_20260913_220514335_4_s1,KakaoTalk_20260913_220514335_03_4_s1 \
        --out ../cardimage/out/_cardgen/e1_grid_4.png

없는 파일 칸은 회색으로 둔다. 판정은 이 격자를 눈으로 본다(검수 점수만 믿지 않는다 — roadmap 2번 공통 규칙).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CARD_W, CARD_H = 994, 1582
LABEL_W = 220
HEADER_H = 60
MISSING = (128, 128, 128)


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("malgun.ttf", "NotoSansKR-Regular.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def build_grid(columns: list[tuple[str, Path]], names: list[str], *, thumb_width: int = 360) -> Image.Image:
    thumb_h = round(CARD_H * thumb_width / CARD_W)
    grid = Image.new("RGB", (LABEL_W + thumb_width * len(columns), HEADER_H + thumb_h * len(names)), (255, 255, 255))
    draw = ImageDraw.Draw(grid)
    font = _font(28)
    small = _font(16)
    for c, (label, _) in enumerate(columns):
        draw.text((LABEL_W + c * thumb_width + 10, 14), label, fill=(0, 0, 0), font=font)
    for r, name in enumerate(names):
        top = HEADER_H + r * thumb_h
        draw.multiline_text((8, top + 10), name.replace("_", "\n"), fill=(0, 0, 0), font=small)
        for c, (_, folder) in enumerate(columns):
            path = folder / f"{name}.png"
            if path.exists():
                cell = Image.open(path).convert("RGB").resize((thumb_width, thumb_h), Image.LANCZOS)
            else:
                cell = Image.new("RGB", (thumb_width, thumb_h), MISSING)
            grid.paste(cell, (LABEL_W + c * thumb_width, top))
    return grid


def _col(text: str) -> tuple[str, Path]:
    label, sep, folder = text.partition("=")
    if not sep or not label or not folder:
        raise argparse.ArgumentTypeError(f"--col 은 라벨=폴더 형식입니다: {text!r}")
    return label, Path(folder)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--col", type=_col, action="append", required=True, help="라벨=폴더 (여러 번)")
    parser.add_argument("--names", required=True, help="쉼표로 구분한 카드 이름 (확장자 없이)")
    parser.add_argument("--out", required=True)
    parser.add_argument("--thumb-width", type=int, default=360)
    args = parser.parse_args(argv)

    grid = build_grid(args.col, [n for n in args.names.split(",") if n], thumb_width=args.thumb_width)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    grid.save(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_cardgen_grid_tool.py -v`
Expected: 전부 PASS

Run (돈 안 나감 — 09-15 기준 결과로 격자만 만든다):
`uv run python tools/cardgen_grid.py --col 기준=../cardimage/out/_cardgen/task8-klein --names KakaoTalk_20260913_220514335_4_s1,KakaoTalk_20260913_220514335_03_4_s1,KakaoTalk_20260913_220514335_08_4_s1 --out ../cardimage/out/_cardgen/e1_grid_smoke.png`
Expected: 경로 한 줄 출력, 파일이 생기고 세 장이 세로로 보인다(한글 라벨이 네모로 깨지면 보고 — 판정용이라 치명적이지 않다).

- [ ] **Step 5: 컨트롤러가 커밋한다**

```bash
git add backend/tools/cardgen_grid.py backend/tests/test_cardgen_grid_tool.py
git commit -m "비교 조건 폴더들을 카드 이름별 격자 한 장으로 모으는 도구를 둔다 (#557 E1)"
```

---

### Task 4: 유료 실행 — 컨트롤러가 사람과 함께 (하위 에이전트 금지)

**Files:** 없음 (산출물은 `cardimage/out/_cardgen/e1-*/` — 미추적)

**Interfaces:**
- Consumes: Task 2 명령줄, Task 3 명령줄
- Produces: `cardimage/out/_cardgen/e1-panel/`, `e1-2048/`, `e1-both/` 각 `results.jsonl` + PNG 6장, 격자 `e1_grid_4.png`·`e1_grid_9.png`

- [ ] **Step 1: 전체 테스트와 규칙 검사 (돈 안 나감)**

Run: `uv run check` 그리고 `uv run pytest` (약 9분)
Expected: check 통과. pytest 는 이 브랜치와 무관한 기존 walk/territory 실패(09-15 에 13건) 외에 새 실패 없음 — 실패 목록을 dev 와 대조해 보고한다.

- [ ] **Step 2: 사람 승인**

사람에게 다시 보인다: 조건 3개 × 사진 3장 × 4·9월 × seed 1 = **18장**, 기준은 09-15 결과 재사용, 예상 L4 약 25분 ≈ ₩600(추정) + 검수 18회. **승인 전에는 Step 3 이후를 하지 않는다.**

- [ ] **Step 3: 프록시를 열고 서비스를 깨운다**

8091 에 이미 떠 있는 프록시가 있는지 먼저 본다(`netstat -ano | findstr 8091`). 없으면 PowerShell 에서 백그라운드로:
`gcloud run services proxy daengs-cardgen-klein --region=asia-southeast1 --port=8091`
그다음 `curl -s http://127.0.0.1:8091/health` 를 30초 간격으로 확인해 `"ready": true` 가 될 때까지 기다린다(09-15 로드 425~430초). `"error"` 가 차면 멈추고 서비스 로그(`gcloud logging read`, Bash)를 보고 확인/추정으로 보고한다.

- [ ] **Step 4: 세 조건을 차례로 돌린다** (`backend/` 에서, 한 조건씩 끝난 것을 확인하고 다음)

```bash
P=../cardimage/test/KakaoTalk_20260913_220514335.jpg,../cardimage/test/KakaoTalk_20260913_220514335_03.jpg,../cardimage/test/KakaoTalk_20260913_220514335_08.jpg
uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 --photos $P --months 4,9 --seeds 1 --panel-text --out ../cardimage/out/_cardgen/e1-panel
uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 --photos $P --months 4,9 --seeds 1 --gen-size 1280x2048 --out ../cardimage/out/_cardgen/e1-2048
uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 --photos $P --months 4,9 --seeds 1 --panel-text --gen-size 1280x2048 --out ../cardimage/out/_cardgen/e1-both
```

각 조건이 끝나면 `results.jsonl` 6줄과 `service.size`(`1024x1632` / `1280x2048`)를 확인한다. 한 장이라도 실패하면 **다음 조건으로 넘어가지 않고** 원인을 확인/추정으로 갈라 보고한다. 끝나면 프록시를 끈다(인스턴스는 약 10분 뒤 스스로 내려간다 — 그 시간도 과금).

- [ ] **Step 5: 격자를 만들어 사람에게 보인다**

```bash
N4=KakaoTalk_20260913_220514335_4_s1,KakaoTalk_20260913_220514335_03_4_s1,KakaoTalk_20260913_220514335_08_4_s1
N9=KakaoTalk_20260913_220514335_9_s1,KakaoTalk_20260913_220514335_03_9_s1,KakaoTalk_20260913_220514335_08_9_s1
C="--col 기준=../cardimage/out/_cardgen/task8-klein --col 문구=../cardimage/out/_cardgen/e1-panel --col 2048=../cardimage/out/_cardgen/e1-2048 --col 둘다=../cardimage/out/_cardgen/e1-both"
uv run python tools/cardgen_grid.py $C --names $N4 --out ../cardimage/out/_cardgen/e1_grid_4.png
uv run python tools/cardgen_grid.py $C --names $N9 --out ../cardimage/out/_cardgen/e1_grid_9.png
```

격자와 개별 카드를 Read 로 직접 보고, 카드마다 **아래 패널 문구 정확(눈) · 닮음 · 목줄 · 장당 시간(`service.seconds`)** 을 표로 만든다. 문구는 확대해서 본다 — 검수 `text_ok` 만 믿지 않는다.

---

### Task 5: 기록 — 컨트롤러

**Files:**
- Create: `docs/cardimage/compare-2026-09-16-klein-e1.md`
- Modify: `docs/cardimage/worklog.md`(09-16 절), `docs/cardimage/roadmap.md`(표 2번 진행 · 체크리스트 E1 · E1 절에 결론 한 줄), PR #557 본문 작업 목록 체크

- [ ] **Step 1: 비교 문서를 쓴다** — 구성은 `compare-2026-09-15-cardgen.md` 를 따른다: 조건 표(무엇을 바꿨나), 결과 표(조건 × 카드: 문구 정확 · 닮음 · 목줄 · 장당 시간), 잰 것과 추정 분리, **결론 한 문장**(어느 조건을 3번 카드 기본값으로 가져갈지 또는 추가 실험). 실제 과금 구간은 서비스 로그의 인스턴스 시작~종료로 적는다(없으면 "추정").
- [ ] **Step 2: worklog · roadmap · PR 본문을 갱신한다** — roadmap 표 2번 진행 `▰▱▱▱▱ 1/5`, 체크리스트 E1 `[x]`, E1 절 끝에 결론 한 줄과 비교 문서 링크. PR 본문 작업 목록의 E1·E1 도구 `[x]`.
- [ ] **Step 3: 커밋·push** (`uv run check` 뒤)

```bash
git add docs/cardimage/compare-2026-09-16-klein-e1.md docs/cardimage/worklog.md docs/cardimage/roadmap.md
git commit -m "FLUX.2-klein-4B 글씨 유지 실험(E1) 결과와 결론을 적는다 (#557)"
git push
```
