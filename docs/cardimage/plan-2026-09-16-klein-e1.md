# FLUX.2-klein-4B 실험 구현 계획 — E1 글씨 유지 · E2 4장 뽑기 · 이미지 하나로 맞추기 · E3 콜드 스타트 (#557)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (E1) FLUX.2-klein-4B 카드의 아래 패널 문구 깨짐(09-15 `PETAL PAUSE` → `PETL PPAUSE`, 3/12)이 ① 프롬프트에 문구 명시 ② 생성 크기 1280×2048 ③ 둘 다 중 무엇으로 줄어드는지 18장으로 판정한다. (E2) 한 요청에 4장을 뽑을 때 순차 4회와 한 번에 4장을 비교하고 사진별 "쓸 만한 장" 수를 센다. (이미지) 서비스·잡을 새 이미지 한 태그로 맞추고 옛 cardgen 이미지 둘을 지운다. (E3) GCS FUSE 마운트 옵션으로 모델 로드 425~430초가 줄어드는지 잰다. E4(비용 실측)는 보류, E5 는 안 한다(사진 안내 문구로 대신 — 09-16 사용자).

**Architecture:** E1 은 클라이언트 쪽만 넓힌다 — `HttpCardImageEngine` 에 생성 크기 인자(기본값 그대로), 비교 도구 `tools/cardgen_compare.py` 에 `--gen-size`·`--panel-text`(엔진을 감싸 프롬프트 끝에 문구 문장을 붙인다)·영문 이름 기본값, 조건별 결과를 한 장에 모으는 `tools/cardgen_grid.py`. 그 뒤 서비스에서 Qwen 을 걷어내고 `/generate` 에 `count`(1~4, 기본 1 은 지금과 같은 PNG 응답)를 더해 이미지를 **한 번** 새로 굽고, 서비스·잡을 그 태그로 맞춘 다음 E2·E3 를 돈다. 유료 실행·배포·삭제와 기록은 컨트롤러가 한다.

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
- **밤새 무인 진행 규칙 (09-16 사용자 "E1,E2,E3,이미지 하나로 맞추기 쭉 진행"):** 비용은 크레딧이라 매 실행 승인 대신 아래 상한으로 대신한다 — L4 가 떠 있는 시간 **누적 3시간**을 넘기면 멈춘다. 같은 원인으로 **두 번** 실패하면 그 실험을 멈추고 원인을 확인/추정으로 적은 뒤 다음으로. 품질 판정은 컨트롤러가 잠정으로 하고 격자를 남긴다 — 3번 카드 기본값 같은 결정은 사람 몫.
- **하지 않는 것:** dev 머지 · VM `DAENGS_CARDGEN_URL` · **가중치 잡 실행**(`jobs execute` — 버킷을 다시 쓴다. 잡은 이미지 태그만 바꾼다) · 가중치를 이미지에 굽기(15GB — 공식 권장은 10GB 미만, 빌드 비용·시간 큼) · 의존성 추가/삭제(`bitsandbytes` 는 Qwen 을 걷어내도 그대로 둔다 — 경계 테스트가 그룹 구성을 본다).
- **Windows 에서 gcloud:** 쉼표가 든 인자는 PowerShell 에서 따옴표로. Git Bash 는 `MSYS2_ARG_CONV_EXCL` (cardgen.sh 가 설정). `gcloud logging read` 는 Bash. 10분 넘는 명령(Cloud Build · 전체 pytest · 비교 실행)은 `Start-Process` 분리 프로세스 + 로그 폴링(`infra/gcp/README.md` 「cardgen.sh」 표).

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

---

### Task 6: 서비스에서 Qwen-Image-Edit-2511 을 걷어낸다

**Files:**
- Modify: `backend/src/daengs_cardgen/diffusion.py` (QwenModel · QWEN_* · `os` import · `MODEL_REPOS` 의 qwen 항목 · 모듈 docstring)
- Modify: `backend/src/daengs_cardgen/app.py` (모듈 docstring 4행)
- Modify: `backend/tests/test_cardgen_diffusion.py`
- Modify: `infra/gcp/cardgen.sh` (12~20행 MODEL 분기, 84행 주석, 93행 `CARDGEN_QWEN_QUANT`)
- Modify: `infra/gcp/cardgen-teardown.sh` (14행 서비스 목록)
- Modify: `docker/cardgen/Dockerfile` (24행·38~39행 주석만 — 명령은 그대로)

**Interfaces:**
- Consumes: 없음
- Produces: `MODEL_REPOS == {"klein-4b": "black-forest-labs/FLUX.2-klein-4B"}`, `MODELS == {"klein-4b": KleinModel}`, `KLEIN_DEFAULTS`, `resolve`, `model_by_name`. `cardgen.sh` 는 `MODEL=klein` 만 받는다.

- [ ] **Step 1: 테스트를 먼저 klein 하나로 바꾼다**

`backend/tests/test_cardgen_diffusion.py` 를 아래로 바꾼다(Qwen 테스트 셋 삭제):

```python
"""모델 선택과 기본값 (D-078). GPU·가중치 없이 도는 부분만 — 실제 추론은 Cloud Run 에서 확인한다.

Qwen-Image-Edit-2511 은 09-15 L4 결과가 깨져 D-078 에서 뺐다 — 후보는 FLUX.2-klein-4B 하나다 (#557)."""

import pytest
from PIL import Image

from daengs_cardgen.diffusion import KLEIN_DEFAULTS, MODEL_REPOS, KleinModel, model_by_name, resolve
from daengs_cardgen.models import EditRequest


def _req(**over) -> EditRequest:
    base = {"images": [Image.new("RGB", (8, 8))], "prompt": "p", "seed": 1, "width": 1024, "height": 1632}
    base.update(over)
    return EditRequest(**base)


def test_model_by_name_knows_only_klein() -> None:
    assert MODEL_REPOS == {"klein-4b": "black-forest-labs/FLUX.2-klein-4B"}
    assert isinstance(model_by_name("klein-4b"), KleinModel)
    with pytest.raises(ValueError, match="klein-4b"):
        model_by_name("qwen-edit-2511")


def test_resolve_uses_model_defaults_unless_request_overrides() -> None:
    assert resolve(_req(), KLEIN_DEFAULTS) == (4, 1.0)
    assert resolve(_req(steps=8, guidance=2.5), KLEIN_DEFAULTS) == (8, 2.5)


def test_edit_before_load_is_a_clear_error() -> None:
    with pytest.raises(RuntimeError, match="load"):
        KleinModel().edit(_req())
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_cardgen_diffusion.py -v`
Expected: `test_model_by_name_knows_only_klein` FAIL (qwen 항목이 아직 있다).

- [ ] **Step 3: 코드에서 걷어낸다**

`diffusion.py` 머리를 이렇게:

```python
"""diffusers 편집 파이프라인 (D-078). torch·diffusers 는 `load()`·`edit()` 안에서만 import 한다.

FLUX.2-klein-4B 는 약 13GB 라 L4(24GB)에 bf16 그대로 올린다. Qwen-Image-Edit-2511 은 09-15 L4 nf4 결과가
깨져(틀 강아지가 남고 노이즈) D-078 에서 뺐다 — 코드도 #557 에서 걷어냈다.
"""

from __future__ import annotations

from typing import Any

from PIL import Image

from daengs_cardgen.models import CardGenModel, EditRequest

MODEL_REPOS = {
    "klein-4b": "black-forest-labs/FLUX.2-klein-4B",
}
KLEIN_DEFAULTS = {"steps": 4, "guidance": 1.0}      # 증류판 권장값 (모델 카드)
```

`class QwenModel` 전체와 `QWEN_DEFAULTS`·`QWEN_QUANTS` 를 지우고, 맨 아래를 `MODELS: dict[str, type] = {KleinModel.name: KleinModel}` 로. `resolve`·`KleinModel`·`model_by_name` 은 그대로.

`app.py` docstring 3~4행을:

```python
**포트는 곧바로 열린다** — 모델은 lifespan 이 띄운 백그라운드 스레드가 올린다. Cloud Run 의 시작
프로브는 240초가 상한인데 FLUX.2-klein-4B 도 GCS FUSE 에서 425~430초 걸린다(09-15 실측).
```

`infra/gcp/cardgen.sh`:
- 4행 사용법은 그대로(`MODEL=klein`).
- 12~20행을:

```bash
: "${MODEL:?klein}"
: "${INVOKER:?user:<gcloud 계정> — gcloud run services proxy 로 부를 사람}"
PROJECT="${PROJECT:-daengs}"
STEP="${STEP:-all}"
case "${MODEL}" in
  klein) MODEL_NAME=klein-4b ;;
  *) echo "MODEL 은 klein (Qwen-Image-Edit-2511 은 D-078 에서 뺐다)" >&2; exit 2 ;;
esac
```

- 84행 주석을 `# Cloud Run 시작 프로브는 240초가 상한이라 "다 올린 뒤 포트를 연다" 는 FLUX.2-klein-4B(425~430초)에서도 못 맞춘다.` 로.
- 93행을 `    --set-env-vars="CARDGEN_MODEL=${MODEL_NAME},HF_HUB_OFFLINE=1"` 로.

`infra/gcp/cardgen-teardown.sh` 14행을 `for s in daengs-cardgen-klein; do` 로.

`docker/cardgen/Dockerfile` 주석만:
- 24행: `# PYTORCH_CUDA_ALLOC_CONF: 2026-09-15 Qwen OOM 때 넣었다("1.97GiB reserved but unallocated") — 조각난 캐시를 다시 쓰게 한다. FLUX.2-klein-4B 에도 해가 없어 둔다.`
- 38~39행: `# torchvision 도 같이 덮어쓴다 — 처음엔 Qwen2VLProcessor 때문에 넣었다. FLUX.2-klein-4B 만 남은 지금 빼도 되는지는` / `# 확인 안 했다(빼려면 GPU 에서 다시 로드해 봐야 한다). torch 만 cu126 으로 바꾸면` 로 바꾸고 40행(`# torchvision(+cpu)과 ABI 가 어긋나므로 둘을 한 명령으로 맞춘다.`)은 그대로.

- [ ] **Step 4: 통과와 잔여를 확인한다**

Run: `uv run pytest tests/test_cardgen_diffusion.py tests/test_cardgen_app.py tests/test_cardgen_boundary.py tests/test_cardgen_fetch.py -v`
Expected: 전부 PASS

Grep(도구)로 `(?i)qwen` 을 `backend/src/daengs_cardgen`·`backend/tests/test_cardgen_*`·`infra/gcp/cardgen*.sh` 에서 찾는다.
Expected: diffusion.py docstring 과 test docstring 의 "뺐다" 설명, cardgen.sh 오류 메시지만 남는다.

Run (Git Bash): `bash -n infra/gcp/cardgen.sh && bash -n infra/gcp/cardgen-teardown.sh`
Expected: 출력 없음, 종료 0

- [ ] **Step 5: 컨트롤러가 커밋한다**

```bash
git add backend/src/daengs_cardgen/diffusion.py backend/src/daengs_cardgen/app.py backend/tests/test_cardgen_diffusion.py infra/gcp/cardgen.sh infra/gcp/cardgen-teardown.sh docker/cardgen/Dockerfile
git commit -m "GPU 서비스에서 Qwen-Image-Edit-2511 을 걷어낸다 — D-078 에서 뺀 모델 (#557)"
```

---

### Task 7: 서비스 `/generate` 에 `count` — 한 요청에 여러 장

**Files:**
- Modify: `backend/src/daengs_cardgen/models.py`
- Modify: `backend/src/daengs_cardgen/diffusion.py` (`KleinModel.edit`)
- Modify: `backend/src/daengs_cardgen/app.py`
- Test: `backend/tests/test_cardgen_app.py`

**Interfaces:**
- Consumes: Task 6 의 `diffusion.py`
- Produces:
  - `MAX_COUNT = 4`, `EditRequest.count: int = 1` (마지막 필드), `CardGenModel.edit(req) -> list[Image.Image]` (**길이 = req.count**)
  - `seeds_for(seed: int, count: int) -> list[int]` — `[(seed + i) % 2**31 for i in range(count)]`
  - HTTP: 요청 JSON 에 `"count": 1..4`(기본 1). **count 가 1 이면 응답은 지금과 글자 그대로 같은 PNG.** count 가 2 이상이면 `200 application/json` `{"model", "size": "WxH", "seconds": float, "seeds": [int…], "images_png_b64": [str…]}` + 같은 `X-Cardgen-*` 헤더.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_cardgen_app.py` 의 `FakeModel.edit` 을 리스트로 바꾸고(색으로 장 번호를 구분) import 에 `seeds_for` 를 더한다:

```python
from daengs_cardgen.models import EditRequest, seeds_for, snap
```

```python
    def edit(self, req: EditRequest) -> list[Image.Image]:
        self.requests.append(req)
        return [Image.new("RGB", (req.width, req.height), (1, 2, i)) for i in range(req.count)]
```

파일 끝에 더한다:

```python
def test_seeds_for_counts_up_and_wraps() -> None:
    assert seeds_for(7, 4) == [7, 8, 9, 10]
    assert seeds_for(2**31 - 1, 2) == [2**31 - 1, 0]


def test_count_one_is_the_same_png_response() -> None:
    fake = FakeModel()
    with TestClient(create_app(model=fake)) as client:
        response = client.post("/generate", json=_body(count=1))
    assert response.headers["content-type"] == "image/png"
    assert fake.requests[0].count == 1


def test_count_many_returns_json_with_seeds_and_images() -> None:
    fake = FakeModel()
    with TestClient(create_app(model=fake)) as client:
        response = client.post("/generate", json=_body(count=3))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["X-Cardgen-Size"] == "992x1584"
    data = response.json()
    assert (data["model"], data["size"], data["seeds"]) == ("fake", "992x1584", [7, 8, 9])
    assert data["seconds"] >= 0
    pixels = [Image.open(io.BytesIO(base64.b64decode(b))).getpixel((0, 0)) for b in data["images_png_b64"]]
    assert pixels == [(1, 2, 0), (1, 2, 1), (1, 2, 2)]
    assert fake.requests[0].count == 3


def test_count_is_validated() -> None:
    with TestClient(create_app(model=FakeModel())) as client:
        assert client.post("/generate", json=_body(count=0)).status_code == 422
        assert client.post("/generate", json=_body(count=5)).status_code == 422
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_cardgen_app.py -v`
Expected: import 단계에서 FAIL — `cannot import name 'seeds_for'`.

- [ ] **Step 3: 구현한다**

`models.py`:

```python
#: 한 요청의 이미지 수 상한. 우리는 틀·사진 두 장만 쓴다 — klein 호스팅 API 의 상한(4)에 맞췄다.
MAX_IMAGES = 4
#: 한 요청에 뽑는 장수 상한 — "4장 뽑아 고르기"(roadmap 3번). L4 메모리는 #557 E2 에서 잰다.
MAX_COUNT = 4
SIZE_STEP = 16


@dataclass(frozen=True)
class EditRequest:
    images: list[Image.Image]
    prompt: str
    seed: int
    width: int
    height: int
    steps: int | None = None
    guidance: float | None = None
    count: int = 1


class CardGenModel(Protocol):
    name: str

    def load(self) -> None: ...

    def edit(self, req: EditRequest) -> list[Image.Image]:
        """`req.count` 장을 돌려준다. i 번째 장의 seed 는 `seeds_for(req.seed, req.count)[i]`."""
        ...


def seeds_for(seed: int, count: int) -> list[int]:
    """장마다 다른 seed — 요청 seed 부터 1씩(2**31 에서 0 으로 돈다). 운영에서는 이 값을 장별로 저장한다(roadmap 3번)."""
    return [(seed + i) % 2**31 for i in range(count)]
```

(`snap` 은 그대로 둔다.)

`diffusion.py` `KleinModel.edit`:

```python
    def edit(self, req: EditRequest) -> list[Image.Image]:
        if self._pipe is None:
            raise RuntimeError("load() 를 먼저 불러야 합니다")
        import torch

        steps, guidance = resolve(req, KLEIN_DEFAULTS)
        # 장마다 자기 seed 의 generator — count=1 은 지금과 같은 단일 generator(09-15 결과와 같은 입력).
        seeds = seeds_for(req.seed, req.count)
        generators = [torch.Generator("cuda").manual_seed(s) for s in seeds]
        result = self._pipe(
            image=req.images, prompt=req.prompt, width=req.width, height=req.height,
            num_inference_steps=steps, guidance_scale=guidance,
            num_images_per_prompt=req.count,
            generator=generators[0] if req.count == 1 else generators,
        )
        return list(result.images)
```

import 줄: `from daengs_cardgen.models import CardGenModel, EditRequest, seeds_for`

`app.py`:
- import: `from daengs_cardgen.models import MAX_COUNT, MAX_IMAGES, CardGenModel, EditRequest, seeds_for, snap`
- `GenerateBody` 에 `count: int = Field(default=1, ge=1, le=MAX_COUNT)` 를 마지막 필드로.
- `EditRequest(...)` 에 `count=body.count` 를 더한다.
- `with lock:` 부터 끝까지를:

```python
        with lock:
            outs = current.edit(req)
        seconds = time.monotonic() - started
        headers = {
            "X-Cardgen-Model": current.name,
            "X-Cardgen-Seconds": f"{seconds:.1f}",
            "X-Cardgen-Size": f"{req.width}x{req.height}",
        }
        pngs = []
        for out in outs:
            buf = io.BytesIO()
            out.save(buf, "PNG")
            pngs.append(buf.getvalue())
        if req.count == 1:
            return Response(pngs[0], media_type="image/png", headers=headers)
        return JSONResponse(
            {
                "model": current.name, "size": f"{req.width}x{req.height}", "seconds": round(seconds, 1),
                "seeds": seeds_for(req.seed, req.count),
                "images_png_b64": [base64.b64encode(p).decode() for p in pngs],
            },
            headers=headers,
        )
```

모듈 docstring 끝에 한 줄: `` `count`(1~4)는 한 요청에 여러 장 — 1 이면 PNG 한 장, 2 이상이면 JSON(장별 seed·PNG base64) (#557 E2). ``

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_cardgen_app.py tests/test_cardgen_diffusion.py tests/test_cardgen_boundary.py -v`
Expected: 전부 PASS (기존 `test_generate_returns_png_at_snapped_size_and_passes_request` 포함)

- [ ] **Step 5: 컨트롤러가 커밋한다**

```bash
git add backend/src/daengs_cardgen/models.py backend/src/daengs_cardgen/diffusion.py backend/src/daengs_cardgen/app.py backend/tests/test_cardgen_app.py
git commit -m "GPU 서비스가 한 요청에 여러 장(count 1~4)을 장별 seed 로 뽑는다 — 1 장은 지금 응답 그대로 (#557 E2)"
```

---

### Task 8: 클라이언트 여러 장 — `generate_batch` 와 비교 도구 `--batch`

**Files:**
- Modify: `backend/src/daengs_cardimage/engine.py` (`HttpCardImageEngine`)
- Modify: `backend/tests/test_cardimage_engine_http.py`
- Modify: `backend/tools/cardgen_compare.py`
- Modify: `backend/tests/test_cardgen_compare_tool.py`

**Interfaces:**
- Consumes: Task 1 의 `HttpCardImageEngine(gen_size=…)`, Task 2 의 `PromptSuffixEngine`·명령줄, Task 7 의 HTTP 계약(count ≥ 2 → JSON)
- Produces:
  - `HttpCardImageEngine.generate_batch(*, template_png: bytes, photo_jpeg: bytes, prompt: str, count: int) -> list[bytes]` — 장마다 `CARD_SIZE` PNG. `last_meta = {"seeds": [...], "seconds": float, "model": str, "size": "WxH", "count": int}`. 응답이 JSON 이 아니거나 장수가 다르면 `EngineError("no_image", …)`.
  - 도구: `class BatchReplayEngine(inner, count)` — 첫 `generate` 에서 `inner.generate_batch` 를 한 번 부르고 이후 호출마다 다음 장을 준다. `last_meta` 는 방금 준 장의 `{"seed", "index", "batch_seconds", "model", "size", "count"}`.
  - 명령줄 `--batch N`(2~4, cardgen 엔진만): 사진·달·seed 마다 서비스 1회 호출로 N장 → 카드 이름 `<사진>_<달>_s<seed>_b<i>`. 결과 행에 `"batch": N` (없으면 `null`).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_cardimage_engine_http.py` 끝에:

```python
def test_generate_batch_decodes_each_image_and_keeps_seeds() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        images = [base64.b64encode(png(1024, 1632, (i, i, i))).decode() for i in range(3)]
        return httpx.Response(200, json={"model": "fake", "size": "1024x1632", "seconds": 9.5,
                                         "seeds": [11, 12, 13], "images_png_b64": images})

    engine = _engine(handler)
    outs = engine.generate_batch(template_png=png(), photo_jpeg=b"j", prompt="P", count=3)

    assert seen["body"]["count"] == 3 and seen["body"]["seed"] == 11
    assert [Image.open(io.BytesIO(o)).size for o in outs] == [(994, 1582)] * 3
    assert engine.last_meta == {"seeds": [11, 12, 13], "seconds": 9.5, "model": "fake",
                                "size": "1280x2048" if False else "1024x1632", "count": 3}


def test_generate_batch_wrong_count_is_no_image() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "fake", "size": "1024x1632", "seconds": 1.0, "seeds": [11],
                                         "images_png_b64": [base64.b64encode(png()).decode()]})

    with pytest.raises(EngineError) as info:
        _engine(handler).generate_batch(template_png=png(), photo_jpeg=b"j", prompt="P", count=2)
    assert info.value.code == "no_image"


def test_generate_batch_non_200_is_upstream() -> None:
    engine = _engine(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(EngineError) as info:
        engine.generate_batch(template_png=png(), photo_jpeg=b"j", prompt="P", count=2)
    assert info.value.code == "upstream"
```

`backend/tests/test_cardgen_compare_tool.py` 끝에 (import 줄에 `BatchReplayEngine` 추가):

```python
class _BatchInner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.last_meta = None

    def generate_batch(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str, count: int) -> list[bytes]:
        self.calls.append((prompt, count))
        self.last_meta = {"seeds": [5, 6], "seconds": 3.0, "model": "fake", "size": "1024x1632", "count": count}
        return [b"c0", b"c1"]


def test_batch_replay_calls_service_once_and_hands_out_each_card() -> None:
    inner = _BatchInner()
    engine = BatchReplayEngine(inner, 2)
    first = engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="P")
    assert engine.last_meta == {"seed": 5, "index": 0, "batch_seconds": 3.0, "model": "fake",
                                "size": "1024x1632", "count": 2}
    second = engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="P")
    assert (first, second) == (b"c0", b"c1")
    assert engine.last_meta["seed"] == 6 and engine.last_meta["index"] == 1
    assert inner.calls == [("P", 2)]
    with pytest.raises(RuntimeError):
        engine.generate(template_png=b"t", photo_jpeg=b"p", prompt="P")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `uv run pytest tests/test_cardimage_engine_http.py tests/test_cardgen_compare_tool.py -v`
Expected: FAIL — `AttributeError: ... 'generate_batch'` 와 `ImportError: cannot import name 'BatchReplayEngine'`.

- [ ] **Step 3: 구현한다**

먼저 Step 1 의 첫 테스트에서 `"size": "1280x2048" if False else "1024x1632"` 를 `"size": "1024x1632"` 로 정리한다(값은 같다).

`engine.py` 의 `HttpCardImageEngine` — 요청 몸통과 호출을 두 메서드가 같이 쓰게 뽑고, `generate_batch` 를 더한다:

```python
    def _post(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str, count: int) -> tuple[int, httpx.Response]:
        if not self._base:
            raise EngineError("no_key", "DAENGS_CARDGEN_URL 이 비어 있습니다")
        seed = self._seed if self._seed is not None else random.randrange(2**31)
        body = {
            "images_b64": [base64.b64encode(template_png).decode(), base64.b64encode(photo_jpeg).decode()],
            "prompt": prompt, "seed": seed, "width": self._gen_size[0], "height": self._gen_size[1],
        }
        if count != 1:
            body["count"] = count
        try:
            headers = {"Authorization": f"Bearer {self._auth(self._base)}"} if self._auth else {}
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                resp = client.post(f"{self._base}/generate", json=body, headers=headers)
        except Exception as exc:  # 토큰 발급 실패까지 "응답을 못 받은" 것으로 모은다 (realtime_client 와 같은 판단)
            raise EngineError("upstream", f"카드 생성 서비스 호출 실패: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise EngineError("upstream", f"카드 생성 서비스가 {resp.status_code} 을 돌려줬습니다: {resp.text[:200]!r}")
        return seed, resp

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        seed, resp = self._post(template_png=template_png, photo_jpeg=photo_jpeg, prompt=prompt, count=1)
        self.last_meta = {"seed": seed, "seconds": resp.headers.get("X-Cardgen-Seconds"),
                          "model": resp.headers.get("X-Cardgen-Model"),
                          "size": f"{self._gen_size[0]}x{self._gen_size[1]}"}
        return _decode_and_fit(resp.content, pad=0, padded_width=CARD_SIZE[0])

    def generate_batch(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str, count: int) -> list[bytes]:
        """한 요청에 `count` 장(서비스 `count`, #557 E2). 장마다 카드 크기 PNG, 장별 seed 는 `last_meta["seeds"]`."""
        _, resp = self._post(template_png=template_png, photo_jpeg=photo_jpeg, prompt=prompt, count=count)
        try:
            data = resp.json()
            images = [base64.b64decode(b) for b in data["images_png_b64"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise EngineError("no_image", f"여러 장 응답을 읽을 수 없습니다: {exc}") from exc
        if len(images) != count:
            raise EngineError("no_image", f"{count} 장을 요청했는데 {len(images)} 장이 왔습니다")
        self.last_meta = {"seeds": data.get("seeds"), "seconds": data.get("seconds"), "model": data.get("model"),
                          "size": data.get("size"), "count": count}
        return [_decode_and_fit(image, pad=0, padded_width=CARD_SIZE[0]) for image in images]
```

(기존 `generate` 본문은 위 `_post` + `generate` 로 대체된다. docstring 에 `generate_batch` 한 줄을 더한다.)

`tools/cardgen_compare.py`:
- `PromptSuffixEngine` 뒤에:

```python
class BatchReplayEngine:
    """서비스를 한 번 불러 N장을 받아 두고, `generate_card` 가 부를 때마다 다음 장을 준다 (#557 E2).
    `generate_card(judge_min=1)` 은 카드 한 장에 엔진을 한 번만 부르므로 N번 부르면 N장이 된다."""

    def __init__(self, inner, count: int) -> None:
        self._inner, self._count = inner, count
        self._cards: list[bytes] | None = None
        self._next = 0
        self.last_meta: dict | None = None

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
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
```

- 인자: `parser.add_argument("--batch", type=int, default=0, help="cardgen 한 요청에 N장(2~4) — 카드 이름에 _b<i> (#557 E2)")`
- 검증(`--url` 검사 뒤): `if args.batch and (args.engine != "cardgen" or not 2 <= args.batch <= 4): print("--batch 는 cardgen 엔진에서 2~4", file=sys.stderr); return 2`
- seed 루프 안을 이렇게 바꾼다 — 엔진을 만든 뒤 `copies` 번 카드를 만든다:

```python
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
                    )
                    seconds = round(time.monotonic() - started, 1)
                    name = f"{photo_path.stem}_{month}_s{seed}" + (f"_b{copy}" if args.batch else "")
                    # (아래 저장·행 쓰기는 기존과 같고, row 에 "batch": args.batch or None 을 "panel_text" 뒤에 더한다)
```

  기존 저장·`row`·`results.jsonl`·`print` 코드는 이 `for copy` 블록 안으로 한 단계 들여쓰고, `row` 에 `"batch": args.batch or None` 을 더한다.
- 모듈 docstring 의 E1 예시 뒤에 E2 예시 한 줄: `# #557 E2 — 한 요청에 4장: --batch 4 (순차 4회 비교는 --seeds 1,2,3,4)`

- [ ] **Step 4: 통과를 확인한다**

Run: `uv run pytest tests/test_cardimage_engine_http.py tests/test_cardgen_compare_tool.py tests/test_ai_card_engine.py -v`
Expected: 전부 PASS

Run: `uv run python tools/cardgen_compare.py --engine gemini --photos x.jpg --batch 4 --out _`
Expected: 종료 코드 2 — 단, 이 검사는 키 확인 뒤에 있으므로 개발 PC 의 `backend/.env` 에 키가 있으면 `--batch 는 cardgen 엔진에서 2~4` 가, 없으면 키 오류가 나온다(둘 다 네트워크 호출 전).

- [ ] **Step 5: 컨트롤러가 커밋한다**

```bash
git add backend/src/daengs_cardimage/engine.py backend/tests/test_cardimage_engine_http.py backend/tools/cardgen_compare.py backend/tests/test_cardgen_compare_tool.py
git commit -m "카드 엔진과 비교 도구가 한 요청 여러 장을 받아 카드마다 나눠 만든다 (#557 E2)"
```

---

### Task 9: 새 이미지 한 번 굽기 · 서비스·잡 같은 태그 · 옛 이미지 삭제 — 컨트롤러

**Files:** 없음 (GCP 리소스). 기록은 Task 12.

- [ ] **Step 1: 로컬 게이트** — `uv run check`, cardgen 관련 테스트(`tests/test_cardgen_* tests/test_cardimage_engine_http.py tests/test_cardgen_compare_tool.py tests/test_cardgen_grid_tool.py`) PASS, 커밋이 모두 push 됐는지.
- [ ] **Step 2: 이미지 빌드** — Git Bash, 분리 프로세스(13~16분):
  `MODEL=klein PROJECT=daengs INVOKER=user:choiyc05@gmail.com STEP=image bash infra/gcp/cardgen.sh` → 로그의 태그를 기록한다. 빌드 뒤 **파일을 고치지 않는다**(태그 재계산 함정).
- [ ] **Step 3: 서비스 배포** — 같은 명령 `STEP=deploy`. 배포 뒤 `gcloud run services describe daengs-cardgen-klein --region=asia-southeast1 --format="value(spec.template.spec.containers[0].image,status.latestReadyRevisionName)"` 로 새 태그·새 리비전 확인. 그다음에만 `/health` 를 부른다(옛 리비전 호출 함정). 로드 완료까지 폴링 → `load_seconds` 기록(= E3 기준값). 1장 스모크: `cardgen_compare.py --photos <정면 사진> --months 4 --seeds 1 --out ../cardimage/out/_cardgen/smoke-count` 로 count=1 경로가 그대로 되는지.
- [ ] **Step 4: 잡 이미지만 바꾼다** — `gcloud run jobs update cardgen-weights --region=asia-southeast1 --image=<새 태그>` (PowerShell). **execute 하지 않는다.** `jobs describe` 로 이미지·command·args(`-m daengs_cardgen.fetch klein-4b`)·env(`HF_HUB_DISABLE_XET=1`) 가 그대로인지 본다.
- [ ] **Step 5: 옛 것 정리** — `gcloud run revisions list --service=daengs-cardgen-klein --region=asia-southeast1` 로 옛 리비전(`c917c96` 사용, 트래픽 0%)을 확인해 삭제 → `gcloud artifacts docker images list …/cardgen --include-tags` 로 digest 를 보고 `c917c96`·`90a42ef` 를 `--delete-tags` 로 삭제 → 목록에 새 태그 하나만 남는지.

---

### Task 10: E2 4장 뽑기 — 컨트롤러

**Files:** 없음 (산출물 `cardimage/out/_cardgen/e2-*/`)

조건은 E1 에서 가장 나은 조건(문구 명시 / 크기)을 쓴다 — 고른 이유를 ledger 에 룰링으로 적는다.

- [ ] **Step 1: 순차 4회** — `--seeds 1,2,3,4` 로 사진 3장 × 4·9월 = 24장, `--out ../cardimage/out/_cardgen/e2-seq`.
- [ ] **Step 2: 한 번에 4장** — `--seeds 1 --batch 4` 로 같은 24장, `--out ../cardimage/out/_cardgen/e2-batch`. 첫 호출이 CUDA OOM(503·500, 서비스 로그 `OutOfMemoryError`)이면 `--batch 2` 로 한 번 더 — 그것도 OOM 이면 "L4 에서 한 번에 여러 장 불가"로 기록하고 순차만 남긴다.
- [ ] **Step 3: 시간 비교** — 순차: 사진·달마다 `service.seconds` 4개 합. 한 번에: `service.batch_seconds`. 서비스 로그의 인스턴스 메모리 경고도 본다.
- [ ] **Step 4: 판정** — 사진·달마다 4장 격자(`cardgen_grid.py --col 순차=… --col 한번에=…`, 이름은 순차 `_s1.._s4`·한 번에 `_s1_b0.._b3` 라 열을 사진별로 따로 만든다: `--col` 을 폴더 둘로 주고 `--names` 에 두 이름 규칙을 모두 넣으면 없는 칸은 회색). 카드마다 쓸 만함 기준 = **문구 정확 · 닮음(눈, 검수 likeness ≥ 4 참고) · 목줄 없음 · 틀 강아지로 안 돌아감**. "4장 중 쓸 만한 장" 수를 사진·달별 표로. 서로 충분히 다른지(포즈·표정) 한 줄.

---

### Task 11: E3 콜드 스타트 — 컨트롤러

**Files:**
- Modify (마지막에 이긴 설정만): `infra/gcp/cardgen.sh` 배포 단계 `--add-volume` 한 줄

기준은 Task 9 Step 3 의 `load_seconds`. 변형마다 새 리비전을 만들고 → `latestReadyRevisionName` 확인 → `/health` 로 깨워 `ready` 까지 폴링 → `load_seconds` 와 서비스 로그의 컨테이너 시작~`cardgen model=… load_seconds=` 줄 시각을 적는다. 변형 사이 유휴 대기 없이 다음 변형으로 넘어간다.

공식 문서(docs.cloud.google.com/run/docs/configuring/services/gpu-best-practices, 09-16 조회): FUSE 는 `cache-dir=cr-volume:<in-memory 볼륨>` 또는 `enable-buffered-read=true` 를 권하고, 켜면 FUSE 메모리가 컨테이너 한도에 잡힌다. CLI 는 `mount-options="K=V;K=V"`.

- [ ] **Step 1: V1 buffered read** (PowerShell, 따옴표 주의):
  `gcloud run services update daengs-cardgen-klein --region=asia-southeast1 --remove-volume-mount=/models --remove-volume=weights "--add-volume=name=weights,type=cloud-storage,bucket=daengs-cardgen-weights,readonly=true,mount-options=enable-buffered-read=true" "--add-volume-mount=volume=weights,mount-path=/models"`
  `services describe --format=yaml(spec.template.spec.volumes)` 로 저장값 확인 → 측정.
- [ ] **Step 2: V2 파일 캐시 in-memory** — 메모리 32Gi 안에서 가중치 약 15GiB 캐시 + 모델 로드가 들어가는지가 관건. `--add-volume=name=fcache,type=in-memory,size-limit=16Gi` 를 더하고 weights 볼륨 `mount-options=cache-dir=cr-volume:fcache` 로 바꾼다(buffered read 는 cache-dir 이 우선이라 뺀다). 메모리 초과로 죽으면(로그 `Memory limit … exceeded`) 한 번만 `size-limit=15Gi` 로 재시도 없이 **기록만** 하고 V1 이나 기준으로 되돌린다.
- [ ] **Step 3: 이긴 설정으로 고정** — `load_seconds` 가 기준보다 **60초 이상** 짧은 변형이 있으면 그것을 서비스에 남기고 `cardgen.sh` 배포 단계 `--add-volume` 을 같게 고친다(주석에 09-16 실측 숫자). 없으면 기준 설정으로 되돌린다. 가중치 굽기·CPU boost 는 이유(15GB > 권장 10GB · GPU 문서에 CPU boost 언급 없음, `--no-cpu-throttling` 이미 켬)만 기록.
- [ ] **Step 4:** 끝나면 프록시를 끄고, L4 누적 시간을 ledger 에 합산한다.

---

### Task 12: 전체 게이트 · 기록 — 컨트롤러

**Files:**
- Create: `docs/cardimage/compare-2026-09-16-klein-e2-e3.md`
- Modify: `docs/cardimage/worklog.md` · `roadmap.md`(표 2번, 체크리스트 E2·E3·이미지 하나로 맞추기, E2·E3 절 결론 한 줄, 맨 위 「GCP 에 남은 것」 이미지 태그) · `infra/gcp/README.md`(cardgen 절 「남아 있는 것」 이미지 태그, 실측 줄에 E3 결과, 이미지 두 개 함정 행에 "09-16 하나로 맞춤") · `docs/cardimage/README.md` 「지금 상태」 · PR #557 본문(작업 목록 체크 · 남은 것)

- [ ] **Step 1:** 전체 `uv run pytest`(분리 프로세스, 약 9분) — 09-15 기준 무관 실패 13건 외 새 실패가 없는지 dev 대조.
- [ ] **Step 2:** 문서들을 쓴다. 잰 것 / 추정을 행으로 가르고 결론 한 문장씩. **아침에 사람이 볼 요약**(무엇을 했나 · 격자 경로 · 결정할 것)을 worklog 09-16 절 맨 위에.
- [ ] **Step 3:** `uv run check` → 커밋 → push → `gh pr edit 557 --body-file`.
- [ ] **Step 4:** 최종 브랜치 리뷰(가장 강한 모델, "태스크 경계를 넘는 값 흐름" — `count`·seed·`gen_size`·`panel_text` 가 서비스→엔진→도구→결과 행까지 맞는지)를 돌리고 지적을 반영한다.
