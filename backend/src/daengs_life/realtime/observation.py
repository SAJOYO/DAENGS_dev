"""공통 관측 모델 — **provider 와 판정 룰 사이의 계약** (RT-001 ②).

이 파일이 있는 이유는 하나다: 이것이 없으면 룰이 7개 API 의 스키마를 직접 알게 되고, API 하나가
`T1H` 를 `TMP` 로 바꾸는 날 룰이 깨진다. 실제로 **같은 기온이 실황에서는 `T1H`, 단기예보에서는
`TMP`** 로 온다 (§6.3). `Q` 가 그 개명을 provider 층에 가둔다.

RAG-018 이 파서에서 쓴 것과 같은 형태다 — 파서가 공통 IR 6종을 내보내고 청커가 소비하듯,
provider 가 `Measurement` 목록을 내보내고 룰이 소비한다.

세 종류를 갈라 담는다 (②-a)
  관측값  양 + 수치          → `Measurement`
  상태    구간 + 범주, 수치 없음 → `State`   (특보 · 미세먼지 예보통보뿐)
  위치 해석 조회 키            → `ResolvedLocation`
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .geo import Grid, LatLon


# ---------------------------------------------------------------- 값의 표현 (②-d-1)

@dataclass(frozen=True)
class Code:
    """범주 코드. **provider 가 의미 있는 이름으로 번역해서 싣는다.**

    원본 숫자를 `raw` 에 남기는 이유 — 번역이 틀렸을 때 원값이 없으면 확인할 방법이 없다.
    """

    name: str
    raw: str


@dataclass(frozen=True)
class Interval:
    """구간. **점으로 바꾸지 않는다** (②-d-1).

    `'1.0mm 미만'` 을 `0.5` 로 누르면 없는 정밀도를 만든다. 구간을 구간으로 담으면 정보 손실도
    임의 정밀도도 없고, 룰은 `hi > 0`(비 오나) · `lo >= 30`(30mm 이상인가)으로 그대로 판정한다.
    """

    label: str
    lo: float
    hi: float

    @property
    def is_zero(self) -> bool:
        return self.hi == 0.0


Value = float | Code | Interval


class PrecipKind(StrEnum):
    """`PTY` — 강수 형태. 단기예보 기준 0~4 (§6.3 실측은 `'0'` 뿐이었다)."""

    NONE = "none"
    RAIN = "rain"
    RAIN_SNOW = "rain_snow"
    SNOW = "snow"
    SHOWER = "shower"


class SkyState(StrEnum):
    """`SKY` — 1 맑음 · 3 구름많음 · 4 흐림. 2 는 쓰이지 않는다."""

    CLEAR = "clear"
    PARTLY_CLOUDY = "partly_cloudy"
    CLOUDY = "cloudy"


# ------------------------------------------------------------------ 어휘 (②-d-3)

class Q(StrEnum):
    """관측량. **닫힌 어휘 23개.**

    파생량은 넣지 않는다 — 체감온도는 `source` 가 "우리"라 섞으면 "이 값이 어디서 왔나"가
    흐려지고, 그것이 ②의 존재 이유다. 체감온도는 ③이 만들어 ⑥ 응답에 별도로 싣는다.

    특보·미세먼지 예보통보도 없다. 값이 아니라 (구간 · 구역 · 자연어)라서 `State` 로 간다.
    """

    TEMP = "temp_c"
    TEMP_MIN = "temp_min_c"
    TEMP_MAX = "temp_max_c"
    HUMIDITY = "humidity_pct"
    WIND = "wind_ms"
    WIND_U = "wind_u_ms"
    WIND_V = "wind_v_ms"
    WIND_DIR = "wind_dir_deg"
    PRECIP_KIND = "precip_kind"
    PRECIP_MM = "precip_mm"
    SNOW_CM = "snow_cm"
    PRECIP_PROB = "precip_prob_pct"
    SKY = "sky"
    LIGHTNING = "lightning"
    PM10 = "pm10"
    PM25 = "pm25"
    SO2 = "so2_ppm"
    NO2 = "no2_ppm"
    CO = "co_ppm"
    O3 = "o3_ppm"
    KHAI = "khai"
    UV = "uv_index"
    WAVE_M = "wave_m"


# ②-d-1 이 못 박은 "quantity ↔ 표현 1:1". 표가 아니라 **검사로** 둔다 — 문서에만 있으면
# provider 가 `Q.TEMP` 에 문자열을 넣어도 룰에서야 터진다.
REPRESENTATION: dict[Q, type] = {
    Q.PRECIP_KIND: Code, Q.SKY: Code,
    Q.PRECIP_MM: Interval, Q.SNOW_CM: Interval,
    **{q: float for q in Q if q not in {Q.PRECIP_KIND, Q.SKY, Q.PRECIP_MM, Q.SNOW_CM}},
}

# `RN1` 이 실황에서는 `'0'`(숫자), 예보에서는 `'강수없음'`(구간)으로 온다 — 같은 이름의 도메인이
# 오퍼레이션마다 흔들린다. **넓은 쪽(`Interval`)으로 통일**하고 실황의 점은 폭 0 구간으로 올린다
# (②-d-3 결정 1). 좁은 쪽으로 맞추면 `'1.0mm 미만'` 을 임의 수치로 눌러야 한다.

# 등급을 싣는 양 (②-d-2). provider 가 **`Grade1h`(현재 기준)** 를 골라 흡수한다 —
# 실측 PM10 37 은 `Grade`=1(24h 평균), `Grade1h`=2(현재)로 갈렸다.
GRADED = {Q.PM10, Q.PM25, Q.SO2, Q.NO2, Q.CO, Q.O3, Q.KHAI, Q.UV}

# 확인 전 (②-d-3). `LGT` 는 실측값이 `'0'` 뿐이라 숫자인지 코드인지 모르고,
# `UV` 는 apihub 403 이라 응답 자체를 못 봤다.
UNVERIFIED = {Q.LIGHTNING, Q.UV}


# ------------------------------------------------------------------ 출처 (⑤-d)

class Source(StrEnum):
    """`provider:operation`. **오퍼레이션까지 내려가는 이유는 ⑤-d 가 요구해서다.**

    ②-b 는 `source` 를 provider id 로 스케치했는데, ⑤-d 가 기온의 출처 넷을 우선순위로 세울 때
    그중 **셋이 같은 provider**다 (실황·초단기예보·단기예보가 한 서비스). provider id 로는
    그 셋이 구분되지 않아 우선순위가 성립하지 않는다. ④-a 의 캐시 키도 같은 축을 쓴다.
    """

    AWS_MIN = "kma-apihub:aws"                  # 분 단위 지점 관측
    NCST = "kma-vilage-fcst:ncst"               # 초단기실황
    FCST_ULTRA = "kma-vilage-fcst:ultra"        # 초단기예보
    FCST_VILLAGE = "kma-vilage-fcst:village"    # 단기예보
    AIRKOREA = "airkorea-realtime:dnsty"
    AIRKOREA_FORECAST = "airkorea-realtime:frcst"
    LIFE_INDEX = "kma-life-index:senta"
    WARNING = "kma-warning:pwn"

    # 아래 셋은 **값을 만들지 않는다** — 조회 키를 만드는 provider 이거나(②-a 근거 2) 표기
    # 전용이다. `Measurement.source` 로는 절대 안 쓰이고, `ProviderResult` 로만 나타난다.
    # 그래도 여기 있는 이유는 저하 이유를 사람에게 말해야 하기 때문이다 (⑥ `sources`) —
    # 측정소 목록을 못 받아서 `AIR=UNKNOWN` 이 된 것과 대기질 API 가 죽은 것은 다른 사건이다.
    # `SOURCE_PRIORITY` 에는 넣지 않는다. 정렬에 낄 일이 없고, 끼면 순위표가 거짓말이 된다.
    AIRKOREA_STATIONS = "airkorea-stations:list"
    AWS_STATIONS = "kma-apihub:stn_inf"
    KAKAO = "kakao-local:region"


# ⑤-d 확정 순서. 앞에 있을수록 이긴다.
#
# **긴장은 신선도와 공간 대표성이 반대 방향이라는 것이다** — AWS 는 분 단위로 가장 신선하지만
# 그 지점이 내 위치를 대표한다는 보장이 없고, 격자는 최대 100분 지연되지만 **정의상 내 위치를
# 포함한다.** 그래서 1순위는 "가까운 AWS" 가 아니라 **"같은 격자 안의 AWS"** 이고, 그 판정은
# 여기가 아니라 provider 가 `geo.same_grid()` 로 한다 — 같은 격자가 아닌 지점은 애초에
# `Measurement` 를 만들지 않는다. 임계를 두지 않으므로 새 숫자가 생기지 않는다.
SOURCE_PRIORITY: tuple[Source, ...] = (
    Source.AWS_MIN,
    Source.NCST,
    Source.FCST_ULTRA,
    Source.FCST_VILLAGE,
    Source.AIRKOREA,
    Source.AIRKOREA_FORECAST,
    Source.LIFE_INDEX,
    Source.WARNING,
)
_RANK = {source: i for i, source in enumerate(SOURCE_PRIORITY)}

# 우선순위표에서 빠지는 **유일한 예외**를 코드에 명시한다 (RT-002 ②-a). 테스트에만 두면
# 새 provider 가 조용히 이 목록에 얹히고, 그 순간 우선순위가 총함수가 아니게 된다.
# `Measurement.__post_init__` 가 이 셋을 값의 출처로 쓰는 것을 막는다.
NON_VALUE_SOURCES: frozenset[Source] = frozenset(
    {Source.AIRKOREA_STATIONS, Source.AWS_STATIONS, Source.KAKAO})


# -------------------------------------------------------------------- 관측값 (②-b)

@dataclass(frozen=True)
class Measurement:
    """한 양의 한 시각 값.

    **시각이 둘인 이유** (②-c) — 실측 5개 모양 중 4개가 시각을 둘 주고, 나머지 하나(실황)도
    14:00 관측값을 14:40에야 준다. 하나로 뭉치면 그 40분을 담을 자리가 없다.
      `valid_at`  이 값이 말하는 시각    ← 판정(③)이 본다
      `issued_at` 발표·관측 시각         ← 신선도(④)가 본다

    **예보를 위한 별도 타입은 없다** — 관측은 리드타임 0인 측정값이다. 두 타입으로 가르면 룰이
    같은 계산을 두 경로로 쓰게 되고 체감온도 산식이 두 곳에 생긴다.
    """

    quantity: Q
    value: Value
    valid_at: datetime
    issued_at: datetime
    source: Source
    spatial_ref: str                 # '격자 61,125' | '측정소 강남대로'
    grade: int | None = None         # API 가 준 것만. 없으면 None — 결함이 아니다 (②-d-2)

    def __post_init__(self) -> None:
        # **시간대를 요구한다.** 실측 시각은 전부 KST 인데 naive 로 두면 aware 값과 섞이는 순간
        # `bisect` 안쪽에서 TypeError 가 난다 — 원인이 provider 라는 것을 잃는 자리다.
        # ②-c 가 시각을 판정의 축으로 삼았으므로 여기서 못 박는 값이 맞다.
        if self.valid_at.tzinfo is None or self.issued_at.tzinfo is None:
            raise ValueError(f"{self.quantity.name}: valid_at·issued_at 은 시간대를 가져야 한다 (KST)")
        expected = REPRESENTATION[self.quantity]
        if not isinstance(self.value, expected):
            # 경계에서 터뜨린다. 룰까지 흘러가면 원인이 provider 라는 것을 잃는다.
            raise TypeError(
                f"{self.quantity.name} 은 {expected.__name__} 이어야 한다 "
                f"(받은 것: {type(self.value).__name__}). ②-d-1 의 quantity↔표현 1:1")
        if self.source in NON_VALUE_SOURCES:
            # 측정소 목록·표기 provider 는 값을 만들지 않는다. 여기로 오면 조립층이 출처를
            # 잘못 붙인 것이고, 그대로 두면 ⑤-d 우선순위에서 조용히 꼴찌로 끼어든다.
            raise ValueError(f"{self.source.value} 은 값을 만들지 않는 출처다 (NON_VALUE_SOURCES)")
        if self.grade is not None and self.quantity not in GRADED:
            raise ValueError(f"{self.quantity.name} 에는 공식 등급이 없다 (②-d-2 의 GRADED 를 볼 것)")


# ---------------------------------------------------------------------- 상태 (②-a)

class StateKind(StrEnum):
    """특보 11종(기상청 고정 목록) + 대기질 예보. **값이 아니라 범주다.**"""

    HEAT = "heat"                 # 폭염
    COLD = "cold"                 # 한파
    HEAVY_RAIN = "heavy_rain"     # 호우
    HEAVY_SNOW = "heavy_snow"     # 대설
    WIND = "wind"                 # 강풍
    DRY = "dry"                   # 건조
    TYPHOON = "typhoon"           # 태풍
    YELLOW_DUST = "yellow_dust"   # 황사
    HIGH_SEAS = "high_seas"       # 풍랑
    STORM_SURGE = "storm_surge"   # 폭풍해일
    TSUNAMI = "tsunami"           # 지진해일
    AIR_FORECAST = "air_forecast"  # 미세먼지 예보통보


@dataclass(frozen=True)
class State:
    """구간 + 범주. **수치가 없다.**

    특보를 `Measurement` 로 넣으면 `value=None, grade='경보'` 라는 기형이 생기고, 그 `None` 을
    모든 룰이 특수 처리하게 된다 (②-a 근거 1). `getPwnStatus` 의 `t6` 은 전국 발효 현황이
    **자연어 1,610자**로 들어오는 물건이다.

    `category` 는 기관이 준 라벨 그대로다 ('경보'·'주의보'·'나쁨'). 정규화하지 않는 이유는
    ②-d-2 와 같다 — 정규화하면 룰이 그걸로 판정하고 싶어지고, 기관 기준이 바뀔 때 표시와
    산책 임계가 함께 묶여 어느 쪽도 못 움직인다.
    """

    kind: StateKind
    category: str
    area: str                        # '서울동남권'(특보구역) | '서울'(권역)
    valid_from: datetime
    valid_to: datetime | None        # 특보는 해제 시각이 미정이라 None 이 정상이다
    issued_at: datetime
    source: Source
    detail: str = ""                 # 원문 조각. 번역이 틀렸을 때 확인할 자리

    def __post_init__(self) -> None:
        if self.valid_from.tzinfo is None or self.issued_at.tzinfo is None:
            raise ValueError(f"{self.kind.name}: 시각은 시간대를 가져야 한다 (KST)")

    def covers(self, t: datetime) -> bool:
        return self.valid_from <= t and (self.valid_to is None or t < self.valid_to)


# ------------------------------------------------------------------ 위치 해석 (②-a)

@dataclass(frozen=True)
class ResolvedLocation:
    """조회 키 묶음. **방향이 관측값과 반대다** — 룰이 읽는 값이 아니라 다음 호출의 인자였다.

    그래도 버리지 않는 이유는 ⑥ 응답이 **"○○동 (측정소: △△) 기준"** 이라는 정직 표기를
    요구하기 때문이다 (`realtime-apis.md` §0). 그건 값 하나의 속성이 아니라 조회 전체의 속성이다.
    """

    point: LatLon
    grid: Grid
    label: str                            # 서버가 만든 표기. 카카오가 없으면 좌표가 그대로 온다
    station: str | None = None            # 대기질 측정소명
    station_km: float | None = None
    aws_station: str | None = None        # 같은 격자 안의 AWS 지점 (없으면 None → ⑤-d 1순위 미발동)
    warning_area: str | None = None       # 특보구역
    region: str | None = None             # 미세먼지 예보 권역


# -------------------------------------------------------------- 조회의 메타 (②-e)

@dataclass(frozen=True)
class ProviderResult:
    """provider 하나의 결과. **부분 실패는 값의 부재로 표현되지 않는다.**

    "없다"의 원인이 실측에서 이미 셋으로 갈렸고 ⑤의 대응이 각각 다르다 — 일시 장애(재시도 후
    저하) · 미신청(기능을 켜지 않는다) · 경로 없음(버그). `list[Measurement]` 는 셋을 전부
    "그냥 없음"으로 뭉갠다.

    실패를 `Measurement` 로 표현하지 않는 이유가 하나 더 있다 — 죽은 provider 는 양이
    여럿인데(`PM10`·`PM25`·`SO2`…) 실패는 provider 하나다. **개수가 안 맞는다.**
    """

    provider: Source
    ok: bool
    reason: str | None = None        # 'resultCode 05' | 'HTTP 403' | 'DATA_GO_KR_KEY 없음'
    attempts: int = 1
    stale: bool = False              # 캐시된 옛 값으로 채웠다 (⑤-c)


@dataclass
class Observations:
    """한 번의 조회 전체. **룰은 이것 하나만 받는다.**

    관측과 실패를 두 인자로 넘기면 둘의 짝이 어긋날 수 있다. 한 묶음이면 어긋날 수 없다.

    프레임 하나가 872건(8 + 66 + 798)이라 `(quantity, valid_at)` 색인을 만들어 둔다 (②-c).
    """

    location: ResolvedLocation
    fetched_at: datetime
    measurements: list[Measurement] = field(default_factory=list)
    states: list[State] = field(default_factory=list)
    providers: list[ProviderResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._reindex()

    def _reindex(self) -> None:
        """양별로 모으고 `valid_at` 오름차순 · 그 안에서 ⑤-d 우선순위로 정렬한다.

        정렬을 한 번만 하면 `at()` 이 이분탐색으로 끝난다 — 872건을 룰이 시각마다 훑으면
        T+24h 타임라인에서 그 스캔이 24번 반복된다.
        """
        buckets: dict[Q, list[Measurement]] = defaultdict(list)
        for m in self.measurements:
            buckets[m.quantity].append(m)
        for series in buckets.values():
            # 두 번 정렬한다. 파이썬 정렬이 안정적이라 **뒤 정렬이 앞 정렬을 동점 안에서 보존**한다 —
            # 방향이 다른 키(valid_at 오름 · issued_at 내림)를 한 튜플에 못 담기 때문이고,
            # `-timestamp()` 같은 부호 뒤집기는 naive/aware 가 섞이면 조용히 틀린다.
            series.sort(key=lambda m: m.issued_at, reverse=True)
            # ⑤-d 출처 우선순위가 **먼저**, 같은 출처 안에서 최신 issued_at (②-c).
            # 순서가 뒤바뀌면 15:00 에 대해 13:30 발표 초단기예보 대신 14:00 발표 단기예보를
            # 고르게 되는데, 6시간 이내는 초단기예보가 더 정확하다.
            series.sort(key=lambda m: (m.valid_at, _RANK.get(m.source, len(_RANK))))
        self._by_quantity: dict[Q, list[Measurement]] = dict(buckets)
        self._times: dict[Q, list[datetime]] = {
            q: [m.valid_at for m in series] for q, series in buckets.items()}

    # -- 조회 헬퍼 -------------------------------------------------------

    def at(self, t: datetime, q: Q) -> Measurement | None:
        """시각 `t` 에 유효한 값. **`valid_at <= t` 중 가장 늦은 것**이다.

        "가장 가까운 것"이 아닌 이유 — 가까움을 쓰려면 허용 오차가 필요하고 그건 근거 없는
        숫자다 (③-d 가 경계한 것). 관측값은 자기 `valid_at` 부터 다음 값까지 유효한
        계단 함수이므로, 그 정의를 그대로 쓰면 새 숫자가 생기지 않는다.

        같은 `valid_at` 에 여럿이면 ⑤-d 우선순위 → 최신 `issued_at` 순으로 첫 번째를 준다.
        """
        times = self._times.get(q)
        if not times:
            return None
        i = bisect_right(times, t)
        if i == 0:
            return None                       # t 이전의 값이 아직 없다
        # 같은 valid_at 이 여럿이면 그 블록의 **첫** 항목이 이긴다 (정렬이 이미 그 순서다).
        # bisect_left 로 블록 시작을 잡는다 — list.index 는 O(n) 이라 이분탐색이 무의미해진다.
        return self._by_quantity[q][bisect_left(times, times[i - 1])]

    def latest(self, q: Q) -> Measurement | None:
        """지금 시점의 값 = `at(fetched_at, q)`.

        `valid_at` 이 가장 늦은 것이 아니다 — 목록에는 T+24h 예보가 섞여 있어서 그것을 쓰면
        "지금 기온"으로 내일 값을 답한다.
        """
        return self.at(self.fetched_at, q)

    def series(self, q: Q) -> list[Measurement]:
        """`valid_at` 하나당 하나로 추린 시계열. ③의 타임라인이 이것을 걷는다."""
        picked: list[Measurement] = []
        for m in self._by_quantity.get(q, []):
            if not picked or picked[-1].valid_at != m.valid_at:
                picked.append(m)              # 정렬 덕에 블록의 첫 항목이 곧 우선순위 승자다
        return picked

    def states_at(self, t: datetime, kind: StateKind | None = None) -> list[State]:
        return [s for s in self.states
                if s.covers(t) and (kind is None or s.kind == kind)]

    def failed(self) -> list[ProviderResult]:
        """⑥ 응답의 `sources` 가 "왜 저하됐는지"를 여기서 만든다."""
        return [p for p in self.providers if not p.ok]


# --------------------------------------------------------- 구간 문자열 파서 (②-d-1)

# `'강수없음'` `'적설없음'` `'1.0mm 미만'` `'30.0~50.0mm'` `'5.0cm 이상'`
_NONE = re.compile(r"^(강수|적설)?없음$")
_UNDER = re.compile(r"^([\d.]+)\s*(mm|cm)?\s*미만$")
_OVER = re.compile(r"^([\d.]+)\s*(mm|cm)?\s*이상$")
_RANGE = re.compile(r"^([\d.]+)\s*~\s*([\d.]+)\s*(mm|cm)?$")
_POINT = re.compile(r"^([\d.]+)\s*(mm|cm)?$")


def parse_interval(raw: str) -> Interval:
    """기상청 강수·적설 표기를 구간으로.

    **모르는 문자열은 조용히 0 으로 떨어지지 않고 실패한다** (②-d-1). 실측 당일은 비가 오지
    않아 `'강수없음'` 하나만 나왔고 구간 문자열의 실제 형태는 문서 기준이다. 조용히 0 이 되면
    비 오는 날 "강수 없음"이라고 답하고도 아무 신호가 없다 — 그게 이 판정에서 가장 위험한 실패다.
    """
    text = (raw or "").strip()
    if _NONE.match(text):
        return Interval(text or "없음", 0.0, 0.0)
    if m := _UNDER.match(text):
        return Interval(text, 0.0, float(m.group(1)))
    if m := _OVER.match(text):
        # 상한이 없다. `inf` 로 두면 "30mm 이상?"(`lo >= 30`)은 그대로 답하고,
        # 상한을 임의 수치로 지어내지 않는다.
        return Interval(text, float(m.group(1)), float("inf"))
    if m := _RANGE.match(text):
        return Interval(text, float(m.group(1)), float(m.group(2)))
    if m := _POINT.match(text):
        # 실황의 `RN1 '0'` · `'2.5'` — 점은 폭 0 인 구간이다 (②-d-3 결정 1)
        v = float(m.group(1))
        return Interval(text, v, v)
    raise ValueError(
        f"모르는 구간 표기: {raw!r}. 조용히 0 으로 떨어뜨리지 않는다 (②-d-1). "
        "비 오는 날 프로브를 다시 돌려 실제 문자열을 확인하고 여기에 규칙을 추가할 것")


_PRECIP_CODES = {"0": PrecipKind.NONE, "1": PrecipKind.RAIN, "2": PrecipKind.RAIN_SNOW,
                 "3": PrecipKind.SNOW, "4": PrecipKind.SHOWER}
_SKY_CODES = {"1": SkyState.CLEAR, "3": SkyState.PARTLY_CLOUDY, "4": SkyState.CLOUDY}


def parse_precip_kind(raw: str) -> Code:
    """`PTY` → `Code`. 모르는 코드는 실패한다.

    ⚠️ 표는 단기예보 기준 0~4 다 (②-d-3). **초단기 계열이 5·6·7(빗방울·빗방울눈날림·눈날림)을
    쓴다고 알려져 있으나 실측에서 확인하지 못했다.** 조용히 `NONE` 으로 떨어뜨리면 눈 날리는 날
    "강수 없음"이 되므로, 실패하게 두고 실물이 잡히면 여기에 추가한다.
    """
    kind = _PRECIP_CODES.get((raw or "").strip())
    if kind is None:
        raise ValueError(f"모르는 PTY 코드: {raw!r} (확인된 것은 0~4). ②-d-3 의 미실측 공백")
    return Code(kind.value, raw)


def parse_sky(raw: str) -> Code:
    state = _SKY_CODES.get((raw or "").strip())
    if state is None:
        raise ValueError(f"모르는 SKY 코드: {raw!r} (1·3·4 만 쓰인다)")
    return Code(state.value, raw)
