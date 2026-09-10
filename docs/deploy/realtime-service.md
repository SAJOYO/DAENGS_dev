# 실시간 산책·날씨 Cloud Run 서비스 — 설계

**작성 2026-09-11 (#435) · 결정 번호 D-068 · 구현 미착수.**

`daengs_life/realtime/`(산책 적합도 · 과거 날씨)을 **Cloud Run 서비스로 떼는 설계**다. 여기에는
**설계와 결정 근거**만 둔다 — `corpus-pipeline.md`(D-062)와 같은 자리이고, 명령 절차는 구현 카드가
`runbook.md` 에 더한다.

⚠ **이 문서는 아직 도는 것을 적은 것이 아니다.** §7 의 검증 일곱 중 **①②가 안 되면 이 설계는
성립하지 않는다.** 그 둘은 아직 확인되지 않았다.

## 0. 한 줄

저장소에서 **유일하게 DB·모델·파일이 없는 서빙 코드**를 떼어, 아무도 안 부르면 인스턴스가 0개인
서버리스 서비스로 돌린다. 상태(Redis)는 옮기지 않고 VM 에 그대로 둔다.

## 1. 목적과 판단 기준

목적은 사람이 골랐다 — **발표(It8 · 09-19)에서 「모놀리스에서 서버리스 서비스 하나를 떼어
scale-to-zero 로 돌린다」를 실물로 보이는 것.**

그래서 판단 기준이 둘로 좁는다.

| | 기준 | 빠진 것 |
| --- | --- | --- |
| ① | **지금 돌아가는 것을 안 깨뜨린다** | 성능 최적화 — realtime 은 지금 느려서 문제가 된 적이 없다 |
| ② | **갈랐다는 것이 문서가 아니라 실물로 보인다** | 비용 절감 — VM 은 이미 있고 크레딧으로 돈다 |

**이것은 D-021 이 예고한 2단계다.** 그쪽이 `/life/ask` 의 임베딩 모델을 배포되는 API 프로세스에
상주시키기로 정하면서 이렇게 적어 뒀다:

```
daengs_backend/main.py:67
2단계에서 조건이 오면 daengs_life.app.main:app 을 따로 띄우고 이 자리를 게이트웨이로
바꿉니다. 이사가 싼 채로 남으려면 접점이 이 세 줄을 넘으면 안 됩니다.
```

다만 **접점은 세 줄이 아니라 넷이다** — §3 참조. 오케스트레이션(D-035)이 뒤에 들어오면서 셋이
늘었고 CLAUDE.md 가 그것을 안 받아 적었다.

## 2. 뗄 수 있는 이유 — realtime 이 들고 갈 것이 없다

2026-09-11 에 import 를 전수 조사했다.

| 무엇 | realtime 이 쓰나 |
| --- | --- |
| 라이브러리 | `httpx` · `redis` · `pyyaml` · `pydantic-settings` · `fastapi` — **여섯**(+`tzdata` 간접) |
| DB | **없음.** SQLAlchemy · psycopg · asyncpg 를 한 줄도 안 쓴다 |
| 모델 | **없음.** torch 없음 |
| 파일 | **없음.** 표 셋(`warning_areas.csv` · `thresholds.yaml` · `cache.yaml`)이 **코드 옆**에 있다 |
| `data/reference/` | 지금 비어 있고 **`REFERENCE_DIR` 을 실제로 읽는 코드가 없다** |
| 같은 저장소 | `daengs_life.crawler.core.config` 한 줄 (`KST` · `.env` 병합 순서 · `DATA_DIR`) |

마지막 줄이 유일한 사내 의존이고, **데이터 경로가 없어도 안 터진다** — `_find_repo_root()` 가
`None` 을 돌려주고 `DATA_DIR` 도 `None` 이 된다 (`crawler/core/config.py:36` 이 그 이유를 적어 뒀다).

## 3. 경계 — 무엇이 가고 무엇이 남나

### 가는 것

| 무엇 | 크기 |
| --- | --- |
| `daengs_life/realtime/` 전체 | 25개 파일 · 약 4,900줄 |
| `app/controllers/walk.py` · `weather.py` | 라우터 둘 |
| `app/services/walk.py` · `weather.py` | 유스케이스 둘 |
| `app/dto/walk.py` · `weather.py` | 응답 계약 |
| `app/deps.py` 의 `get_cache` · `get_now` | **이 둘은 realtime 만 쓴다** (실측 — `ask` 는 안 쓴다) |

### 남는 것

`rag/` · `crawler/` · `jobs/` · `tasks/` · `app/controllers/ask.py` · `daengs_backend` 전부.

### 접점은 넷이고 전부 이음새를 갖고 있다

| # | 접점 | 이음새 | 분리 후 |
| --- | --- | --- | --- |
| 1 | `main.py:78·292` — `/life/walk-conditions` 라우터 등록 | **등록 시점 주입** (`dependencies=[Depends(admin_or_app_user(Perm.READ))]`) | 등록을 지우고 `daengs_backend` 에 **인증 + 전달만** 하는 라우터를 둔다 |
| 2 | `main.py:81·112·169` — `get_cache` | lifespan 두 줄 | **없어진다.** backend 프로세스가 realtime 캐시용 Redis 연결을 안 잡는다 |
| 3 | `adapters/walk.py:26` — 어시스턴트 산책 능력 | **생성자 주입** `WalkCapabilityAdapter(walk=...)` | 기본값을 HTTP 호출로 |
| 4 | `routers/walk.py:43` — finalize 의 과거 날씨 | **FastAPI 의존성** `get_walk_weather_lookup()` | 그 함수가 HTTP 클라이언트를 돌려주게 |

`adapters/life.py:77` 의 `ask` 는 **in-process 로 그대로 남는다** — RAG 는 이 카드에서 안 옮긴다.

### 4번이 위험해 보이지만 이미 안전하다

`POST /app/walks/{id}/finalize` 는 사용자의 산책 기록을 **확정하는** 트랜잭션이다
(`activity_game.acquire` 로 잠금까지 잡은 상태). 거기에 네트워크 홉이 생기면
"날씨 서비스가 느려서 산책 저장이 실패했다"가 될 수 있다.

**그 방어가 이미 두 겹이다.**

```
services/walk.py:331   """외부 관측의 어떤 실패도 산책 분석 트랜잭션 밖으로 새지 않게 한다."""
services/walk.py:337   except Exception:  # 주입된 adapter도 같은 저하 계약을 지킨다
adapters/life.py:153   except Exception:  # 날씨 보강은 Walk 봉인을 실패시키지 않는다
```

HTTP 타임아웃도 그 `except` 로 들어간다. 날씨 칸만 `status="failed"` 로 남고 산책은 정상 저장된다.
**계약이 이미 이 상황을 덮고 있어서 새로 만들 안전장치가 없다.**

### 되돌리기는 환경변수 하나

넷이 다 이음새를 가지므로 설정 하나로 갈라 둔다.

| `DAENGS_REALTIME_URL` | `/life/walk-conditions` | 어시스턴트 산책 | finalize 날씨 |
| --- | --- | --- | --- |
| 비어 있음 (**개발 PC · 개발서버 기본**) | 지금처럼 라우터 등록 | in-process | in-process |
| 값 있음 (**GCP**) | 프록시 | HTTP | HTTP |

두 가지를 준다. ① **되돌리기가 변수 하나**다 — 지우고 backend 를 다시 만들면 분리 전이다.
② 발표에서 **「분리 전(개발서버) / 분리 후(GCP)」를 나란히** 보일 수 있다. 그래서 개발서버는
**일부러 안 바꾼다.**

## 4. 조율 상태 — Redis 를 옮기지 않는다 (안 A)

### Redis 에 사는 것 다섯

| 키 | 무엇 | 프로세스를 넘어 살아야 하는 이유 |
| --- | --- | --- |
| `rt:<feed>:<조회키>` | 기상청·에어코리아 **응답 원본** | 값 캐시. TTL = 발표주기 × 4 |
| `rt-snapshot:<feed>:<조회키>` | **과거 관측 회차** (finalize 용) | 같은 격자·회차를 사람마다 다시 안 부르게 |
| `rt:budget:<그룹>:<날짜>` | **일 호출 카운터** (48h 만료) | 프로세스 메모리면 재시작마다 리셋 → 1,000회/일 제약이 안 지켜진다 |
| `rt:active` | 최근 **실제로 요청된** 조회 키 (24h) | 프리페치가 "무엇을 데울지" 읽는 자리 |
| `rt:lock:<키>` | single-flight 락 (`SET NX`, 20초) | 동시 요청이 같은 API 를 N번 부르는 것을 막는다 |

한도는 **활용신청(API)별**이다 — `datagokr-vilage-fcst` 1,000(병목: 실황+초단기+단기가 한 서비스) ·
`datagokr-airkorea` 1,000 · `datagokr-warning` 1,000 · `datagokr-stations` 1,000 ·
`apihub`·`kakao` 는 `null`(**"모른다"이지 "무제한"이 아니다**). 과거 관측은 그중 **560회를 미리
예약**한다 (`snapshot.live_reserve_calls`).

### 기각: 공유 상태 없이 프로세스 메모리로 — **근거가 실측이다**

`Cache` 에는 Redis 가 없을 때 프로세스 메모리로 떨어지는 길이 설계상 있다 (RT-001 ④-c). 그래서
"Cloud Run 은 그걸 쓰면 되지 않나"가 자연스럽지만 **scale-to-zero 와 정면으로 충돌한다.**

측정소 목록의 발표 주기가 **30일**이다 (`cache.yaml:63`). Redis 가 그것을 한 달간 들고 있어서
지금은 아무도 다시 안 받는다. 공유 캐시가 없으면 **콜드 인스턴스마다 시도 17개를 다시 받는다.**

| 깨지는 것 | 근거 |
| --- | --- |
| 응답 시간 | 정적 수집 **17.4초 실측** (2026-08-25, `collect.py:95`). 요청 예산 8초는 그것이 끝난 **뒤에** 시작하므로 사용자는 20초를 기다린다 |
| 일 1,000회 | 콜드 스타트 횟수 × 17회. 하루 60번만 식으면 한도를 넘는다. **scale-to-zero 는 인스턴스가 사라지는 것이 정상**이라 그 횟수를 미리 알 수 없다 |

**즉 zero-to-scale 이 성립하는 조건 자체가 "상태가 프로세스 밖에 있다"이고, 그 상태가 이미 밖에 있다.**

### 안 셋과 고른 것

| | 안 | 코드 변경 | 대가 |
| --- | --- | --- | --- |
| **A ✅** | **VM 의 Redis 를 그대로 쓴다** (Direct VPC egress) | **0** — `REDIS_URL` 환경변수 하나 | "서버리스"가 VM 에 의존한다. **다만 GCP 서빙 전체가 그 VM 이라 오늘 잃는 것은 없다** |
| B | Memorystore 를 새로 띄운다 | 0 | ① **돈** (크레딧 11-17 만료 후 자동 청구 — D-042) ② **카운터가 갈라진다** — VM 의 backend 는 여전히 VM Redis 를 보므로, 하나로 두려면 backend 도 바꿔야 하고 그러면 기준 ①이 깨진다 |
| C | Firestore 로 `Store` 를 새로 구현 | ~100줄 | VPC 도 VM 도 필요 없는 순정 서버리스이지만, `reserve()` 의 원자적 증가를 트랜잭션으로 다시 써야 한다. 8일에 새 저장소 구현은 위험 |

**A 를 골랐다.** 「zero to scale」 주장은 인스턴스가 0에서 뜨고 자동으로 늘어난다는 것으로 이미
성립하고, B·C 는 "상태를 어디에 두나"를 한 칸 더 옮기는 일이라 발표 그림이 안 달라진다.
**B(또는 C)는 다음 단계로 남긴다** — VM 의존을 끊는 자리다.

### 길은 이미 운영에서 돌고 있다

| 확인한 것 (2026-09-11 `gcloud` 실측) | 값 |
| --- | --- |
| VM | `daengs` · **`10.178.0.2`** · 서브넷 `default` · asia-northeast3 |
| 서브넷 범위 | `10.178.0.0/20` |
| 코퍼스 잡의 연결 방식 | `--network=default --subnet=default --vpc-egress=private-ranges-only` (`pipeline.sh:179`) |
| 방화벽 | `allow-pg-from-run` — `10.178.0.0/20` → VM **tcp:5432** (`pipeline.sh:106` 이 만든다) |
| Cloud Run 서비스 | **0개.** 이 프로젝트에는 아직 잡만 있다 (`corpus-refresh` · `corpus-embed-full` · `credit-report`) |

**커넥터가 아니라 Direct VPC egress** 다 — 인스턴스가 내 서브넷의 IP 를 받아 VM 과 같은 네트워크의
이웃이 된다. `private-ranges-only` 라 사설 대역만 VPC 로 가고 기상청 호출은 평소대로 인터넷으로
나간다 (그래서 Cloud NAT 가 필요 없다).

6379 는 **규칙 한 줄**만 더한다:

```bash
gcloud compute firewall-rules create allow-redis-from-run --network=default \
  --direction=INGRESS --action=ALLOW --rules=tcp:6379 --source-ranges=10.178.0.0/20
```

`default-allow-internal` 이 `10.128.0.0/9` 에서 tcp 전 포트를 허용하고 `10.178.0.0/20` 이 그 안에
들어가므로 **이미 덮여 있을 수 있다.** 그래도 5432 를 그렇게 한 것과 같은 이유로 명시 규칙을 만든다.
출처가 서브넷으로 못박혀 **인터넷에서는 여전히 못 닿는다** — D-042 의 *"6379 를 인터넷에 안 연다"*
가 그대로 지켜진다.

### 곁가지 — 카운터는 이미 환경별로 갈라져 있다

개발서버와 GCP 가 각자 자기 Redis 를 쓰므로 `datagokr-*` 카운터는 **이미 두 벌**이다. 이 카드가
만든 문제가 아니고, Cloud Run 이 GCP 의 Redis 를 쓰면 **GCP 쪽 한 벌은 유지**된다.

## 5. 인증 — IAM 만 (ⓐ)

### 문제: 로그인 검사 코드가 안 따라간다

`/life/walk-conditions` 의 인증은 `main.py:292` 한 줄이고, 그것이 `daengs_life` 안에 **없는 것이
일부러**다:

```
main.py:274~278
인증은 라우터가 아니라 여기서 겁니다. walk.router 는 daengs_life 것이고, 저쪽이
daengs_backend.core.deps 를 import 하면 의존 방향이 뒤집혀 daengs_life.app.main(단독
ASGI 앱)과 python -m daengs_life.realtime walk CLI 가 이 레포의 인증 없이는 못 도는
물건이 됩니다 (RAG-001 원칙 1 · D-018).
```

**즉 떼어낸 서비스는 로그인이 뭔지 모르는 상태로 태어난다.**

### 안 셋과 고른 것

| | 안 | 앱·프런트 변경 | 대가 |
| --- | --- | --- | --- |
| **ⓐ ✅** | **IAM 만 — backend 가 게이트웨이.** 인터넷에 안 열고, backend 가 로그인을 검사한 뒤 Google 서명 ID 토큰을 붙여 부른다 | **0** — 주소도 코드도 그대로 | 요청이 여전히 VM 을 통과한다. 발표에서 물으면 *"인증은 게이트웨이가 하고 계산은 서버리스가 한다"* 가 정직한 답이다 |
| ⓑ | 공개 + 자체 인증. 앱이 직접 부른다 | 새 주소를 박아 **재배포** + CORS | 인증을 새로 써야 하는데 **D-018 이 막은 방향**이다. 8일에 넣을 일이 아니다 |
| ⓒ | 공개 + 무인증 | 0 | **기각.** 좌표만 받아 공개 날씨를 답하니 개인정보는 안 새지만, **누구나 우리 기상청 키로 하루 1,000회를 태울 수 있다.** 주소는 곧 알려진다 |

`--no-allow-unauthenticated` 가 **`gcloud run deploy` 의 기본값**이라, ⓐ 는 플래그를 빼먹어도
안전한 쪽으로 기운다.

### 선례

VM 의 backend 가 Cloud Run 잡을 부를 때 **키 파일 없이** 메타데이터 서버 인증(ADC)을 쓴다 —
`services/cloudrun_jobs.py`, #326 에서 실물로 돌았다. 서비스 호출은 클라이언트가 다르지만
(잡은 `run_v2`, 서비스는 대상 URL 을 audience 로 한 **ID 토큰**) **자격 증명 출처가 같다.**

## 6. 새 서비스의 모양

### 6-1. 새로 만드는 것 일곱

| # | 파일 | 하는 일 |
| --- | --- | --- |
| 1 | `backend/src/daengs_life/app/realtime_main.py` | **realtime 전용 ASGI 앱.** walk · weather 라우터만. 임베딩 예열 없음 |
| 2 | `backend/pyproject.toml` 의 `[dependency-groups] realtime` | §6-3 |
| 3 | `docker/realtime/Dockerfile` + `Dockerfile.dockerignore` | 이미지 |
| 4 | `infra/gcp/realtime.sh` | 배포 스크립트 (`pipeline.sh` 와 같은 모양) |
| 5 | `backend/src/daengs_backend/services/realtime_client.py` | ID 토큰 발급 + HTTP 호출 |
| 6 | `backend/src/daengs_backend/routers/life_walk.py` | `/life/walk-conditions` 프록시 (인증 + 전달만) |
| 7 | `backend/src/daengs_backend/config.py` 에 `realtime_url: str = ""` | §3 의 갈림길 |

**기존 `app/main.py` 는 손대지 않는다.** 그쪽은 `ask` 까지 등록하는데 새 이미지에는 `ml`(torch)이
없어서 import 가 깨질 수 있고, 안 깨져도 realtime 서비스가 RAG 코드를 들고 있는 것은 분리의
취지에 안 맞는다. 개발 PC 에서 셋 다 띄우는 길(`uvicorn daengs_life.app.main:app`)은 그대로 남는다.

### 6-2. 경로는 바꾸지 않는다

| 새 서비스가 내는 것 | 지금과 같은가 |
| --- | --- |
| `GET /life/walk-conditions?lat=&lon=` | **같다** |
| `POST /weather/at` | **같다** (지금은 `daengs_backend` 에 등록 안 된 상태 — realtime/README 가 일부러 그렇게 뒀다) |

경로가 같으면 프록시가 **순수 통과**가 되고, 변환이 없으면 틀릴 자리도 없다.

⚠ **프록시는 503 본문을 그대로 넘겨야 한다.** `controllers/walk.py:37` 이 판정 불가일 때 503 을
주면서 **응답 본문 전체를 `detail` 에 싣는다** — 어느 출처가 죽었는지(`sources`) 보이게 하려는
것이고 프런트가 그것을 안다 (`ask-inspect.tsx:42`). 흔히 하듯 502 로 뭉개면 **콘솔 패널이 조용히
망가진다.**

### 6-3. 의존성은 자기 그룹만 받는다

```toml
realtime = [
    "fastapi[standard]>=0.141.1",   # 앱 + uvicorn
    "httpx>=0.28.1",                # transport/base.py
    "pydantic-settings>=2.15.0",    # realtime·crawler.core 의 config
    "pyyaml>=6.0.3",                # cache.yaml · thresholds.yaml
    "redis>=6.0",                   # 캐시 저장소 (지연 import)
    "tzdata>=2026.3",               # ZoneInfo("Asia/Seoul")
]
```

**`--group` 이 아니라 `--only-group` 이다** — 전자는 base 의존성까지 받고, 후자는 그 그룹만 받는다.
선례가 있다: `docker-compose.yml:467` 의 `gait-v4` 가 `--only-group gait-v4 --no-install-project` 로 돈다.

**base 를 받지 않는 이유가 두 개다.** ① base 에는 realtime 이 안 쓰는 것이 스무 개쯤 있고
(`sqlalchemy` · `celery` · `langgraph` · `kiwipiepy` · `shapely` · `google-genai` …) **앞으로 늘어난다.**
다른 팀원이 base 에 무거운 걸 추가하면 realtime 이미지가 따라 커지는데 **그 사람은 그 사실을 모른다.**
② 그룹으로 적으면 **경계가 기계가 읽는 형태로 남는다** — `gait` 그룹이 같은 논리로 만들어졌다
(`pyproject.toml:158`).

⚠ **`tzdata` 가 함정 자리다.** 코드가 `import tzdata` 를 하지 않는다 —
`zoneinfo.ZoneInfo("Asia/Seoul")` 이 간접적으로 쓴다. 그래서 전수 조사에서 놓치기 쉽고, 빠지면
import 는 되는데 시간대 조회에서 터진다. **리눅스 slim 이미지에 정말 필요한지는 §7 ⑦이 확인한다.**

⚠ **놓친 패키지는 빌드가 아니라 런타임 import 에서 터진다.** 그래서 §7 에 스모크 테스트가 있다.

### 6-4. 이미지 — 코드를 굽는다 (잡과 다르다)

```dockerfile
FROM python:3.12-slim
# uv (docker/uv/Dockerfile 의 uv:1 을 베이스로 쓸 수 있는지 구현 때 확인)
ENV PYTHONPATH=/app/src
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --only-group realtime --no-install-project
COPY backend/src/daengs_life ./src/daengs_life
CMD uvicorn daengs_life.app.realtime_main:app --host 0.0.0.0 --port ${PORT}
```

`--no-install-project` + `PYTHONPATH` 는 **#427 이 찾은 것과 같은 수**다 — `pyproject.toml` 의
`[tool.uv.build-backend] module-name` 이 여덟 패키지를 나열해서, 일부만 이미지에 넣으면 설치
단계가 깨진다. 프로젝트를 아예 설치하지 않고 경로만 알려 주면 그 제약이 사라진다.

**#427 과 다른 한 곳은 코드를 어디서 받는가다.**

| | #427 (잡) | 이번 (서비스) |
| --- | --- | --- |
| 코드 | GCS 에서 받는다 (기동 때 복사) | **이미지에 굽는다** |
| 이유 | 코드 배포에서 이미지 굽기를 없애려고 | ① scale-to-zero 는 **콜드 스타트가 잦은데** 거기에 파일 복사가 붙으면 그만큼 느려진다 ② 서비스는 "지금 어느 코드가 도는가"가 **배포 단위와 같아야** 롤백이 된다 |

### 6-5. 배포 플래그와 근거

```bash
gcloud run deploy daengs-realtime --region=asia-northeast3 \
  --image="${IMAGE}" --service-account="${SA_EMAIL}" \
  --no-allow-unauthenticated \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --min-instances=0 --max-instances=5 \
  --cpu=1 --memory=512Mi --concurrency=40 --timeout=60s \
  --set-secrets="REDIS_URL=realtime-redis-url:latest,\
DATA_GO_KR_KEY=corpus-data-go-kr-key:latest,\
KAKAO_REST_KEY=realtime-kakao-key:latest,\
KMA_HUB_KEY=realtime-kma-hub-key:latest"
```

| 플래그 | 값 | 근거 |
| --- | --- | --- |
| `--min-instances` | **0** | **이 한 줄이 「zero to scale」이다.** 아무도 안 부르면 인스턴스가 없다 |
| `--max-instances` | 5 | 카운터는 Redis 가 공유하므로 안전하지만 상한은 걸어 둔다 |
| `--timeout` | 60s | 최악(정적 30초 + 판정 8초)을 덮고 nginx 의 60초(`gcp.conf:78`)와 맞춘다 |
| `--concurrency` | 40 | 기본 80 을 낮춘다 — 요청 하나가 `ThreadPoolExecutor` 로 스레드 여러 개를 쓴다. **§7 ③ 뒤에 조정** |
| `--memory` | 512Mi | 모델도 DB 풀도 없다. **§7 ③ 뒤에 조정** |
| `--vpc-egress` | `private-ranges-only` | Redis 는 VPC 로, 기상청은 인터넷으로 |

**`DAENGS_DATA_DIR` 은 주지 않는다.** 이미지에 저장소 루트가 없어서 `DATA_DIR` 이 자동으로 `None`
이 되고, realtime 은 파일을 안 읽으니 그게 맞는 값이다. 더미 경로를 주면 없는 폴더를 가리키는
설정이 하나 는다.

### 6-6. 비밀값

`DATA_GO_KR_KEY` 는 **이미 Secret Manager 에 있다** (`corpus-data-go-kr-key` — `pipeline.sh:97`).
새로 만들 것은 셋이다.

| 새 시크릿 | 담는 것 |
| --- | --- |
| `realtime-redis-url` | `redis://:<암호>@10.178.0.2:6379/0` — 암호가 들어 있으니 env 가 아니라 시크릿 |
| `realtime-kakao-key` | 카카오 Local (표기 전용 — 없어도 관통된다) |
| `realtime-kma-hub-key` | 기상청 API허브 (AWS 분자료 · 생활기상지수) |

### 6-7. backend 가 부를 때의 타임아웃

8초·30초는 **외부 API 를 부르는 시간**이라 어디서 돌든 같다. 새로 붙는 것은 하나다 —
**컨테이너 콜드 부팅.** 그래서 `realtime_client` 의 HTTP 타임아웃은 **15초**로 잡는다
(판정 8초 + 부팅 + 여유). 정적 수집이 도는 콜드 캐시는 그보다 오래 걸릴 수 있는데, 그때는
finalize 쪽이 `status="failed"` 로 닫고 산책은 저장된다 (§3).

## 7. 검증 — 일곱

**①②가 안 되면 이 설계는 성립하지 않는다.** 구현 카드가 이것부터 밟는다.

| # | 확인할 것 | 지금 |
| --- | --- | --- |
| ① | Cloud Run **서비스**(잡이 아니라)에서 Direct VPC egress 가 되나 | 🟡 잡에서만 확인됨 |
| ② | 그 인스턴스가 `10.178.0.2:6379` 에 실제로 닿나 | 🟡 5432 는 운영에서 확인됨. 6379 는 미확인 |
| ③ | 이미지 크기와 콜드 스타트 실측 | 🔴 추정만 |
| ④ | 503 본문이 프록시를 통과해 그대로 오나 (콘솔 패널) | 🔴 미확인 |
| ⑤ | VM 의 backend 가 ID 토큰으로 서비스를 부를 수 있나 | 🟡 잡 호출은 됨(#326). 서비스는 방식이 다르다 |
| ⑥ | `DAENGS_REALTIME_URL` 을 지우면 옛 경로로 정확히 돌아오나 | 🔴 미확인 |
| ⑦ | **스모크 — 이미지를 로컬에서 띄워 두 엔드포인트를 실제로 부른다** | 🔴 미확인. `--only-group` 에서 놓친 패키지를 잡는 유일한 그물 |

## 8. 안 하는 것 (범위 밖으로 못박는다)

| 안 하는 것 | 왜 |
| --- | --- |
| **8초 · 30초 예산 변경** | 같이 바꾸면 「분리 때문인가 값 때문인가」를 못 가린다. 판단 자체는 근거가 있다 — 개별 호출 5초 · 재시도 2회 · 백오프 0.5→1.5초라 최악 17초인데 8초가 자르므로 **재시도가 실질 한 번만 돈다** (`transport/base.py:124`). nginx 는 60초를 준다. **별도 항목** |
| **프리페치 부활** | 지금 `warm_active` 는 **아무 데서도 안 돈다** — Beat 가 1분마다 쏘지만 기본 `celery` 큐의 소비자가 없다 (`docker-compose.yml:748~755`). 분리하면 **Cloud Scheduler → 데우기 엔드포인트**로 Celery 없이 되살릴 수 있다. **선택 항목** |
| Memorystore 로 상태 이전 | §4 의 다음 단계 |
| 개발서버 분리 | 「분리 전 / 분리 후」를 나란히 보이려고 **일부러** 안 한다 (§3) |
| 앱 · 프런트 주소 변경 | 인증 ⓐ 를 골랐으니 변경 0 (§5) |
| `daengs_realtime` 새 패키지 신설 | 동결된 파트에 대규모 리네임이고, `crawler.core.config` 의존이 안 없어져 **완전히 독립하지도 못한다.** 발표에 나오는 것은 구성도와 Cloud Run 콘솔이지 파일 트리가 아니다 |
| CLAUDE.md 의 「접점 세 줄」 문장 갱신 | 실제로는 넷이다(§1). 구현 때 코드와 같이 고치는 것이 맞다 |

## 9. 이 파트는 동결 상태다

로드맵 §6 에서 사람이 **ⓐ 지금 상태로 동결**(발표 09-19까지)을 골랐다. 설계 문서는 코드를 안
건드리므로 동결과 충돌하지 않지만, **구현은 발표 뒤로 갈 수 있다.** 지금 설계해 두는 값은
**이 코드를 가장 잘 아는 시점이라는 것** 하나다.
