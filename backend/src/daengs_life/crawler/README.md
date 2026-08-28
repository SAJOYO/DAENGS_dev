# crawler/

오케스트레이터 무관 순수 파이썬 패키지 + CLI (RAG-001 원칙 1). `app/`·`tasks/` 를 import 하지 않는다.

```
crawler/
├── __init__.py        패키지 설명 (최상위만 둔다. core/·sources/ 는 빈 __init__ 없이 namespace 서브패키지)
├── __main__.py        CLI: list / run  — `python -m crawler` 의 진입점
├── core/                                  ← 모든 소스가 공유. 고치면 전부에 적용된다
│   ├── config.py      Settings(pydantic-settings) — .env, API 키, 경로, UA, 요청 간격
│   ├── fetch.py       Fetcher — UA·호스트별 1.5s 간격·재시도·robots.txt
│   ├── store.py       raw/ 저장 + .meta.json + crawl_log.jsonl, sha256 변경 감지
│   ├── textutil.py    블록 단위 본문 추출 + 「법령」 조항 인용 파싱 (한국 법령 문서 공통)
│   └── registry.py    seed_sources.yaml 로드, id → sources/ 모듈 매핑
└── sources/                               ← 사이트 고유 지식만. 폴더 = yaml 의 domain
    ├── base.py        Source / Target / Extracted 계약
    └── law/
        ├── _lawgokr.py               law.go.kr 웹 원문 공통 베이스 (앞의 _ = 소스가 아니라 베이스)
        ├── easylaw_pet.py            생활법령 해설
        ├── law_animal_protection.py  동물보호법 3종       ┐ _lawgokr 상속.
        ├── law_livestock_epidemic.py 가축전염병예방법 3종  ┘ 법령 목록만 선언한다
        └── law_drf_api.py            법령 Open API (조문 단위 XML) — LAW_OC 필요
```

`registration/` `vaccination/` `leash-muzzle/` `transport/` `subsidy/` `insurance/` 도 같은 방식으로 생긴다.
(`realtime` 은 저장하지 않고 API 직조회라 소스 모듈이 없다.)

**레이어는 셋이다.**

| 층 | 위치 | 아는 것 |
|---|---|---|
| ① 공통 | `core/` | 받기·저장·변경감지·텍스트 정리·키 마스킹. 사이트를 모른다 |
| ② 사이트 | `sources/{domain}/_*.py` | 사이트 하나의 구조 (URL 패턴, 셀렉터) |
| ③ 대상 | `sources/{domain}/{id}.py` | 그 사이트에서 무엇을 받을지 |

소스가 23개가 되어도 새로 쓰는 코드는 ③뿐이어야 한다. 같은 사이트를 두 번째로 건드리면 ②를 만들고,
사이트가 달라도 반복되면 ①로 올린다 (`textutil` 이 그렇게 올라왔다).

**모듈 위치는 계산한다** — yaml 의 `domain` + `id` → `sources/{domain}/{id}.py` (`-` → `_`).
폴더를 스캔하지 않으므로 소스가 늘어도 조회 비용이 그대로다. `domain` 은 `data/raw/{domain}/` 저장
위치와 같은 축이라 새 규약이 늘지 않는다.

registry 는 모듈 안에서 `__module__` 이 그 모듈인 `Source` 하위 클래스를 찾는다 —
`_lawgokr.py` 에서 import 해 온 베이스 클래스는 자동으로 걸러지므로 상속해도 충돌하지 않는다.

## API 키와 경로

`backend/.env` 에 넣는다 (`backend/.env.example` 참고). 이름은 `docs/data-sources.md` §9 와 같다.

`core/config.py` 의 `Settings(BaseSettings)` 가 **pydantic-settings** 로 읽는다 (RAG-015).
값을 추가하려면 필드를 선언하면 되고, 키라면 `_SECRET_FIELDS` 에도 이름을 넣어 마스킹 대상으로 만든다.

읽는 순서는 **실제 환경변수 > `backend/.env` > 레포 루트 `.env`** (RAG-014).
두 파일은 병합된다 — 루트에만 있는 값도 올라오고, 겹치면 backend 쪽이 이긴다.
배포에서는 오케스트레이터가 넣은 환경변수가 항상 이기므로 파일이 없어도 그대로 돈다.

**키가 URL 에 들어가는 소스는 저장·로그·출력 전에 `config.redact()` 를 통과한다.**
`.meta.json` 은 이제 커밋되지 않지만(RAG-017), 키가 박힌 URL 은 그대로 재사용 가능한 자격증명이라
로컬 파일·로그·화면 어디에도 남기지 않는다. 새 API 소스를 만들 때 확인할 것.

`data/` 위치는 레포 안에서 실행하면 자동으로 찾는다. 레포 밖(컨테이너)에서는 `DAENGS_DATA_DIR` 로
알려준다 — 안 주면 `require_data_dir()` 이 안내와 함께 실패한다. **import 는 실패하지 않는다**:
백엔드 이미지에는 `data/` 가 없고 `app → crawler` 라, import 시점에 터지면 컨테이너가 안 뜬다.

## 테스트

```bash
uv sync --group dev
uv run pytest
```

- `test_import_direction.py` — `crawler` 안의 import 문을 AST 로 훑어 `app`·`tasks`·`main` 을
  끌어다 쓰지 않는지 검사한다 (RAG-009 의존 방향, RAG-014). 검사기가 살아 있는지 확인하는 테스트가
  한 개 더 있다 — 검사기가 조용히 아무것도 안 보게 되면 그쪽이 먼저 실패한다.
- `test_config_env.py` — 설정 우선순위, `.env` 파싱(주석·export·따옴표), 키 마스킹,
  레포 밖 실행 (RAG-014, RAG-015).

## 실행

```bash
cd backend
uv run python -m crawler list                                   # 소스 목록 + 구현 여부
uv run python -m crawler run --source easylaw-pet --dry-run --limit 3   # 아무것도 안 쓰고 추출 결과만
uv run python -m crawler run --source easylaw-pet               # 수집 (원본 없는 것은 자동 재수집)
uv run python -m crawler run --source easylaw-pet --force       # sha256 같고 원본 있어도 새 파일로
```

## 소스 하나 추가하는 절차

1. **정찰** — `curl -A "daengs-life-crawler/0.1"` 로 URL 살아있는지, 본문 셀렉터, 페이징, 인코딩, robots 확인.
   yaml의 `status: verified` 를 믿지 말 것 (easylaw-pet 시드 URL도 깨져 있었다). 깨졌으면 yaml 수정.
2. **모듈** — `sources/{domain}/{id_with_underscores}.py` 에 `Source` 서브클래스 하나.
   도메인 폴더가 없으면 만든다 (`__init__.py` 없이 — namespace 패키지).
   - 클래스 속성: `id domain category subcategory source_type format trust_level license` → `.meta.json` 으로 그대로 흘러감
   - `discover(fetcher) -> list[Target]` : 받을 URL + slug(파일명) 목록
   - `extract(res, target) -> Extracted` : 제목·본문·`published_at`·`cites`.
     html 소스는 이 본문 텍스트가 변경 감지 지문(sha256)이 되므로 조회수 같은 노이즈를 여기서 제거
   - 문서마다 분류가 다르면 `Target.meta["subcategory"]` 로 덮어쓴다 (한 소스가 여러 법령을 받는 경우)
   - 키가 필요하면 `config.SECRETS` 에 이름을 추가하고, 미설정일 때 **발급처를 알려주며** 멈춘다
3. **시험** — `--dry-run --limit 3`. 제목이 비거나 메뉴가 본문으로 잡히면 여기서 잡는다.
   API 소스는 여기서 `.meta.json` 의 `source_url` 에 키가 가려졌는지 반드시 눈으로 확인.
4. **수집** — `run --source {id}`. `data/raw/{domain}/` 에 원본 + meta, `crawl_log.jsonl` 에 한 줄씩.
5. **기록** — `data/` 의 수집 결과물은 **커밋하지 않는다** (원본·meta·크롤로그 전부 로컬, RAG-017).
   대신 `docs/data-sources.md` 체크리스트에 수집 완료를 표시하고,
   `data/README.md` 값 사전(subcategory 표)에 새 값을 추가한다.

소스 모듈은 site-specific 지식만 가진다. 받기·저장·로그·변경 감지·예절은 전부 `core/` — 고치면 모든 소스에 적용된다.

> `.meta.json` 은 원본이 **바뀔 때만** 다시 쓰인다. 그래서 meta 를 만드는 로직(예: `cites` 정규식)을
> 고쳐도 기존 meta 는 옛 값 그대로다. 같은 날이면 `--force` 가 파일명이 같아 제자리 갱신이 된다.

## 설계 메모

- `sha256` 은 html 이면 `extract().text` 해시, 그 외는 원본 바이트 해시. `store.py` 주석 참고.
- 같은 slug 의 최신 `.meta.json` 과 지문이 같으면 아무것도 안 쓰고 로그만 `changed:false`.
  다르면 오늘 날짜로 새 파일 (옛 파일 보존 — 원본 불변).
- **지문이 같아도 meta 의 `raw_file` 이 디스크에 없으면 다시 받는다** (RAG-010, 출력 라벨 `RAW-MISSING`).
  `data/` 를 PC 사이에 복사했거나 용량 때문에 원본만 지운 경우, 그대로 두면 전부 `same` 으로 스킵돼
  파싱 단계가 빈손이 된다. `--force` 없이 그냥 `run` 하면 없는 것만 채워진다.
- 왜 받았는지는 `crawl_log.jsonl` 의 `reason`: `new`/`changed`/`raw-missing`/`forced`/`same`.
- `Fetcher` 는 4xx 는 즉시 반환(재시도 무의미), 5xx·네트워크 오류만 지수 백오프 3회.
- 시간대는 `Asia/Seoul` 고정. Windows 에는 tz DB 가 없어 `tzdata` 패키지가 의존성에 들어 있다.
