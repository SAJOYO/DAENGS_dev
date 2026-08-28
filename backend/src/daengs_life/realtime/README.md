# realtime/

파트② **실시간 조회형** 엔진 — 산책 적합 날씨·미세먼지. 설계 전체는 `docs/decisions-realtime.md`
**RT-001**(하위 18결정 확정)에 있고, 그 근거는 전부 `docs/realtime-apis.md` **§6 실측**이다.

```
GPS ──geo──▶ 조회 키(격자·측정소·행정동) ──providers──▶ 공통 관측 모델 ──rules──▶ 산책 적합도
```

## 이 패키지가 crawler 를 안 닮는 지점

**저장하지 않는다.** 10분 뒤면 틀릴 값을 벡터로 박아두면 계속 오답을 뱉는다
(`data/README.md` §5, RAG-012). 그래서 크롤러 소스 모듈도 `.meta.json` 도 생기지 않는다.
`data/reference/` 에 두는 것은 측정소 목록 같은 **월 1회 갱신 정적 메타뿐**이다.

## 배치 — 층을 전송/소스로 가른다 (RT-001 ①)

```
realtime/
├── __main__.py       CLI: config · geo  (walk 이 7단계에서 붙는다)
├── config.py         Settings 키 3종 + 경로. 키는 읽는 즉시 정규화
├── transport/        ✅ ① 전송 층 — 인증·봉투·에러코드·재시도
│   ├── base.py         예산(8초)·재시도(2회)·네트워크 오류. **봉투는 안 연다**
│   ├── datagokr.py     serviceKey + resultCode 분기 + numOfRows≤100  ← 5종이 공유
│   ├── kakao.py        KakaoAK 헤더 + documents[]
│   └── kmahub.py       authKey + typ01 EUC-KR 텍스트 · typ02 JSON
├── providers/        ✅ ② 소스 층 — API 서비스 하나 = 모듈 하나 (7 모듈)
│   ├── kma_vilage_fcst.py   실황·초단기·단기 — `T1H`↔`TMP` 개명을 여기서 흡수
│   ├── kma_warning.py       `t6` 자연어 1,610자에서 내 특보구역 찾기
│   ├── kma_life_index.py    체감온도 대조표 · UV
│   ├── kma_apihub.py        AWS 분 단위 — **같은 격자 아니면 안 만든다**(⑤-d)
│   ├── airkorea_realtime.py 측정소 실시간(`Grade1h`) · 권역 예보(State)
│   ├── airkorea_stations.py 조회 키를 만드는 provider
│   └── kakao_local.py       표기 전용 — 실패해도 관통된다
├── collect.py        ✅ ★ 조립 — provider 들을 `Observations` 하나로 (RT-002 ②-a).
│                        **이 층이 아는 유일한 것은 호출 순서다** — providers 는 서로를 모르고
│                        rules 는 조회를 모른다. 9단계 `app/` 는 여기 위의 껍데기다
├── geo.py            ✅ WGS84 → LCC 격자 · 하버사인 최근접 · 같은 격자 판정(⑤-d)
├── observation.py    ✅ ★ 계약 — Q(23) · Measurement · State · ResolvedLocation ·
│                        ProviderResult · Observations + 조회 헬퍼(⑤-d → ②-c)
├── rules.py          ✅ 판정 — 축 3개 · 최악 우선 · ⑤-a 상한 · 타임라인/권장구간
├── thresholds.yaml   ✅ 임계 — **값마다 출처.** 비인용 넷은 `우리 선택` 으로 표시
├── cache.py          ✅ 신선도 — 조회 키(④-a) · TTL/재호출/`issued_at` 3분리(④-b) ·
│                        single-flight(④-d) · 일 예산(④-e) · stale(⑤-c). Redis 없어도 돈다
└── cache.yaml        ✅ 발표 주기 11종 — **④-b 실측에서 유도.** 비인용 숫자 셋은 `우리 선택`
```

프리페치(④-d)는 `realtime` 밖에 있다 — `backend/tasks/` 의 Celery Beat 다 (RAG-009 · RT-002 ②-b).
**여기서 Celery 를 알면 `python -m realtime walk` 가 브로커 없이는 안 돈다** (RAG-001 원칙 1).
그래서 태스크가 아는 것은 스케줄과 예산 판단뿐이고, 데우는 일은 `collect.warm` 이 한다 —
요청 경로와 **같은 함수**라 프리페치만 조용히 낡는 일이 없다.

**왜 기관 축이 아닌가** — 기상청 하나가 전송 계약 **셋**(data.go.kr · apihub typ01 · typ02)에
걸쳐 있다. 기관으로 자르면 `kma.py` 안에 인증 2종·응답 포맷 3종이 들어간다. 경계가 아무것도
막지 못하면 그건 경계가 아니다.

**`core/` 는 두지 않는다** (RT-001 ①-3). `core/` 는 "N 개가 공유하는 것"의 자리인데 여기서
N 은 `providers/` 7개이고 걔들이 공유하는 것은 이미 `transport/` 다. 층이 겹친다.

**provider 는 API *서비스* 하나 = 모듈 하나다. 오퍼레이션 단위가 아니다.** 단기예보의
`getUltraSrtNcst`·`getUltraSrtFcst`·`getVilageFcst` 는 같은 봉투·같은 격자 입력이라 한 모듈 안의
함수 셋이다. 그래서 7개가 `docs/data-sources.md` §8 의 연동 체크 7개와 1:1 로 맞는다.

## 의존 방향

`app → realtime → crawler.core.config` 한쪽뿐이고, **그 하나뿐**이라는 것까지 기계가 막는다
(`tests/test_import_direction_packages.py`, RT-001 ①-2). 넓히면 `Fetcher`·`store` 가 따라 들어오고
그 순간 "실시간은 저장하지 않는다"가 흐려진다.

`rag` 는 허용이 둘(`config` + `textutil`)이라 목록이 패키지별로 갈려 있다. `tasks`·`app` 도 같은 폭으로 등재돼 있다.

서빙 층(`app/`)의 안쪽 배치는 **RAG-027** 이다 — 파트①의 `/ask` 와 공유하는 결정이라 `RT-` 가 아니라 `RAG-` 다.
`app/controllers/` 가 `realtime.rules`·`realtime.collect` 를 import 하는 것을 가드가 막는다: **로직은 `app/services/` 에.**

## 실행

```bash
cd backend
uv run python -m realtime config              # 키·경로가 실제로 읽히는지
uv run python -m realtime geo 37.4979 127.0276   # 위경도 → 격자·대표점
```

⚠️ `DATA_GO_KR_KEY` 는 포털이 **Encoding / Decoding 두 벌**로 준다. 맞는 것은 Decoding 쪽인데
어느 쪽을 넣어도 되게 `config.normalize_key()` 가 `%` 를 보고 1회 디코딩한다 (§6.1 함정 1).
위 CLI 가 `Encoding 키 → 디코딩함 (98자 → 88자)` 로 실제 동작을 보여준다.

## 실측이 이미 넘긴 제약 (§6)

- 값이 전부 **문자열**이고, 기온 이름이 `T1H`(실황) ↔ `TMP`(단기예보)로 **개명된다**
- 특보는 값도 상태도 아니라 **자연어 1,610자**다 — 특보구역명 매핑표가 `data/reference/` 에 하나 더 필요
- 대기질 **등급이 두 벌**이다 — `Grade`(24h 평균) vs `Grade1h`(현재). 산책은 `Grade1h`
- data.go.kr `05` 는 **HTTP 200 안에 숨어 있다.** status code 만 보면 성공으로 오인한다
- apihub 활용신청은 **오퍼레이션 단위**다 — 같은 서비스 아래에서도 `getUVIdxV3` 만 403 이다
- typ01 의 `help=1` 주석 헤더는 **EUC-KR** 이라 `r.text` 가 깨뜨린다 → `content.decode("euc-kr")`
- ⚠️ **개발계정은 API 당 일 1,000회.** 단기예보가 격자 17개뿐이라 운영계정 전환이 사실상 필수 (④-e)
