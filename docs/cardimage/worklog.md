# cardimage 작업 기록

세션이 끝날 때마다 한 절씩 위에 추가한다 (최신이 위). 무엇을 했고, 무엇을 정했고, 무엇을
다음 세션에 넘기는지. 조사 내용 자체는 `research-*.md` 에, 요약·현재 상태는 `README.md` 에.

## 2026-09-16~17 — #572 12달 열기 + 뽑기 (Task 1~8 · 최종 리뷰 수정 파동, 머지 대기)

`docs/superpowers/plans/2026-09-16-ai-card-12months-and-two-picks.md` 를 subagent-driven-development 로
실행한 카드 하나의 기록이다(원장은 SDD 폴더 `progress.md`). 한때 「최종 리뷰 수정 파동」과 「Task 1~7」
두 절로 나뉘어 있던 것을 Task 8 에서 한 절로 합쳤다(최신이 위).

### 2026-09-17 — Task 8: 한 요청의 장수는 엔진이 정한다 (사람 결정)

**왜.** 최종 리뷰 Important 2 가 「머지 즉시 운영 앱 동작이 바뀐다」를 사람 결정으로 올렸다. 운영은
`DAENGS_CARDGEN_URL` 이 비어 `ai_card_engine.default_engine()` 이 Nano Banana 2 를 쓰는데,
`cardimage_pick_count` 기본값 2 라 요청마다 Nano Banana 2 2회 + 검수 2회를 부르게 된다. 앱에는 두 장 중
고르는 화면이 없어 같은 달 카드 두 장이 보이고, 하나를 지워도 나머지 때문에 `month_taken` 이며, 대표가
`ready` 인데 둘째가 도는 중 다시 누르면 409 다. `PICK_COUNT=1` 로 좁혀도 안 됐다 — 서비스가 항상
`generate(seed=...)` 로 불러 명시 seed 는 재시도가 없으므로(`generate.py` 의 `seed is not None` 분기),
이 브랜치는 앱 경로의 닮음 미달 재시도를 없앤 상태였다.

**사람 결정 (2026-09-17).** 2장은 `FLUX.2-klein-4B` GPU 경로에서만, 운영 Nano Banana 2 는 「1장 + 닮음
미달이면 재시도」 유지. 사용자: 「어차피 2장으로 바꾸면 앱도 같이 손 봐야」 — 두 장은 앱의 고르기 화면과
`DAENGS_CARDGEN_URL` 켜기와 한 묶음으로 나간다. 함께 정한 것: 10달 Nano Banana 2 스모크는 안 한다,
개발서버 DB 는 `dev` 머지 직전에, GCP DB 는 dev→main 때 마이그레이션을 적용한다.

**바꾼 것.**

- **판정 한 곳** — `services/ai_card_engine.py::gpu_path_active()`(`settings.cardgen_url.strip()` 이
  비어 있지 않으면 참). `default_engine()` · 새 `plan_request_seeds()`(장수) ·
  `services/ai_card.py::_finish_ready`(seed 기록) · `ai_card_quota.stale_after()`(예산) ·
  `ready_check()`(키 확인)가 전부 이것을 부른다. 수정 파동이 `_finish_ready` 에 넣었던 인라인
  `strip()` 판정은 지웠다
- **`plan_request_seeds(month, rng)`** — 길이가 곧 행 수다. Nano Banana 2 경로는 `[None]`(한 장, seed
  미지정), GPU 경로는 `plan_seeds(month, cardimage_pick_count, rng)`(서로 다른 seed 만큼, 최대 2)
- **`start`** 는 그 목록으로 행을 만든다 — Nano Banana 2 경로는 행 하나, `seed` 칸은 처음부터 `None`
- **`_run`** 은 목록의 seed 를 그대로 넘긴다. `None` 이면 `generate_card` 가 seed 없는 `count == 1`
  경로로 가서 첫 장의 닮음이 `cardimage_judge_min` 미만이면 **같은 행 안에서** 한 번 더 만들고 나은
  쪽을 남긴다(`attempts` 2). 슬롯은 행마다 한 번만 잡으므로 시도 표시는 첫 유료 호출 전에 한 번만
  남는다. `stale_after()` 의 `4 × cardimage_timeout_ms + 60초` 가 원래 이 두 시도(엔진·검수 × 2)를
  덮도록 잡힌 값이라 예산은 그대로다
- `cardimage_pick_count` 의 기본값·범위, 관리자 콘솔 `/admin/cardimage/generate`, DB 스키마·마이그레이션은
  안 바꿨다
- **테스트** — Nano Banana 2 경로(행 1개·seed `None`·기준 이상이면 엔진 1회·미달이면 2회 + `attempts`
  2·표시는 첫 호출 전 한 번·`total == 1`·`finished`), GPU 경로 명시 seed·재시도 없음, 그리고 URL 이
  있음/앞뒤 공백/빈 값/공백뿐일 때 판정·엔진 선택·장수·seed 기록이 함께 가는지. 두 장을 보던 기존
  테스트는 `cardgen_url` 을 명시로 켜서(`_two_card_gpu_path`) 계속 두 장 경로를 본다
- **문서** — README 「지금 상태」·「정해진 것」, roadmap 3번 행, D-084, PR 본문 초안의 「요청마다 2장」
  서술을 엔진별로 고쳤다. PR 본문 「배포 영향」은 위 결정대로 다시 썼다: 개발서버 DB 는 `dev` 머지 직전
  (`.github/workflows/deploy.yml` 이 `dev` push 로 배포), GCP DB 는 dev→main 때
  `docs/deploy/runbook.md` §6 절차를 따르되 ③(마이그레이션)을 ④(배포) 전에 한다 — ① 개발 PC 에서
  `git push gcp main`(객체만 보낸다, 배포 아님) → ② VM `git fetch` · `git diff --stat HEAD origin/main` →
  ③ VM 에서 새 마이그레이션을 파일 이름 순서로 `-v ON_ERROR_STOP=1` 을 붙여 적용하고 `verify_*.sql` 도 →
  ④ VM `git merge --ff-only origin/main` = 배포. 수정 파동이 적은 「`db-migrate.yml` 로 개발서버·GCP DB
  양쪽에」는 틀렸다 — 그 워크플로는 `runs-on: [self-hosted, Windows, X64]`(개발서버)라 GCP VM 의 DB 에는
  닿지 않는다(수정 라운드 1 에서 그 항목에도 정정을 붙였다)

### 2026-09-16 — 최종 리뷰 수정 파동 (Critical 0, 머지 전 마지막 손질)

전체 브랜치 리뷰(opus, `review-d0c707ac..ea7e921c.diff`)가 READY AFTER FIXES 로 승인하며 남긴
Important 1건(배포 영향 서술)·minor 5건을 한 번에 처리했다.

- **PR 본문 「배포 영향」** — 실제 사고는 409 가 아니라, 09-16 마이그레이션 전에 새 코드가 뜨면
  `select(AiCard)` 가 없는 컬럼을 읽어 `/app/ai-cards` 전체와 **모든 사용자의 회원 탈퇴**가 500 이
  되는 것이다(`services/app_auth.py:370` · `repositories/ai_card.py:211` 확인). 마이그레이션 목록을
  참 파일 이름 순서로 바로잡고, `.github/workflows/db-migrate.yml`(self-hosted, `ref`·`verify`
  입력 확인)로 머지 전에 개발서버·GCP DB 양쪽에 적용하는 절차를 적었다 **(Task 8 에서 정정 — 그
  워크플로는 `runs-on: [self-hosted, Windows, X64]` 라 개발서버 DB 만 닿는다. GCP 는 runbook §6)**. 옛 코드로 롤백하면 옛
  `count_usage_since` 가 오늘 미달·실패·삭제 시도까지 세어 429 를 낼 수 있다는 것도 남겼다
  (초과해서 세는 것뿐, 데이터 손실은 없다)
- **`ai_cards.seed` 거짓 기록** — Nano Banana 2 는 seed 인자를 받고도 무시하는데 뽑은 값을 그대로
  저장하고 있었다. `services/ai_card.py::_finish_ready` 에서 `cardgen_url` 이 비어 있으면(엔진
  팩토리와 같은 `strip()` 판정) `card.seed = None` 으로 저장하도록 고쳤다 — 엔진에 넘기는 seed
  자체는 그대로다. 두 경우(GPU 엔진 있음/Nano Banana 2) 테스트를 추가했다
- **콜드 스타트 수치 출처** — `ai_card_quota.py::stale_after` docstring 과 D-084 가 340.7·374.9·
  403.1초를 전부 #557 E3 탓으로 돌리고 있었다. `worklog`·`roadmap.md`·
  `compare-2026-09-16-klein-e2-e3.md` 를 다시 찾아 403.1초는 E1(옛 이미지로 재기동), 374.9초는
  이미지 교체 뒤 첫 기동, 340.7초는 E3(기준 리비전으로 되돌려 재측정)로 바로잡고 끊긴 문장에
  마침표를 붙였다
- **`plate_probe.py` 주석 둘** — 탐침 열 범위 `230~296` → 실제 `range(230, 300, 4)` 끝값인 `298`
  로, 「과반수가 어둡다」→ 코드(`>= len(PROBE_XS) // 2`, 정확히 절반도 통과)에 맞게
  「절반 이상이 어둡다」로. **코드는 그대로 뒀다** — 사람이 이미 승인한 12개 plate 가 다시 재질 수
  있어서다
- **`test_every_month_has_its_own_measured_plate`** — `!= APRIL_PLATE` 만 보던 것을
  `len({...}) == 12`(전부 서로 다름)로 강화. 돌려 보니 12달 plate 가 이미 전부 달라 그대로 통과했다
- **7·10·12월 `scene` 육안 대조** — 아무도 이미지와 비교한 적이 없었다(예산 부족, 11월 turkey/rooster
  전례). `7_beach`·`10_ghost`·`12_santa` 의 틀·완성 이미지를 열어 소품·의상 문장을 하나씩 대조 —
  세 달 모두 `scene`·`outfit` 이 이미지와 정확히 일치해 고칠 것이 없었다
- **4월 강아지 교체 실패 주석** — 모델 이름 없이 적혀 있어 운영 엔진(Nano Banana 2)의 결함으로 읽힐
  수 있었다. `FLUX.2-klein-4B` 를 명시하고 운영(Nano Banana 2)은 영향받지 않는다고 적었다

#### 최종 리뷰가 남기기로 한 것 (LEAVE)

머지를 막지 않는 minor. 위 FIX 항목과 겹치지 않는다.

- **T2 — 손측정 edge 여유** — `APRIL_PLATE`·`SEPTEMBER_PLATE` 의 손측정 edge 가 실제보다
  8~17px(4월)·6~8px(9월) 넉넉하다. 도구로 재측정하면 제목이 판 밖으로 번지는 것을 더 막지만,
  사람이 눈으로 승인한 상수라 재측정 여부는 별도 판단이 필요하다
- **T2 — settings 테스트 중복 단언** — `test_cardimage_settings.py` 의 12달 기본값 단언이 계획이
  시킨 중복이다
- **T3a — CHECKS 항목 순서** — 새 `CHECKS` 항목이 날짜 순서에서 벗어나 있다(기능과는 무관)
- **T4 — verify 스크립트의 컬럼·조건 확인 두 건** — `verify_..._pick_group.sql` 이 인덱스 정의는
  읽지만 그것이 `(pick_group)` 컬럼 위인지는 보지 않고, 인덱스 대상 컬럼과 AND/OR 조건도 보지 않는다
- **T4 — `choose_card` 가 커밋 전에 저장소 객체를 지운다** — `delete_card` 와 같은 기존 패턴이라
  이 카드가 새로 만든 문제는 아니다
- **T5 — 세마포어 대기 중 첫 카드 만료** — 빈 생성 슬롯을 기다리는 첫 카드가 정리 기준을 넘겨
  만료될 수 있다(돈은 안 나간다). 요청 하나가 이제 카드 최대 2장 동안 생성 슬롯(세마포어)을 쥐므로
  대기열이 그만큼 길어진다는 점도 함께 남긴다
- **T5 — `to_regclass` 의 `search_path`** — `search_path` 의 모든 스키마를 본다(옛
  `CREATE TABLE IF NOT EXISTS` 는 첫 스키마만 봤다). 운영은 스키마가 `public` 하나뿐이라 무관하다
- **T6 — 안내 문구가 `aria-describedby` 로 안 묶임** — `#cardimage-photo` 입력에 안내 `<p>` 가
  연결돼 있지 않아 스크린리더가 입력만 읽을 때는 안 들린다. 옛 문구도 같았으니 회귀는 아니다
- **T6 — 안내 문구 두 상수의 손 동기화** — 서버(`PHOTO_GUIDANCE`)와 콘솔 문구를 손으로 맞춰야
  한다. 새 엔드포인트 없이, 백엔드 테스트가 프런트 파일을 텍스트로 읽어 `PHOTO_GUIDANCE` 를
  그대로 포함하는지 단언하는 정도면 싸게 방어할 수 있다
- **T7 — `5feaeab7` 커밋의 트레일러** — "Claude Sonnet 5" 그대로 둔다. `HEAD` 가 아니라 고치려면
  리베이스가 필요하고, 실제로 Sonnet 에이전트가 쓴 커밋이라 트레일러가 사실과 맞다

### 2026-09-16 — Task 1~7 (12달 열기 + 2장 뽑기, 구현·리뷰 완료)

> Task 8(위)이 「한 요청에 2장」을 `FLUX.2-klein-4B` GPU 경로로 좁혔다 — 아래 「2장」·「요청당 엔진
> 호출 최대 `cardimage_pick_count`」 서술은 그 경로의 이야기다. Nano Banana 2 경로는 한 장 + 재시도다.

`docs/superpowers/plans/2026-09-16-ai-card-12months-and-two-picks.md` 를 subagent-driven-development 로
Task 1~7 을 순서대로 실행했다(구현자 → 리뷰어 → 수정 라운드, 원장은 SDD 폴더 `progress.md`). 목적은
#557 실험이 찾은 것을 제품에 박고 달을 12개로 넓히는 것 — GCP 크레딧 만료(11-17) 전에.

**Task 별 요약 (커밋은 `dev` 기준 병합 전, 브랜치 `feat/ai-card-multi-generate`):**

- Task 1 — 착수(roadmap 3번 행 · D-084 예약 줄) (`77aeeadf`, fix `08021477` — D-084 를 33줄 확정
  결정문으로 먼저 썼다가 예약 한 줄로 되돌림. 리뷰가 그 결정문에서 지어낸 식별자 6개도 잡음)
- Task 2 — 12달 무대·의상·제목판 (`62cb4c7b`, fix `50956efb`) — 10달의 `scene`·`outfit`·`Plate`
  를 채웠다. 11월 소품을 turkey 에서 rooster 로 잘못 쓴 것과, 4월 탐침 테스트가 다른 기준값과
  우연히 맞아떨어진 것을 fix 라운드가 잡았다. 새 10달의 `center_y` 는 계산값 98 이 아니라 사용자가
  두 번 눈으로 고른 99 로(4월과 같은 판 span). plate_probe 탐침 열을 4→18 로 늘린 것을 리뷰어가
  12달 전부 픽셀로 재현해 확인
- Task 3a — seed 구조(코드만, GPU 불필요) (`f46f1ba5`, fix `2b0a906a`) — `MonthCard.seeds` ·
  `pick_seeds` · `GeneratedCard.seed` · `AiCard.seed`(`Integer`, `SmallInteger` 아님 — seed 가
  32767 을 넘을 수 있다) · `db/migrations/2026-09-16_ai_card_seed.sql`. fix 라운드가 `generate_card`
  가 호출자의 명시 seed 를 조용히 버리던 것을 잡음 — 그대로 뒀으면 3b 의 유료 실험 데이터가 실제와
  다른 seed 로 기록될 뻔했다. 규칙: **명시 seed 는 그대로 쓰고 재시도 없음(1회) · 미지정이면
  `pick_seeds(month, 2, rng)` 로 서로 다른 두 seed 로 재시도**
- Task 3b — GPU 실험으로 seed 값 채우기 (`7c3f1774`, 시작 전 육안 재검증 포함) — 아래 「GPU 실험」 절
- Task 4 — 한 요청에 2장 순차 생성 + `/choose` (`5b1bce6a`, fix 1 `71fe5fc8`, fix 2 `222c835d`) —
  `pick_group` · `AiCard.pick_group` · `generate_cards`/`plan_seeds`. 초안은 둘째 카드 행을 첫째가
  끝난 뒤에야 만들어 취소·경쟁 구멍이 여럿 났다 → **요청 시점에 `count`개 행을 전부 `generating`
  으로 미리 만들고 한 장씩 채우는 방식**으로 재구성(fix 1). 그 과정에서 동시 생성 방어 인덱스
  (`idx_ai_cards_one_generating`)를 `(app_user_id)` 에서 `(app_user_id) WHERE id = pick_group`
  으로 좁혀야 했는데, 그 전환을 별도 마이그레이션 파일로 냈다가 **파일 이름 순서상 컬럼이 생기기
  전에 인덱스 마이그레이션이 먼저 돌아 배포가 깨지는 문제**를 재리뷰가 잡아 `2026-09-16_ai_card_
  pick_group.sql` 안 `BEGIN/COMMIT` 한 트랜잭션으로 합쳤다(fix 2). 실제 pgvector:pg17 하네스
  594건 통과
- Task 5 — 한도 재설계·정리 기준·GPU 예산 (`7c87a319`, fix `ba051c6c`) — D-084 확정. 아래
  「한도 변경」 절
- Task 6 — 사진 안내 문구 (`1198f793`) — `PHOTO_GUIDANCE` 상수, `/app/ai-cards` 응답과 콘솔에.
  브리프 초안 그대로 채택("얼굴이 정면으로 보이고 앉아 있는 사진이 가장 잘 나와요. 엎드려 있거나
  옆을 보는 사진은 닮지 않게 나올 수 있어요") — 근거 수치(정면 사진 72장 판정기 닮음 평균 4.11,
  `_08` 엎드린 옆모습 4·9월 4장 중 0장)가 이미 이 문장과 정확히 맞아 고칠 곳이 없었다
- Task 7 — 이 절 + roadmap · README · PR 본문 초안(`pr-body-final.md`) + 머지 전 게이트 전체
  (fix `5feaeab7` — 전체 `uv run pytest` 가 `test_closed_month_is_404` 를 잡았다. Task 2 가
  `DAENGS_CARDIMAGE_MONTHS` 기본값을 1~12월 전부로 넓히면서 "12월은 아직 안 열림"을 가정한 그
  테스트가 깨져 있었다 — 다른 테스트 파일과 같은 방식으로 `cardimage_months` 를 `{4,9}` 로
  좁혀 고쳤다)

#### GPU 실험 (Task 3b, `compare-2026-09-16-months-seeds.md`)

**조건:** 사진 `KakaoTalk_20260827_120826215_03.jpg`(정면) × 12달 × seed 1~6 = 72장, 크기 1024×1632.

**측정값(실측):** 장당 14.4~14.6초(평균 14.4초) · 콜드 스타트 460초 · 판정기 닮음 평균 4.11(5점
51장·1점 11장) · 판정기 임계값(3) 이상 72장 중 60장. **비용은 재지 않았다** — 장당 ₩43(추정,
E4 미실측·가중치 잡 단가를 옮겨 쓴 값)으로 72장이면 약 ₩3,100 **추정**이다.

**눈으로 검증한 최종 seed 목록** (`catalog.MonthCard.seeds`, 사람이 72칸을 직접 열어 본 결과가 근거 —
`compare-2026-09-16-months-seeds.md` 의 「검증된 seed 목록」과 같다):

| 월 | seed | 월 | seed |
| --- | --- | --- | --- |
| 1 새해 | (1,2,3,4,6) | 7 해변 | (1,2,3,4,6) |
| 2 사랑 | (1,6) | 8 장마 | (1,2,3,4,5,6) |
| 3 입학 | (1,3) | 9 한가위 | (1,2,3,4,5) |
| 4 벚꽃 | (2,3) | 10 유령 | (2,4,5,6) |
| 5 홈팀 | (1,3,4) | 11 추수감사 | (1,2,3,6) |
| 6 수영장 | (1,2,3,5,6) | 12 산타 | (1,3,5,6) |

**다음 세션이 다시 겪지 않아도 되도록 — 네 가지 발견:**

1. **자동 판정기(`text_ok`)는 사람 눈과 어긋난다 — 72칸 중 11칸, 양방향으로.** 판정기가 너무
   관대한 쪽 7칸(2월 seed 2·3·4·5 는 부제 `FEBRUARY SPECIAL` 배너 자체가 통째로 안 나오는데
   판정기는 통과시켰다 · 4월 seed 5 `petal`→`peial` · 6월 seed 4 `initializing`→`initial'zing` ·
   9월 seed 6 `surprise`→`surprie`), 너무 엄격한 쪽 4칸(5월 seed 1·4 · 7월 seed 2 · 12월 seed 1 —
   전부 실제로는 깨끗했다). **판정기만 믿었으면 깨진 seed 7개를 "검증됨"으로 잘못 커밋했을
   것이다** — 그래서 최종 seed 목록은 자동 판정이 아니라 사람이 격자를 직접 열어 본 결과를 썼다.
   앞으로 seed 목록을 다시 만들 일이 있으면 **격자를 눈으로 본다**를 건너뛰지 말 것.
2. **`FLUX.2-klein-4B` 에서 4월은 강아지가 안 바뀐다.** seed 와 무관하게 6개 전부에서 사용자
   사진의 강아지가 아니라 참조 카드 원본 주인공 「네오」(크림/살구색 곱슬 푸들)로 나온다. 텍스트가
   깨끗한 seed 2·3 도 마찬가지라, 4월은 텍스트 기준으로는 "검증 seed 2개"를 채우지만 강아지
   정체성 기준으로는 **쓸 수 있는 장이 0장**이다. `DAENGS_CARDGEN_URL` 을 켜기 전에 반드시 이
   결함부터 풀어야 한다 — 지금 운영은 여전히 Nano Banana 2 라 이 결함이 사용자에게 나가지 않는다.
3. **seed 목록은 글씨 깨짐을 줄이지만 보증하지 않는다** — 사진도 결과에 영향을 준다. #557 은 같은
   (틀, seed, 크기) 면 사진이 달라도 같은 자리가 깨진다고 결론냈는데, 이번 실험(다른 사진)에서는
   #557 의 4월 `{3,4}`·9월 `{1,4}` 가 재현되지 않았다 — 4월 seed 4 는 #557 사진에서는 깨끗했지만
   이번 사진에서는 부제 `APRIIAL`(SPECIAL 소실)로 깨졌다. 원인은 판정기 신뢰도 문제(위 1번)와
   사진 영향이 겹친 것으로 본다. 새 사진으로 seed 목록을 다시 쓸 일이 있으면 "예전에 검증됐다"를
   그대로 믿지 말 것.
4. **동시 생성 방어 인덱스 변경과 그것이 강제하는 배포 순서.** Task 4 가 `idx_ai_cards_one_
   generating` 의 조건을 `(app_user_id)` 에서 `(app_user_id) WHERE status='generating' AND
   id=pick_group` 으로 좁혔다 — 요청 하나가 `pick_group` 이 같은 행 여러 개를 동시에
   `generating` 으로 만들어야 하기 때문이다. 이 전환은 `pick_group` 컬럼을 추가하는 것과 **같은
   트랜잭션**(`2026-09-16_ai_card_pick_group.sql`)에 있어야 한다 — 한때 별도 파일로 냈다가 파일
   이름 순서(`g` < `p`)로 인덱스 마이그레이션이 컬럼 마이그레이션보다 먼저 돌아, DROP INDEX 는
   커밋되고 CREATE 는 없는 컬럼을 참조해 실패해서 **운영 DB 가 동시 생성 방어 인덱스 없이 남는**
   사고를 재리뷰가 잡았다. **배포는 반드시 DB 마이그레이션 전체 먼저, 코드는 그 다음** — 새 코드가
   옛 인덱스(컬럼 없음) 앞에서 돌면 모든 요청의 둘째 행이 걸려 POST 가 전부 `409
   already_generating` 이 된다.

#### 한도 변경 (Task 5, D-084)

D-077(하루 1회, 카드 장수 기준)이 "요청 하나에 카드 여러 장" 을 전제하지 않아 세 군데가 깨졌었다
— 닮음 미달 카드가 `ready` 라 한도에 안 걸려 나쁜 사진이 하루치를 공짜로 반복 소모하고, 유료 실패
상한이 카드 행을 세서 카드를 지우면 초기화되고(시작→삭제 반복이 무제한 유료 호출이 됨), 정리
기준이 둘째 카드의 대기 시간을 자기 예산에 넣어 멀쩡한 요청을 `interrupted` 로 죽였다.

정리한 규칙(전문은 `docs/decisions.md` D-084): 세는 단위는 카드 장수가 아니라 **요청(`pick_group`)
하나**. 요청의 첫 카드가 슬롯을 잡는 순간(`_claim_slot`, 유료 호출보다 먼저) `ai_card_usage.
unfulfilled_attempt = true` 한 줄을 지울 수 없게 남기고, 닮음 기준(`cardimage_judge_min`, 기본 3)
이상 카드가 나오면 그 표시를 지우고 사용 기록으로 바꾼다. 검수 점수가 없으면(장애) 기준 이상으로
본다. 하루 한도는 사용 기록만 세고, 돈 나간 시도 상한(`MAX_PAID_FAILURES_PER_DAY`=5)은 지워지지
않는 표시 수만 센다 — 그래서 KST 하루 = 좋은 뽑기 `DAENGS_CARDIMAGE_DAILY_LIMIT` 번 + 좋은 카드를
못 얻은 요청 최대 5번, 요청당 엔진 호출은 최대 `cardimage_pick_count`(2)번. 정리 기준(`stale_
after`)은 행이 아니라 요청의 가장 최근 `updated_at` 부터 재고, `cardgen_url` 이 켜져 있으면
`cardgen_timeout_s`(기본 900초, 콜드 스타트 포함)를 통째로 더한다. 새 컬럼(`ai_card_usage.
unfulfilled_attempt`)이라 `db/migrations/2026-09-15_ai_card_usage.sql` 의 옛 백필("ready 카드마다
자기 id 로 사용 기록 한 줄")이 거짓이 됐고, **그 파일을 재실행 안전하게 고쳤다** — `to_regclass`
가드로 표를 그 실행에서 처음 만들 때만 백필하고, 이미 적용된 DB(표가 있음)에서는 재실행이 아무것도
안 바꾼다.

**고치지 않고 남긴 것 (D-084 명시):** 요청의 첫 카드가 생성 슬롯(`cardimage_concurrency`)을 기다리는
동안 정리되면 돈은 안 나가지만 사용자는 요청을 잃는다.

### 남은 것 (다음 세션)

- **4월 강아지 정체성 결함** — `DAENGS_CARDGEN_URL` 을 켜기 전에 반드시 해결. 아직 원인 미분석
- **여러 사진으로 seed 목록 재검증** — 사진이 결과에 영향을 준다는 것이 확인됐다(발견 3). 지금
  목록은 사진 1장 스윕으로만 확정
- **앱 고르기 화면** — `SAJOYO/DAENGS_APP` 과 계약 조율, 2장을 보여 주고 고르는 UI. **`DAENGS_CARDGEN_URL`
  을 넣는 것과 함께 나가야 한다**(Task 8) — 그 값이 들어가는 순간 한 요청이 두 장이 된다
- **E4 비용 실측** — 여전히 미측정. 장당 ₩43 은 가중치 잡 단가를 옮겨 쓴 추정
- **과일·채소 카드** (로드맵 4번) — 아래 참고
- `DAENGS_CARDGEN_URL` 운영 반영 여부 — 4월 결함이 풀린 뒤 별도 판단

## 2026-09-16 밤 — #557 FLUX.2-klein-4B 실험: E1 · E2 · 이미지 하나로 · E3 (무인 진행)

> **아침에 볼 것 (사람)**
> 1. 결과 문서 두 개: `compare-2026-09-16-klein-e1.md`(글씨) · `compare-2026-09-16-klein-e2-e3.md`(4장 뽑기·콜드 스타트). 판정은 전부 **잠정** — 격자를 직접 확인해 주세요:
>    `cardimage/out/_cardgen/e1_panels_4.png` · `e1_panels_9.png`(글씨 크롭) · `e1_grid_4.png` · `e1_grid_9.png` · `e2-seq_panel_4.png` · `e2-seq_panel_9.png` · `e2-seq_card_4.png` · `e2-seq_card_9.png`.
> 2. **정할 것:** ① 3번 카드에서 "달 틀마다 검증된 seed 목록에서 4장"으로 갈지(무작위 seed 대신) ② L4 에서 한 번에 여러 장이 안 되니 순차 4장(요청당 GPU 약 1분)을 받아들일지 ③ E3 파일 캐시(in-memory) 설정을 한 번 더 확인해 볼지 ④ E4(비용 실측)는 여전히 보류인지.
> 3. GCP 는 서비스 `daengs-cardgen-klein`(리비전 `00005-rhn` 하나, 기본 마운트) · 잡 `cardgen-weights` · 이미지 `07e7a55` 하나만 남았다. 인스턴스는 요청이 없으면 스스로 내려간다.

- **사용자 지시(09-16 밤):** E1 · E2 · E3 · 이미지 하나로 맞추기를 자는 동안 쭉 진행. 비용은 크레딧이라 매번 승인 대신 상한(L4 누적 3시간, 같은 원인 두 번 실패면 중단). E4 는 당장 못 함(보류), E5 는 안 하고 사진 안내 문구로.
- **작업 방식:** 계획 `plan-2026-09-16-klein-e1.md`(Task 1~12)를 superpowers subagent-driven-development 로. 코드 태스크 6개(엔진 생성 크기 · 비교 도구 조건 · 격자 도구 · Qwen 제거 · 서비스 `count` · 클라이언트 여러 장)는 Sonnet 구현자 + 태스크 리뷰, 전부 승인(지적은 Minor 뿐). 유료 실행·배포·삭제·기록은 컨트롤러.
- **E1 (02:05~02:14, 18장):** 문구 명시는 더 깨짐, 1280×2048 은 깨지는 자리를 옮김(40% 느림). **같은 틀·seed·크기면 사진이 달라도 같은 자리가 깨진다.** 서비스 로드 403.1초(옛 이미지).
- **이미지 (02:05~02:36):** Qwen 제거 + `count` 넣은 `07e7a55` 빌드 14분 6초 → 서비스 리비전 `00002-dwn`(배포 4분, 배포만으로 인스턴스가 떠서 로드 374.9초) · 잡은 이미지만 교체(실행 안 함) · 스모크 1장이 09-15 카드와 제목판(이름 `MOMO`/`테스트` 로 다름)을 뺀 나머지 **픽셀 차이 0** · 옛 이미지 `c917c96`·`90a42ef`·옛 리비전 삭제.
- **E2 (02:31~02:41):** 순차 seed 1~4 × 사진 3 × 4·9월 = 24장, 장당 16.2~16.4초. 한 번에 4장·2장은 **CUDA OOM**(두 번 같은 원인 → 중단). 글씨는 seed 로 갈림(seed 4 는 두 달 다 제목·아래 패널 깨끗), 쓸 만한 장 정면 1~2 · 엎드린 옆모습 0.
- **E3 (02:42~02:57):** `enable-buffered-read=true` 로드 735.6초(2배 느림). in-memory 파일 캐시는 컨테이너가 마운트 단계에서 못 떠 실패(원인 미확정, 추측 재배포 안 함) → 기본 설정으로 되돌림(`00005-rhn`), V1·V2·옛 리비전 삭제.
- **기록:** `compare-2026-09-16-klein-e1.md` · `compare-2026-09-16-klein-e2-e3.md` · `roadmap.md`(표·체크리스트·E1~E3 결과) · `infra/gcp/README.md`(이미지 하나, 배포만 해도 인스턴스가 뜬다, E3 실측).

## 2026-09-16 — #544 마무리: Task 8 비교 · 둘 다 유지 결정 · 로드맵

- **Task 8** (FLUX.2-klein-4B vs Nano Banana 2, 사진 3 × 4·9월 × seed 2 = 엔진당 12장): 결과는 `compare-2026-09-15-cardgen.md`. Nano Banana 2 닮음 4.42 · 결함 0, FLUX.2-klein-4B 닮음 3.17(정면만 5) · 목줄 2/6 · 문구 깨짐 3(전부 4월·seed 1) · 장당 18초. FLUX.2-klein-4B 12장 배치 과금 구간 22분 26초(깨움 14:18:53Z → 종료 14:41:19Z) → 장당 약 ₩43 추정.
- **검수 함정**: 제목에 한글 이름("테스트")이 들어가면 검수가 깨진 글자로 판정한다(Nano Banana 2 1건). 실험은 영문 이름으로.
- **사용자 결정**: Nano Banana 2 · FLUX.2-klein-4B **둘 다 유지.** FLUX.2-klein-4B 은 "4장 뽑아 고르기"·과일·채소 개인화 경로 후보. 하루 1회 한도는 Nano Banana 2 장당 과금 때문이었으니 FLUX.2-klein-4B 경로에선 다시 설계. seed 는 운영에서 장마다 다르게 + 저장, 실험은 고정. 운영 경로(11-17 이후) 결정은 지금 중요하지 않다고 봄 — 보류.
- **정리**: D-078 기록, `roadmap.md`(2번 실험 카드 자세히 · 3·4번 간단히), `CLAUDE.md` 폴더 표, `infra/gcp/README.md` cardgen 절(실측·함정 표). FLUX.2-klein-4B 서비스·가중치·이미지는 남김. 가중치 잡은 `90a42ef` + `python -m daengs_cardgen.fetch klein-4b` 로 되돌림(실행 안 함).

## 2026-09-15 밤 — #544 GPU 서비스 Task 7: Qwen-Image-Edit-2511 nf4 on L4 (잰 값만)

- **가중치**: 잡 `cardgen-weights-7czsf` 성공 31분 3초(다운로드 30분 43초), 33파일 57.72GB(최대 9.99GB transformer 샤드), incomplete 0, 심볼릭 링크 33개 정상.
- **1차 서비스** (이미지 `c917c96`, rev `00001-6dz`): Ready 25초, 로드 **1145.8초**(재시작·메모리 초과 없음). 카드 1장 → 확산 40/40 11분 19초(17s/step) 뒤 **CUDA OOM** — `autoencoder_kl_qwenimage` 정규화에서 612MiB 요청, GPU 22.03GiB 중 25MiB 남음, PyTorch 19.80GiB 할당·1.97GiB 예약만 됨. (처음엔 proxy 가 끊은 500 으로 추정했으나 서비스 Traceback 으로 반증.)
- **수정**: `QwenModel.load` 에 `vae.enable_tiling()`(diffusers 0.40.0 에 있음을 임시 env 로 먼저 확인), Dockerfile `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, 테스트 1개(`96b5e6bf`). 이미지 재빌드 12분 54초 → `90a42ef`.
- **2차 서비스** (rev `00002-zf2`): 새 이미지 첫 import 로 Ready 296초, 로드 **1237.8초**. 카드 1장 → 확산 11분 34초, 디코딩 ~4초, **200 OK (OOM 해결)**, 서비스 711.6초.
  결과가 **깨짐**: 검수 닮음 2 · 글자 실패. 눈으로 보면 **틀의 갈색 푸들 그대로 + 화면 전체 고른 노이즈** — 강아지·아바타 교체가 일어나지 않았다(FLUX.2-klein-4B 은 교체됨). `cardimage/out/_cardgen/smoke-qwen/`.
  원인은 가르지 못함(추정): text_encoder nf4 로 참조 이해 붕괴 / transformer nf4 로 디노이즈 붕괴 / VAE tiling. L4 에선 양자화를 빼면 메모리가 모자라 이 자리에서 싸게 가를 방법이 없다.
- **판정**: L4 + nf4 로는 Qwen 비교 표본을 못 얻었다. 서빙 관점에서도 로드 ~20분·장당 ~12분이라 어렵다.
- **비교 이미지**: `cardimage/out/_cardgen/compare_4_template_nanobanana_klein_qwen.png` (틀 · Nano Banana 2 09-14 `service_check_1.png` · FLUX.2-klein-4B · Qwen, 같은 사진 `_03`·4월). 눈으로: Nano Banana 2 는 교체·무대·글자 모두 깨끗, FLUX.2-klein-4B 은 교체·무대는 근접하나 목줄 추가·`PETL PPAUSE`, Qwen 은 교체 없음 + 노이즈.
- **정리 (사용자 결정 "둘 다 지우자")**: 서비스 `daengs-cardgen-qwen` 삭제, 버킷 `hub/models--Qwen--Qwen-Image-Edit-2511/`(53.75GiB)·`hub/.locks/models--Qwen--…` 삭제, 로컬 proxy 8092 종료. FLUX.2-klein-4B 서비스·가중치(14.88GiB)는 남김. **Task 8 은 FLUX.2-klein-4B vs Nano Banana 2 만.**

## 2026-09-15 오후 — #544 GPU 서비스 Task 6: FLUX.2-klein-4B 배포·첫 카드 (잰 값만)

계획 `plan-2026-09-15-cardgen-gpu.md` Task 1~5(코드) 뒤 첫 유료 단계. 사용자 승인 뒤 진행.

- **이미지**: Cloud Build `32a629a6`, 15분 32초, `asia-northeast3-docker.pkg.dev/daengs/daengs/cardgen:c917c96`. 빌드 안 확인 `torch 2.13.0+cu126 · cuda 12.6 · torchvision 0.28.0+cu126`.
- **가중치 잡 `cardgen-weights`** (asia-southeast1) — 세 번째에 성공. 시도별 원인:
  | 시도 | 결과 | 확인된 원인 | 바꾼 것 |
  | --- | --- | --- | --- |
  | 1 (python -m fetch, 4CPU/16Gi) | 7분 뒤 실패 | 메모리 한도 도달 — 큰 blob 3개(1.41·4.97·6.17GB)가 `.incomplete` 로 동시에 | 한 파일씩, xet 끔, 8CPU/32Gi |
  | 2 (`hf download --include a b`) | 37초 실패 | `--include` 는 값 하나 — `*/*` 가 파일명으로 해석 | `--include` 두 번 |
  | (거부) | 실행 안 됨 | gcloud `--args` 가 목록 안 `--include` 중복 거부 | `/bin/sh -c` 한 줄 + `set -f` |
  | 3 | **성공 8분 59초** (다운로드 8분 38초, ~29MB/s) | — | — |
  버킷 blobs 14.88GiB(transformer 7.75GB · text_encoder 4.97+3.08GB · vae 168MB). snapshots/ 링크는 gcsfuse 심볼릭 링크(`gcsfuse_symlink_target`)로 저장됨.
  ⚠ 지금 잡의 command 는 `/bin/sh -c "set -f; exec /opt/venv/bin/hf download ..."` 로 덮어써진 상태 — 저장소의 `fetch.py`(max_workers=1)·`cardgen.sh`(8CPU/32Gi, `HF_HUB_DISABLE_XET=1`)는 같은 조건으로 고쳤지만 이미지는 다시 안 구웠다.
- **Git Bash·PowerShell 함정 (전부 실측)**: ① `--set-env-vars=HF_XET_CACHE=/tmp/xet` 이 Git Bash 경로 변환으로 `C:/Users/.../Temp/xet` 저장 → `MSYS2_ARG_CONV_EXCL` 에 `--set-env-vars` 추가 ② PowerShell 은 따옴표 없는 인자의 쉼표를 배열로 쪼갬 → `--add-volume`·`--add-volume-mount`·`--set-env-vars` 는 따옴표 ③ `cardgen.sh STEP=deploy` 는 파일 해시로 태그를 다시 계산해, 빌드 뒤 파일을 고치면 없는 태그를 배포 → 이번엔 태그를 고정해 직접 배포 ④ `gcloud run services proxy` 는 `cloud-run-proxy` 컴포넌트가 필요하고, SDK 가 Program Files 라 관리자 권한으로 설치(사용자).
- **서비스 `daengs-cardgen-klein`** (L4, min 0·max 1): 배포 수락 10:06:35Z → 인스턴스 시작 10:06:41Z → 포트 열림·기본 TCP 프로브 통과 10:06:45Z → Ready 10:07:03Z(이미지 가져오기 수 초). 백그라운드 로드 **`load_seconds` 430.3**(ready 10:14:05Z) — 대부분 버킷에서 ~15GB 읽기. 오프라인 캐시 로드·읽기 전용 마운트 문제 없음.
- **카드 1장** (`KakaoTalk_20260913_220514335_03.jpg`, 4월, seed 1): 서비스 생성 26.2초(1024×1632, 4 step), 검수 포함 33.4초. 검수 닮음 5 · 아바타 OK · 글자 실패. 틀 밀림 dx 6·dy -2, 제목판 -1px. `cardimage/out/_cardgen/smoke-klein/`.
  눈 확인(틀과 나란히): 강·다리·빌딩·바구니·매트·테두리·문구는 틀 그대로. **결함 ① 목줄이 생김**(프롬프트 금지, 검수가 못 잡음) **② `PETAL PAUSE` → `PETL PPAUSE`**. 한 장이라 경향인지는 Task 8 에서.
- **요청 뒤 인스턴스가 내려간 시각**: 마지막 요청 10:14:54Z → `Shutting down` 10:24:59Z, **유휴 약 10분 5초**. 인스턴스 수명 10:06:41Z~10:24:59Z **약 18분 20초**(GPU 과금 구간).
  → 요청이 드물면 카드 1장에 콜드 스타트 7분 + 생성 26초 + 유휴 10분 ≈ 18분이 붙는다. 가중치 잡에서 잰 L4(8CPU/32Gi) 단가 시간당 약 ₩1,400 을 서비스에 그대로 적용하면 **장당 약 ₩420 (추정 — 서비스 단가 미확인)** 으로 Nano Banana 2 2K($0.10) 보다 비쌀 수 있다. 떠 있는 동안 몰아 만들면 장당 비용은 크게 준다.

## 2026-09-15 — #543 한도 제품 규칙

- 계기: 앱(DAENGS_APP#414) 실기기에서 9월 카드를 만들고 지운 뒤 4월 카드가 또 만들어짐 — 한도가 남아 있는 ready 행을 셈.
- 사용자 결정: 하루 1회(지워도 안 돌아옴·실패 안 셈) · 강아지마다 달마다 한 장(보호자마다 따로) · 제목에만 쓰는 이름 · 목록의 남은 횟수. D-077.
- 구현: 표 `ai_card_usage`(카드 ready 때 한 줄, card_id FK 없음, 기존 ready 백필) · `check_quota` 교체(`AiCardMonthTakenError`) · `title_name` · `daily_limit`/`daily_remaining` · 탈퇴 정리. 계획 `plan-2026-09-15-ai-card-quota-rules.md`.
- 확인한 것: `ai_cards.month` 는 연도가 없어 달별 한 장은 테마 달 기준 — 그 달 카드를 지우면 그 달은 다시 열린다.
- 배포: `db/migrations/2026-09-15_ai_card_usage.sql` 을 코드보다 먼저 개발서버·GCP 에 적용.

## 2026-09-14 저녁 — 앱 사용자 경로 (#537)

설계 대화에서 정한 다섯 가지: ① 생성 로직만 `daengs_cardimage` 로 분리(나중에 Cloud Run 으로 뗄 부분을 한
덩어리로) ② 앱 계약 비동기 ③ backend 프로세스 안 백그라운드 ④ 994×1582 그대로 ⑤ 하루 1장(테스트 단계). 전부
D-076. 코드를 보다가 고친 것: 정리 기준을 5분에서 `4 × timeout + 60초`(9분)로 — 엔진·검수 120초에 재시도까지
최악 8분이라 5분이면 정상 작업을 실패로 덮는다. `StoragePort` 에 서버 쓰기 메서드가 없어 local·GCS 를 갈라 쓴다.
구현은 subagent-driven-development 로 Task 1~6. 리뷰에서 고친 것: 정리 기준의 시각을 `created_at` 이 아니라
`updated_at` 으로 — 생성 차례를 기다리던 행이 그사이 만료돼도 유료 호출이 그대로 나가던 것을 막으려고, 차례를
얻으면 행을 다시 읽고 `updated_at` 을 찍은 뒤 그 시각부터 잰다. `ai_cards` verify 는 처음에 부분 인덱스의
`WHERE` 조건이 `'generating'` 문자열이 아니라 인덱스 이름에 속아 통과했는데, `'generating'::text` 로 고치고
나서야 버리는 Postgres 하네스가 569건을 통과했다. 최종 리뷰에서 고친 것: 업로드 중 사용자 행 잠금 제거(POST 를
토큰만 확인으로 받고 사진을 다 받은 뒤 서비스가 잠근다), 실패한 유료 호출(upstream·no_image·storage) 하루 5번 상한.

## 2026-09-14 오후 — 콘솔 확인 뒤 다듬기 · 9월 추가

사용자가 콘솔(`uv run dev` + `npm run dev`)에서 4월 카드를 직접 만들어 "잘 나온다"고 확인했다. 그 자리에서 세 가지를 더 했다.

- **PNG 저장** 버튼 (`75d086c1`) — 서버에 저장하지 않는 경로라 `data:` URL 을 `download` 로. 파일명 `BLOSSOM_네오.png`.
- **제목 x=252** (`86090cb4`) — 원본 268 이 오른쪽으로 치우쳐 보여 244/252/260 세 장을 만들어(`cardimage/out/_title_test/_left_x_variants.png`) 사용자가 252 를 골랐다.
- **9월 카드** — "9월까지만 하나 더". 4월과 다른 점이 셋이라 `catalog.MonthCard` 에 필드를 뺐다:
  - `subtitle` — 달별 부제(`SEPTEMBER SPECIAL`)를 catalog 로 옮기고 `generate.SUBTITLES` 표는 지웠다. 부제가 빈 달은 `CardImageUnavailable`.
  - `outfit` — 4월 문장 "아무것도 안 입는다, 목줄·하네스·옷 금지"가 9월 틀(한복 + 송편 쟁반)과 충돌한다. 9월은 "이미지 1 의 개와 **같은 한복**을 입고 같은 쟁반을 든다, 이미지 2 의 목줄·하네스는 금지". `build_prompt(..., outfit=)` 로 문장 통째 주입.
  - `plate_edge` — 9월 제목판 오른쪽 경계를 픽셀로 쟀다: y65→750, y100→731, y135→709. 4월(791→758)보다 약 40px 좁다. `title.draw_title(..., edge=)`.
  - → **세로 중심도 달랐다.** 사용자가 콘솔에서 9월 카드를 보고 "제목 위치가 또 틀리다"고 지적. 틀을 다시 재니 9월 판은 y 40~135(중심 88), 4월은 53~145(중심 99) — 11px 차이인데 중심 y 가 공용 상수였다. `title.Plate(center_y, edge, left_x=252)` 로 묶어 `MonthCard.plate` 로 달마다 갖게 했다(`APRIL_PLATE` · `SEPTEMBER_PLATE`). 왼쪽 끝은 두 틀이 같아(226·228) x=252 공용. `tools/cardimage_title.py --month 9` 로 미리 볼 수 있다. 확인 그림 `cardimage/out/_title_test/sep/_before_after.png`.
  - → 그래도 "좀 아래"(사용자, 실제 생성 카드). 실제 출력을 재니 **모델이 그린 판이 틀보다 4~5px 위**다(9월 출력 윗선 36 vs 틀 40, 4월 47 vs 52 — `service_check_*.png` 실측). 틀 상수만으로는 안 맞아서 `draw_title` 이 **출력에서 판 윗선을 다시 재어**(글자 없는 열 x=234~246, 어두운 픽셀 15줄 연속) 그 차이만큼 중심을 옮긴다(`_plate_shift`, ±20px 안에서만, 못 찾으면 0). `Plate.top_y`(4월 52 · 9월 40) 추가. 대문자 띠 중심과 문자열 전체 잉크 중심은 한글 이름이 붙어도 1px 차이라 그대로 둔다.
  - 같은 자리에서 걸린 것 둘: ① **콘솔 500 = Next 개발 프록시의 30초 기본 타임아웃** — backend 는 200 을 냈는데 브라우저는 `socket hang up`. `next.config.ts` 에 `experimental.proxyTimeout: 300_000`(nginx 와 같은 값). 배포에는 영향 없음(nginx 가 먼저 받는다). ② `uv run dev` 를 죽일 때 **reloader 만 죽고 uvicorn 워커(multiprocessing 자식)가 살아남아 옛 코드로 8000 을 계속 받았다** — 사용자가 본 "여전히 아래"의 일부는 옛 프로세스가 답한 것일 수 있다. 다시 띄울 때는 `daengs_backend|multiprocessing` 로 python 프로세스를 찾아 자식까지 죽일 것.
  - 카드명은 `HARVEST MOON` 이 판에 안 들어가 축소돼 사용자가 **`CHUSEOK`** 으로 바꿨다. 배지 `26SEP` 는 틀에 이미 구워져 있다.
  - `cardimage_months` 기본값 `{4, 9}`, `.env.example` 주석도. 콘솔 탭에 `<select>`(4월 · BLOSSOM / 9월 · CHUSEOK), 버튼 글자가 고른 달을 따라간다.
  - 실호출 1회: `_03` 사진 → `CHUSEOK 네오`, 유사도 5/5, `text_ok`·`avatar_ok` 통과, 29.8초, 한복·쟁반 유지. `cardimage/out/_service_check/service_check_sep_1.png`.
  - 테스트 67개 통과(달별 outfit 문장·edge·9월 틀 전송 검증 추가; "닫힌 달 404" 테스트는 12월로 바꿈), ruff · `uv run check` · `npm run lint` · `tsc` 통과.

- **사진 상한 8 → 20 MiB** — 사용자가 콘솔에서 9월을 시도하자 413 이 났다(로그). 상한이 프로필 사진 값(8 MiB)이었는데 `cardimage/test/` 의 실제 폰 사진 13장 중 3장이 9.7~10.2MB 다 — "테스트 사진은 사용자가 보통 넣는 상황"이 전제였으니 상한을 다른 nginx 블록과 같은 20 MiB 로(`photo.MAX_PHOTO_BYTES`, nginx `client_max_body_size 20m`). 픽셀 상한 6000만은 그대로라 디코드 폭탄 방어는 유지.

**다음 달을 열 때 하는 일(체크리스트):** ① `catalog.py` 에 `scene`·`subtitle`, 틀 강아지가 뭔가 입었으면 `outfit`, ② 제목판을 재서 `title.Plate` — 세로 범위(중심 y)와 오른쪽 경계 세 점, 왼쪽 끝이 226 근처인지(`backend/tools/cardimage_title.py --month N` 으로 미리보기), ③ 실호출 1장, ④ `cardimage_months` 기본값과 콘솔 `MONTHS` 표, ⑤ 카드명이 길면 사용자와 짧은 이름 상의.

## 2026-09-14 — 1단계 구현 (에이전트 실행, Task 1~9·11)

`docs/cardimage/plan-2026-09-14-phase1.md` 를 승인받아 subagent-driven-development 로
Task 1~9·11 을 순서대로 실행했다 (Task 10 제외 — 사용자 결정 09-14). 매 Task 마다 구현자→
리뷰어→수정 라운드를 돌렸고 판정·근거는 `progress.md`(ledger)에 있다.

**Task 별 요약:**

- Task 1 — 도감 카드 설정값(`DAENGS_CARDIMAGE_*`)과 틀 폴더 컨테이너 마운트 (`f72995a`, fix `971c9a8`)
- Task 2 — 달마다 틀 파일·카드명·무대 묘사를 한 곳에 모은 catalog, 빈 무대는 허용 목록에 있어도 거부 (`ab3f859`, fix `123cd25`)
- Task 3 — 업로드 사진 검증·앱과 같은 크기로 리사이즈 (`9fd96c0`, fix `b9eb9c9`)
- Task 4 — 제목 얹기를 실험 도구에서 서비스로 이관 (`4d834ed`, 리뷰 클린)
- Task 5 — 강아지 교체 엔진을 Protocol 뒤에 두고 Nano Banana 2 구현 이관 (`73de7bc`, fix `4acb8df`)
- Task 6 — 생성된 카드가 그 강아지인지 묻는 검수 (`adfa930`, fix `b3ff6d6`)
- Task 7 — 생성 파이프라인 — 유사도가 모자라면 한 번 더 만든다 (`28cef2e`, fix `0c8c1d9`)
- Task 8 — 관리자 API `POST /admin/cardimage/generate` (`e08d5ee`, fix `6a40070`)
- Task 9 — 콘솔 「기능 / 검색 점검」에 「도감 카드 생성」 갈래 (`130a4ea`, 리뷰 클린)
- Task 11 — 이 절 + `docs/decisions.md` D-074 + `docs/console/roadmap.md` · `CLAUDE.md` 갱신 (이 커밋)

**구현 중 내려진 판단(ledger 의 Ruling):**

- **Pillow 는 기본 의존성으로 승격** (Task 3) — 계획이 전제한 것과 달리 Pillow 가 `screening`
  그룹에만 있었는데, `services/cardimage` 가 backend 본체(main.py 라우터)에 들어가 import
  시점에 필요해졌다. `uv add pillow` 로 올렸다 — `uv.lock` 이 바뀌어 배포 때 backend 재생성이
  필요하다(어차피 필요한 재생성).
- **`cardimage_dir` 기본값을 절대 경로로** (Task 8) — `"cardimage"` 상대 경로는 CWD 에 따라
  갈려서 `backend/` 에서 `uv run dev` 하면 틀을 못 찾았다(Task 1 결함). `Path(config.py).parents[3]
  /"cardimage"` 로 바꿔 개발 PC 는 저장소 루트, 컨테이너(`/app/src/…`)는 `/cardimage` 가 되어
  compose 마운트 경로와 일치시켰다.
- **압축 폭탄·투명 배경 처리** (Task 3) — 선언 크기 상한을 검사하지 않으면
  `DecompressionBombError` 가 그대로 새 나가고, 알파 채널이 있는 사진은 검정으로 뭉개졌다.
  선언 크기 상한 검사와 흰 배경 합성을 둘 다 넣었다.
- **검수 응답은 엄격 타입** (Task 6) — `bool()`/`int()` 로 느슨하게 강제하면 검수가
  검수로서 의미가 없어진다. JSON 원래 타입일 때만 받고 아니면 `JudgeError`.
- **엔진 응답 파싱 방어** (Task 5) — 모델이 빈 응답이나 안전 차단 응답을 주면 `EngineError`
  밖의 예외가 새 나갔다. 응답 파싱을 감싸 전부 `EngineError` 로 나가게 했다.
- **스트리밍 413** (Task 8) — MIME 대소문자를 안 가리던 것과, 본문을 통째로 버퍼링한 뒤
  크기를 검사하던 것(gait 의 스트리밍 상한 패턴과 다름) 둘 다 고쳤다 — `.lower()` 비교 +
  스트리밍 도중 상한 검사.
- **Task 10 은 실행하지 않음** — 사용자 결정 09-14. 앱에 카드를 어떻게 얹을지(표시 계약)는
  이 카드에서 안 연다.
- **콘솔 브라우저 확인은 사용자에게 넘김** (Task 9 뒤) — 관리자 로그인 정보가 로컬에 없다.
  HTTP 계층은 `test_cardimage_admin_api`(8 tests)로, 파이프라인은 아래 실호출로 확인했다.

**최종 브랜치 리뷰(전체 diff, 가장 높은 모델)와 수정 묶음 (`3e93f4d`):** Critical 0. Important 둘을 고쳤다 — ① **nginx `/api/` 블록의 `proxy_read_timeout 60s`** 가 20~60초짜리 생성(재시도면 두 배)을 504 로 끊고 돈은 그대로 나가는 문제 → `location /api/admin/cardimage/` 블록을 따로 두어 300s (계획의 빈틈 — 배포 영향에 nginx 가 없었다) ② 모델이 준 바이트를 이미지로 여는 두 줄이 오류 매핑 밖이라 손상 출력이 500 으로 새던 것 → `EngineError("no_image")`. 같은 묶음에 공백 이름 400(`bad_name`)과 죽은 상수 `TITLE_PLATE` 삭제. 전체 `uv run pytest`: 5942 passed · 526 skipped · 1 failed(territory 사진 저장소 동시 쓰기 테스트 — 이 브랜치 미변경 영역, 단독 3/3 통과, 부하 flaky). 미룬 것: 손상된 틀·글꼴 → 500, `bad_length` 테스트, 글꼴 재로드, `to_thread` 기본 executor(앱에 열 때 세마포어), `daengback.~` `location /` 60s(Task 10 때).

**실호출 확인 1회:** 사진 `_03`(정면, 4월, 이름 "네오") → 유사도 검수 5/5, `text_ok`·`avatar_ok`
통과, `attempts` 1, 28.8초, PNG 2.5MB. `cardimage/out/_service_check/service_check_1.png`.
비용 약 $0.10(생성) + 검수. Next 개발 서버는 `next.config.ts` 의 rewrites 로 `/api/:path*` 를
backend 로 넘긴다.

## 2026-09-13 밤 — 실험 1번 (Nano Banana 2, 원본 사진, art 모드, 1K)

`cardimage/out/0913_234757_gemini-3.1-flash-image_art_1K_1.png` (합성) · `_raw.png` (모델 출력 896×1200). 첫 호출, 약 $0.07.

- **닮음: 좋다.** 흰 장모·귀 끝 갈색·한쪽 선 귀·붉은 목줄까지 테스트 강아지다. 포즈(앉아서 꽃잎 올려다봄)·나무·바구니·다리·강·꽃잎 유지.
- **틀리는 것:** 피크닉 매트가 초록·흰 체크 → 빨강·초록 타탄으로 바뀌었다. 프롬프트에 매트 색을 명시할 것.
- **뜻밖의 것:** 그림 상자에 머리띠가 포함돼 있어서 모델이 제목·배지·**아바타 원까지 스스로 그렸고**, 아바타에 강아지 얼굴을 잘 넣었다. 글자도 안 깨졌다(`NEO-APR25` 는 오른쪽이 살짝 잘림). 내가 사진에서 오려 붙인 아바타(몸통이 잡힘)보다 낫다 → 아바타는 모델 출력에서 원을 잘라 쓰는 쪽으로 바꿀 것 (추가 호출 없음).
- **이음새(y≈235):** 눈에 안 띈다.
- 판정: Nano Banana 2 는 **「됨」쪽.** 남은 확인은 일관성(같은 조건 반복)·full 모드·다른 사진.

### 실험 2번 — 같은 사진을 1600px 로 줄여서 (프롬프트 동일)

`0913_235228_…_art_1K_1.png`. 보낸 사진 1200×1600 (1번은 3000×4000). 약 $0.07.

- **닮음 차이 없음.** 흰 장모·귀 끝 갈색·선 귀 그대로. 아바타도 모델이 잘 그림.
- 이번엔 매트가 원본대로 초록·흰 체크로 유지됐고, 대신 1번에 있던 **붉은 목줄이 빠졌다.** 둘 다 입력 해상도가 아니라 **샘플링 편차**로 보인다 — 같은 조건 반복(3번)에서 확인.
- 결론: **서비스는 앱이 이미 하듯 1600px 로 줄여 보내도 된다.** 전송만 4~6MB → 0.5MB 로 준다.

### 실험 3번 — 같은 조건 3장, 프롬프트에 무대 고정 (1600px, 1K)

`0913_235809_…_art_1K_{1,2,3}.png`. 약 $0.2. 프롬프트에 두 줄 추가: 매트는 "green-and-white checked (keep this exact color and pattern)", 사진의 목줄·리드줄·하네스·옷은 **넣지 말 것**.
사용자 결정(09-13): 무대는 잠그고 변주는 강아지 쪽에만. "매번 다르게 나오는 게 개인화의 매력" 논의 → 강아지 렌더링 편차는 살리되 매트·소품 같은 무대가 흔들리는 건 카드 정체성 훼손이라 고정.

- **무대 3/3 고정 성공.** 매트 초록·흰 체크, 목줄 없음, 바구니·다리·강·꽃잎 유지.
- **닮음 3/3.** 흰 장모·귀 끝 갈색·선 귀. 1번은 입 벌린 표정(사진처럼), 2·3번은 다문 표정 — 이런 편차는 괜찮은 쪽.
- **아바타 원은 3번에서 실패** — 원본 크림 푸들이 그대로 남았다. 지금까지 5장 중 4장 성공. 모델에게 맡기면 5장에 1장꼴로 틀린 강아지가 아바타에 남는다 → **아바타는 모델에 안 맡긴다.** 후보: ⓐ 그림 상자를 머리띠 아래(y≥235)로 줄여 모델이 아바타를 그릴 일을 없애고, 아바타는 별도 0.5K 호출($0.045)로 "이 강아지 얼굴 초상" 하나 더 받기 ⓑ 사진에서 얼굴 검출로 오리기(AI 없음, 정확도는 검출기에 달림). 1단계 설계에서 정한다.
- 글자는 3/3 무사.

### 실험 4번 — full 모드(카드 통째) 2장 (1600px, 1K) — 2026-09-14 00:01

`0914_000144_…_full_1K_{1,2}.png`. 약 $0.14. 카드(994×1582)를 좌우 검은 띠로 2:3(1055×1582)으로 만들어 보내고 결과에서 띠를 잘라냈다.

- **글자 2/2 전부 무사** — 제목·배지·`NEO-APR25`·`PETAL PAUSE`·`SPRING 920`·별·한 줄 설명·홀로그램 테두리까지. "통째로 보내면 글자가 깨진다"는 걱정은 이 카드에서는 **안 맞았다.**
- 아바타 2/2 성공. 매트 2/2 유지. 닮음 2/2.
- **목줄이 2/2 들어갔다(빨강·분홍).** 원인은 모델이 아니라 **내 실수** — full 프롬프트에 "소품 넣지 말 것"·매트 고정 두 줄을 안 넣었었다(art 프롬프트만 고쳤다). 지금 넣었다. 다음 full 실행에서 다시 본다.
- 해상도: 1K 출력이 848×1264 라 카드 크기로 **1.24배 확대**된다. art 모드는 896×1200 → 886×1139 로 오히려 살짝 줄여 넣어 더 선명하다. full 모드로 가려면 2K($0.10)가 맞다.
- art 대 full: 글자 보존은 둘 다 됐고, 남는 차이는 **해상도·비용**(art 1K $0.067 로 충분, full 은 2K $0.10)과 **아바타를 모델에 맡기느냐**(full 은 맡겨야 함, 지금까지 7장 중 1장 실패). 결정은 5·7번 뒤에.

### 실험 5번 — 다른 사진 2장 (art, 1K, 1600px) · 7번 — full 2K 1장 + 고친 프롬프트 full 1K 1장 — 09-14 00:07~00:08

약 $0.32. 5번 두 장은 스크립트 버그(상대 경로 `relative_to`)로 `.json` 사이드카가 안 써졌고 이미지는 저장됐다 — 사이드카는 손으로 적었고 버그는 고쳤다(`resolve()`).

- **5-a `…000709` (사진 `_03`, 정면·귀 벌림):** 닮음 좋음. 귀 벌린 모양까지 따라옴. 소품 없음, 매트 유지.
- **5-b `…000725` (사진 `_08`, 엎드린 옆모습·귀 젖힘):** **닮음 약함** — 귀가 늘어진 크림 장모견(하바니즈 느낌)이 됐다. 사진에서 귀가 젖혀 있어 모델이 그대로 따라간 것. 그리고 raw 머리띠의 `NEO-APR25` 가 `NEO-APR5` 로 **처음 글자가 깨졌다**(art 모드라 합성본에서는 원본 띠로 덮여 무관). → **사진 각도가 닮음을 좌우한다.** 정면·귀가 보이는 사진을 권해야 한다.
- **7-a `…000749` (full 2K, 사진 `_335` 기본):** 선명하지만 **닮음 실패** — 귀 늘어진 크림견. 같은 사진으로 1K 에서는 5/5 성공했으니 2K 탓인지 편차인지 1장으로는 모른다. 글자·매트·소품 없음은 OK.
- **7-b `…000811` (full 1K, 고친 프롬프트):** 닮음 좋음, **목줄 없음**(프롬프트 수정이 먹혔다), 매트·글자·아바타 OK.

**누계 (Nano Banana 2, 11장, 약 $0.85):** 닮음 9/11 (실패 둘: 귀 젖힌 옆모습 사진 1, full 2K 1) · 글자 깨짐 raw 1/11 (art 라 무관) · 아바타 실패 1/11 · 무대 고정 프롬프트 이후 매트·소품 실패 0.

**판정 — Nano Banana 2: 「조건부 됨」.**
1. 정면·귀 보이는 사진일 때 잘 된다. 앱에서 그런 사진을 고르도록 안내가 필요하다.
2. 다섯~여섯 장에 하나꼴로 닮음이 어긋난다 → 2단계 닮음 심판 + 1회 재시도는 필수.
3. ~~art 모드 권고~~ → **사용자가 09-14 에 art 모드를 폐기했다.** 합성 이음새가 어색하고, 무엇인지 사전 설명이 안 된 채 7장($0.5)을 썼다. **full 모드로 확정.** 아바타·글자도 모델이 그리고, 검사로 잡는다.
4. 남은 비교: 같은 입력을 Qwen-Image-Edit-2511(fal)로 — fal 크레딧 필요.

### full 추가 5장 (09-14 00:23~00:25) — Nano Banana 2 판정 확정

약 $0.40. 고친 프롬프트, 1600px 입력. `0914_0023xx/0024xx_…_full_1K_1.png` ×3 (사진 `_335`·`_03`·`_08`), `0914_002456_…_full_2K_{1,2}.png` (사진 `_335`).

| | 닮음 | 글자 | 아바타 | 소품/매트 |
| --- | --- | --- | --- | --- |
| 1K `_335` (정면, 귀 선) | OK | OK | OK | OK |
| 1K `_03` (정면, 귀 벌림) | OK | OK | OK | OK |
| 1K `_08` (엎드린 옆모습, 귀 젖힘) | **실패** — 귀 늘어진 코커 느낌. 7-b 와 같은 사진에서 같은 방향으로 틀림 | OK | OK(사진 닮음) | OK |
| 2K #1 `_335` | OK | OK | OK | OK |
| 2K #2 `_335` | OK | OK | OK | OK |

**full 누계 (고친 프롬프트 7장):** 닮음 5/7 — 실패 둘은 ① 7-a(2K, 편차) ② `_08` 사진(입력 탓, 2/2 재현). 정면 사진만 세면 5/6. 글자 9/9(프롬프트 수정 전 포함), 아바타 9/9, 목줄 0/7, 매트 7/7. 2K 는 3장 중 2장 OK — 7-a 는 편차로 본다.

**판정 — Nano Banana 2 (full): 「됨」.** 서비스 조건:
1. **2K** 로 간다 (카드 크기에 확대 없이 맞고, 닮음도 1K 와 다르지 않다). 장당 $0.10.
2. **닮음 심판 + 1회 재시도**는 필수 — 정면 사진에서도 여섯에 하나꼴로 어긋난다.
3. **정면·귀가 보이는 사진**을 앱에서 안내한다. 엎드린 옆모습은 2/2 실패.
4. 아바타·글자는 모델에 맡겨도 된다(9/9). 심판이 글자도 같이 본다.

Nano Banana 2 쪽 실험은 여기서 **끝**. 총 16장 약 $1.25. 남은 것은 Qwen-Image-Edit-2511 비교(fal).

### 글자 없는 틀 (09-14 00:35) — 개인화 텍스트 설계

사용자 방향: 배지는 `NEO-APR25` → `APR26` 만, 제목은 `BLOSSOM <강아지 이름>`. 이름·한글을 **AI 에게 쓰게 하지 않는다** —
한글이 깨지고, 글꼴이 사용자마다 달라지고, 이름 길이를 못 맞추기 때문. **글자는 전부 Pillow 가 그리고 AI 는 강아지만.**

- `backend/tools/cardimage_blank_title.py` 로 4월 카드에서 `BLOSSOM NEO`·`NEO-APR25` 글자만 지운 틀을 2K 한 장 뽑음($0.10):
  `cardimage/out/0914_003528_4_blossom_blank_title_2K_1.png`. 제목판·배지판은 비었고 나머지는 그대로. 제목판 뒤의 어두운 그라데이션 띠는 같이 사라져 은색 판이 됨 — 글자 얹을 때 띠도 같이 그리면 된다.
- 시안: 그 위에 `BLOSSOM 초코 / 몽실이 / Charlie / 김제리김제리` + `APR26` 을 윈도우 맑은고딕 Bold 로 얹어 봄 (`cardimage/out/_mock_title/`). 서비스 글꼴은 배포 가능한 것(Pretendard·Noto Sans KR)으로 바꾼다.
- 미결: 제목 형식(영문 카드명+이름 vs 한글), `26` 이 발급 연도인지. 틀 채택 여부는 사용자 확인 뒤 `cardimage/4_blossom_template.webp` 로 커밋.
- 사용자 지적(09-14 00:40): ① 배지를 다 지우면 안 된다 — `26APR` 로 **쓰되 글자 크기는 그대로**, 짧아진 만큼 배지를 줄이고 **제목판을 그만큼 늘려** 이름 칸을 확보 ② 제목판의 **검은 배경 띠는 남기고** 글자만 지울 것. 첫 판(00:35)은 둘 다 안 맞아 폐기.
- 두 번째 판 (00:43, $0.10): `cardimage/out/0914_004345_4_blossom_blank_title_2K_1.png`. ② 검은 띠 유지 OK, 배지 `26APR` OK. **제목판 확장은 안 됨**, 배지 글자는 사용자 보기에 너무 큼.
- 세 번째 판 (00:48, $0.10) — **두 번째 판의 raw 2K 에서 검은 띠만 잘라낸 것을 입력**으로, "배지 글자를 `APRIL SPECIAL` 글자 높이로, 배지는 그만큼 좁히고, 경계를 옮겨 제목판을 늘려라" 한 가지만 시킴(`--step badge`): `cardimage/out/0914_004818__blank_v2_2Kcrop_badge_2K_1.png`. **셋 다 됨** — 글자 작아짐, 배지 좁아짐, 제목판이 약 x=745 → 790 까지 늘어 이름 칸 약 490 → 545px. 나머지 픽셀 그대로(재생성 두 겹인데 눈으로 차이 없음). 교훈: 도형 배치는 **한 번에 한 가지만, 기준(다른 글자 높이)을 주고** 시키면 된다.
- **사용자 결정 (00:50):** ① 세 번째 판을 틀로 채택 → `cardimage/4_blossom_template.webp` (q92), 2K 원출력은 `cardimage/raw/`(폴더만 추적) ② 제목은 `BLOSSOM <이름>` ③ 배지 `26APR` 은 틀에 구운 채로 둔다, 연도는 내년까지 생각 안 함. 원본 `4_blossom.webp` 는 지우지 않는다.

### 11장 틀 만들기 (09-14 01:40~01:55) — 약 $2.2

`cardimage_make_templates.py` 가 `headers.json` 을 읽어 카드마다 blank → badge 두 단계를 이어서 돌렸다 (4월 제외 11장, 22호출).
결과는 `cardimage/out/templates/<stem>_blank.png` / `<stem>_badge.png` (+ `_badge_raw2k.png`), 비교표 `_sheet.png`(원본/blank/badge 머리띠).
배지는 `26JAN`…`26DEC`. **사람 확인 전** — 채택은 사용자가 보고 정한다. 확인 포인트: 검은 띠 유지, 배지 글자 크기, 제목판 확장, 강아지·아바타·아래판 불변.
`backend/.env.example` 에 `DAENGS_CARDIMAGE_GEMINI_API_KEY` 항목이 없던 것을 사용자가 지적 → 추가.
- 확인 결과: ① 11/11 성공. ② 는 제목판 확장이 3·7·10·11·12 만 되고 1·5·6·9 는 그대로, 2·8 은 배지 글자도 안 줄었다. 여섯 장 재시도를 권했으나 **사용자 결정: "끝이 없다, 다 그대로 쓰고 9월만 한 번 더"** → 9월 ② 재실행($0.10)은 거의 안 변했고 그 판을 채택. **11장 전부 `N_<이름>_template.webp` 로 커밋**, 2K raw 는 `cardimage/raw/`.
- 9월 한 번 더 (02:05, $0.10, 사용자 요청 "4월과 최대한 동일하게, 프롬프트 바꿔도 좋다"): **4월 채택 틀을 참조 이미지(image 2)로 같이 보내** "이 머리띠와 같은 비율로" 시킴 (`scratchpad/sep_match_april.py`). 제목판이 조금 더 늘었지만 배지 글자 크기는 여전히 4월보다 큼. 그 판을 9월 틀로 교체 채택. 도형 크기 맞추기는 모델이 약한 일이라, 더 맞추려면 Pillow 로 배지 영역을 4월 것으로 합성하는 편이 확실하다 — 필요해지면.
- **Pillow 배지 합성 (02:20, AI 없음)** — 사용자 요청 "Pillow 로 배지 합성해서 9월만 맞춰봐". `backend/tools/cardimage_badge_fix.py`: ① 4월 틀의 머리띠 오른쪽(x≥690, y 52~148: 판 끝·경계·배지·테두리)을 24px 이음새 블렌드로 붙이고 ② `26APR` 글자 픽셀만 주변색으로 번져 지우고 ③ 9월 틀에 찍혀 있던 `26SEP` 글자를 마스크(얇은 선은 침식으로 제거)로 떠서 4월 글자 높이(49→38px)로 줄여 같은 자리에 얹음. 첫 시도는 복사 띠에 벚꽃 배경이 딸려 오고 `2` 가 잘렸다 → 띠 범위·탐색 상자·알파를 고쳐 해결. 결과 `cardimage/out/templates/9_harvest_moon_badgefix.png`, 비교 `_badgefix_compare.png`(원본 크기), `_badgefix_zoom.png`(3배). 3배에서 지운 자리의 옅은 잔상이 조금 남는다. → **사용자 거부 (02:25): "구려, 기존 거 그대로 쓸게. 여기까지."** 9월 틀은 4월 참조 판(`0381139`)을 유지. 도구 `cardimage_badge_fix.py` 는 지웠다(다음 세션이 또 쓰지 않도록). 교훈: 사용자는 **합성 티(이음새·잔상)에 민감**하다 — art 모드에 이어 두 번째 거부. 모델이 통째로 그린 것을 그대로 쓰는 쪽을 선호한다.

### 세션 마무리 (09-14 02:25)

- 12장 틀 채택·커밋 완료. 제목 얹기 도구 완료(글꼴 KR Black, 판 중심 정렬, 영문 대문자). 12장 머리띠 글자 `headers.json`.
- 이 세션 비용 합계 약 $5.5 (Nano Banana 2 실험 16장 ≈ $1.25 · 틀 4월 3장 ≈ $0.3 · 11장×2 + 9월 2회 ≈ $2.6 · 나머지 OCR 등 <$0.05).
- 다음 세션 갈림길: ① fal 충전 → Qwen-Image-Edit-2511 비교 ② 1단계 서비스 설계(계획 문서 먼저). 남은 숙제: 달별 제목판 오른쪽 경계 상수(4월 값만 잰 상태).
- 남는 숙제: 달마다 제목판 오른쪽 끝(y=100 기준 713~769)이 달라서 `cardimage_title.py` 의 경계 상수(4월 값)를 달별로 재야 한다. 틀 만들기 총비용 약 $2.3.

### 글자 얹기 테스트 (09-14 00:55) — AI 없음

`backend/tools/cardimage_title.py` 로 채택한 틀에 `BLOSSOM NEO` · `BLOSSOM 네오` · `BLOSSOM neeeeeeo` 를 얹음 (`cardimage/out/_title_test/`).
- 셋 다 제목판 안에 들어감. 앞 둘은 64pt, `neeeeeeo` 는 폭에 맞춰 52pt 로 자동 축소. 한글 문제 없음.
- 글꼴은 아직 시안용 맑은고딕 Bold. 원본 제목은 굵은 세리프라 인상이 다르다 → 글꼴 결정 필요(배포 가능한 것 중: 세리프면 Noto Serif KR, 산세리프면 Pretendard/Noto Sans KR).
- 제목판 안쪽 상자 `(262, 70, 780, 148)`. 긴 이름이 배지 경계(x≈790)에 바짝 붙어서 오른쪽 여백을 10px 더 줄 여지 있음.
- 사용자가 "fal 전에 할 것" = **1~12월 틀 전부 만들기**라고 밝힘.
- 사용자 지적 "칸에 제대로 안 들어간다, 더 정밀하게" → 원본 제목을 픽셀로 잼: **왼쪽 정렬 x=268, 대문자 띠 y 87~134(48px), 기준선 134, `BLOSSOM NEO` 폭 416**, 검은 판 오른쪽 경계는 기울어짐(y65→x791, y135→x758). 첫 시안은 가운데 정렬·산세리프·눈대중 상자라 틀렸던 것. 스크립트를 이 값 기준으로 다시 씀 — 기준선 맞춤, 글자 단위 글꼴 분기(영문/한글), 은색 그라데이션+외곽선+그림자.
- 글꼴은 OFL 인 Noto Serif 계열로: 영문 **Noto Serif Display**(가변, Weight·Width 축) + 한글 **Noto Serif KR**(가변). 후보 셋을 `_font_variants.png` 에 나란히: Display 폭62(원본 폭에 가장 가깝지만 가늘어 신문체 느낌) / Display 폭80(굵기 근접, 대문자 47) / **KR Black 통일**(글자 모양이 원본과 가장 비슷, 한 글꼴, 대문자 44~46). → **사용자 선택: KR Black 통일** (09-14 01:10). 나중에 무료·구매 글꼴로 바꾸면 파일만 교체.
- 사용자 지적 "아직 내려와 있다, `26APR` 과 가운데를 맞춰라" → 배지 글자 띠를 잼: y 88~125, 중심 106.5 (원본 제목 띠 중심 110.5 보다 4px 위). 제목 대문자 띠를 그 중심에 맞춤(48px → 82~130, 기준선 130). 확대 비교에서 두 띠의 중심선이 겹침.
- 글꼴 파일은 **저장소에 넣음** — `cardimage/fonts/NotoSerifKR.ttf` (24MB, 가변) + `OFL-NotoSerifKR.txt`. "서버에 따로 두고 경로만 설정" 대안은 배포 때마다 파일 유무를 챙겨야 해서 접음.
- 결과: `BLOSSOM NEO` 대문자 44px, `BLOSSOM 네오` 46px, `BLOSSOM neeeeeeo` 33px (KR Black 이 넓어 이름 칸 폭에 맞춰 줄어듦).
- 사용자 재지적 (01:20) "몇 px 더 위로 — **검은 칸 중앙과 글씨 중앙**이 같게" → 검은 판을 잼: y 53~145, 중심 99. 대문자 띠 중심을 99 에 (기준선 123). 배지 글자 중심(106.5)·원본 제목 중심(110.5)은 둘 다 판보다 아래였다 → 폐기. `_zoom_compare.png` 의 빨간선(판 중심)이 글자 한가운데를 지난다.
- 사용자 재지적 (01:30) "`neeeeeeo` 는 여전히 처진다" → 원인: 대문자 띠 기준이라 소문자만 있는 이름은 띠의 아래 절반에만 글자가 있어 처져 보임. 고침 둘: ① 세로 맞춤을 **잉크(실제 찍히는 글자 덩어리)의 중심**으로 ② **영문 이름은 기본으로 대문자**(`--keep-case` 로 끌 수 있음) — 카드 제목이 전부 대문자 양식이라. `_zoom_compare.png` 다섯 줄(틀·NEO·네오·NEEEEEEO·소문자 유지) 모두 빨간선이 글자 중심.

### 첫 커밋 정리 (09-14)

- 참조 카드 12장을 **WebP q92** 로 변환해 커밋 (33MB → 5.8MB). q92/q95 를 PNG 와 픽셀 비교: PSNR 37~38 / 38~39 dB, 평균 오차 2.1 / 1.9 (255 기준) — 차이가 눈에도 모델에도 무의미해 앱 저장소 기준(q92)으로. 앱 카드 크기(1080×1440)로 줄이지는 않음 — 비율이 달라 카드 재디자인이 되는 별개 결정.
- `cardimage/test/`·`out/` 은 `.gitkeep` 으로 폴더만, 내용은 `.gitignore`. PNG 원본도 ignore.
- 실험 스크립트에서 art 모드 코드를 **지웠다** (full 만 남김). 다음 세션이 다시 쓰지 않도록.
- 이 시점의 **실험은 멈춤 상태** — full 추가 5장($0.40) 제안은 사용자 승인 대기.

### art 폐기 뒤 full 모드 증거 정리 (09-14)

full 모드로만 세면 **4장뿐**이다: 4-a·4-b(1K, 무대 고정 프롬프트 **전** — 목줄 들어감, 닮음·글자·아바타 OK), 7-a(2K, 고친 프롬프트, **닮음 실패**), 7-b(1K, 고친 프롬프트, 전부 OK). 고친 프롬프트로는 2장이고 그중 1장이 실패라 **full 의 성공률·2K 여부는 아직 근거가 부족하다.** 추가 full 실행이 필요하다.

## 2026-09-13 — 카드 열기 · 조사 · 실험 준비

**한 일**

- 처음에 DAENGS_APP 에 잘못 열었다가(#376, 닫음) **DAENGS_dev #496** 으로 다시 열었다. Project 3: Todo · Size L · P1 · Iteration 6.
- `gohome/neo` → 저장소 최상단 `cardimage/` 로 참조 카드 12장 복사, `cardimage/test/` 에 실제 강아지 사진 13장. 둘 다 **미추적.** 처음 이름 `neo/` 를 `cardimage/` 로 바꿨다.
- 조사: 참조 이미지 편집 방법 네 갈래, 후보 모델, 비용, 서빙 방식, LTX/Krea 2 가 왜 해당 없는지, Krea 2 LoRA 생태계, fal·Gemini 결제. → `research-2026-09-13.md`
- 실험 스크립트 `backend/tools/cardimage_try.py` 작성. `--dry-run` 으로 기하(그림 영역 상자·머리띠 경계·아바타 원)를 확인했고 상자를 `(60, 58, 946, 1197)` 로 잡았다. **API 호출은 아직 0회.**
- 앱이 실제로 만드는 사진 크기를 확인했다 (README 「안 정해진 것」 첫 항목).

**정한 것** (사용자)

- 범위: 사진 한 장 → 4월 카드 한 장. 12장 세트 아님.
- 엔진 후보: Nano Banana 2 · Qwen-Image-Edit-2511 둘. ComfyUI 는 러너일 뿐 후보가 아니다.
- 카드 생성용 Gemini 키는 별도 프로젝트, `DAENGS_CARDIMAGE_GEMINI_API_KEY`. 이름에 NEO 안 씀.
- 참조·테스트 사진은 원본 화질 그대로 둔다. 줄일지는 실험으로 정한다.

**지적받은 것 — 다음 세션도 같은 실수 말 것**

- 엔진을 사용자가 안 정했는데 계획을 Nano Banana 2 기준으로 써 버렸다. → 엔진은 어댑터 뒤로 빼고 결정은 평가가 한다.
- 호출 전에 "1K 한 장 돌리겠다"고만 하고 순서를 설명하지 않았다. → **호출 전에 실험 순서·비용을 설명하고 승인.**
- 입력 사진을 1600 으로 줄이는 것을 기본값으로 박았다. → 원본 그대로가 사용자 가정. 줄이는 것은 비교 항목.

**다음 세션에 넘기는 것**

- README 「다음에 할 일」 1번부터. 키는 `.env` 에 들어 있다.
- fal 크레딧은 아직 없음 (Qwen 비교는 그 뒤).
- 실험 결과가 나오면 이 파일에 절을 추가하고 README 「지금 상태」를 갱신한다.

**설계 그림 (아직 계획 문서 아님 — 1단계 들어갈 때 writing-plans 로 다시 쓴다)**

| 단계 | 내용 | 카드 |
| --- | --- | --- |
| 0 | 타당성 실험 (위 순서) | #496 |
| 1 | 서비스 경계 — `PUT /app/cards/{card_id}/…`(card_id 는 앱이 만드는 D-052 규칙 유지)로 사진을 받아 생성, 결과는 기존 `cards/<user>/<card>/face.png` 저장 경로에. 엔진 어댑터 Protocol 을 처음부터, 기본 엔진은 설정값. 처음은 동기 + 타임아웃, 여러 달로 넓힐 때 `tasks/` Celery | #496 |
| 2 | 닮음 심판 — 결과와 원본을 `gemini-3.1-flash-lite` 에 넣어 5점 척도, 미달이면 1회 재생성 | #496 |
| 3 | 평가 하네스 `daengs_evals/cardimage_quality` — test 13장 × 후보 엔진, 심판 점수 + DINO 유사도 표. 결과 `backend/evals/cardimage/`. 엔진 결정 → decisions.md | 후속 카드 |
| 4 | 오픈 모델 어댑터 — Qwen-Image-Edit-2511, 처음은 fal, 다음은 빌린 GPU 의 diffusers | 후속 카드 |
| 5 | 앱 연결 — 사진 고르기 → 호출 → 도감에 카드 (표시 계약 새로 필요, 캔버스 크기 다름) | DAENGS_APP 카드 |
