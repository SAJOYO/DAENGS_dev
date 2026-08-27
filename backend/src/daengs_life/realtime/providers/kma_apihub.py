"""기상청 API허브 AWS 매분자료 — ⑤-d 의 1순위 (§6.8 ②).

**가장 신선하고 가장 대표성이 약한 출처다.** 분 단위 실황이지만 관측소 *지점* 값이라 내 위치를
대표한다는 보장이 없다. ⑤-d 가 "가까우면 AWS" 가 아니라 **"같은 격자면 AWS"** 를 고른 이유이고,
그 판정을 이 모듈이 진다 — **같은 격자가 아닌 지점은 애초에 `Measurement` 를 만들지 않는다.**
그래서 `observation` 의 우선순위표는 임계 없이 성립한다.

**data.go.kr 한도를 안 쓴다.** apihub 소속이고 `stn=0` 로 전국을 1회 호출로 받으므로, 지점이
있는 격자는 초단기실황(24회/일)을 생략할 수 있다 — ④-e 가 "가장 큰 절감"이라고 한 대응이다.

⚠️ **매분자료 응답에는 지점 좌표가 없다.** `STN` 번호뿐이라 격자 판정을 하려면 지점 목록이
따로 필요하고, 그것이 같은 모듈의 `fetch_stations`(`stn_inf.php`)다. 활용신청이 **오퍼레이션
단위**라 이쪽만 따로 승인이 필요했는데 2026-08-25 승인됐다 (RT-002 ②-c). 그래도 `fetch` 는
좌표표를 **인자로 받는다** — 목록은 월 1회짜리 정적 메타라 캐시 수명이 완전히 다르고,
비어 있으면 이 provider 는 아무것도 못 낸 채 ⑤-d 가 2순위(격자 실황)로 내려간다.

**두 오퍼레이션이 한 모듈에 있는 것이 규칙대로다** — provider 는 API *서비스* 하나 = 모듈 하나이고
(RT-001 ①), `stn_inf.php` 와 `nph-aws2_min` 은 같은 typ01 봉투·같은 인증·같은 지점 축이다.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ..config import KST
from ..geo import Grid, LatLon, to_grid
from ..observation import Measurement, Q, Source
from ..transport import kmahub
from ..transport.base import Budget

PATH = "/api/typ01/cgi-bin/url/nph-aws2_min"
STATIONS_PATH = "/api/typ01/url/stn_inf.php"

# `# YYMMDDHHMI STN WD1 WS1 WDS WSS WD10 WS10 TA RE RN-15m RN-60m RN-12H RN-DAY HM PA PS TD`
# 열의 의미는 **이 오퍼레이션의 지식**이라 전송 층이 아니라 여기 있다 (kmahub.rows 주석).
TM, STN = 0, 1
COLUMNS: dict[int, Q] = {
    2: Q.WIND_DIR,      # WD1  1분 평균 풍향
    3: Q.WIND,          # WS1  1분 평균 풍속
    8: Q.TEMP,          # TA   1분 평균 기온
    14: Q.HUMIDITY,     # HM   1분 평균 상대습도
}


def parse_aws(text: str, coords: Mapping[int, LatLon], grid: Grid) -> list[Measurement]:
    """전국 분 단위 관측에서 **내 격자 안 지점만** 뽑는다.

    `coords` 는 `STN` → 좌표. 모르는 지점은 격자를 판정할 수 없으므로 건너뛴다 —
    "아마 여기일 것"으로 넘기면 ⑤-d 의 대표성 근거가 그 자리에서 무너진다.
    """
    out: list[Measurement] = []
    for row in kmahub.rows(text):
        if len(row) <= max(COLUMNS):
            continue
        try:
            stn = int(row[STN])
        except ValueError:
            continue
        point = coords.get(stn)
        if point is None or to_grid(point) != grid:
            continue
        when = _stamp(row[TM])
        if when is None:
            continue
        for index, q in COLUMNS.items():
            value = kmahub.value(row[index])
            if value is None:
                continue                 # 결측 `-99.9`. 0 으로 두면 영하도 무풍도 사실이 된다
            out.append(Measurement(
                quantity=q, value=value,
                valid_at=when, issued_at=when,      # 관측이라 시각이 하나다 (②-c)
                source=Source.AWS_MIN, spatial_ref=f"AWS {stn}"))
    return out


def _stamp(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw.strip(), "%Y%m%d%H%M").replace(tzinfo=KST)
    except (TypeError, ValueError):
        return None


# --- 받기와 파싱을 가른다 (RT-002 ②-a) ---------------------------------------
# 캐시가 저장하는 것은 **원본 응답**이라(`cache.py`) 조립층은 `raw_*` 로 받아 두었다가
# 히트일 때 같은 `parse_*` 를 다시 먹인다. URL·파라미터 지식은 provider 에 남는다.

def raw_aws(now: datetime, *, budget: Budget | None = None) -> str:
    return kmahub.get_text(PATH, {"tm": now.strftime("%Y%m%d%H%M"), "stn": 0, "disp": 0, "help": 0},
                           budget=budget)


def raw_stations(*, budget: Budget | None = None) -> str:
    return kmahub.get_text(STATIONS_PATH, {"inf": "AWS", "stn": 0, "help": 0}, budget=budget)


def fetch(coords: Mapping[int, LatLon], grid: Grid, now: datetime,
          *, budget: Budget | None = None) -> list[Measurement]:
    """`stn=0` — 전국을 한 번에. 격자별로 부르는 것보다 싸고, 그것이 ④-e 대응 1의 전제다."""
    if not coords:
        return []                        # 좌표표가 없으면 부를 이유가 없다. 예산을 아낀다
    text = kmahub.get_text(PATH, {"tm": now.strftime("%Y%m%d%H%M"), "stn": 0, "disp": 0, "help": 0},
                           budget=budget)
    return parse_aws(text, coords, grid)


# ------------------------------------------------------- 지점 목록 (`stn_inf.php`, RT-002 ②-c)

# `# STN LON LAT STN_SP HT HT_WD LAU STN STN_KO STN_EN FCT_ID LAW_ID BASIN LAW_ADDR`
# 뒤쪽 열은 이름이 붙은 채로 공백을 품는다(`서울특별시 서초구 서초동`). 앞에서부터만 읽는다.
S_STN, S_LON, S_LAT, S_NAME, S_LAW = 0, 1, 2, 8, 11


def parse_stations(text: str) -> dict[int, LatLon]:
    """`STN` → 좌표. `fetch` 가 그대로 먹는 모양으로 낸다.

    ⚠️ **열 순서가 `LON` 먼저다.** `airkorea_stations` 의 `dmX`(위도) 함정과 방향만 반대인 같은
    사고라, 여기서도 `LatLon(위도, 경도)` 로 **뒤집어서** 넣는다. 뒤집힌 쌍은 틀린 격자를
    내놓고 아무 예외도 안 낸다 — 검문소가 없으면 조용히 ⑤-d 1순위가 영영 안 걸린다.
    """
    out: dict[int, LatLon] = {}
    for row in kmahub.rows(text):
        if len(row) <= S_LAT:
            continue
        try:
            stn = int(row[S_STN])
            point = LatLon(float(row[S_LAT]), float(row[S_LON]))
        except ValueError:
            continue                     # 좌표가 없는 지점은 격자를 판정할 수 없다
        out[stn] = point
    return out


# 시도 정식명 → 에어코리아 `addr`·`informGrade` 가 쓰는 단축명 (실측 2026-08-25).
# **표를 만드는 대신 규칙으로 자르지 않는다** — `전북특별자치도`→`전북`, `충청북도`→`충북` 처럼
# 자르는 자리가 제각각이라 규칙으로 하면 반드시 하나가 틀린다. 17개뿐이라 표가 싸다.
SIDO_SHORT: dict[str, str] = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
    "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원",
    "충청북도": "충북", "충청남도": "충남",
    "전북특별자치도": "전북", "전라북도": "전북", "전라남도": "전남",
    "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주",
}


def sido_of(addr: str) -> str | None:
    """주소 첫 토큰 → 시도 단축명. 이미 단축명이면 그대로 돌려준다.

    **두 자리에서 같은 답이 필요하다** — AWS 지점표의 `LAW_ADDR`(정식명)로 어느 시도의 측정소
    목록을 받을지 고르는 자리, 그리고 측정소 주소로 미세먼지 예보 권역을 정하는 자리다.
    두 곳이 각자 자르면 한쪽만 `서울특별시` 로 남아 `informGrade` 와 영영 안 맞는다.
    """
    head = (addr or "").split()
    if not head:
        return None
    first = head[0]
    if first in SIDO_SHORT:
        return SIDO_SHORT[first]
    return first if first in set(SIDO_SHORT.values()) else None


def station_addrs(text: str) -> dict[int, str]:
    """`STN` → 시도 단축명. `LAW_ADDR` 은 공백을 품으므로 **뒤에서 자르지 않고** 그 자리부터
    끝까지를 주소로 본다.
    """
    out: dict[int, str] = {}
    for row in kmahub.rows(text):
        if len(row) <= S_LAW + 2 or not row[S_STN].isdigit():
            continue
        sido = sido_of(" ".join(row[S_LAW + 2:]))
        if sido:
            out[int(row[S_STN])] = sido
    return out


def station_names(text: str) -> dict[int, str]:
    """`STN` → 지점명. 값 판정에는 안 쓰고 `spatial_ref` 표기·디버깅용이다."""
    return {int(row[S_STN]): row[S_NAME]
            for row in kmahub.rows(text)
            if len(row) > S_NAME and row[S_STN].isdigit()}


def fetch_stations(*, budget: Budget | None = None) -> dict[int, LatLon]:
    """AWS 지점 목록 전국. **월 1회짜리 정적 메타다** (④-b 표) — 요청마다 부르면 안 된다.

    `airkorea_stations` 와 같은 방향의 provider 다 (②-a 근거 2): 룰이 읽을 값이 아니라
    **다음 호출의 인자**를 만든다.
    """
    text = kmahub.get_text(STATIONS_PATH, {"inf": "AWS", "stn": 0, "help": 0}, budget=budget)
    return parse_stations(text)
