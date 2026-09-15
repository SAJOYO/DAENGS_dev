# cardimage — 사용자 강아지 사진으로 도감 카드 만들기

카드 **#496** (`feat/comfyui-image-generation`, draft). 이 폴더는 그 카드의 **세션 인수인계 문서**입니다.
다음 세션의 사람과 Claude 가 이 폴더만 읽고 이어서 하는 것이 목적입니다.

| 문서 | 무엇 |
| --- | --- |
| 이 파일 | 목표 · 지금 상태 · 파일 위치 · 정해진 것과 안 정해진 것 · 다음에 할 일 |
| [`worklog.md`](worklog.md) | 날짜별 진행 기록. **세션이 끝날 때마다 한 절 추가** |
| [`research-2026-09-13.md`](research-2026-09-13.md) | 2026-09-13 조사 — 생성 방법 네 갈래 · 후보 모델 · 비용 · 서빙 방식 · 결제 |

## 목표

사용자가 앱에서 **강아지 사진 한 장**을 넣으면, 도감 카드 12장 중 **4월 카드
(`cardimage/4_blossom.webp`, 벚꽃 아래 피크닉 매트에 앉아 꽃잎을 올려다보는 강아지) 한 장**을
만들어 준다. 구도·배경·카드 틀·문구는 그대로, **강아지 본체와 왼쪽 위 원형 아바타만 사용자의
강아지**로.

- 12장 한 세트를 한 번에 만드는 것이 **아니다.** 4월 한 장이 되면 나머지 달은 같은 경로에 카드만 바꿔 끼우는 후속.
- 앱에 이미 있는 사진 카드 경로(폰 안 누끼로 강아지를 오려 카드 얼굴창에 끼우는 것 — `DAENGS_APP` `ui/dogcard/Cutout.kt`, `docs/card-holes.md`)는 **그대로 두고**, 이것은 별도 경로다.
- 엔진은 ComfyUI 로 못 박지 않는다. 브랜치 이름에 comfyui 가 남아 있는 것은 카드를 열 때의 짐작이다.

## 지금 상태 (2026-09-14, 앱 경로 #537 + 1단계 + 9월)

**앱 사용자 경로 (09-14, #537 · D-076).** 앱이 `POST /app/ai-cards?month=&dog_name=&dog_id=`(본문 사진)로
카드 만들기를 시작하면 202 + `status: generating` 을 받고, `GET /app/ai-cards/{id}` 가 `ready` +
`image_url`(994×1582) 을 줄 때까지 다시 조회한다. 서버는 표 `ai_cards` 에 행을 먼저 남기고 backend 프로세스
안 백그라운드에서 만든다. 한도는 사용자별 동시 1장 + 하루 완성 1장(`DAENGS_CARDIMAGE_DAILY_LIMIT`). 생성
로직은 `backend/src/daengs_cardimage/` 로 옮겼다. 설계 `spec-2026-09-14-app-ai-cards.md`, 계획
`plan-2026-09-14-app-ai-cards.md`. **배포 전에** `db/migrations/2026-09-14_ai_cards.sql` 을 개발서버·GCP DB 에 적용한다.

**9월 카드 추가 (09-14 오후, 사용자가 콘솔 확인 뒤 요청).** 열린 달이 **4월(BLOSSOM)·9월(CHUSEOK)** 둘이다
(`cardimage_months` 기본값 `4,9`). 달마다 다른 것 세 가지를 `catalog.MonthCard` 필드로 뺐다 — ① `subtitle`
(`SEPTEMBER SPECIAL`, 옛 `generate.SUBTITLES` 표 삭제) ② `outfit`: 4월은 "아무것도 안 입는다", 9월은
"이미지 1 의 개와 같은 한복을 입고 같은 송편 쟁반을 든다"(9월 틀 강아지가 한복 차림이라 4월 문장이 충돌)
③ `plate`(`title.Plate`): 9월 제목판은 4월보다 **11px 위**(y 40~135, 중심 88 — 4월은 53~145, 중심 99)에 있고 약 40px 좁아 오른쪽 경계 `((65,750),(135,709))` 를 따로 쟀다(4월 `((65,791),(135,758))`). 왼쪽 시작 x=252 는 두 달이 같다.
카드명은 사용자가 **`CHUSEOK`** 으로 정했다(`HARVEST MOON` 은 판에 안 들어가 축소됨). 콘솔 탭에 달 고르기
(`<select>`)가 붙었다. 실호출 1회: `_03` 사진, 이름 "네오" → 유사도 5/5, 29.8초
(`cardimage/out/_service_check/service_check_sep_1.png`). 콘솔의 PNG 저장 버튼과 제목 x=252 도 같은 날.

**1단계 서비스 구현 완료 (09-14, 에이전트 실행 Task 1~9·11).** `backend/src/daengs_cardimage/`(#537 에서
`daengs_backend/services/cardimage/` 에서 옮김) (catalog · photo · title · engine · judge · generate) 가 파이프라인을 갖췄고, `POST
/admin/cardimage/generate`(search:inspect) 로 열렸다. 콘솔 「기능 / 검색 점검」에 「도감 카드
생성」 갈래가 붙었다(`/console/search` 탭 5). 설정은 `DAENGS_CARDIMAGE_*`(`config.py`), compose 가
`cardimage/` 를 컨테이너에 마운트한다. 엔진 결정은 `docs/decisions.md` D-074.

실호출로 파이프라인 자체는 확인했다 — 사진 한 장(`_03`, 4월, 이름 "네오")으로 유사도 검수
5/5, 29초, PNG 2.5MB (`cardimage/out/_service_check/`). **남은 것:** 사용자가 콘솔 화면에서
눈으로 확인(관리자 로그인 정보가 로컬에 없어 넘김 — 진행자 결정), draft 해제·머지, 배포 시
서버 `backend/.env` 에 `DAENGS_CARDIMAGE_GEMINI_API_KEY` 를 넣고 `docker compose up -d
backend`(의존성·마운트 변경이라 재생성 필요). nginx 는 `location /api/admin/cardimage/`(300s)가
추가돼 자동 배포의 `nginx -t` 후 reload 로 반영된다. 상세 진행은 `worklog.md` 09-14 절.

**09-14 02:25 세션 종료 시점 (0단계, 실험 마감):** 아래 상태에서 멈췄었다. 9월 틀의 Pillow 배지 합성은 사용자가 거부("구려") → 도구 삭제, 모델이 그린 판 그대로. 합성 방식(art 모드·배지 합성)은 두 번 거부됐으니 **다시 제안하지 말 것.**

**틀 12장 채택 (09-14 01:55):** `cardimage/N_<이름>_template.webp` ×12, 제목 `<카드명> <이름>` 은 Pillow 로 (`cardimage_title.py`, 글꼴 KR Black, 검은 판 중심 정렬, 영문 대문자). "fal 전에 할 것"(12장 틀)은 끝났고 **다음은 fal 의 Qwen-Image-Edit-2511 비교**(크레딧 필요) 또는 1단계 서비스 설계.

**0단계 — Nano Banana 2 쪽 끝. 판정 「됨」** (full 모드, 총 16장 약 $1.25, art 모드 7장은 09-14 폐기). 고친 프롬프트 full 7장: 닮음 5/7(실패는 2K 편차 1 + 엎드린 옆모습 사진 1), 글자 9/9, 아바타 9/9, 목줄 0/7, 매트 7/7. 서비스 조건은 **2K · 닮음 심판+재시도 · 정면 사진 안내**. 상세는 `worklog.md`. **남은 것은 6번 — Qwen-Image-Edit-2511 비교 (fal 크레딧 필요).** 사용자가 "fal 전에 할 것이 하나 있다"고 함 (09-14) — 내용 미정.

- 엔진 후보를 **Nano Banana 2(`gemini-3.1-flash-image`) 와 Qwen-Image-Edit-2511** 둘로 좁혔다 (사용자 결정, 09-13).
- Gemini 쪽 결제가 준비됐고, 카드 생성용 키가 채팅 키와 **다른 프로젝트**로 `backend/.env` 의 `DAENGS_CARDIMAGE_GEMINI_API_KEY` 에 들어 있다.
- fal(Qwen 호스팅) 크레딧은 아직 없다. 최소 충전 금액은 공식 문서에 없고 결제 화면에서만 보인다.
- 실험 순서는 사용자와 합의됐다 (아래 「다음에 할 일」). **호출 전에 순서를 설명하고 승인을 받는다** — 09-13 에 설명 없이 "1K 한 장 돌리겠다"고 해서 지적받았다.

## 파일 위치

| 경로 | 무엇 | git |
| --- | --- | --- |
| `cardimage/1_new_year.webp` … `12_santa.webp` | 참조 카드 12장 (994×1582, **WebP q92**, 장당 약 0.5MB). 크림 푸들 "네오"가 주인공. 출처 `gohome/neo` 의 PNG | **커밋** (09-14). PNG 원본(장당 2.75MB)은 `.gitignore` 로 빼서 로컬에만. q92 는 원본 대비 PSNR 37~38 dB 로 눈·모델 입력 모두 구분 불가 |
| `cardimage/N_<이름>_template.webp` ×12 | **채택한 틀 12장** (4월 09-14 00:50, 나머지 11장 01:55). 제목판은 검은 띠만 남기고 글자 없음, 배지는 `26JAN`…`26DEC` 로 구워짐(연도 고정). 사용자 카드는 틀 + 사진 → 강아지 교체 → `<카드명> <이름>` 을 Pillow 로 얹기. ⚠ 11장은 ②단계(배지 줄이기·제목판 늘리기)가 절반쯤만 먹었는데 **사용자가 "그대로 쓴다"고 결정**(9월만 한 번 더, 거의 안 변함). 달마다 제목판 오른쪽 끝이 713~769 로 달라 `cardimage_title.py` 의 경계 상수(4월 값)를 **달별로 재야 한다** — 1단계 항목 | 커밋 |
| `cardimage/fonts/NotoSerifKR.ttf` · `OFL-NotoSerifKR.txt` | 제목 글꼴(가변, 24MB)과 라이선스 | 커밋 |
| `cardimage/raw/<stem>_template_2K.png` ×12 | 위 틀들의 2K 원출력(검은 띠 제거, 약 1612×2528). 더 큰 카드가 필요해질 때 | 폴더만 커밋(`.gitkeep`), 내용은 `.gitignore` |
| `cardimage/headers.json` | 카드 12장의 원본 제목·배지·부제 (`cardimage_read_headers.py` 산출) | 커밋 |
| `cardimage/test/*.jpg` | 실제 강아지 사진 13장 (3000×4000, 4~6MB, 카카오톡 원본). **일부러 원본 화질** — 사용자가 폰 원본을 그대로 넣는 상황 | 폴더만 커밋(`.gitkeep`), 내용은 `.gitignore`. 실제 개 사진이라 커밋 금지 |
| `cardimage/out/` | 실험 산출물. 결과 PNG 옆에 같은 이름 `.json`(모델·크기·프롬프트), 모델 원출력 `_raw.png` | 폴더만 커밋, 내용은 `.gitignore` |
| `backend/tools/cardimage_try.py` | 0단계 실험 스크립트 (full 모드만). `uv run --with pillow python tools/cardimage_try.py` | 커밋 |
| `backend/src/daengs_cardimage/` | **생성 로직 패키지** (#537 에서 `daengs_backend/services/cardimage/` 에서 옮김) — catalog(틀·무대)·photo(검증·리사이즈)·title(Pillow 제목)·engine(Nano Banana 2 어댑터)·judge(닮음 검수)·generate(파이프라인). backend 를 import 하지 않는다 | 커밋 |
| `backend/src/daengs_backend/services/ai_card_engine.py` · `ai_card.py` · `ai_card_quota.py` · `routers/ai_card.py` · `routers/admin_cardimage.py` | backend 쪽 — 설정으로 엔진 만들기(유일한 호출 자리) · 앱 경로 서비스 · 한도 · `/app/ai-cards` · `/admin/cardimage/generate` | 커밋 |
| `docs/cardimage/plan-2026-09-14-phase1.md` | 1단계 구현 계획 (Task 1~11) | 커밋 |
| `docs/cardimage/` | 이 폴더 | 커밋 |

`data/` 에 넣지 않는다 — 거기는 RAG 코퍼스 자리라 `.gitignore` 규칙과 `DAENGS_DATA_DIR` 이 얽힌다.
서비스가 되면 4월 카드는 backend 가 읽을 자리로 옮기거나 여기서 읽게 한다 — 1단계 설계 항목.

## 정해진 것

| 항목 | 결정 | 근거 |
| --- | --- | --- |
| 범위 | 사진 한 장 입력 → 카드 한 장. **4월(BLOSSOM) + 9월(CHUSEOK)** 까지 이 카드에서, 나머지 달은 후속 | 사용자, 09-13 (4월) · 09-14 (9월 "하나 더", 카드명 CHUSEOK) |
| 엔진 | **Nano Banana 2 (`gemini-3.1-flash-image`), 2K, 카드 통째** — D-074 | 사용자 결정 09-14 (후보 둘 중 실험 16장으로; Qwen/fal 비교는 접음) |
| 생성 방식 | **full 모드(카드 통째)로 확정. art 모드(그림만 잘라 생성해 원본 틀에 되붙이기)는 폐기.** | 사용자 결정 09-14 — "합치는 부분이 어색하고 돈만 날린 정도". art 모드가 무엇인지 사전에 제대로 설명이 안 된 채 7장을 썼다. **다시 쓰지 말 것.** 글자 깨짐은 full 에서 닮음 심판과 함께 검사한다 |
| 무대 고정 | 프롬프트로 매트 색·소품 금지(목줄·리드줄·하네스·옷) 명시. 변주는 강아지 쪽에만 | 사용자 결정 09-13 — "무대가 흔들리면 카드 정체성 훼손" |
| 입력 크기 | 앱처럼 긴 변 1600px 로 줄여 보냄 | 실험 1·2, 원본과 차이 없음 |
| 출력 크기 | **2K** (Gemini 2:3 → 1696×2528, 카드 994×1582 에 확대 없이 맞춤) | 1K 는 1.25배 확대가 필요. 2K 닮음 3장 중 2장 OK, 1K 와 차이 없음 (09-14) |
| 글자 | **이름·제목은 AI 가 아니라 Pillow 가 그린다** (`backend/tools/cardimage_title.py`). 제목 형식 `<카드명> <이름>`(`BLOSSOM 네오` · `CHUSEOK 네오`). 왼쪽 정렬 x=252(원본 268 에서 16px 왼쪽 — 사용자 선택 09-14), 대문자 띠의 세로 중심은 **검은 제목판의 중심** 과 일치(4월 y=99, 9월 y=88 — 판 위치가 달마다 달라 `title.Plate` 로 틀별로 잰다. **모델 출력은 판이 틀보다 4~5px 위에 그려져** 출력에서 판 윗선을 다시 재어 그만큼 옮긴다), 검은 판의 기울어진 오른쪽 경계(`Plate.edge`) 안에서 길면 축소. 은색 그라데이션+외곽선+그림자 | 사용자 결정 09-14. 한글 깨짐·글꼴 불일치·길이 문제 회피. 원본 픽셀을 재서 맞춤 |
| 글꼴 | **Noto Serif KR 가변, Black** 하나로 영문·한글 — `cardimage/fonts/NotoSerifKR.ttf` (OFL). 바꾸려면 파일 교체 | 사용자 선택 09-14. 후보 비교는 `cardimage/out/_title_test/_font_variants.png` |
| 글꼴 후보 (영문 전용, 미채택) | 사용자가 적어 둔 후보: [ITC Novarese Bold](https://freefonts.co/fonts/itc-novarese-bold) · [ITC Korinna Extra Bold](https://freefonts.co/fonts/itc-korinna-extra-bold). 영문만 있어 한글은 KR 글꼴과 섞어 써야 함(`--font-latin` 자리) | 사용자 09-14 "후보로만 적어 둬, 지금은 받은 것 그대로". ⚠ ITC 계열은 원래 Monotype 상용 글꼴이라 그 사이트의 "free" 가 재배포·상업 사용을 허락하는지 **라이선스를 확인한 뒤** 써야 한다 |
| 틀 | `cardimage/N_<이름>_template.webp` 12장 채택. 배지 `26JAN`…`26DEC` 는 틀에 구워 둠(연도 고정) | 사용자 결정 09-14 — "내년까지 생각 안 해도 됨", "11장은 그대로 쓴다". 원본 `N_<이름>.webp` 도 그대로 둔다 |
| 키 | 카드 생성용 Gemini 키는 채팅용과 **다른 GCP 프로젝트**, 결제 계정은 하나 | 프로젝트 단위로 지출 상한·사용량이 갈리기 때문 (research §결제) |
| 이름 | `NEO` 를 코드·변수·문서 이름에 쓰지 않는다 | 네오는 참조 카드 강아지 이름 |
| 앱 경로 | `/app/ai-cards` 비동기(202 → 조회), backend 프로세스 안 백그라운드, 994×1582 그대로, 사용자별 동시 1장 + 하루 완성 1장 — **D-076** | 사용자 결정 09-14 (#537) |

## 안 정해진 것

- ~~입력 사진을 줄여 보낼지~~ → 실험 1·2 로 **1600px 로 줄여도 닮음 차이 없음** 확인 (09-13). 서비스는 앱이 하듯 1600 으로 보낸다.
- **앱에서 AI 카드를 어떻게 보여 줄지.** 서버는 994×1582(5:8) 를 그대로 주기로 했다(D-076) — 기존 3:4 카드와 섞을지·탭을 나눌지는 DAENGS_APP 쪽 결정.
- **제품 규칙** — 카드를 몇 장·어떤 조건(달마다 한 장, 활동 보상, 유료 등)으로 줄지. 지금 한도는 테스트 단계용이고 `services/ai_card_quota.py::check_quota` 를 통째로 바꾼다.
- **Qwen 을 어디서 돌릴지.** 실험은 fal 호스팅으로, 서비스·포트폴리오용은 diffusers 로 직접 돌리는 경로가 후보 (research §서빙).
- ~~엔진 최종 결정 → `docs/decisions.md` 에 번호로.~~ → **D-074 로 확정 (09-14, Nano Banana 2)**

## 다음에 할 일 (합의된 실험 순서)

호출마다 돈이 나간다. 1K 한 장 약 $0.067, 2K 약 $0.10. 전부 해도 $1 안쪽.

1. **원본 그대로**(축소 없음) art 모드 1K 한 장 — 연결 확인 + 닮음 + 머리띠 경계(y≈235) 이음새 확인.
2. 같은 사진을 **1600px 로 줄여** art 모드 1K 한 장 — 1 과 나란히 놓고 닮음 차이를 본다. 차이 없으면 서비스는 1600 으로 줄여 보낸다.
3. 같은 조건 2~3장 더 — 생성은 매번 달라서 한 장은 운일 수 있다.
4. full 모드 1~2장 — 글자가 깨지는지. 안 깨지면 덮어쓰기 없이도 되고, 깨지면 art 모드 확정.
5. 다른 test 사진 2장으로 1 반복 — 각도·배경에 따른 흔들림.
6. 결과를 보고 Nano Banana 2 를 「됨 / 조건부 / 안 됨」으로 적는다. 그 뒤 같은 입력을 Qwen-Image-Edit-2511(fal)로 — fal 크레딧 필요.
7. 2K 는 1K 가 좋을 때 마지막에 한 장.

그 뒤 단계(1단계 서비스 경계 · 2단계 닮음 심판 · 3단계 평가 하네스 · 4단계 오픈 모델 어댑터 · 5단계 앱 연결)의 그림은
`worklog.md` 09-13 절과 PR #496 본문에 있다. **1단계부터는 계획 문서를 먼저 쓴다** (writing-plans).
