# 실시간 산책·날씨를 Cloud Run 으로 — 설계

```
상태:        설계 승인됨 (구현 전)
날짜:        2026-09-11
카드:        #435 · 결정 번호 D-070
사람 결정:   4건 (아래 §2)
영향 범위:   daengs_life/realtime + app/{controllers,services,dto}/{walk,weather}
             daengs_backend — 접점 4곳 중 3곳 (ask 는 그대로)
             코드 변경 0 — 이번 카드는 설계까지
```

**운영 설계(이미지 · 배포 플래그 · 비밀값 · 검증 목록)는
[`docs/deploy/realtime-service.md`](../../deploy/realtime-service.md) 에 있다.**
여기에는 **어떻게 이 결론에 왔는가 — 조사한 것, 기각한 것, 그 이유**를 남긴다. 두 문서가
갈린 이유는 `corpus-pipeline.md`(D-062)와 같다: 하나는 두고 보는 것이고 하나는 왜인지다.

## 0. 이 카드가 열린 경위

#427(파이프라인 코드를 이미지 밖으로) 을 논의하던 중 사람이 물었다 —
**"`daengs_life` 안에서도 날씨 판정하는 실시간 검색들은 정말 순수하게 떼서 Cloud Run 으로
뺄 수 있을 것 같은데 어때?"**

그때 나는 *"별도 카드로 적어만 두자"* 고 권했고 **사람이 「이번에 설계까지 한다」를 골랐다.**
#427 이 머지된 뒤 그것이 이 카드가 됐다.

## 1. 조사

### 1-1. 정말 순수한가 — 맞다 (import 전수 조사, 2026-09-11)

| 무엇 | realtime 이 쓰나 |
| --- | --- |
| 라이브러리 | `httpx` · `redis` · `pyyaml` · `pydantic-settings` · `fastapi` (+간접 `tzdata`) |
| DB | **없음** — SQLAlchemy · psycopg · asyncpg 0줄 |
| 모델 | **없음** — torch 0줄 |
| 파일 | **없음** — `warning_areas.csv` · `thresholds.yaml` · `cache.yaml` 이 **코드 옆**에 있다 |
| `data/reference/` | 비어 있고 **`REFERENCE_DIR` 을 읽는 코드가 없다** (`config.py` 와 CLI 출력에만 이름이 나온다) |
| 사내 | `daengs_life.crawler.core.config` 한 줄 — `KST` · `.env` 병합 순서 · `DATA_DIR` |

마지막 줄이 유일한 사내 의존이고 **데이터 경로가 없어도 안 터진다.** `_find_repo_root()` 가
`None` 을 돌려주게 되어 있고, 그 이유가 `crawler/core/config.py:33` 에 적혀 있다 —
*"백엔드 이미지에는 data/ 가 없고 app 이 crawler 를 import 하므로 import 시점에 터지면
컨테이너가 아예 뜨지 않는다."*

`warning_areas.py:8` 은 그보다 강하게 말한다 — 표를 `data/reference/` 에 **두지 않은** 이유가
*"`REFERENCE_DIR` 은 `DAENGS_DATA_DIR` 에서 파생되는데 그 값은 배포마다 다른 곳을 가리킨다"*
였다. **그 결정 하나가 지금 이 카드를 싸게 만들고 있다.**

### 1-2. 부르는 쪽은 셋이다 (내가 처음에 넷으로 셌다)

| # | 부르는 쪽 | 방식 |
| --- | --- | --- |
| 1 | `GET /life/walk-conditions` | HTTP — 콘솔 패널(`walk-inspect.tsx`) · 앱 |
| 2 | 어시스턴트 산책 능력 (`/assistant/query`) | **같은 프로세스 함수 호출** — `adapters/walk.py:26` |
| 3 | 산책 finalize 의 과거 날씨 | **같은 프로세스 함수 호출** — `routers/walk.py:194` |
| ~~4~~ | ~~Beat 프리페치 (`warm_active`)~~ | **한 번도 돈 적이 없다** — 아래 |

#### 🔴 프리페치는 실행된 적이 없다

Beat 는 1분마다 `warm_active` 를 **기본 `celery` 큐**에 쏜다. 그런데 그 큐를 먹는 워커가 없다 —
`crawler-worker` 는 `--queues crawl` 로 크롤 큐만 먹는다(`docker-compose.yml:743`). `expires: 55`
라 쌓인 메시지는 나중에 소비자가 생겨도 **실행 없이 버려진다.**

저장소 주석이 이미 그렇게 적어 뒀다(`docker-compose.yml:748~755` — *"소비자가 없는 동안 기본
`celery` 큐에 쌓입니다(하루 1,440개)"*). RT-002 가 예정한 워커가 아직 안 떴다.

**그래서 이 API 는 늘 콜드 캐시에서 시작하고, 사용자 요청이 그 비용을 낸다.** 이 사실이 §3 의
「공유 상태 없음」 기각 근거를 만든다. 그리고 **분리하면 되살릴 길이 생긴다** — Cloud Scheduler
가 데우기 엔드포인트를 두드리면 Celery 없이 ④-d 설계가 돈다. **범위 밖(선택 항목)으로 뒀다.**

### 1-3. Redis 에 사는 것 다섯 — 셋은 캐시가 아니라 조율 상태다

| 키 | 무엇 | 성격 |
| --- | --- | --- |
| `rt:<feed>:<조회키>` | API 응답 원본 | 캐시 (없어도 된다, 느려질 뿐) |
| `rt-snapshot:<feed>:<조회키>` | 과거 관측 회차 | 캐시 |
| `rt:budget:<그룹>:<날짜>` | **일 호출 카운터** | **조율** — 프로세스 메모리면 재시작마다 리셋 |
| `rt:active` | 최근 실제로 요청된 조회 키 | **조율** — 프리페치가 읽는 자리 |
| `rt:lock:<키>` | single-flight 락 | **조율** — 동시 요청이 같은 API 를 N번 부르는 것을 막는다 |

**"버려도 되는 캐시"가 아니라는 것이 이 설계의 중심 사실이다.**

### 1-4. Cloud Run → VM 사설 IP 는 이미 운영에서 돈다 (`gcloud` 실측)

| 확인한 것 | 값 |
| --- | --- |
| VM | `daengs` · `10.178.0.2` · 서브넷 `default` · asia-northeast3 |
| 서브넷 범위 | `10.178.0.0/20` |
| 코퍼스 잡 | `--network=default --subnet=default --vpc-egress=private-ranges-only` (`pipeline.sh:179`) |
| 방화벽 | `allow-pg-from-run` — `10.178.0.0/20` → VM tcp:5432 |
| Cloud Run 서비스 | **0개** — 이 프로젝트에는 아직 잡만 있다 |

**커넥터가 아니라 Direct VPC egress** 다. 이 발견이 「Redis 를 어떻게 하나」를 거의 다 풀었다 —
새 인프라가 아니라 **검증된 경로에 포트 하나를 더 여는 일**이 됐다.

## 2. 사람 결정 (2026-09-11)

| | 물은 것 | 고른 것 |
| --- | --- | --- |
| ① | 이것으로 무엇을 얻으려는가 | **발표용 아키텍처 — scale-to-zero** ("zero to scale 을 해 보고 싶다") |
| ② | 조율 상태를 어떻게 하나 | **안 A — VM 의 Redis 를 그대로 쓴다** |
| ③ | 인증을 어디서 거나 | **ⓐ IAM 만 — backend 가 게이트웨이** |
| ④ | 코드를 물리적으로 옮기나 | **안 1 — 코드는 제자리, 이미지만 분리** |

①이 나머지를 정했다. 목적이 발표용이면 판단 기준이 둘로 좁아진다 — **① 지금 돌아가는 것을 안
깨뜨린다 ② 갈랐다는 것이 실물로 보인다.** 성능·비용은 근거에서 빠진다.

## 3. 기각한 것과 그 이유

### 3-1. 공유 상태 없이 프로세스 메모리로 — **기각 근거가 실측이다**

`Cache` 에는 Redis 가 없을 때 프로세스 메모리로 떨어지는 길이 **설계상** 있다(RT-001 ④-c,
`MemoryStore`). 그래서 "Cloud Run 은 그걸 쓰면 되지 않나"가 자연스럽다.

**scale-to-zero 와 정면으로 충돌한다.** 측정소 목록의 발표 주기가 **30일**(`cache.yaml:63`)이라
Redis 가 한 달간 들고 있어서 지금은 아무도 다시 안 받는다. 공유 캐시가 없으면 **콜드 인스턴스마다
시도 17개를 다시 받는다.**

| 깨지는 것 | 근거 |
| --- | --- |
| 응답 시간 | 정적 수집 **17.4초 실측** (2026-08-25, `collect.py:95` 주석) |
| 일 1,000회 | 콜드 스타트 × 17회. **하루 60번만 식으면 한도 초과** |

**즉 zero-to-scale 이 성립하는 조건 자체가 "상태가 프로세스 밖에 있다"이고, 그 상태가 이미
밖에 있다.** 이것이 안 A 를 고른 진짜 이유다 — 돈을 아끼는 것이 아니다.

#### 곁가지 — 예산이 두 뜻으로 쓰이고 있다

이 자리에서 설명이 두 번 막혔다. 저장소가 「예산」을 **개수**(일 1,000회)와 **시간**(요청 8초 ·
정적 30초) 둘에 쓴다. 다른 말이다.

시간 예산이 둘인 이유도 실측이다. 옛날에는 스톱워치가 하나였고, 콜드 캐시에서 A단계(조회 키
만들기)가 17.4초를 쓰면 B단계(실제 날씨)에서 **7개 전부 "예산 초과"** 로 죽었다 — 판정을 한
번도 못 냈다. 그래서 `collect.py:303` 의 `run.start_request()` 가 **A단계가 끝난 뒤에** 8초
스톱워치를 켠다. 두 예산은 **콜드 케이스를 망가지는 대신 느려지게** 만든 장치다.

### 3-2. Memorystore (관리형 Redis)

코드 변경 0이고 VM 의존을 끊는다. 그런데 **카운터가 갈라진다** — VM 의 backend 는 여전히 VM
Redis 를 보므로 하나로 두려면 backend 도 바꿔야 하고, 그러면 기준 ①이 깨진다. 그리고 크레딧이
11-17 에 만료되고 그 뒤는 자동 실비다(D-042). **다음 단계로 남겼다.**

### 3-3. Firestore 로 `Store` 를 새로 구현

`Store` 가 이미 **9개 메서드짜리 Protocol**(`cache.py:166`)이라 끼울 자리가 설계상 열려 있다.
VPC 도 VM 도 필요 없는 순정 서버리스이고 발표 주장이 가장 강해진다. 그런데 `reserve()` 의 원자적
증가를 트랜잭션으로 다시 써야 한다 — **8일에 새 저장소 구현은 위험하다.**

### 3-4. 공개 API + 자체 인증 / 무인증

`/life/walk-conditions` 의 인증이 `daengs_life` 안에 **없는 것이 일부러**다(`main.py:274` —
*"저쪽이 `daengs_backend.core.deps` 를 import 하면 의존 방향이 뒤집혀 단독 앱과 CLI 가 이 레포의
인증 없이는 못 도는 물건이 된다"*). 그래서 떼어낸 서비스는 **로그인이 뭔지 모르는 상태로
태어난다.**

- **자체 인증을 새로 붙이는 것**은 D-018 이 막은 방향이고, 앱·프런트에 새 주소를 박아 재배포해야
  하고 CORS 도 열어야 한다.
- **무인증 공개**는 개인정보는 안 새지만(좌표만 받아 공개 날씨를 답한다) **누구나 우리 기상청
  키로 하루 1,000회를 태울 수 있다.** 주소는 곧 알려진다.

### 3-5. `daengs_realtime` 새 패키지 신설

`place-search` · `journey-service` 와 같은 모양이 되고 트리에서 보인다. 그런데 import 경로 전부
변경 · 테스트 전부 변경 · 의존 방향 가드 재작성이고, **완전히 독립하지도 못한다** —
`crawler.core.config` 를 여전히 부르거나 그 20줄을 복사해야 한다.

그리고 `daengs_place` 는 **상류 저장소에서 통째로 들여온 것**이라 처음부터 별 패키지였다. 있는
패키지를 가르는 것과 조건이 다르다.

**발표에 나오는 것은 구성도와 Cloud Run 콘솔이지 파일 트리가 아니다.**

### 3-6. `--group realtime` (base 의존성까지 받기)

처음 제안이 `uv sync --frozen --no-install-project`(base 전부)였는데 **사람이 지적해서 뒤집혔다** —
*"기본 의존성이 변경될 수도 있잖아. 아예 그룹으로 빼면 더 편한 거 아니야?"*

맞다. base 에는 realtime 이 안 쓰는 것이 스무 개쯤 있고 **앞으로 늘어난다.** 다른 팀원이 base 에
무거운 것을 넣으면 realtime 이미지가 따라 커지는데 **그 사람은 그 사실을 모른다.**

그리고 내가 못 본 이득이 하나 더 있었다 — 그룹으로 적으면 **경계가 기계가 읽는 형태로 남는다.**
지금 "realtime 이 쓰는 것은 여섯"은 조사 결과이고 문서에도 없다. `gait` 그룹이 정확히 같은
논리로 만들어졌다(`pyproject.toml:158`).

`--only-group` 선례도 이미 있었다 — `docker-compose.yml:467` 의 `gait-v4`.

## 4. 설계 요약 (본문은 realtime-service.md)

| | |
| --- | --- |
| 경계 | `realtime/` + walk·weather 의 controller·service·dto + `deps` 의 `get_cache`·`get_now` |
| 남는 접점 | 넷 중 **`ask` 하나만** in-process. `main.py` 의 `get_cache` 두 줄은 **없어진다** |
| 갈림길 | `DAENGS_REALTIME_URL` — 비면 in-process(개발서버), 값 있으면 HTTP(GCP) |
| 되돌리기 | 그 변수를 지우고 backend 를 다시 만든다 |
| 이미지 | `uv sync --frozen --only-group realtime --no-install-project` + `PYTHONPATH` + **코드를 굽는다** |
| 상태 | VM 의 `10.178.0.2:6379`, Direct VPC egress, 방화벽 규칙 한 줄 |
| 인증 | `--no-allow-unauthenticated` + backend 의 ID 토큰 |

### 왜 이 카드가 싸게 끝나는가 — 접점 넷이 다 이음새를 갖고 있다

| # | 접점 | 이음새 |
| --- | --- | --- |
| 1 | `/life/walk-conditions` | 등록 시점 의존성 주입 (`main.py:292`) |
| 2 | 어시스턴트 산책 | 생성자 주입 (`adapters/walk.py:26`) |
| 3 | finalize 날씨 | FastAPI 의존성 (`routers/walk.py:43`) |
| 4 | `ask` | 그대로 둔다 |

**3번이 제일 위험해 보였는데 이미 안전했다.** 사용자의 산책 기록을 확정하는 트랜잭션인데,
실패를 두 겹으로 잡는다 — `services/walk.py:337` 과 `adapters/life.py:153`. 후자의 주석이
*"날씨 보강은 Walk 봉인을 실패시키지 않는다"* 다. HTTP 타임아웃도 그 자리로 들어간다.

**이것이 D-021 이 예고한 2단계다.** `main.py:67` 이 *"이사가 싼 채로 남으려면 접점이 이 세 줄을
넘으면 안 된다"* 고 적어 뒀고, 예측이 맞았다 — 셋이 넷이 된 것만 빗나갔다(오케스트레이션이
뒤에 들어왔다).

## 5. 세 단계 경계 — 발표에서 쓸 수 있는 이야기

논의 중 사람이 물었다 — *"이렇게 부르면 패키지 경계 나누는 게 필요가 있어?"* 반쯤 맞는
의심이라 여기 남긴다.

**경계가 주는 것**: 방향이 하나다(`daengs_life` → `daengs_backend` import **0건**, 기계가 지킨다 —
`test_import_direction.py` · `test_import_direction_packages.py` · `test_main_stays_light.py`).
접점이 넷이고 다 이름이 있다.

**경계가 안 주는 것**: 같은 프로세스 · 같은 venv · 같은 lock · 같은 배포 단위다. 즉 지금 경계는
**격리를 주지 않고 「옮길 수 있음」을 준다.**

그래서 이 저장소에는 경계가 **세 단계로 나란히** 있고, 각 단계에 머문 이유가 문서에 있다.

| 단계 | 실물 | 왜 그 단계인가 |
| --- | --- | --- |
| ① 패키지 경계 | `daengs_life` · `daengs_gait` | 팀 작업 분리 + 옮길 수 있게. 격리는 없다 |
| ② 프로세스 경계 | `place-search` · `journey-service` | 자기 DB(PostGIS)가 있다 — D-026 · D-039 |
| ③ 서버리스 경계 | **이 카드** | DB·모델·파일이 없다 |

**「전부 마이크로서비스로 갈랐다」보다 「무엇을 어느 단계에 두고 왜 그랬는지」가 어려운
이야기다.** 한 문장으로 하면 — *경계를 미리 그어 두면 MSA 전환이 이사가 되고, 안 그으면
재작성이 된다. 이번 이사 비용이 접점 넷이었다.*

## 6. 범위 밖 · 후속

| | 왜 |
| --- | --- |
| **8초 · 30초 예산 변경** | 같이 바꾸면 「분리 때문인가 값 때문인가」를 못 가린다. 판단 자체는 근거가 있다 — 개별 호출 5초 · 재시도 2회 · 백오프 0.5→1.5초라 최악 17초인데 8초가 자르므로 **재시도가 실질 한 번만 돈다**(`transport/base.py:124`). nginx 는 60초를 준다 |
| **프리페치 부활** | Cloud Scheduler → 데우기 엔드포인트. **선택 항목.** 지금은 아무 데서도 안 돈다(§1-2) |
| Memorystore 이전 | §3-2 의 다음 단계 |
| 개발서버 분리 | 「분리 전 / 분리 후」를 나란히 보이려고 **일부러** 안 한다 |
| CLAUDE.md 「접점 세 줄」 갱신 | 실제로는 넷이다. 구현 때 코드와 같이 고친다 |

## 7. 근거

| 무엇 | 어디 |
| --- | --- |
| 운영 설계 · 배포 플래그 · 검증 일곱 | `docs/deploy/realtime-service.md` |
| 결정과 되돌리기 비용 | `docs/decisions.md` **D-070** |
| 이 카드가 2단계인 이유 | `docs/decisions.md` D-021 · `daengs_backend/main.py:62~78` |
| 인증을 라우터에 안 넣은 이유 | `daengs_backend/main.py:274~292` · D-018 |
| Cloud Run 을 기각했던 근거(서빙 전체) | `docs/decisions.md` D-042 |
| Direct VPC egress 선례 | `infra/gcp/pipeline.sh:103~108 · 179` · D-062 |
| realtime 엔진 설계(층 · 캐시 · 저하) | `docs/life/decisions-realtime.md` RT-001 |
| 예산·발표주기의 실측 | `docs/life/realtime-apis.md` §6 · `backend/src/daengs_life/realtime/cache.yaml` |
| 정적 17.4초 사고 | `backend/src/daengs_life/realtime/collect.py:93~97` |
| 프리페치 소비자가 없다는 사실 | `docker-compose.yml:748~755` · `backend/src/daengs_life/tasks/celery_app.py:40~55` |
| `--only-group` 선례 | `docker-compose.yml:467` |
