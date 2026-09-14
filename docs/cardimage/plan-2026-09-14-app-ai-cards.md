# 앱 사용자용 AI 도감 카드 생성 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 앱 사용자가 사진 한 장으로 AI 도감 카드를 만들고(`POST /app/ai-cards` → 202), 서버가 백그라운드에서 생성·보관한 카드를 조회·삭제할 수 있게 하고, 생성 로직을 `daengs_cardimage` 패키지로 뺀다.

**Architecture:** 생성 로직(`catalog·photo·title·engine·judge·generate`)은 `backend/src/daengs_cardimage/` 의 순수 패키지이고 `daengs_backend` 를 import 하지 않는다. backend 는 `services/ai_card_engine.py` 한 곳에서만 그것을 부른다. 앱 경로는 행을 `generating` 으로 커밋한 뒤 같은 프로세스에서 `asyncio` 작업으로 생성 → 저장 → `ready/failed` 로 갱신하고, 한도는 `services/ai_card_quota.py` 의 함수 하나가 판단한다.

**Tech Stack:** FastAPI · SQLAlchemy 2 async · Pillow · google-genai(엔진·검수, 테스트는 가짜) · pytest(`tests/fakes.py` 의 가짜 리포지토리 + 진짜 `LocalBridgeStorage`)

**Spec:** `docs/cardimage/spec-2026-09-14-app-ai-cards.md` (반드시 같이 읽는다) · PR #537

> **구현 중 바뀐 것 (2026-09-15):** 정리 기준 시각은 `created_at` 이 아니라 생성 차례를 얻은 시각 `updated_at` 이고(`expire_generating(..., stale_before=)`), 차례를 얻을 때 행을 다시 확인한다. 업로드 중 사용자 행을 잠그지 않도록 POST 는 `CurrentAppMemberTokenOnly` 로 받고 서비스가 잠근다. 실패한 유료 호출은 하루 5번까지. 결정은 D-076, 경위는 `worklog.md`.

## Global Constraints

- `daengs_cardimage` 는 `daengs_backend`·`fastapi`·`sqlalchemy`·`starlette`·`pydantic_settings` 를 import 하지 않는다.
- backend 에서 `daengs_cardimage` 의 생성·엔진·검수를 부르는 곳은 `services/ai_card_engine.py` 하나다(예외·상수·`title_text`·`prepare_photo` import 는 허용).
- 실제 Gemini 를 부르는 테스트는 없다. 엔진·검수는 `tests/cardimage_fakes.py` 의 `FakeEngine`·`FakeJudge`.
- 앱 API 오류 본문은 `{"code": ..., "message": ...}` 이고 `message` 는 앱이 그대로 띄울 한국어 문장이다. 운영자용 예외 메시지(환경 변수 이름 등)를 내보내지 않는다.
- 업로드는 요청 본문 원시 바이트, 메타는 쿼리. `python-multipart` 를 추가하지 않는다.
- 설정 기본값: `DAENGS_CARDIMAGE_DAILY_LIMIT=1`(0 이면 무제한) · `DAENGS_CARDIMAGE_CONCURRENCY=2`. 정리 기준 `stale = 4 × cardimage_timeout_ms + 60초`(기본 9분), 기준 시각은 `created_at`.
- 한도는 KST 하루 `status='ready'` 만 센다. 관리자 경로 `/admin/cardimage/generate` 에는 한도가 없다.
- `dog_id` 확인은 `pet_repo.get_accessible`(공동 돌봄 구성원 포함).
- 기본 DB 스키마 원본은 `db/init/*.sql`, 이미 있는 DB 는 `db/migrations/` 에 여러 번 돌려도 안전한 SQL + `verify_` 짝 + `tools/check_migration_verification.py` 의 `CHECKS` 등록.
- 백엔드 의존성은 바꾸지 않는다. 파일 읽기·쓰기는 전용 도구로(heredoc·sed 로 파일을 만들지 않는다). grep 은 Grep 도구로.
- 커밋 메시지는 한글 서술형, 접두사 없음, 끝에 저장소 규칙의 Co-Authored-By · Claude-Session 두 줄.
- **하위 에이전트는 커밋·stage·stash·push 를 하지 않는다.** 커밋은 컨트롤러가 한다. 전체 `uv run pytest` 는 컨트롤러가 마지막에 한 번 돌린다(에이전트는 자기 태스크 테스트만).

---

### Task 1: 생성 로직을 `daengs_cardimage` 로 옮기고 backend 의 호출 자리를 하나로

**Files:**
- Move: `backend/src/daengs_backend/services/cardimage/{__init__,catalog,photo,title,engine,judge,generate}.py` → `backend/src/daengs_cardimage/` (`__pycache__` 는 옮기지 않는다 — `git mv` 로 파일만)
- Modify: `backend/src/daengs_cardimage/__init__.py`, `backend/src/daengs_cardimage/generate.py`, 옮긴 모듈들의 import
- Create: `backend/src/daengs_backend/services/ai_card_engine.py`
- Modify: `backend/src/daengs_backend/routers/admin_cardimage.py`
- Modify: `backend/tests/test_cardimage_{title,judge,photo,engine,generate,admin_api,catalog}.py`, `backend/tests/cardimage_fakes.py`, `backend/tools/cardimage_title.py` (import 경로만)
- Modify: `backend/pyproject.toml` (휠 패키지 목록)
- Test: `backend/tests/test_cardimage_boundary.py`, `backend/tests/test_ai_card_engine.py`

**Interfaces:**
- Produces: 패키지 `daengs_cardimage` — `from daengs_cardimage import CardImageUnavailable, GeneratedCard, generate_card, catalog`; 서브모듈 `daengs_cardimage.{catalog,photo,title,engine,judge,generate}` (옛 `daengs_backend.services.cardimage.*` 와 이름·시그니처 동일, `generate.default_engine/default_judge` 만 없어짐)
- Produces: `daengs_backend.services.ai_card_engine` — `default_engine() -> CardImageEngine`, `default_judge() -> CardJudge`, `generate(*, photo: bytes, content_type: str, month: int, dog_name: str, engine: CardImageEngine, judge: CardJudge | None) -> GeneratedCard`, `ready_check(month: int) -> catalog.MonthCard` (닫힌 달 `MonthNotOpenError`, 키·틀·글꼴 없음 `CardImageUnavailable`)

- [ ] **Step 1: 실패하는 경계 테스트**

```python
# backend/tests/test_cardimage_boundary.py
"""`daengs_cardimage` 는 순수 생성 로직이다 — backend·웹·DB 를 import 하지 않는다 (D-076).

나중에 이 패키지만 별도 서비스로 뗄 수 있으려면 이 선이 지켜져야 한다. 설정은 인자로 받고,
설정을 읽어 엔진을 만드는 일은 `daengs_backend/services/ai_card_engine.py` 가 한다.
"""

import ast
import importlib.util
from pathlib import Path

FORBIDDEN = {"daengs_backend", "fastapi", "sqlalchemy", "starlette", "pydantic_settings"}


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def test_package_does_not_import_backend_web_or_db() -> None:
    import daengs_cardimage

    root = Path(daengs_cardimage.__file__).parent
    offenders = [
        f"{p.relative_to(root)}: {name}"
        for p in sorted(root.rglob("*.py"))
        for name in _imports(p)
        if name.split(".")[0] in FORBIDDEN
    ]
    assert offenders == []


def test_old_service_path_is_gone() -> None:
    assert importlib.util.find_spec("daengs_backend.services.cardimage") is None
```

- [ ] **Step 2: 실패 확인** — Run: `cd backend && uv run pytest tests/test_cardimage_boundary.py -v` → FAIL (`ModuleNotFoundError: No module named 'daengs_cardimage'`)

- [ ] **Step 3: 파일 이동**

```bash
cd backend
git mv src/daengs_backend/services/cardimage/__init__.py src/daengs_cardimage/__init__.py
git mv src/daengs_backend/services/cardimage/catalog.py src/daengs_cardimage/catalog.py
git mv src/daengs_backend/services/cardimage/photo.py src/daengs_cardimage/photo.py
git mv src/daengs_backend/services/cardimage/title.py src/daengs_cardimage/title.py
git mv src/daengs_backend/services/cardimage/engine.py src/daengs_cardimage/engine.py
git mv src/daengs_backend/services/cardimage/judge.py src/daengs_cardimage/judge.py
git mv src/daengs_backend/services/cardimage/generate.py src/daengs_cardimage/generate.py
```

`git mv` 는 대상 폴더를 만들지 않으면 실패한다 — 먼저 `mkdir -p src/daengs_cardimage` (이것은 명령 실행이라 Bash 로 한다). 옛 폴더에 `__pycache__` 만 남으면 지운다: `rm -rf src/daengs_backend/services/cardimage`. (`git mv` 는 stage 를 만든다 — 하위 에이전트는 **그대로 두고** 컨트롤러에게 알린다. 추가 `git add` 는 하지 않는다.)

- [ ] **Step 4: 옮긴 모듈의 import 를 새 경로로** — 옮긴 7개 파일에서 `daengs_backend.services.cardimage` 를 전부 `daengs_cardimage` 로 바꾼다 (Edit 도구). `catalog.py` 의 `from daengs_backend.services.cardimage.title import ...` 도 포함.

- [ ] **Step 5: `generate.py` 에서 설정 의존을 뺀다** — 다음을 **삭제**한다: `from daengs_backend.config import settings`, `default_engine()`·`default_judge()` 두 함수 전체. 그 결과 쓰지 않게 된 `GeminiCardImageEngine`·`GeminiCardJudge` import 도 삭제한다(`CardImageEngine`·`EngineError`·`build_prompt`·`CardJudge`·`JudgeError`·`JudgeResult` 는 남는다). `generate_card` 본문은 그대로다.

- [ ] **Step 6: `daengs_cardimage/__init__.py`**

```python
"""도감 카드 AI 생성 — 사진 한 장 + 달 + 이름 → 카드 PNG (#496 · #537, docs/cardimage/).

**순수 생성 로직이다.** DB·웹·`daengs_backend` 를 import 하지 않고 설정은 전부 인자로 받는다
(`tests/test_cardimage_boundary.py` 가 지킨다, D-076). 설정을 읽어 엔진을 만들고 이것을 부르는
자리는 backend 의 `services/ai_card_engine.py` 하나다 — 나중에 이 패키지만 별도 서비스로 떼면
그 한 곳이 HTTP 호출로 바뀐다.

흐름은 `generate.py`. 엔진(Nano Banana 2)과 검수(flash-lite)는 Protocol 뒤에 있다 — 테스트는 가짜로.
"""

from daengs_cardimage.generate import (  # noqa: F401
    CardImageUnavailable,
    GeneratedCard,
    generate_card,
)
```

- [ ] **Step 7: `services/ai_card_engine.py` 를 만든다**

```python
"""backend 가 도감 카드 생성(`daengs_cardimage`)을 부르는 **유일한 자리** (D-076).

설정을 읽어 엔진·검수를 만들고, `generate_card` 에 설정값을 넣어 부른다. 관리자 콘솔
(`routers/admin_cardimage.py`)과 앱 경로(`services/ai_card.py`)가 둘 다 이것을 쓴다.

나중에 생성을 별도 서비스(Cloud Run 등)로 떼면 **이 모듈 안에서** HTTP 호출로 갈라진다 —
D-070 이 `DAENGS_REALTIME_URL` 값에 따라 같은 프로세스 호출과 HTTP 를 가른 것과 같은 모양이다.
그러니 다른 모듈이 `daengs_cardimage.generate_card` 나 Gemini 어댑터를 직접 부르게 하지 말 것.
"""

from __future__ import annotations

from daengs_backend.config import settings
from daengs_cardimage import CardImageUnavailable, GeneratedCard, catalog, generate_card
from daengs_cardimage.engine import CardImageEngine, GeminiCardImageEngine
from daengs_cardimage.judge import CardJudge, GeminiCardJudge


def default_engine() -> CardImageEngine:
    """설정에서 실제 엔진을 만든다. 전역 `settings.gemini_api_key` 로 대체하지 않는다 —
    카드 생성 키는 `DAENGS_CARDIMAGE_GEMINI_API_KEY` 하나뿐이다(채팅 예산을 먹지 않게)."""
    return GeminiCardImageEngine(
        api_key=settings.cardimage_gemini_api_key.get_secret_value(),
        model=settings.cardimage_model,
        size=settings.cardimage_size,
        timeout_ms=settings.cardimage_timeout_ms,
    )


def default_judge() -> CardJudge:
    return GeminiCardJudge(
        api_key=settings.cardimage_gemini_api_key.get_secret_value(),
        model=settings.cardimage_judge_model,
        timeout_ms=settings.cardimage_timeout_ms,
    )


def generate(
    *,
    photo: bytes,
    content_type: str,
    month: int,
    dog_name: str,
    engine: CardImageEngine,
    judge: CardJudge | None,
) -> GeneratedCard:
    """동기 호출(20~60초)이다. 이벤트 루프에서는 `asyncio.to_thread` 로 부른다."""
    return generate_card(
        photo=photo,
        content_type=content_type,
        month=month,
        dog_name=dog_name,
        engine=engine,
        judge=judge,
        base_dir=settings.cardimage_dir,
        open_months=settings.cardimage_months,
        judge_min=settings.cardimage_judge_min,
    )


def ready_check(month: int) -> catalog.MonthCard:
    """**돈이 나가기 전에** 거를 수 있는 설정 문제를 먼저 본다.

    닫힌 달은 `MonthNotOpenError`, 키·틀·글꼴이 없으면 `CardImageUnavailable`. 앱 경로는 이것을
    행을 만들기 전에 불러, 어차피 실패할 요청이 한도를 먹거나 백그라운드로 가지 않게 한다.
    """
    card = catalog.require_open(month, settings.cardimage_months)
    if not settings.cardimage_gemini_api_key.get_secret_value().strip():
        raise CardImageUnavailable("DAENGS_CARDIMAGE_GEMINI_API_KEY 가 비어 있습니다")
    for path in (
        catalog.template_path(month, settings.cardimage_dir),
        catalog.font_path(settings.cardimage_dir),
    ):
        if not path.exists():
            raise CardImageUnavailable(f"카드 생성 자산이 없습니다: {path}")
    return card
```

- [ ] **Step 8: 관리자 라우터를 새 자리에 연결** — `routers/admin_cardimage.py` 에서
  - import 블록의 `from daengs_backend.config import settings` 와 `from daengs_backend.services.cardimage ...` 네 줄을 지우고 아래로 바꾼다:

```python
from daengs_backend.services import ai_card_engine
from daengs_backend.services.ai_card_engine import default_engine, default_judge
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError
from daengs_cardimage.engine import EngineError
from daengs_cardimage.photo import MAX_PHOTO_BYTES, PhotoError
```

  - `generate_card` 호출을 다음으로 바꾼다(모듈 수준 이름 `default_engine`·`default_judge` 는 테스트가 monkeypatch 하므로 **이름 그대로 이 모듈에서 부른다**):

```python
        card = await asyncio.to_thread(
            ai_card_engine.generate,
            photo=body,
            content_type=content_type,
            month=month,
            dog_name=dog_name,
            engine=default_engine(),
            judge=default_judge(),
        )
```

- [ ] **Step 9: 테스트·도구의 import 경로** — `tests/test_cardimage_{title,judge,photo,engine,generate,admin_api,catalog}.py`, `tests/cardimage_fakes.py`, `tools/cardimage_title.py` 에서 `daengs_backend.services.cardimage` → `daengs_cardimage`. `tools/cardimage_title.py` 머리 docstring 의 경로 설명도 같이 고친다. 테스트에서 `generate.default_engine`/`default_judge` 를 참조하는 곳이 있으면 `daengs_backend.services.ai_card_engine` 으로 바꾼다(없으면 그대로).

- [ ] **Step 10: 휠 패키지 목록** — `backend/pyproject.toml` 의 패키지 목록(`"daengs_backend",` 가 있는 배열)에 `"daengs_backend",` 바로 다음 줄로 `"daengs_cardimage",` 를 넣는다. 의존성이 아니므로 `uv add` 대상이 아니다. 그 뒤 `uv lock --check` 가 통과하는지 본다.

- [ ] **Step 11: `ai_card_engine` 테스트**

```python
# backend/tests/test_ai_card_engine.py
"""`services/ai_card_engine.py` — backend 가 카드 생성을 부르는 유일한 자리 (D-076)."""

import io

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from PIL import Image
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.services import ai_card_engine
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


def test_ready_check_passes_for_open_month_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    assert ai_card_engine.ready_check(4).month == 4


def test_ready_check_closed_month(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "cardimage_months", frozenset({4, 9}))
    with pytest.raises(MonthNotOpenError):
        ai_card_engine.ready_check(12)


def test_ready_check_without_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("   "))
    with pytest.raises(CardImageUnavailable):
        ai_card_engine.ready_check(4)


def test_ready_check_missing_assets_is_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "cardimage_dir", tmp_path)
    with pytest.raises(CardImageUnavailable):
        ai_card_engine.ready_check(4)


def test_generate_feeds_settings_into_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_months", frozenset({4, 9}))
    card = ai_card_engine.generate(
        photo=_jpeg(), content_type="image/jpeg", month=4, dog_name="네오",
        engine=FakeEngine(), judge=FakeJudge([5]),
    )
    assert card.title == "BLOSSOM 네오"
    assert card.attempts == 1
    assert Image.open(io.BytesIO(card.png)).size == (994, 1582)
```

- [ ] **Step 12: 통과 확인** — Run: `cd backend && uv run pytest tests/test_cardimage_boundary.py tests/test_ai_card_engine.py tests/test_cardimage_title.py tests/test_cardimage_judge.py tests/test_cardimage_photo.py tests/test_cardimage_engine.py tests/test_cardimage_generate.py tests/test_cardimage_admin_api.py tests/test_cardimage_catalog.py -v` → 전부 PASS. 그리고 `uv run python tools/cardimage_title.py --help` 가 import 오류 없이 도는지.

- [ ] **Step 13: 남은 옛 경로가 없는지** — Grep 도구로 저장소 전체(`docs/cardimage/plan-2026-09-14-phase1.md`·`worklog.md` 같은 기록 문서는 제외)에서 `services.cardimage` 와 `services/cardimage` 를 찾아 0건인지. `docs/cardimage/README.md` 는 Task 6 이 고치므로 남아도 된다.

- [ ] **Step 14: 커밋 (컨트롤러)**

```bash
git add -A backend/src/daengs_cardimage backend/src/daengs_backend/services backend/src/daengs_backend/routers/admin_cardimage.py backend/tests backend/tools/cardimage_title.py backend/pyproject.toml
git commit -m "카드 생성 로직을 daengs_cardimage 패키지로 옮기고 backend 의 호출 자리를 하나로 모았다"
```

---

### Task 2: 표 `ai_cards` — SQL 원본 · 마이그레이션 · verify · 하네스 등록 · 모델

**Files:**
- Create: `db/init/38_ai_cards.sql`, `db/migrations/2026-09-14_ai_cards.sql`, `db/migrations/verify_2026-09-14_ai_cards.sql`
- Modify: `tools/check_migration_verification.py` (`CHECKS` 맨 앞에 항목 하나)
- Create: `backend/src/daengs_backend/models/ai_card.py`
- Modify: `backend/src/daengs_backend/models/__init__.py` (import + `__all__`)
- Test: `backend/tests/test_ai_card_model.py`

**Interfaces:**
- Produces: 모델 `daengs_backend.models.AiCard` (칸: `id, app_user_id, dog_id, month, dog_name, title, status, error_code, storage_key, generation, size_bytes, width, height, likeness, attempts, created_at, updated_at`), 상수 `daengs_backend.models.AI_CARD_STATUSES = ("generating", "ready", "failed")`
- Produces: 인덱스 이름 `idx_ai_cards_owner_created` · `idx_ai_cards_storage_key`(부분 UNIQUE) · `idx_ai_cards_one_generating`(부분 UNIQUE, `status = 'generating'`), 트리거 `trg_ai_cards_updated_at`

- [ ] **Step 1: 실패하는 모델 테스트**

```python
# backend/tests/test_ai_card_model.py
"""`models/ai_card.py` 가 `db/init/38_ai_cards.sql` 을 따라가는지 (모델은 SQL 을 따라가는 쪽)."""

from daengs_backend.models import AI_CARD_STATUSES, AiCard


def test_columns_follow_sql() -> None:
    assert set(AiCard.__table__.c.keys()) == {
        "id", "app_user_id", "dog_id", "month", "dog_name", "title", "status", "error_code",
        "storage_key", "generation", "size_bytes", "width", "height", "likeness", "attempts",
        "created_at", "updated_at",
    }


def test_named_constraints_and_indexes() -> None:
    names = {c.name for c in AiCard.__table__.constraints if c.name}
    assert {
        "ai_cards_month", "ai_cards_dog_name", "ai_cards_status", "ai_cards_ready_set",
        "ai_cards_failed_code", "ai_cards_size", "ai_cards_likeness", "ai_cards_attempts",
    } <= names
    indexes = {i.name: i for i in AiCard.__table__.indexes}
    assert indexes["idx_ai_cards_one_generating"].unique
    assert "generating" in str(indexes["idx_ai_cards_one_generating"].dialect_options["postgresql"]["where"])
    assert indexes["idx_ai_cards_storage_key"].unique


def test_statuses() -> None:
    assert AI_CARD_STATUSES == ("generating", "ready", "failed")
```

- [ ] **Step 2: 실패 확인** — Run: `cd backend && uv run pytest tests/test_ai_card_model.py -v` → FAIL (`ImportError: cannot import name 'AI_CARD_STATUSES'`)

- [ ] **Step 3: SQL 원본** — `db/init/38_ai_cards.sql`:

```sql
-- ---------------------------------------------------------------------
-- ai_cards : 사진 한 장으로 서버가 만든 달 도감 카드 (#537, D-076, docs/cardimage/)
-- ---------------------------------------------------------------------
-- dog_cards(앱이 누끼 얼굴을 끼워 만든 카드)와 **별개다.** 이건 서버가 만든 카드 한 장 통째
-- PNG(994×1582) 이고, 그래서 id 도 서버가 만든다(POST).
--
-- 생성은 30~60초 걸리는 유료 호출이라 비동기다: POST 가 이 행을 'generating' 으로 커밋하고
-- 202 를 준 뒤, backend 프로세스 안의 백그라운드 작업이 'ready' 나 'failed' 로 바꾼다.
-- 배포 재시작과 겹쳐 사라진 작업은 조회 때 'failed' + error_code 'interrupted' 로 정리된다.
--
-- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴는 app_users 행을 남기므로 이 CASCADE 는 영영 안 돈다 —
--    탈퇴 경로가 명시로 지운다(dog_cards 와 같다). 저장소 객체는 FK 가 없어 더더욱 그렇다.
--
-- ⚠️ **원본 사진은 저장하지 않는다.** 백그라운드에 메모리로 넘기고 끝이다.

CREATE TABLE IF NOT EXISTS ai_cards (
    id UUID PRIMARY KEY,
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    -- 어느 아이로 만들었나. **아이를 지워도 카드는 남는다** (SET NULL).
    dog_id UUID REFERENCES pets(id) ON DELETE SET NULL,
    month SMALLINT NOT NULL,
    -- 카드에 **인쇄된** 이름. 개명해도 이미 만든 카드의 글자는 안 바뀐다.
    dog_name VARCHAR(40) NOT NULL,
    title VARCHAR(80) NOT NULL,
    status VARCHAR(16) NOT NULL,
    error_code VARCHAR(32),
    -- 아래 칸들은 'ready' 가 되어야 채워진다.
    storage_key VARCHAR(200),
    generation VARCHAR(64),
    size_bytes INTEGER,
    width SMALLINT,
    height SMALLINT,
    likeness SMALLINT,
    attempts SMALLINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ai_cards_month CHECK (month BETWEEN 1 AND 12),
    CONSTRAINT ai_cards_dog_name CHECK (length(btrim(dog_name)) > 0),
    CONSTRAINT ai_cards_status CHECK (status IN ('generating', 'ready', 'failed')),
    -- 'ready' 인데 이미지가 없으면 앱이 빈 카드를 받는다.
    CONSTRAINT ai_cards_ready_set CHECK (
        status <> 'ready' OR (
            storage_key IS NOT NULL AND generation IS NOT NULL AND size_bytes IS NOT NULL
            AND width IS NOT NULL AND height IS NOT NULL
        )
    ),
    -- 실패에는 이유가 있고, 실패가 아니면 이유가 없다.
    CONSTRAINT ai_cards_failed_code CHECK ((status = 'failed') = (error_code IS NOT NULL)),
    CONSTRAINT ai_cards_size CHECK (size_bytes IS NULL OR size_bytes > 0),
    CONSTRAINT ai_cards_likeness CHECK (likeness IS NULL OR likeness BETWEEN 1 AND 5),
    CONSTRAINT ai_cards_attempts CHECK (attempts IS NULL OR attempts BETWEEN 1 AND 2)
);

-- 목록은 늘 "내 카드를 최근 것부터", 한도는 "내 오늘 카드 수".
CREATE INDEX IF NOT EXISTS idx_ai_cards_owner_created
    ON ai_cards (app_user_id, created_at DESC);

-- bridge 가 키 하나로 행을 찾는 자리. 전역 유일해야 한다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_cards_storage_key
    ON ai_cards (storage_key) WHERE storage_key IS NOT NULL;

-- **사용자별 동시 1장을 DB 가 보장한다.** 앱이 두 번 누른 요청이 동시에 들어와도 한 행만 선다.
-- WHERE 가 빠지면 카드를 평생 한 장밖에 못 만든다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_cards_one_generating
    ON ai_cards (app_user_id) WHERE status = 'generating';

DROP TRIGGER IF EXISTS trg_ai_cards_updated_at ON ai_cards;
CREATE TRIGGER trg_ai_cards_updated_at
    BEFORE UPDATE ON ai_cards
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
```

- [ ] **Step 4: 마이그레이션** — `db/migrations/2026-09-14_ai_cards.sql` 은 Step 3 의 파일과 **내용이 같다**(머리 주석 첫 줄만 `-- 이미 돌고 있는 DB 에 적용 (db/init/38_ai_cards.sql 과 같은 내용, 여러 번 돌려도 안전).` 로 바꾼다). 전부 `IF NOT EXISTS` / `DROP TRIGGER IF EXISTS` 라 두 번 돌려도 된다.

- [ ] **Step 5: verify (단언형)** — `db/migrations/verify_2026-09-14_ai_cards.sql`:

```sql
-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — SELECT 나열이면 틀려도 종료 코드 0 이라 녹색이 된다 (#273 · #292).
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('ai_cards') IS NULL THEN
        RAISE EXCEPTION 'missing table: ai_cards';
    END IF;
    relation := to_regclass('ai_cards');

    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        ('app_user_id', 'uuid', 'true'),
        -- 강아지를 지워도 카드는 남는다 (아래 FK 의 SET NULL 과 짝)
        ('dog_id', 'uuid', 'false'),
        ('month', 'smallint', 'true'),
        ('dog_name', 'character varying(40)', 'true'),
        ('title', 'character varying(80)', 'true'),
        ('status', 'character varying(16)', 'true'),
        ('error_code', 'character varying(32)', 'false'),
        ('storage_key', 'character varying(200)', 'false'),
        ('generation', 'character varying(64)', 'false'),
        ('size_bytes', 'integer', 'false'),
        ('width', 'smallint', 'false'),
        ('height', 'smallint', 'false'),
        ('likeness', 'smallint', 'false'),
        ('attempts', 'smallint', 'false'),
        ('created_at', 'timestamp with time zone', 'true'),
        ('updated_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: ai_cards.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- FK 둘의 **삭제 동작이 서로 다르다**. 계정은 CASCADE, 강아지는 SET NULL.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id', NULL, NULL, NULL),
        ('f', 'app_user_id', 'app_users', 'id', 'c'),
        ('f', 'dog_id', 'pets', 'id', 'n')
    ) AS expected(kind, columns, target_table, target_columns, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND (item.kind <> 'f' OR (
                  c.confrelid = to_regclass(item.target_table)
                  AND c.confdeltype::text = item.delete_action
                  AND ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num, pos)
                            JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num
                            ORDER BY k.pos) = string_to_array(item.target_columns, ',')
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: ai_cards kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    FOR item IN SELECT * FROM (VALUES
        ('ai_cards_month'), ('ai_cards_dog_name'), ('ai_cards_status'), ('ai_cards_ready_set'),
        ('ai_cards_failed_code'), ('ai_cards_size'), ('ai_cards_likeness'), ('ai_cards_attempts')
    ) AS expected(conname) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = item.conname
              AND c.contype = 'c' AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
    END LOOP;

    -- 인덱스 셋. 부분 UNIQUE 둘은 `WHERE` 가 빠지면 조용히 틀린다 —
    -- one_generating 은 카드를 평생 한 장만 만들게 되고, storage_key 는 생성 중인 카드가 둘일 수 없게 된다.
    FOR item IN SELECT * FROM (VALUES
        ('idx_ai_cards_owner_created', 'false', NULL),
        ('idx_ai_cards_storage_key', 'true', 'storage_key IS NOT NULL'),
        ('idx_ai_cards_one_generating', 'true', 'generating')
    ) AS expected(index_name, unique_wanted, predicate) LOOP
        definition := NULL;
        SELECT pg_get_indexdef(i.indexrelid) INTO definition
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = relation AND c.relname = item.index_name
          AND i.indisvalid AND i.indisready
          AND i.indisunique = item.unique_wanted::boolean;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'index mismatch: % missing, invalid, or unique flag is not %',
                item.index_name, item.unique_wanted;
        END IF;
        IF item.predicate IS NOT NULL AND position(item.predicate IN definition) = 0 THEN
            RAISE EXCEPTION 'index mismatch: % lost its WHERE %, got %',
                item.index_name, item.predicate, definition;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid = relation AND t.tgname = 'trg_ai_cards_updated_at'
          AND NOT t.tgisinternal
    ) THEN
        RAISE EXCEPTION 'trigger mismatch: trg_ai_cards_updated_at missing';
    END IF;
END
$verify$;

-- 사람이 눈으로 보는 자리. 적용 직후에는 전부 0 이다.
SELECT status, count(*) FROM ai_cards GROUP BY status ORDER BY status;
```

- [ ] **Step 6: 하네스 등록** — `tools/check_migration_verification.py` 의 `CHECKS = (` 바로 다음 줄(현재 첫 항목 `('2026-09-13', 'walk_measurements', ...` 앞)에:

```python
        ('2026-09-14', 'ai_cards', APP_USERS + PETS_ONLY + SET_UPDATED_AT, 'ai_cards', [
            'ALTER TABLE ai_cards DROP COLUMN status CASCADE',
            'ALTER TABLE ai_cards ALTER COLUMN dog_name TYPE varchar(80)',
            'ALTER TABLE ai_cards ALTER COLUMN dog_id SET NOT NULL',
            'ALTER TABLE ai_cards DROP CONSTRAINT ai_cards_status',
            'ALTER TABLE ai_cards DROP CONSTRAINT ai_cards_ready_set',
            'ALTER TABLE ai_cards DROP CONSTRAINT ai_cards_failed_code',
            # **FK 삭제 동작을 뒤바꾸는 변조.** SET NULL → CASCADE 면 강아지를 지울 때 카드가 같이 사라진다.
            'ALTER TABLE ai_cards DROP CONSTRAINT ai_cards_dog_id_fkey;'
            ' ALTER TABLE ai_cards ADD FOREIGN KEY(dog_id) REFERENCES pets(id) ON DELETE CASCADE',
            # 부분 조건을 잃는 변조 — 카드를 평생 한 장밖에 못 만든다
            'DROP INDEX idx_ai_cards_one_generating;'
            ' CREATE UNIQUE INDEX idx_ai_cards_one_generating ON ai_cards (app_user_id)',
            'DROP TRIGGER trg_ai_cards_updated_at ON ai_cards',
        ]),
```

- [ ] **Step 7: 모델** — `backend/src/daengs_backend/models/ai_card.py`:

```python
"""AI 도감 카드 — 사진 한 장으로 서버가 만든 달 카드 (#537, D-076).

스키마 원본은 `db/init/38_ai_cards.sql` 입니다. 이 모델은 그 SQL 을 따라가는 쪽이라,
SQL 을 고치면 여기도 손으로 맞춰야 합니다 (저장소 규칙).

`dog_cards`(앱이 얼굴을 끼워 만든 카드, id 도 앱이 만듦)와 **별개입니다** — 이건 서버가
만든 카드 한 장 통째 PNG 라 id 도 서버가 만듭니다.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

AI_CARD_STATUSES = ("generating", "ready", "failed")


class AiCard(Base):
    __tablename__ = "ai_cards"

    __table_args__ = (
        CheckConstraint("month BETWEEN 1 AND 12", name="ai_cards_month"),
        CheckConstraint("length(btrim(dog_name)) > 0", name="ai_cards_dog_name"),
        CheckConstraint("status IN ('generating', 'ready', 'failed')", name="ai_cards_status"),
        CheckConstraint(
            "status <> 'ready' OR (storage_key IS NOT NULL AND generation IS NOT NULL"
            " AND size_bytes IS NOT NULL AND width IS NOT NULL AND height IS NOT NULL)",
            name="ai_cards_ready_set",
        ),
        CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name="ai_cards_failed_code"),
        CheckConstraint("size_bytes IS NULL OR size_bytes > 0", name="ai_cards_size"),
        CheckConstraint("likeness IS NULL OR likeness BETWEEN 1 AND 5", name="ai_cards_likeness"),
        CheckConstraint("attempts IS NULL OR attempts BETWEEN 1 AND 2", name="ai_cards_attempts"),
        Index("idx_ai_cards_owner_created", "app_user_id", "created_at"),
        Index(
            "idx_ai_cards_storage_key", "storage_key", unique=True,
            postgresql_where=text("storage_key IS NOT NULL"),
        ),
        #: 사용자별 동시 1장. 서비스가 add+commit 을 같은 try 로 감싸 IntegrityError → 409 로 바꿉니다.
        Index(
            "idx_ai_cards_one_generating", "app_user_id", unique=True,
            postgresql_where=text("status = 'generating'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    #: ⚠️ **이 CASCADE 에 기대면 안 됩니다.** 탈퇴는 `app_users` 행을 남깁니다 — 탈퇴 경로가 명시로 지웁니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id", ondelete="CASCADE"))

    #: 어느 아이로 만들었나. **아이를 지워도 카드는 남습니다** (SET NULL).
    dog_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("pets.id", ondelete="SET NULL"))

    month: Mapped[int] = mapped_column(SmallInteger)
    #: 카드에 **인쇄된** 이름. 개명해도 안 바뀝니다.
    dog_name: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(80))

    #: `generating` → `ready` | `failed`. 되돌아가지 않습니다.
    status: Mapped[str] = mapped_column(String(16))
    #: `failed` 일 때만. `upstream`·`no_image`·`unavailable`·`storage`·`interrupted`·`internal`.
    error_code: Mapped[str | None] = mapped_column(String(32))

    storage_key: Mapped[str | None] = mapped_column(String(200))
    generation: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(SmallInteger)
    height: Mapped[int | None] = mapped_column(SmallInteger)
    likeness: Mapped[int | None] = mapped_column(SmallInteger)
    attempts: Mapped[int | None] = mapped_column(SmallInteger)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    def __repr__(self) -> str:
        return f"<AiCard {self.id} {self.status}>"
```

- [ ] **Step 8: 모델 등록** — `models/__init__.py` 에서 `from daengs_backend.models.admin_user import ...` 줄 **바로 앞**(알파벳 순)에 `from daengs_backend.models.ai_card import AI_CARD_STATUSES, AiCard` 를 넣고, `__all__` 목록에 `"AI_CARD_STATUSES",` 와 `"AiCard",` 를 기존 정렬 규칙에 맞는 자리에 넣는다.

- [ ] **Step 9: 통과 확인** — Run: `cd backend && uv run pytest tests/test_ai_card_model.py -v && uv run check` → PASS, check 는 마이그레이션 짝·이름·등록(`Coverage: ... 짝 · 단언 · 등록 모두 확인`)을 통과.

- [ ] **Step 10: 커밋 (컨트롤러)**

```bash
git add db/init/38_ai_cards.sql db/migrations/2026-09-14_ai_cards.sql db/migrations/verify_2026-09-14_ai_cards.sql tools/check_migration_verification.py backend/src/daengs_backend/models backend/tests/test_ai_card_model.py
git commit -m "서버가 만든 AI 도감 카드를 담을 표와 모델을 두었다"
```

(버리는 Postgres 하네스는 컨트롤러가 최종 게이트에서 돌린다.)

---

### Task 3: 설정 두 개 · 리포지토리 · 가짜 리포지토리 · 한도 규칙

**Files:**
- Modify: `backend/src/daengs_backend/config.py` (cardimage 블록 끝, `_parse_months` validator **앞**)
- Modify: `backend/.env.example` (`#DAENGS_CARDIMAGE_JUDGE_MIN=3` 다음 줄)
- Create: `backend/src/daengs_backend/repositories/ai_card.py`
- Modify: `backend/tests/fakes.py` (`Store.__init__` 에 `self.ai_cards: list = []`, `install()` 의 도감 카드 블록 바로 뒤에 AI 카드 대역)
- Create: `backend/src/daengs_backend/services/ai_card_quota.py`
- Test: `backend/tests/test_ai_card_quota.py`

**Interfaces:**
- Consumes: `daengs_backend.models.AiCard` (Task 2)
- Produces: `settings.cardimage_daily_limit: int`(기본 1, ≥0), `settings.cardimage_concurrency: int`(기본 2, ≥1)
- Produces: `repositories.ai_card` — `add(session, card) -> AiCard`; `async get_owned(session, app_user_id, card_id, *, for_update=False) -> AiCard | None`; `async get_for_update(session, card_id) -> AiCard | None`; `async list_for_owner(session, app_user_id, *, limit=200) -> list[AiCard]`(created_at DESC); `async has_generating(session, app_user_id) -> bool`; `async count_ready_since(session, app_user_id, since: datetime) -> int`; `async expire_generating(session, app_user_id, *, created_before: datetime, now: datetime) -> int`; `async find_ready_by_storage_key(session, storage_key) -> AiCard | None`; `async list_for_owner_for_update(session, app_user_id) -> list[AiCard]`; `async delete(session, card) -> None`; `async delete_all_for_owner(session, app_user_id) -> int`
- Produces: `services.ai_card_quota` — `KST`, `class AiCardBusyError(Exception)`, `class AiCardLimitError(Exception)`, `stale_after() -> timedelta`, `kst_day_start(now: datetime) -> datetime`, `async check_quota(session, app_user_id, *, now: datetime, daily_limit: int) -> None`

- [ ] **Step 1: 실패하는 테스트**

```python
# backend/tests/test_ai_card_quota.py
"""`services/ai_card_quota.py` — 앱 사용자 AI 카드 생성 한도 (#537, D-076).

지금 규칙은 테스트 단계용이다: 사용자별 동시 1장 + KST 하루 `ready` N장. 제품 규칙이 정해지면
`check_quota` 하나를 통째로 바꾼다 — 이 파일도 같이 바뀐다.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeAdmin, FakeAppUser, Store, install

from daengs_backend.config import Settings, settings
from daengs_backend.models import AiCard
from daengs_backend.services import ai_card_quota as quota

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()
NOW = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)  # KST 12:00


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    monkeypatch.setattr(settings, "cardimage_timeout_ms", 120_000)
    return s


def _card(status: str, created_at: datetime, owner: uuid.UUID = OWNER) -> AiCard:
    return AiCard(
        id=uuid.uuid4(), app_user_id=owner, month=4, dog_name="네오", title="BLOSSOM 네오",
        status=status, error_code="upstream" if status == "failed" else None,
        created_at=created_at, updated_at=created_at,
    )


def _check(limit: int = 1) -> None:
    asyncio.run(quota.check_quota(None, OWNER, now=NOW, daily_limit=limit))


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("DAENGS_CARDIMAGE_DAILY_LIMIT", "DAENGS_CARDIMAGE_CONCURRENCY"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.cardimage_daily_limit == 1
    assert s.cardimage_concurrency == 2


def test_stale_after_covers_worst_case_retry(store: Store) -> None:
    # 엔진·검수 120초씩 × 재시도 2회 = 8분. 그보다 1분 길다.
    assert quota.stale_after() == timedelta(minutes=9)


def test_kst_day_start() -> None:
    assert quota.kst_day_start(NOW) == datetime(2026, 9, 13, 15, 0, tzinfo=UTC)


def test_empty_is_allowed(store: Store) -> None:
    _check()


def test_fresh_generating_is_busy(store: Store) -> None:
    store.ai_cards.append(_card("generating", NOW - timedelta(minutes=1)))
    with pytest.raises(quota.AiCardBusyError):
        _check()


def test_stale_generating_is_expired_not_busy(store: Store) -> None:
    card = _card("generating", NOW - timedelta(minutes=10))
    store.ai_cards.append(card)
    _check()
    assert card.status == "failed" and card.error_code == "interrupted"


def test_ready_today_hits_limit(store: Store) -> None:
    store.ai_cards.append(_card("ready", datetime(2026, 9, 13, 15, 0, tzinfo=UTC)))  # KST 14일 00:00
    with pytest.raises(quota.AiCardLimitError):
        _check()


def test_ready_yesterday_kst_does_not_count(store: Store) -> None:
    store.ai_cards.append(_card("ready", datetime(2026, 9, 13, 14, 59, tzinfo=UTC)))  # KST 13일 23:59
    _check()


def test_failed_does_not_count(store: Store) -> None:
    store.ai_cards.append(_card("failed", NOW - timedelta(hours=1)))
    _check()


def test_zero_limit_is_unlimited(store: Store) -> None:
    for _ in range(5):
        store.ai_cards.append(_card("ready", NOW - timedelta(hours=1)))
    _check(limit=0)


def test_other_users_rows_are_ignored(store: Store) -> None:
    store.ai_cards.append(_card("generating", NOW, owner=STRANGER))
    store.ai_cards.append(_card("ready", NOW, owner=STRANGER))
    _check()
```

- [ ] **Step 2: 실패 확인** — Run: `cd backend && uv run pytest tests/test_ai_card_quota.py -v` → FAIL (`ImportError: cannot import name 'ai_card_quota'`)

- [ ] **Step 3: 설정** — `config.py` 의 `cardimage_judge_min` 필드 바로 다음에:

```python
    # 앱 사용자 하루 생성 한도 (KST 하루, `ready` 만 셈). 0 이면 한도 없음. 테스트 단계라 1 이고,
    # 제품 규칙이 정해지면 `services/ai_card_quota.py` 의 함수를 통째로 바꿉니다 (D-076).
    cardimage_daily_limit: int = Field(default=1, ge=0, validation_alias=AliasChoices("DAENGS_CARDIMAGE_DAILY_LIMIT"))
    # 서버 전체 동시 생성 수. backend 프로세스 안 백그라운드 작업이라 스레드를 씁니다 (D-076).
    cardimage_concurrency: int = Field(default=2, ge=1, validation_alias=AliasChoices("DAENGS_CARDIMAGE_CONCURRENCY"))
```

`backend/.env.example` 의 `#DAENGS_CARDIMAGE_JUDGE_MIN=3` 다음 줄에:

```
# 앱 사용자 하루 생성 한도 (KST, 완성된 카드만 셈). 0 이면 한도 없음. 테스트 단계라 1.
#DAENGS_CARDIMAGE_DAILY_LIMIT=1
# 서버 전체 동시 생성 수 (backend 프로세스 안).
#DAENGS_CARDIMAGE_CONCURRENCY=2
```

- [ ] **Step 4: 리포지토리** — `backend/src/daengs_backend/repositories/ai_card.py`:

```python
"""R — AI 도감 카드 쿼리. 판단은 `services/ai_card.py`·`services/ai_card_quota.py` 가 합니다."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AiCard


def add(session: AsyncSession, card: AiCard) -> AiCard:
    session.add(card)
    return card


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID, *, for_update: bool = False
) -> AiCard | None:
    """**내 것일 때만** 돌려줍니다. 남의 것도 없는 것과 같은 None 입니다."""
    stmt = select(AiCard).where(AiCard.id == card_id, AiCard.app_user_id == app_user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def get_for_update(session: AsyncSession, card_id: uuid.UUID) -> AiCard | None:
    """**소유자를 안 봅니다.** 백그라운드 생성이 자기가 만든 행을 다시 잡을 때만 씁니다."""
    return await session.scalar(select(AiCard).where(AiCard.id == card_id).with_for_update())


async def list_for_owner(session: AsyncSession, app_user_id: uuid.UUID, *, limit: int = 200) -> list[AiCard]:
    stmt = (
        select(AiCard)
        .where(AiCard.app_user_id == app_user_id)
        .order_by(AiCard.created_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def has_generating(session: AsyncSession, app_user_id: uuid.UUID) -> bool:
    stmt = select(AiCard.id).where(AiCard.app_user_id == app_user_id, AiCard.status == "generating").limit(1)
    return await session.scalar(stmt) is not None


async def count_ready_since(session: AsyncSession, app_user_id: uuid.UUID, since: datetime) -> int:
    stmt = select(func.count()).where(
        AiCard.app_user_id == app_user_id, AiCard.status == "ready", AiCard.created_at >= since
    )
    return int(await session.scalar(stmt) or 0)


async def expire_generating(
    session: AsyncSession, app_user_id: uuid.UUID, *, created_before: datetime, now: datetime
) -> int:
    """정리 기준보다 오래된 `generating` 을 `failed`/`interrupted` 로 바꿉니다.

    배포 재시작과 겹쳐 사라진 백그라운드 작업의 행입니다. 커밋은 부르는 쪽이 합니다.
    """
    result = await session.execute(
        update(AiCard)
        .where(
            AiCard.app_user_id == app_user_id,
            AiCard.status == "generating",
            AiCard.created_at < created_before,
        )
        .values(status="failed", error_code="interrupted", updated_at=now)
    )
    return result.rowcount or 0


async def find_ready_by_storage_key(session: AsyncSession, storage_key: str) -> AiCard | None:
    """bridge 전용. ⚠️ 소유자 조건이 없습니다 — 대신 **backend 가 실제로 저장한 키인지**를 봅니다."""
    return await session.scalar(
        select(AiCard).where(AiCard.storage_key == storage_key, AiCard.status == "ready")
    )


async def list_for_owner_for_update(session: AsyncSession, app_user_id: uuid.UUID) -> list[AiCard]:
    stmt = select(AiCard).where(AiCard.app_user_id == app_user_id).with_for_update()
    return list(await session.scalars(stmt))


async def delete(session: AsyncSession, card: AiCard) -> None:
    await session.delete(card)


async def delete_all_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """⚠️ `app_users` CASCADE 에 기대면 안 됩니다 — 탈퇴는 그 행을 남깁니다."""
    result = await session.execute(sql_delete(AiCard).where(AiCard.app_user_id == app_user_id))
    return result.rowcount or 0
```

- [ ] **Step 5: 가짜 리포지토리** — `tests/fakes.py`:
  - 맨 위 import 블록의 `from daengs_backend.repositories import dogcard as card_repo` 바로 앞에 `from daengs_backend.repositories import ai_card as ai_card_repo` (정렬이 어긋나면 ruff isort 순서에 맞춘다).
  - `Store.__init__` 의 `self.dog_cards: list = []` 다음 줄에 `self.ai_cards: list = []`.
  - `install()` 안, `monkeypatch.setattr(card_repo, "delete_all_for_owner", card_delete_all_for_owner)` 줄 **바로 다음**에:

```python

    # ── AI 도감 카드 (#537, D-076) ────────────────────────────────────────
    #
    # 탈퇴가 이것도 명시로 지웁니다. 대역이 없으면 탈퇴 테스트가 진짜 DB 를 찾다가 깨집니다.

    def ai_card_add(session, card):
        # `idx_ai_cards_one_generating` 부분 UNIQUE 를 흉내 냅니다. 진짜 DB 는 commit 에서
        # 터지고, 서비스가 add 와 commit 을 **같은 try** 로 감싸므로 여기서 내도 같은 길을 탑니다.
        if card.status == "generating" and any(
            c.app_user_id == card.app_user_id and c.status == "generating" for c in store.ai_cards
        ):
            raise IntegrityError("idx_ai_cards_one_generating", None, Exception("duplicate"))
        now = datetime.now(UTC)
        if card.created_at is None:
            card.created_at = now
        if card.updated_at is None:
            card.updated_at = now
        store.ai_cards.append(card)
        return card

    async def ai_card_get_owned(session, app_user_id, card_id, *, for_update=False):
        return next(
            (c for c in store.ai_cards if c.id == card_id and c.app_user_id == app_user_id), None
        )

    async def ai_card_get_for_update(session, card_id):
        return next((c for c in store.ai_cards if c.id == card_id), None)

    async def ai_card_list_for_owner(session, app_user_id, *, limit=200):
        rows = [c for c in store.ai_cards if c.app_user_id == app_user_id]
        return sorted(rows, key=lambda c: c.created_at, reverse=True)[:limit]

    async def ai_card_has_generating(session, app_user_id):
        return any(c.app_user_id == app_user_id and c.status == "generating" for c in store.ai_cards)

    async def ai_card_count_ready_since(session, app_user_id, since):
        return sum(
            1
            for c in store.ai_cards
            if c.app_user_id == app_user_id and c.status == "ready" and c.created_at >= since
        )

    async def ai_card_expire_generating(session, app_user_id, *, created_before, now):
        expired = [
            c
            for c in store.ai_cards
            if c.app_user_id == app_user_id and c.status == "generating" and c.created_at < created_before
        ]
        for c in expired:
            c.status, c.error_code, c.updated_at = "failed", "interrupted", now
        return len(expired)

    async def ai_card_find_ready_by_storage_key(session, storage_key):
        return next(
            (c for c in store.ai_cards if c.storage_key == storage_key and c.status == "ready"), None
        )

    async def ai_card_list_for_owner_for_update(session, app_user_id):
        return [c for c in store.ai_cards if c.app_user_id == app_user_id]

    async def ai_card_delete(session, card):
        store.ai_cards.remove(card)

    async def ai_card_delete_all_for_owner(session, app_user_id):
        mine = [c for c in store.ai_cards if c.app_user_id == app_user_id]
        store.ai_cards = [c for c in store.ai_cards if c.app_user_id != app_user_id]
        return len(mine)

    monkeypatch.setattr(ai_card_repo, "add", ai_card_add)
    monkeypatch.setattr(ai_card_repo, "get_owned", ai_card_get_owned)
    monkeypatch.setattr(ai_card_repo, "get_for_update", ai_card_get_for_update)
    monkeypatch.setattr(ai_card_repo, "list_for_owner", ai_card_list_for_owner)
    monkeypatch.setattr(ai_card_repo, "has_generating", ai_card_has_generating)
    monkeypatch.setattr(ai_card_repo, "count_ready_since", ai_card_count_ready_since)
    monkeypatch.setattr(ai_card_repo, "expire_generating", ai_card_expire_generating)
    monkeypatch.setattr(ai_card_repo, "find_ready_by_storage_key", ai_card_find_ready_by_storage_key)
    monkeypatch.setattr(ai_card_repo, "list_for_owner_for_update", ai_card_list_for_owner_for_update)
    monkeypatch.setattr(ai_card_repo, "delete", ai_card_delete)
    monkeypatch.setattr(ai_card_repo, "delete_all_for_owner", ai_card_delete_all_for_owner)
```

  (`IntegrityError`·`datetime`·`UTC` 는 `fakes.py` 가 이미 import 한다 — 없으면 추가.)

- [ ] **Step 6: 한도 규칙** — `backend/src/daengs_backend/services/ai_card_quota.py`:

```python
"""앱 사용자 AI 카드 생성 한도 (#537, D-076).

**지금 규칙은 테스트 단계용입니다** — 사용자별 동시 1장 + KST 하루 `ready` N장
(`DAENGS_CARDIMAGE_DAILY_LIMIT`, 기본 1). 카드를 몇 장·어떤 조건으로 줄지(제품 규칙)가 정해지면
**`check_quota` 를 통째로 바꿉니다.** 부르는 쪽(`services/ai_card.py`)은 두 예외만 압니다.

실패(`failed`)는 세지 않습니다 — 한도가 1장이라 실패 한 번으로 그날 기회가 사라지면 안 되고,
연타는 동시 1장이 막습니다. 전체 지출의 바닥은 카드 생성 키의 별도 GCP 프로젝트 지출 상한입니다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.repositories import ai_card as ai_card_repo

KST = ZoneInfo("Asia/Seoul")


class AiCardBusyError(Exception):
    """이미 만들고 있는 카드가 있습니다. 라우터가 409 `already_generating` 으로 바꿉니다."""


class AiCardLimitError(Exception):
    """오늘 한도를 다 썼습니다. 라우터가 429 `limit_reached` 로 바꿉니다."""


def stale_after() -> timedelta:
    """`generating` 을 사라진 작업으로 볼 기준.

    한 건의 최악은 엔진·검수가 각각 `cardimage_timeout_ms` 를 다 쓰고 재시도까지 하는 경우
    (2 × 2 × timeout)다. 그보다 짧으면 정상 진행 중인 작업을 실패로 덮으므로 1분을 더 둔다.
    """
    return timedelta(milliseconds=4 * settings.cardimage_timeout_ms) + timedelta(seconds=60)


def kst_day_start(now: datetime) -> datetime:
    return now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)


async def check_quota(
    session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime, daily_limit: int
) -> None:
    await ai_card_repo.expire_generating(
        session, app_user_id, created_before=now - stale_after(), now=now
    )
    if await ai_card_repo.has_generating(session, app_user_id):
        raise AiCardBusyError
    if daily_limit and await ai_card_repo.count_ready_since(
        session, app_user_id, kst_day_start(now)
    ) >= daily_limit:
        raise AiCardLimitError
```

- [ ] **Step 7: 통과 확인** — Run: `cd backend && uv run pytest tests/test_ai_card_quota.py tests/test_dog_cards.py -v` → PASS (도감 카드 테스트는 fakes 변경의 회귀 확인).

- [ ] **Step 8: 커밋 (컨트롤러)**

```bash
git add backend/src/daengs_backend/config.py backend/.env.example backend/src/daengs_backend/repositories/ai_card.py backend/src/daengs_backend/services/ai_card_quota.py backend/tests/fakes.py backend/tests/test_ai_card_quota.py
git commit -m "AI 카드 생성 한도를 함수 하나에 모으고 리포지토리와 가짜 대역을 두었다"
```

---

### Task 4: 서비스 — 시작·백그라운드 생성·조회·삭제·탈퇴 정리

**Files:**
- Modify: `backend/src/daengs_backend/core/storage.py` (`build_card_face_key` 함수 바로 다음에 `build_ai_card_key`)
- Create: `backend/src/daengs_backend/services/ai_card.py`
- Modify: `backend/src/daengs_backend/services/app_auth.py` (탈퇴 정리 + 로그)
- Test: `backend/tests/test_ai_card_service.py`

**Interfaces:**
- Consumes: `ai_card_engine.{ready_check, generate, default_engine, default_judge}` (Task 1), `AiCard` (Task 2), `ai_card_repo.*`, `ai_card_quota.{check_quota, stale_after, AiCardBusyError, AiCardLimitError}` (Task 3)
- Produces: `core.storage.build_ai_card_key(app_user_id: uuid.UUID, card_id: uuid.UUID) -> str` (`"ai-cards/{app_user_id}/{card_id}.png"`)
- Produces: `services.ai_card` — 상수 `AI_CARD_BRIDGE_DOWNLOAD_PATH = "/app/ai-cards/_bridge/download"`, `AI_CARD_CONTENT_TYPE = "image/png"`; `class AiCardNotFoundError(Exception)`; `async start(session, app_user_id, *, photo: bytes, content_type: str, month: int, dog_name: str, dog_id: uuid.UUID | None, now: datetime | None = None) -> AiCard`; `async list_cards(session, app_user_id, *, now=None) -> list[AiCard]`; `async get_card(session, app_user_id, card_id, *, now=None) -> tuple[AiCard, str | None]`; `async delete_card(session, app_user_id, card_id) -> None`; `async cleanup_for_owner(session, app_user_id) -> int`; 테스트 훅 `_spawn(coro) -> None`, `_session_factory`(기본 `SessionLocal`)
- `start` 가 내는 예외: `MonthNotOpenError`, `CardImageUnavailable`, `StorageNotConfiguredError`, `AiCardNotFoundError`(남의 강아지), `PhotoError`, `AiCardBusyError`, `AiCardLimitError`

- [ ] **Step 1: 실패하는 테스트**

```python
# backend/tests/test_ai_card_service.py
"""`services/ai_card.py` — 앱 사용자 AI 카드의 시작·백그라운드 생성·조회·삭제 (#537, D-076).

리포지토리는 `fakes.py`, 저장소는 임시 디렉터리 위의 **진짜** `LocalBridgeStorage`, 엔진·검수는
`cardimage_fakes.py`. 백그라운드 작업은 `_spawn` 을 가로채 모아 두었다가 테스트가 직접 돌린다 —
그래야 "요청은 끝났고 생성은 아직" 인 사이의 상태를 볼 수 있다.
"""

import asyncio
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from fakes import FakeAdmin, FakeAppUser, FakePet, FakeSession, Store, install
from PIL import Image
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.core import storage as storage_module
from daengs_backend.core.storage import LocalBridgeStorage, NotConfiguredStorage, StorageNotConfiguredError
from daengs_backend.models import AiCard
from daengs_backend.services import ai_card as service
from daengs_backend.services import ai_card_engine
from daengs_backend.services import ai_card_quota as quota
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError
from daengs_cardimage.engine import EngineError
from daengs_cardimage.photo import PhotoError

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


class _SessionFactory:
    """`SessionLocal()` 대역 — `async with` 로 가짜 세션 하나를 준다."""

    def __call__(self):
        return self

    async def __aenter__(self):
        return FakeSession()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> LocalBridgeStorage:
    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    monkeypatch.setattr(storage_module, "get_storage", lambda: s)
    monkeypatch.setattr(service, "get_storage", lambda: s)
    return s


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch, store: Store, storage: LocalBridgeStorage) -> list:
    collected: list = []
    monkeypatch.setattr(service, "_spawn", collected.append)
    monkeypatch.setattr(service, "_session_factory", _SessionFactory())
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "cardimage_months", frozenset({4, 9}))
    monkeypatch.setattr(settings, "cardimage_daily_limit", 1)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine())
    monkeypatch.setattr(ai_card_engine, "default_judge", lambda: FakeJudge([5]))
    return collected


def _photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


def _start(**kw) -> AiCard:
    args = dict(photo=_photo(), content_type="image/jpeg", month=4, dog_name="네오", dog_id=None)
    args.update(kw)
    return asyncio.run(service.start(FakeSession(), OWNER, **args))


def _run_all(jobs: list) -> None:
    while jobs:
        asyncio.run(jobs.pop(0))


def test_start_then_background_makes_ready_card(store, storage, jobs) -> None:
    card = _start()
    assert card.status == "generating" and card.title == "BLOSSOM 네오"
    assert len(jobs) == 1 and store.ai_cards == [card]

    _run_all(jobs)

    assert card.status == "ready" and card.error_code is None
    assert (card.width, card.height) == (994, 1582)
    assert card.likeness == 5 and card.attempts == 1
    assert card.storage_key == f"ai-cards/{OWNER}/{card.id}.png"
    assert Image.open(storage.local_path(card.storage_key)).size == (994, 1582)

    got, url = asyncio.run(service.get_card(FakeSession(), OWNER, card.id))
    assert got is card and "/app/ai-cards/_bridge/download/" in url


def test_engine_failure_marks_failed_without_object(store, storage, jobs, monkeypatch) -> None:
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine(error=EngineError("upstream", "x")))
    card = _start()
    _run_all(jobs)
    assert card.status == "failed" and card.error_code == "upstream"
    assert card.storage_key is None
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_failed_card_does_not_use_up_daily_limit(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine(error=EngineError("upstream", "x")))
    _start()
    _run_all(jobs)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine())
    assert _start().status == "generating"


@pytest.mark.parametrize(
    ("patch", "error"),
    [
        (lambda m: m.setattr(settings, "cardimage_gemini_api_key", SecretStr("")), CardImageUnavailable),
        (lambda m: None, MonthNotOpenError),
    ],
)
def test_config_problems_create_no_row(store, jobs, monkeypatch, patch, error) -> None:
    patch(monkeypatch)
    month = 12 if error is MonthNotOpenError else 4
    with pytest.raises(error):
        _start(month=month)
    assert store.ai_cards == [] and jobs == []


def test_bad_photo_creates_no_row(store, jobs) -> None:
    with pytest.raises(PhotoError):
        _start(photo=b"nope")
    assert store.ai_cards == [] and jobs == []


def test_storage_not_configured_creates_no_row(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(service, "get_storage", lambda: NotConfiguredStorage())
    with pytest.raises(StorageNotConfiguredError):
        _start()
    assert store.ai_cards == [] and jobs == []


def test_strangers_dog_is_not_found(store, jobs) -> None:
    other = FakePet(app_user_id=STRANGER, name="남의집", breed="mix")
    store.pets.append(other)
    with pytest.raises(service.AiCardNotFoundError):
        _start(dog_id=other.id)
    assert store.ai_cards == []


def test_my_dog_is_linked(store, jobs) -> None:
    pet = FakePet(app_user_id=OWNER, name="네옹", breed="mix")
    store.pets.append(pet)
    assert _start(dog_id=pet.id).dog_id == pet.id


def test_second_start_while_generating_is_busy(store, jobs) -> None:
    _start()
    with pytest.raises(quota.AiCardBusyError):
        _start()


def test_unique_index_race_is_busy(store, jobs, monkeypatch) -> None:
    """한도 검사를 둘 다 통과한 동시 요청 — DB 의 부분 UNIQUE 가 막고 서비스가 409 로 바꾼다."""
    _start()

    async def _no_quota(*a, **kw):
        return None

    monkeypatch.setattr(service, "check_quota", _no_quota)
    with pytest.raises(quota.AiCardBusyError):
        _start()
    assert len(store.ai_cards) == 1


def test_ready_card_uses_up_daily_limit(store, jobs) -> None:
    _start()
    _run_all(jobs)
    with pytest.raises(quota.AiCardLimitError):
        _start()


def test_delete_while_generating_leaves_no_object(store, storage, jobs) -> None:
    card = _start()
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    _run_all(jobs)
    assert store.ai_cards == []
    assert not storage.local_path(f"ai-cards/{OWNER}/{card.id}.png").exists()


def test_delete_ready_removes_object(store, storage, jobs) -> None:
    card = _start()
    _run_all(jobs)
    path = storage.local_path(card.storage_key)
    asyncio.run(service.delete_card(FakeSession(), OWNER, card.id))
    assert store.ai_cards == [] and not path.exists()


def test_stale_generating_reads_as_interrupted(store, jobs) -> None:
    old = datetime.now(UTC) - timedelta(minutes=10)
    card = AiCard(
        id=uuid.uuid4(), app_user_id=OWNER, month=4, dog_name="네오", title="BLOSSOM 네오",
        status="generating", created_at=old, updated_at=old,
    )
    store.ai_cards.append(card)
    got, url = asyncio.run(service.get_card(FakeSession(), OWNER, card.id))
    assert got.status == "failed" and got.error_code == "interrupted" and url is None


def test_strangers_card_is_not_found(store, jobs) -> None:
    card = _start()
    with pytest.raises(service.AiCardNotFoundError):
        asyncio.run(service.get_card(FakeSession(), STRANGER, card.id))
    with pytest.raises(service.AiCardNotFoundError):
        asyncio.run(service.delete_card(FakeSession(), STRANGER, card.id))


def test_list_is_newest_first(store, jobs, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cardimage_daily_limit", 0)
    first = _start()
    _run_all(jobs)
    second = _start()
    second.created_at = first.created_at + timedelta(seconds=1)
    assert [c.id for c in asyncio.run(service.list_cards(FakeSession(), OWNER))] == [second.id, first.id]


def test_cleanup_for_owner_removes_rows_and_objects(store, storage, jobs) -> None:
    card = _start()
    _run_all(jobs)
    path = storage.local_path(card.storage_key)
    assert asyncio.run(service.cleanup_for_owner(FakeSession(), OWNER)) == 1
    assert store.ai_cards == [] and not path.exists()


def test_cleanup_without_objects_does_not_touch_storage(store, jobs, monkeypatch) -> None:
    """저장소가 꺼져 있다고 탈퇴가 막히면 안 된다 — 지울 객체가 없으면 저장소를 부르지 않는다."""
    _start()  # generating, 객체 없음
    monkeypatch.setattr(service, "get_storage", lambda: NotConfiguredStorage())
    assert asyncio.run(service.cleanup_for_owner(FakeSession(), OWNER)) == 1
```

- [ ] **Step 2: 실패 확인** — Run: `cd backend && uv run pytest tests/test_ai_card_service.py -v` → FAIL (`ImportError: cannot import name 'ai_card'`)

- [ ] **Step 3: 저장소 키** — `core/storage.py` 의 `build_card_face_key` 함수 끝 바로 다음에:

```python
def build_ai_card_key(app_user_id: uuid.UUID, card_id: uuid.UUID) -> str:
    """서버가 만든 AI 도감 카드 한 장 (#537). 사용자 폴더 아래라 탈퇴 정리를 접두사로도 할 수 있다."""
    return f"ai-cards/{app_user_id}/{card_id}.png"
```

- [ ] **Step 4: 서비스** — `backend/src/daengs_backend/services/ai_card.py`:

```python
"""앱 사용자 AI 도감 카드의 규칙. 트랜잭션 경계도 여기입니다 (`/app/ai-cards/*`, #537, D-076).

**비동기입니다.** `start` 는 돈이 나가기 전에 거를 수 있는 것(닫힌 달·키·저장소·남의 강아지·
사진·한도)을 전부 동기로 거른 뒤 행을 `generating` 으로 커밋하고 바로 돌아갑니다. 생성은 같은
backend 프로세스 안의 백그라운드 작업(`_run`)이 하고, 끝나면 **새 세션으로** 행을 `ready`/`failed`
로 바꿉니다.

⚠️ **백그라운드는 요청 세션을 쓰지 않습니다** — 요청이 끝나면 그 세션은 닫힙니다.
⚠️ **배포 재시작과 겹친 작업은 사라집니다.** 행은 `stale_after()` 가 지난 뒤 조회에서
   `failed`/`interrupted` 가 됩니다. 그것이 실제로 자주 보이면 워커로 옮길 때입니다 (D-076).
⚠️ **생성 중에 행이 지워질 수 있습니다**(삭제·탈퇴). 끝난 작업은 행이 없거나 이미 `generating`
   이 아니면 방금 쓴 객체를 지웁니다 — 안 그러면 FK 없는 저장소에 영구 고아가 남습니다.
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
import weakref
from datetime import UTC, datetime

from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.core.database import SessionLocal
from daengs_backend.core.storage import (
    GcsStorage,
    LocalBridgeStorage,
    NotConfiguredStorage,
    StorageNotConfiguredError,
    StoredObject,
    build_ai_card_key,
    get_storage,
)
from daengs_backend.models import AiCard
from daengs_backend.repositories import ai_card as ai_card_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.services import ai_card_engine
from daengs_backend.services.ai_card_quota import AiCardBusyError, check_quota, stale_after
from daengs_cardimage import CardImageUnavailable, GeneratedCard
from daengs_cardimage.engine import EngineError
from daengs_cardimage.photo import prepare_photo
from daengs_cardimage.title import title_text

log = logging.getLogger(__name__)

AI_CARD_BRIDGE_DOWNLOAD_PATH = "/app/ai-cards/_bridge/download"
AI_CARD_CONTENT_TYPE = "image/png"

#: 백그라운드가 새 세션을 여는 곳. 테스트가 가짜로 바꿉니다.
_session_factory = SessionLocal

#: 돌고 있는 작업의 참조. 쥐고 있지 않으면 `create_task` 결과가 GC 로 사라질 수 있습니다.
_tasks: set[asyncio.Task] = set()

#: 이벤트 루프마다 하나의 세마포어. 루프 밖에서 만들면 다른 루프에서 쓸 때 깨집니다.
_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


class AiCardNotFoundError(Exception):
    """내 카드(또는 내가 돌보는 강아지)가 아니거나 없습니다. **남의 것일 때도 이 예외입니다.**"""


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slot = _slots.get(loop)
    if slot is None:
        slot = _slots[loop] = asyncio.Semaphore(settings.cardimage_concurrency)
    return slot


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def start(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    *,
    photo: bytes,
    content_type: str,
    month: int,
    dog_name: str,
    dog_id: uuid.UUID | None,
    now: datetime | None = None,
) -> AiCard:
    name = " ".join(dog_name.split())
    meta = ai_card_engine.ready_check(month)
    if isinstance(get_storage(), NotConfiguredStorage):
        raise StorageNotConfiguredError("AI 카드 저장소가 설정되지 않았습니다 (GAIT_STORAGE)")
    if dog_id is not None and await pet_repo.get_accessible(session, app_user_id, dog_id) is None:
        raise AiCardNotFoundError
    photo_jpeg = await asyncio.to_thread(prepare_photo, photo, content_type)

    now = now or datetime.now(UTC)
    await check_quota(session, app_user_id, now=now, daily_limit=settings.cardimage_daily_limit)

    card = AiCard(
        id=uuid.uuid4(),
        app_user_id=app_user_id,
        dog_id=dog_id,
        month=month,
        dog_name=name,
        title=title_text(meta.card_name, name),
        status="generating",
        created_at=now,
        updated_at=now,
    )
    try:
        ai_card_repo.add(session, card)
        await session.commit()
    except IntegrityError:
        # 한도 검사를 둘 다 통과한 동시 요청 — `idx_ai_cards_one_generating` 이 막았습니다.
        await session.rollback()
        raise AiCardBusyError from None

    _spawn(_run(card.id, app_user_id, photo_jpeg, month, name))
    return card


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, EngineError):
        return exc.code if exc.code in ("upstream", "no_image") else "upstream"
    if isinstance(exc, CardImageUnavailable):
        return "unavailable"
    if isinstance(exc, StorageNotConfiguredError):
        return "storage"
    return "internal"


def _store_png(key: str, data: bytes) -> StoredObject:
    storage = get_storage()
    if isinstance(storage, LocalBridgeStorage):
        storage.write_if_absent(key, data)
    elif isinstance(storage, GcsStorage):
        storage.upload_bytes(key, data, content_type=AI_CARD_CONTENT_TYPE)
    else:
        raise StorageNotConfiguredError("AI 카드 저장소가 설정되지 않았습니다 (GAIT_STORAGE)")
    stored = storage.stat(key)
    if stored is None:
        raise RuntimeError(f"저장 직후 객체가 보이지 않습니다: {key}")
    return stored


async def _run(card_id: uuid.UUID, app_user_id: uuid.UUID, photo_jpeg: bytes, month: int, dog_name: str) -> None:
    """백그라운드 한 건. **예외를 밖으로 내지 않습니다** — 낼 곳이 없고, 행에 결과를 남깁니다."""
    try:
        async with _slot():
            try:
                generated = await asyncio.to_thread(
                    ai_card_engine.generate,
                    photo=photo_jpeg,
                    content_type="image/jpeg",
                    month=month,
                    dog_name=dog_name,
                    engine=ai_card_engine.default_engine(),
                    judge=ai_card_engine.default_judge(),
                )
            except Exception as exc:
                code = _error_code(exc)
                if code == "internal":
                    log.exception("AI 카드 생성 실패 (card=%s)", card_id)
                else:
                    log.warning("AI 카드 생성 실패 (card=%s, %s): %s", card_id, code, exc)
                await _finish_failed(card_id, code)
                return

            key = build_ai_card_key(app_user_id, card_id)
            try:
                stored = await asyncio.to_thread(_store_png, key, generated.png)
            except Exception as exc:
                log.exception("AI 카드 저장 실패 (card=%s): %s", card_id, exc)
                await _finish_failed(card_id, "storage")
                return

            await _finish_ready(card_id, key, stored, generated)
    except Exception:
        log.exception("AI 카드 백그라운드 작업이 정리 중에 실패했습니다 (card=%s)", card_id)


async def _finish_ready(card_id: uuid.UUID, key: str, stored: StoredObject, generated: GeneratedCard) -> None:
    width, height = Image.open(io.BytesIO(generated.png)).size
    async with _session_factory() as session:
        card = await ai_card_repo.get_for_update(session, card_id)
        if card is None or card.status != "generating":
            # 생성 중에 지워졌거나(삭제·탈퇴) 정리 기준이 지나 실패로 덮였습니다. 객체를 남기지 않습니다.
            await asyncio.to_thread(get_storage().delete, key)
            await session.rollback()
            return
        card.status = "ready"
        card.storage_key = key
        card.generation = stored.generation
        card.size_bytes = stored.size_bytes
        card.width, card.height = width, height
        card.likeness = generated.judge.likeness if generated.judge else None
        card.attempts = generated.attempts
        card.updated_at = datetime.now(UTC)
        await session.commit()


async def _finish_failed(card_id: uuid.UUID, code: str) -> None:
    async with _session_factory() as session:
        card = await ai_card_repo.get_for_update(session, card_id)
        if card is None or card.status != "generating":
            await session.rollback()
            return
        card.status = "failed"
        card.error_code = code
        card.updated_at = datetime.now(UTC)
        await session.commit()


async def _expire_stale(session: AsyncSession, app_user_id: uuid.UUID, now: datetime) -> None:
    if await ai_card_repo.expire_generating(session, app_user_id, created_before=now - stale_after(), now=now):
        await session.commit()


async def list_cards(session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime | None = None) -> list[AiCard]:
    """내 카드 전부, 최근 것부터. **이미지 주소는 안 싣습니다** — N 장마다 저장소를 두드리게 됩니다."""
    await _expire_stale(session, app_user_id, now or datetime.now(UTC))
    return await ai_card_repo.list_for_owner(session, app_user_id)


async def get_card(
    session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[AiCard, str | None]:
    await _expire_stale(session, app_user_id, now or datetime.now(UTC))
    card = await ai_card_repo.get_owned(session, app_user_id, card_id)
    if card is None:
        raise AiCardNotFoundError
    url = None
    if card.status == "ready" and card.storage_key is not None:
        url = get_storage().download_url(
            card.storage_key,
            expires_in_seconds=settings.gait_download_url_ttl_seconds,
            generation=card.generation,
            bridge_download_path=AI_CARD_BRIDGE_DOWNLOAD_PATH,
        )
    return card, url


async def delete_card(session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID) -> None:
    """카드 하나를 지웁니다. **생성 중이어도 지웁니다** — 끝난 작업이 객체를 치웁니다."""
    card = await ai_card_repo.get_owned(session, app_user_id, card_id, for_update=True)
    if card is None:
        raise AiCardNotFoundError
    if card.storage_key is not None:
        # **객체를 먼저 지웁니다.** 행을 먼저 지우면 키를 잃어 파일이 영구 고아입니다.
        get_storage().delete(card.storage_key)
    await ai_card_repo.delete(session, card)
    await session.commit()


async def cleanup_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """탈퇴가 부릅니다. 커밋은 탈퇴 트랜잭션이 합니다.

    지울 객체가 없으면 저장소를 안 건드립니다 — 저장소가 꺼져 있다고 탈퇴가 막히면 안 됩니다.
    """
    cards = await ai_card_repo.list_for_owner_for_update(session, app_user_id)
    keys = [c.storage_key for c in cards if c.storage_key]
    if keys:
        storage = get_storage()
        for key in keys:
            storage.delete(key)
    return await ai_card_repo.delete_all_for_owner(session, app_user_id)
```

- [ ] **Step 5: 탈퇴 연결** — `services/app_auth.py`:
  - `from daengs_backend.services import dogcard as card_service` (31행 근처) 바로 앞에 `from daengs_backend.services import ai_card as ai_card_service`
  - `deleted_cards = await card_service.cleanup_for_owner(session, user.id)` 줄 다음에:

```python

        # AI 도감 카드도 **같은 이유로 명시 삭제**입니다 (#537) — 생성 중인 행도 지웁니다.
        # 그 작업이 끝나면 행이 없는 것을 보고 방금 쓴 PNG 를 스스로 치웁니다.
        deleted_ai_cards = await ai_card_service.cleanup_for_owner(session, user.id)
```

  - 탈퇴 로그를 `"도감 카드 %d장, AI 카드 %d장, 끊은 세션 %d개)"` 로 바꾸고 인자 목록의 `deleted_cards,` 다음에 `deleted_ai_cards,` 를 넣는다.

- [ ] **Step 6: 통과 확인** — Run: `cd backend && uv run pytest tests/test_ai_card_service.py tests/test_ai_card_quota.py -v` → PASS. 그리고 탈퇴 회귀: Grep 도구로 `backend/tests` 에서 `withdraw` 를 찾아 나온 테스트 파일들을 돌린다(예: `uv run pytest tests/test_app_auth.py -v` — 실제 이름은 Grep 결과를 따른다).

- [ ] **Step 7: 커밋 (컨트롤러)**

```bash
git add backend/src/daengs_backend/core/storage.py backend/src/daengs_backend/services/ai_card.py backend/src/daengs_backend/services/app_auth.py backend/tests/test_ai_card_service.py
git commit -m "AI 카드를 행으로 먼저 받고 같은 프로세스의 백그라운드에서 만들어 보관한다"
```

---

### Task 5: 라우터 `/app/ai-cards` · 스키마 · 본문 읽기 공용화 · 등록

**Files:**
- Create: `backend/src/daengs_backend/routers/raw_body.py`
- Modify: `backend/src/daengs_backend/routers/admin_cardimage.py` (`_read_body`·`_too_large` 를 공용 함수로 교체)
- Create: `backend/src/daengs_backend/schemas/ai_card.py`
- Create: `backend/src/daengs_backend/routers/ai_card.py`
- Modify: `backend/src/daengs_backend/main.py` (import + include)
- Test: `backend/tests/test_ai_cards_api.py`

**Interfaces:**
- Consumes: `services.ai_card.*` (Task 4), `ai_card_quota.{AiCardBusyError, AiCardLimitError}` (Task 3)
- Produces: `routers.raw_body.read_limited_body(request: Request, max_bytes: int) -> bytes` (413 `{"code":"too_large","message":"사진이 너무 큽니다"}`, 400 `{"code":"bad_length",...}`)
- Produces: HTTP — `POST /app/ai-cards?month=&dog_name=&dog_id=` → 202 `AiCardResponse`; `GET /app/ai-cards` → 200 `AiCardListResponse`; `GET /app/ai-cards/{card_id}` → 200 `AiCardResponse`; `DELETE /app/ai-cards/{card_id}` → 204; `GET /app/ai-cards/_bridge/download/{storage_key:path}` → PNG
- 오류 코드: 400 `bad_name`·`bad_mime`·`too_large`(사진 검증 쪽)·`undecodable`, 413 `too_large`, 404 `month_closed`·`dog_not_found`·`not_found`, 409 `already_generating`, 429 `limit_reached`, 503 `unavailable`·`storage`

- [ ] **Step 1: 실패하는 API 테스트**

```python
# backend/tests/test_ai_cards_api.py
"""routers/ai_card.py — `/app/ai-cards/*` HTTP 경계 (#537, D-076).

서비스 규칙은 `test_ai_card_service.py` 가 본다. 여기서는 상태 코드·오류 코드·응답 모양과,
POST 202 → (백그라운드) → GET ready → bridge 로 PNG 를 받는 한 바퀴를 본다.
"""

import asyncio
import io
import uuid

import pytest
from cardimage_fakes import FakeEngine, FakeJudge
from fakes import FakeAdmin, FakeAppUser, FakeSession, Store, install
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from daengs_backend.config import settings
from daengs_backend.core import storage as storage_module
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.core.storage import LocalBridgeStorage
from daengs_backend.routers import ai_card as ai_card_router
from daengs_backend.services import ai_card as service
from daengs_backend.services import ai_card_engine
from daengs_cardimage.photo import MAX_PHOTO_BYTES

OWNER = uuid.uuid4()
JPEG = {"Content-Type": "image/jpeg"}


class _SessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return FakeSession()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    s = install(Store(FakeAdmin()), monkeypatch)
    s.add_app_user(FakeAppUser(kakao_id=1, id=OWNER))
    return s


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch, tmp_path) -> LocalBridgeStorage:
    s = LocalBridgeStorage(str(tmp_path), base_url="http://x")
    monkeypatch.setattr(storage_module, "get_storage", lambda: s)
    monkeypatch.setattr(service, "get_storage", lambda: s)
    return s


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list:
    collected: list = []
    monkeypatch.setattr(service, "_spawn", collected.append)
    monkeypatch.setattr(service, "_session_factory", _SessionFactory())
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr("test-key"))
    monkeypatch.setattr(settings, "cardimage_months", frozenset({4, 9}))
    monkeypatch.setattr(settings, "cardimage_daily_limit", 1)
    monkeypatch.setattr(ai_card_engine, "default_engine", lambda: FakeEngine())
    monkeypatch.setattr(ai_card_engine, "default_judge", lambda: FakeJudge([4]))
    return collected


@pytest.fixture
def client(store: Store, storage: LocalBridgeStorage, jobs: list) -> TestClient:
    app = FastAPI()
    app.include_router(ai_card_router.router)
    app.dependency_overrides[next(iter(CurrentAppUser.__metadata__)).dependency] = lambda: AppPrincipal(
        app_user_id=OWNER
    )
    return TestClient(app, raise_server_exceptions=False)


def _photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


def _post(client: TestClient, **params):
    q = {"month": 4, "dog_name": "네오", **params}
    return client.post("/app/ai-cards", params=q, content=_photo(), headers=JPEG)


def _run_all(jobs: list) -> None:
    while jobs:
        asyncio.run(jobs.pop(0))


def test_한_바퀴(client: TestClient, jobs: list) -> None:
    r = _post(client)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "generating" and body["title"] == "BLOSSOM 네오"
    assert body["image_url"] is None

    _run_all(jobs)

    detail = client.get(f"/app/ai-cards/{body['id']}").json()
    assert detail["status"] == "ready"
    assert (detail["width"], detail["height"]) == (994, 1582)
    assert detail["likeness"] == 4 and detail["attempts"] == 1
    assert "/app/ai-cards/_bridge/download/" in detail["image_url"]

    png = client.get(detail["image_url"].removeprefix("http://x"))
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(png.content)).size == (994, 1582)


def test_list_has_no_image_urls(client: TestClient, jobs: list) -> None:
    _post(client)
    _run_all(jobs)
    cards = client.get("/app/ai-cards").json()["cards"]
    assert len(cards) == 1 and cards[0]["status"] == "ready" and cards[0]["image_url"] is None


def test_closed_month_is_404(client: TestClient) -> None:
    r = _post(client, month=12)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "month_closed"


def test_bad_photo_is_400(client: TestClient) -> None:
    r = client.post("/app/ai-cards", params={"month": 4, "dog_name": "x"}, content=b"nope", headers=JPEG)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "undecodable"


def test_blank_name_is_400(client: TestClient) -> None:
    r = _post(client, dog_name="   ")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_name"


def test_too_large_is_413(client: TestClient) -> None:
    r = client.post(
        "/app/ai-cards", params={"month": 4, "dog_name": "x"}, content=b"a" * (MAX_PHOTO_BYTES + 1), headers=JPEG
    )
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_large"


def test_no_key_is_503_with_user_message(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr(""))
    r = _post(client)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "unavailable" and "DAENGS_" not in detail["message"]


def test_busy_is_409(client: TestClient) -> None:
    assert _post(client).status_code == 202
    r = _post(client)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_generating"


def test_daily_limit_is_429(client: TestClient, jobs: list) -> None:
    _post(client)
    _run_all(jobs)
    r = _post(client)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "limit_reached"


def test_strangers_dog_is_404(client: TestClient) -> None:
    r = _post(client, dog_id=str(uuid.uuid4()))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "dog_not_found"


def test_unknown_card_is_404(client: TestClient) -> None:
    assert client.get(f"/app/ai-cards/{uuid.uuid4()}").status_code == 404
    assert client.delete(f"/app/ai-cards/{uuid.uuid4()}").status_code == 404


def test_delete_is_204_then_404(client: TestClient, jobs: list) -> None:
    card_id = _post(client).json()["id"]
    _run_all(jobs)
    assert client.delete(f"/app/ai-cards/{card_id}").status_code == 204
    assert client.get(f"/app/ai-cards/{card_id}").status_code == 404


def test_bridge_unknown_key_is_404(client: TestClient) -> None:
    assert client.get(f"/app/ai-cards/_bridge/download/ai-cards/{OWNER}/{uuid.uuid4()}.png").status_code == 404
```

- [ ] **Step 2: 실패 확인** — Run: `cd backend && uv run pytest tests/test_ai_cards_api.py -v` → FAIL (`ImportError: cannot import name 'ai_card'` from routers)

- [ ] **Step 3: 본문 읽기 공용화** — `backend/src/daengs_backend/routers/raw_body.py`:

```python
"""요청 본문 원시 바이트를 크기 상한과 함께 받는다 — 사진 한 장을 쿼리 메타와 같이 받는 경로용.

이 저장소는 multipart 를 쓰지 않는다(`python-multipart` 없음). `/admin/cardimage/generate` 와
`/app/ai-cards` 가 같이 쓴다.

`Content-Length` 를 먼저 보고 넘으면 즉시 끊는다 — 다만 그 헤더는 클라이언트가 주는 값이라
**믿지 않고**, 청크마다 누적 크기를 다시 검사해 본문을 끝까지 받기 전에도 413 으로 끊는다.
`await request.body()` 로 통째로 받았다가 검사하면 큰 업로드가 메모리를 다 채운 뒤에야 거절된다.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status


def _too_large() -> HTTPException:
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE, detail={"code": "too_large", "message": "사진이 너무 큽니다"}
    )


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"code": "bad_length", "message": "Content-Length가 올바르지 않습니다"},
            ) from None
        if declared_size > max_bytes:
            raise _too_large()

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise _too_large()
    return bytes(body)
```

  `routers/admin_cardimage.py` 에서 `_too_large`·`_read_body` 두 함수를 **삭제**하고, `from daengs_backend.routers.raw_body import read_limited_body` 를 import 에 더한 뒤 `body = await _read_body(request)` 를 `body = await read_limited_body(request, MAX_PHOTO_BYTES)` 로 바꾼다.

- [ ] **Step 4: 스키마** — `backend/src/daengs_backend/schemas/ai_card.py`:

```python
"""`/app/ai-cards/*` 응답 (#537). 카드 PNG 는 994×1582(5:8) 그대로 — 앱 표시 방식은 앱이 정한다."""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel


class AiCardResponse(BaseModel):
    id: uuid.UUID
    dog_id: uuid.UUID | None
    month: int
    dog_name: str
    title: str
    #: `generating` 이면 앱이 조금 뒤 다시 조회한다. `failed` 면 `error_code` 를 본다.
    status: Literal["generating", "ready", "failed"]
    error_code: str | None
    likeness: int | None
    attempts: int | None
    width: int | None
    height: int | None
    created_at: datetime.datetime
    #: 단건 조회에서 `ready` 일 때만 채운다. 목록에는 싣지 않는다.
    image_url: str | None = None


class AiCardListResponse(BaseModel):
    cards: list[AiCardResponse]
```

- [ ] **Step 5: 라우터** — `backend/src/daengs_backend/routers/ai_card.py`:

```python
"""`/app/ai-cards/*` — 앱 사용자 AI 도감 카드 HTTP 경계 (#537, D-076).

**비동기입니다.** POST 는 202 와 `status: generating` 을 바로 주고, 앱은 `GET /{id}` 로 다시
조회합니다. 사진은 요청 본문 원시 바이트, 메타는 쿼리입니다 (multipart 없음).

⚠️ **오류 본문의 `message` 는 앱이 그대로 띄웁니다.** 예외 메시지를 그대로 내보내지 마세요 —
   운영자용이라 환경 변수 이름이 들어 있습니다 (보행 라우터가 그것을 앱 화면에 띄운 적이 있습니다).
⚠️ **없는 것과 남의 것은 같은 404 입니다** — 403 이면 "그 id 는 존재한다" 가 샙니다.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.core.storage import StorageNotConfiguredError
from daengs_backend.models import AiCard
from daengs_backend.repositories import ai_card as ai_card_repo
from daengs_backend.routers.raw_body import read_limited_body
from daengs_backend.schemas.ai_card import AiCardListResponse, AiCardResponse
from daengs_backend.services import ai_card as ai_card_service
from daengs_backend.services.ai_card_quota import AiCardBusyError, AiCardLimitError
from daengs_cardimage import CardImageUnavailable
from daengs_cardimage.catalog import MonthNotOpenError
from daengs_cardimage.photo import MAX_PHOTO_BYTES, PhotoError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/app/ai-cards", tags=["ai-cards"])

Session = Annotated[AsyncSession, Depends(get_session)]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


def _not_found() -> HTTPException:
    return _error(status.HTTP_404_NOT_FOUND, "not_found", "카드를 찾을 수 없습니다.")


def _to_response(card: AiCard, image_url: str | None = None) -> AiCardResponse:
    return AiCardResponse(
        id=card.id,
        dog_id=card.dog_id,
        month=card.month,
        dog_name=card.dog_name,
        title=card.title,
        status=card.status,
        error_code=card.error_code,
        likeness=card.likeness,
        attempts=card.attempts,
        width=card.width,
        height=card.height,
        created_at=card.created_at,
        image_url=image_url,
    )


@router.post("", response_model=AiCardResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_card(
    request: Request,
    user: CurrentAppUser,
    session: Session,
    month: Annotated[int, Query(ge=1, le=12)],
    dog_name: Annotated[str, Query(min_length=1, max_length=40)],
    dog_id: uuid.UUID | None = None,
) -> AiCardResponse:
    """사진 한 장으로 카드 만들기를 **시작합니다.** 끝나면 `GET /{id}` 가 `ready` 를 줍니다."""
    if not dog_name.strip():
        raise _error(status.HTTP_400_BAD_REQUEST, "bad_name", "강아지 이름이 비어 있습니다.")
    body = await read_limited_body(request, MAX_PHOTO_BYTES)
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    try:
        card = await ai_card_service.start(
            session,
            user.app_user_id,
            photo=body,
            content_type=content_type,
            month=month,
            dog_name=dog_name,
            dog_id=dog_id,
        )
    except PhotoError as exc:
        raise _error(status.HTTP_400_BAD_REQUEST, exc.code, exc.detail) from None
    except MonthNotOpenError:
        raise _error(status.HTTP_404_NOT_FOUND, "month_closed", "지금은 만들 수 없는 달이에요.") from None
    except ai_card_service.AiCardNotFoundError:
        raise _error(status.HTTP_404_NOT_FOUND, "dog_not_found", "강아지를 찾을 수 없습니다.") from None
    except CardImageUnavailable as exc:
        log.warning("AI 카드 생성이 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "unavailable", "카드 만들기는 지금 준비 중이에요.") from None
    except StorageNotConfiguredError as exc:
        log.warning("AI 카드 저장소가 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage", "카드 보관은 아직 준비 중이에요.") from None
    except AiCardBusyError:
        raise _error(
            status.HTTP_409_CONFLICT, "already_generating", "만들고 있는 카드가 있어요. 끝나면 다시 시도해 주세요."
        ) from None
    except AiCardLimitError:
        raise _error(
            status.HTTP_429_TOO_MANY_REQUESTS, "limit_reached", "오늘은 카드를 더 만들 수 없어요. 내일 다시 시도해 주세요."
        ) from None
    return _to_response(card)


@router.get("", response_model=AiCardListResponse)
async def list_cards(user: CurrentAppUser, session: Session) -> AiCardListResponse:
    """내 카드 전부, 최근 것부터. **이미지 주소는 안 싣습니다** — 필요한 것만 단건 조회합니다."""
    cards = await ai_card_service.list_cards(session, user.app_user_id)
    return AiCardListResponse(cards=[_to_response(c) for c in cards])


@router.get("/{card_id}", response_model=AiCardResponse)
async def get_card(card_id: uuid.UUID, user: CurrentAppUser, session: Session) -> AiCardResponse:
    try:
        card, url = await ai_card_service.get_card(session, user.app_user_id, card_id)
    except ai_card_service.AiCardNotFoundError:
        raise _not_found() from None
    except StorageNotConfiguredError as exc:
        log.warning("AI 카드 저장소가 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage", "카드 보관은 아직 준비 중이에요.") from None
    return _to_response(card, url)


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_card(card_id: uuid.UUID, user: CurrentAppUser, session: Session) -> None:
    try:
        await ai_card_service.delete_card(session, user.app_user_id, card_id)
    except ai_card_service.AiCardNotFoundError:
        raise _not_found() from None
    except StorageNotConfiguredError as exc:
        log.warning("AI 카드 저장소가 준비되지 않았습니다: %s", exc)
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "storage", "카드 보관은 아직 준비 중이에요.") from None


# ── local 저장소의 bridge ────────────────────────────────────────────────
#
# ⚠️ **인증 헤더를 요구하지 않습니다 — 대신 키가 자격입니다** (`routers/dogcard.py` 와 같은 이유).
#    `ready` 행에 적힌 키만 내려줍니다.


@router.get("/_bridge/download/{storage_key:path}", include_in_schema=False)
async def _bridge_download(session: Session, storage_key: str):
    from fastapi.responses import FileResponse

    from daengs_backend.core.storage import LocalBridgeStorage

    storage = ai_card_service.get_storage()
    if not isinstance(storage, LocalBridgeStorage):
        raise _not_found()
    if await ai_card_repo.find_ready_by_storage_key(session, storage_key) is None:
        raise _not_found()
    path = storage.local_path(storage_key)
    if not path.exists():
        raise _not_found()
    return FileResponse(path, media_type=ai_card_service.AI_CARD_CONTENT_TYPE)
```

  ⚠ `/_bridge/download/{...}` 와 `/{card_id}` 가 겹치지 않는지: `card_id` 는 UUID 한 조각이라 `_bridge/...` 여러 조각과 매칭되지 않는다. 그래도 테스트 `test_bridge_unknown_key_is_404` 와 한 바퀴 테스트가 실제로 확인한다.

- [ ] **Step 6: 등록** — `main.py`:
  - `from daengs_backend.routers import (` 목록의 `admin_cardimage,` 다음 줄(정렬 순서에 맞게)에 `ai_card,`
  - `app.include_router(admin_cardimage.router)` 줄 바로 다음에 `app.include_router(ai_card.router)`

- [ ] **Step 7: 통과 확인** — Run: `cd backend && uv run pytest tests/test_ai_cards_api.py tests/test_cardimage_admin_api.py tests/test_ai_card_service.py -v` → PASS

- [ ] **Step 8: 앱 전체 기동 확인** — Run: `cd backend && uv run python -c "from daengs_backend.main import app; print(sorted(r.path for r in app.routes if 'ai-cards' in r.path))"` → `/app/ai-cards`, `/app/ai-cards/{card_id}`, `/app/ai-cards/_bridge/download/{storage_key:path}` 가 보인다. (암호화 키 등 `.env` 가 없어 import 가 실패하면 그 사실을 보고만 하고 넘어간다.)

- [ ] **Step 9: 커밋 (컨트롤러)**

```bash
git add backend/src/daengs_backend/routers/raw_body.py backend/src/daengs_backend/routers/admin_cardimage.py backend/src/daengs_backend/schemas/ai_card.py backend/src/daengs_backend/routers/ai_card.py backend/src/daengs_backend/main.py backend/tests/test_ai_cards_api.py
git commit -m "앱 사용자가 AI 카드를 만들고 조회·삭제하는 /app/ai-cards 경로를 열었다"
```

---

### Task 6: 기록 — D-076 · cardimage 문서 · CLAUDE.md

**Files:**
- Modify: `docs/decisions.md` (상단 표 마지막 행 다음 + 파일 끝에 `## D-076` 절)
- Modify: `docs/cardimage/README.md` («지금 상태» 맨 앞 절 추가, «파일 위치» 표의 `services/cardimage/` 행, «정해진 것» 표 행 추가, «안 정해진 것» 첫 항목 갱신)
- Modify: `docs/cardimage/worklog.md` (맨 위 첫 `##` 절 앞에 오늘 절)
- Modify: `CLAUDE.md` (폴더 표에 `backend/src/daengs_cardimage/` 행, `cardimage/` 행의 설명)

**Interfaces:** 없음 (문서)

- [ ] **Step 1: D-076** — `docs/decisions.md` 상단 표의 `D-075` 행 다음에:

```markdown
| [D-076](#d-076) | 도감 카드 생성 로직은 `daengs_cardimage` 로 떼고, 앱 경로는 backend 프로세스 안 비동기로 | 2026-09-14 |
```

  파일 끝에:

```markdown
## D-076
### 도감 카드 생성 로직은 `daengs_cardimage` 로 떼고, 앱 경로는 backend 프로세스 안 비동기로

2026-09-14, #537. 설계는 `docs/cardimage/spec-2026-09-14-app-ai-cards.md`.

**경계.** 사진 → 카드 PNG 를 만드는 로직(`catalog·photo·title·engine·judge·generate`)만
`backend/src/daengs_cardimage/` 로 뗐다. 이 패키지는 `daengs_backend`·웹·DB 를 import 하지 않는다
(`tests/test_cardimage_boundary.py`). 사용자·표 `ai_cards`·저장소·API 는 backend 에 남고, backend 가
생성을 부르는 곳은 `services/ai_card_engine.py` 하나다. `daengs_walk`·`daengs_gait` 와 같은 모양이다 —
**도메인 패키지는 순수 로직, backend 는 사람·저장·입구.** 라우터·모델까지 새 패키지에 넣는 screening
방식은 기각했다(인증·저장소·세션을 거꾸로 끌어와 경계가 흐려지고 관례가 둘로 굳는다).

이렇게 뗀 이유 중 하나는 나중에 생성만 Cloud Run 같은 별도 서비스로 옮길 가능성이다. 그날 할 일은
패키지 앞에 HTTP 한 장, `ai_card_engine` 안에 URL 갈림길 — D-070 의 `DAENGS_REALTIME_URL` 과 같은
모양이다. 지금 HTTP 경계를 미리 만들지는 않았다.

**앱 계약은 비동기.** `POST /app/ai-cards` 는 행을 `generating` 으로 커밋하고 202 를 준다. 생성이
30~60초 걸리는 유료 호출이라, 폰 연결 하나에 걸면 앱을 내리는 순간 돈만 쓰고 결과를 잃는다. 서버
안에서 누가 만드는지가 바뀌어도 앱은 모른다.

**실행은 backend 프로세스 안 백그라운드** (Celery 아님). 새 컨테이너·큐 없이 개발서버와 GCP 가 똑같이
돈다. 대가는 배포 재시작과 겹친 작업이 사라지는 것이고, 그 행은 `4 × cardimage_timeout_ms + 60초`
(기본 9분)가 지나면 조회 때 `failed`/`interrupted` 가 된다. **워커로 옮길 조건:** ⓐ `interrupted` 가
실제로 보일 때 ⓑ 동시 생성이 backend 응답을 느리게 만들 때 ⓒ 서버가 자동 재시도해야 할 때.

**한도는 테스트 단계용.** 사용자별 동시 1장(DB 부분 UNIQUE) + KST 하루 `ready` N장
(`DAENGS_CARDIMAGE_DAILY_LIMIT`, 기본 1). 실패는 세지 않는다. 카드를 몇 장·어떤 조건으로 줄지는 정하지
않았고, 정해지면 `services/ai_card_quota.py::check_quota` 를 통째로 바꾼다.

**이미지는 994×1582(5:8) 그대로** 주고 `width`·`height` 를 싣는다. 3:4 로 자르지 않는다 — 앱 표시는
DAENGS_APP 쪽 결정이다.

되돌리기: 실행 위치는 `services/ai_card.py` 의 `_spawn`·`_run` 안이라 워커로 옮겨도 API·표는 그대로다.
패키지 경계는 되돌릴 이유가 없다.
```

- [ ] **Step 2: `docs/cardimage/README.md`**
  - «## 지금 상태 (2026-09-14, 1단계 구현 완료 + 9월 추가)» 제목을 «## 지금 상태 (2026-09-14, 앱 경로 #537 + 1단계 + 9월)» 로 바꾸고, 그 바로 아래 첫 문단으로:

```markdown
**앱 사용자 경로 (09-14, #537 · D-076).** 앱이 `POST /app/ai-cards?month=&dog_name=&dog_id=`(본문 사진)로
카드 만들기를 시작하면 202 + `status: generating` 을 받고, `GET /app/ai-cards/{id}` 가 `ready` +
`image_url`(994×1582) 을 줄 때까지 다시 조회한다. 서버는 표 `ai_cards` 에 행을 먼저 남기고 backend 프로세스
안 백그라운드에서 만든다. 한도는 사용자별 동시 1장 + 하루 완성 1장(`DAENGS_CARDIMAGE_DAILY_LIMIT`). 생성
로직은 `backend/src/daengs_cardimage/` 로 옮겼다. 설계 `spec-2026-09-14-app-ai-cards.md`, 계획
`plan-2026-09-14-app-ai-cards.md`. **배포 전에** `db/migrations/2026-09-14_ai_cards.sql` 을 개발서버·GCP DB 에 적용한다.
```

  - «파일 위치» 표의 `backend/src/daengs_backend/services/cardimage/` 행을 다음 두 행으로 바꾼다:

```markdown
| `backend/src/daengs_cardimage/` | **생성 로직 패키지** (#537 에서 `daengs_backend/services/cardimage/` 에서 옮김) — catalog(틀·무대)·photo(검증·리사이즈)·title(Pillow 제목)·engine(Nano Banana 2 어댑터)·judge(닮음 검수)·generate(파이프라인). backend 를 import 하지 않는다 | 커밋 |
| `backend/src/daengs_backend/services/ai_card_engine.py` · `ai_card.py` · `ai_card_quota.py` · `routers/ai_card.py` · `routers/admin_cardimage.py` | backend 쪽 — 설정으로 엔진 만들기(유일한 호출 자리) · 앱 경로 서비스 · 한도 · `/app/ai-cards` · `/admin/cardimage/generate` | 커밋 |
```

  - «정해진 것» 표 마지막 행 다음에:

```markdown
| 앱 경로 | `/app/ai-cards` 비동기(202 → 조회), backend 프로세스 안 백그라운드, 994×1582 그대로, 사용자별 동시 1장 + 하루 완성 1장 — **D-076** | 사용자 결정 09-14 (#537) |
```

  - «안 정해진 것» 의 «**출력을 앱 도감에 어떻게 얹을지.**» 항목을 다음으로 바꾼다:

```markdown
- **앱에서 AI 카드를 어떻게 보여 줄지.** 서버는 994×1582(5:8) 를 그대로 주기로 했다(D-076) — 기존 3:4 카드와 섞을지·탭을 나눌지는 DAENGS_APP 쪽 결정.
- **제품 규칙** — 카드를 몇 장·어떤 조건(달마다 한 장, 활동 보상, 유료 등)으로 줄지. 지금 한도는 테스트 단계용이고 `services/ai_card_quota.py::check_quota` 를 통째로 바꾼다.
```

- [ ] **Step 3: `docs/cardimage/worklog.md`** — 첫 `## 2026-09-14 오후` 절 **앞**에:

```markdown
## 2026-09-14 저녁 — 앱 사용자 경로 (#537)

설계 대화에서 정한 다섯 가지: ① 생성 로직만 `daengs_cardimage` 로 분리(나중에 Cloud Run 으로 뗄 부분을 한
덩어리로) ② 앱 계약 비동기 ③ backend 프로세스 안 백그라운드 ④ 994×1582 그대로 ⑤ 하루 1장(테스트 단계). 전부
D-076. 코드를 보다가 고친 것: 정리 기준을 5분에서 `4 × timeout + 60초`(9분)로 — 엔진·검수 120초에 재시도까지
최악 8분이라 5분이면 정상 작업을 실패로 덮는다. `StoragePort` 에 서버 쓰기 메서드가 없어 local·GCS 를 갈라 쓴다.
구현은 subagent-driven-development 로 Task 1~6.
```

- [ ] **Step 4: CLAUDE.md** — 폴더 표에서 `backend/src/daengs_journey/` 행 **다음**에:

```markdown
| `backend/src/daengs_cardimage/` | 도감 카드 AI 생성의 **순수 로직**(틀·사진·제목·Nano Banana 2 엔진·검수·파이프라인). DB·웹·`daengs_backend` 를 import 하지 않고, backend 는 `services/ai_card_engine.py` 한 곳에서만 부릅니다. 앱 경로 `/app/ai-cards` 는 backend 프로세스 안 비동기 — D-074 · D-076. 인수인계는 `docs/cardimage/` |
```

- [ ] **Step 5: 확인** — Grep 도구로 `docs/cardimage/README.md`·`CLAUDE.md` 에 `services/cardimage` 가 남지 않았는지(기록 문서 `plan-2026-09-14-phase1.md`·`worklog.md` 는 과거 기록이라 그대로). `cd backend && uv run check` 통과.

- [ ] **Step 6: 커밋 (컨트롤러)**

```bash
git add docs/decisions.md docs/cardimage/README.md docs/cardimage/worklog.md CLAUDE.md
git commit -m "도감 카드 앱 경로의 경계·비동기·한도 결정을 D-076 으로 기록했다"
```

---

## 최종 게이트 (컨트롤러)

- [ ] `cd backend && uv run check`
- [ ] `cd backend && uv run pytest` (전체, 약 9~12분 — 하위 에이전트가 모두 끝난 뒤)
- [ ] 바꾼 파일 `uv run ruff check` · `uv run ruff format --check` (새 파일 전부 + 수정한 파일)
- [ ] 버리는 Postgres 로 마이그레이션 변조 하네스 (`docs/ci/README.md` ②) — `ai_cards` 항목이 통과하고 변조 9개를 전부 잡는지
- [ ] PR #537 본문: 작업 목록 체크, 「배포 영향」(`db/init/` 변경 + 마이그레이션 손 적용, 새 환경 변수는 기본값 있음), 「확인한 것」에 실제 수치

## Self-Review

- **Spec 대조:** §1 패키지(Task 1) · §2 표·verify·하네스(Task 2) · §3 API·POST 순서 1~10·조회 시 정리(Task 4 `start`·`_expire_stale`, Task 5 라우터) · §4 실행·세마포어·정리 기준·재시작·완료 시 행 확인(Task 4) · §5 한도(Task 3) · §6 삭제·탈퇴(Task 4) · §7 테스트(각 Task) · §8 설정·문서(Task 3·6). 정리 기준 시각은 spec 의 `updated_at` 대신 `created_at` 을 쓴다 — `generating` 행은 완료 전까지 갱신되지 않아 같은 값이고, 한도의 "오늘" 기준과 칸을 맞췄다.
- **자리표시자:** 없음. Task 4 Step 6 의 탈퇴 테스트 파일 이름은 Grep 결과를 따르게 명령으로 적었다.
- **이름 일치:** `ready_check`·`generate`·`default_engine`·`default_judge`(T1→T4), `AiCard`·`AI_CARD_STATUSES`(T2→T3·T4·T5), `check_quota`·`stale_after`·`AiCardBusyError`·`AiCardLimitError`(T3→T4·T5), `expire_generating(..., created_before=, now=)`(T3 실물·가짜·T4), `start/list_cards/get_card/delete_card/cleanup_for_owner`·`AiCardNotFoundError`·`AI_CARD_BRIDGE_DOWNLOAD_PATH`·`AI_CARD_CONTENT_TYPE`·`_spawn`·`_session_factory`(T4→T5), `build_ai_card_key`(T4), `read_limited_body`(T5).
