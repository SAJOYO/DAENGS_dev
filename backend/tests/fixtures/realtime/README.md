# tests/fixtures/realtime/

**검문소 B 의 재료** — provider 7개가 실호출 없이 파싱을 검증받는 원본 응답이다 (RT-001 구현 계획 6).

2026-08-25 10:07 KST, 서울 강남구(37.4979, 127.0276) → 격자 **(61,125)** 기준으로 12회 호출해 받았다.
수집기는 `scratchpad/probe.py` 이고 **레포에 없다** (ADR 의 "프로브" 절). 전송 층을 그대로 태웠으므로
분류·재시도도 실제 게이트웨이에서 함께 확인됐다.

## "실시간은 저장하지 않는다"와 부딪히지 않는다 — 확인 완료

`data/README.md` §5 의 근거가 명시적이다: *"10분 뒤면 틀릴 값을 **벡터로 박아두면** 계속 오답을
뱉는다"*. 그 규칙이 막는 것은 **`data/` 적재와 인덱싱**이다. 이 파일들은 `backend/tests/` 에 있는
**테스트 입력**이고 인덱싱되지 않으며 답변으로 나가지도 않는다 (RAG-012 · RT-001 검문소 B 주석).

**응답 본문만 담는다.** 키가 담긴 URL 은 저장하지 않고, 본문에 키 문자열이 있으면 `***` 로 치환한다.

## 파일

| 파일 | 오퍼레이션 | 모양(§6.3) | 비고 |
|---|---|---|---|
| `kma-vilage-fcst.ncst.json` | `getUltraSrtNcst` | **A** 격자 관측 1점 | 8건 · `obsrValue` · `T1H` |
| `kma-vilage-fcst.ultra.json` | `getUltraSrtFcst` | **B** 격자 예보 시계열 | 66건 · `fcstValue` · `T1H` 유지 |
| `kma-vilage-fcst.village.json` | `getVilageFcst` | **B** | **871건 · 72개 시각** · 기온이 **`TMP`** 로 개명 |
| `kma-warning.list.json` | `getWthrWrnList` | **C** 특보 | `title` 에 자연어 |
| `kma-warning.pwn.json` | `getPwnStatus` | **C** | `t6`·`t7` 이 자연어 덩어리 |
| `airkorea-stations.list.json` | `getMsrstnList` | 목록 | 서울 40곳 · `dmX` 가 **위도** |
| `airkorea-realtime.dnsty.json` | `getMsrstnAcctoRltmMesureDnsty` | **D** 측정소 값+등급 | 등급 2벌(`Grade`/`Grade1h`) |
| `airkorea-realtime.frcst.json` | `getMinuDustFrcstDspth` | **E** 권역 등급 예보 | `informGrade` 가 한 문자열 |
| `kma-apihub.aws.txt` | `nph-aws2_min` | typ01 텍스트 | **EUC-KR** · 전국 지점 · 결측 `-99.9` |
| `kma-apihub.stations.txt` | `stn_inf.php` | typ01 텍스트 | **EUC-KR** · AWS 745 지점 · `LON` 이 먼저다 · 2026-08-25 활용신청 승인 후 수집 (RT-002 ②-c) |
| `kma-life-index.senta.json` | `getSenTaIdxV3` | typ02 JSON | **`03 NO_DATA`** — 아래 |
| `kakao-local.region.json` | `coord2regioncode` | 행정동 | UX 표기용 |
| `kakao-local.transcoord.json` | `transcoord` | TM 좌표 | 대체 경로(우리는 자체 계산) |

## 이번 수집이 새로 잡아낸 것 (§6 에 없던 사실)

1. **포맷 파라미터 이름이 계열마다 다르다.** 기상청(`1360000`)은 `dataType=JSON`, 에어코리아
   (`B552584`)는 **`returnType=json`**. 같은 호스트·같은 봉투인데 이름만 갈린다. `dataType` 만
   보내면 `getMsrstnList` 가 **XML 로 성공**을 돌려주고, JSON 만 읽는 분류기는 그 성공을
   "봉투를 읽지 못했다"로 읽는다 → 전송이 둘 다 보내고, 판정은 XML 도 읽는다
2. **`numOfRows` 상한은 계열마다 다르다.** 기상청 단기예보 `1000` 은 4/4 정상(871건), 에어코리아
   `1000` 은 4번 중 2번 HTTP 504. §6.1 함정 3 은 **에어코리아 이야기였다.** 전역 100 상한은
   단기예보를 100건에서 자르고 T+24h 타임라인을 9시간으로 조용히 줄인다
3. **apihub typ02 는 실패를 data.go.kr 봉투로 낸다.** §6.1 은 실패 봉투가 두 계열 공통
   `{result:{status,message}}` 라고 적었는데 그건 인증·경로 실패다. 호출이 통한 뒤의
   `03 NO_DATA` 는 `{response:{header:{resultCode}}}` 로 온다 — `result` 만 보면 **값 없음이
   성공으로 통과**한다
4. **최근접 측정소는 `강남대로` 1.82km 이고 우리 격자 안이다** (61,125). §6.5 가 본 `강남구`
   측정소(1.9km)는 격자가 (61,126)으로 다르다 → ⑤-d 의 "가까우면"과 "같은 격자면"이 실제로 갈린다

## 다시 받을 때

- **비 오는 날** — `PCP`/`RN1` 의 구간 문자열(`'1.0mm 미만'` 등)을 아직 못 봤다. 이번에도 `'강수없음'`
  뿐이다. `parse_interval` 은 모르는 문자열에 **실패**하므로 그날 여기가 먼저 깨진다
- **`PTY` 5·6·7** — 초단기 계열이 쓴다고 알려져 있으나 실측은 `'0'` 뿐이다. `parse_precip_kind` 도 같다
- **`getSenTaIdxV3`** — 활용신청은 통과했고(403 이 아니다) `03 NO_DATA` 다. 유효한
  발표시각·지역 조합을 아직 못 찾았다 (§6.8 ③). 그 조합이 잡히면 이 픽스처를 정상 응답으로 교체
- **`getUVIdxV3`** — 오퍼레이션 단위 활용신청 대기 중이라 호출 자체를 안 했다
