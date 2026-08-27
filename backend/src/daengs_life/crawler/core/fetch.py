"""HTTP 공통 계층: UA, 호스트별 요청 간격, 재시도, robots.txt.

소스 모듈은 이 모듈의 Fetcher 하나만 쓴다. 예절 규칙이 여기 한 곳에만 있어야
소스가 23개가 되어도 전부 같은 방식으로 동작한다.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from . import config


# ---------------------------------------------------------------------- robots
# **urllib.robotparser 를 쓰지 않는다.** 그것은 파일에 먼저 적힌 규칙이 이기는 방식인데,
# 표준(RFC 9309 §2.2.2)은 **가장 긴 패턴이 이긴다.** 정부24 robots.txt 가 정확히 그 차이에
# 걸린다 — `Disallow: /` 를 먼저 적고 그 아래에서 `/mw/AA020InfoCappView.do` 를 열어 준다.
# urllib 으로 읽으면 사이트가 명시적으로 허용한 경로를 우리가 스스로 막는다.
_TOKEN_RE = re.compile(r"[a-z0-9_-]+")


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str                                     # 원본 경로 패턴 (`*` `$` 포함)
    regex: re.Pattern[str]

    def matches(self, path: str) -> bool:
        return self.regex.match(path) is not None


def _compile(pattern: str) -> re.Pattern[str]:
    """robots 경로 패턴 → 정규식. `*`=아무거나, 끝의 `$`=완전일치, 그 외는 접두사 일치."""
    end = pattern.endswith("$")
    body = pattern[:-1] if end else pattern
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile(rx + ("$" if end else ""))


class Robots:
    """robots.txt 한 장. 그룹 선택과 longest-match 판정만 한다."""

    def __init__(self, text: str) -> None:
        self._groups: dict[str, list[_Rule]] = {}
        agents: list[str] = []
        starting = True                              # 지금 user-agent 줄을 모으는 중인가
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                if not starting:                     # 규칙이 한 번 나온 뒤의 user-agent = 새 그룹
                    agents, starting = [], True
                agents.append(value.lower())
                self._groups.setdefault(value.lower(), [])
            elif field in ("allow", "disallow") and agents:
                starting = False
                if not value:                        # `Disallow:` 빈 값 = 아무것도 막지 않음
                    continue
                rule = _Rule(field == "allow", value, _compile(value))
                for a in agents:
                    self._groups[a].append(rule)

    def _rules_for(self, user_agent: str) -> list[_Rule]:
        """UA 문자열 안의 토큰과 겹치는 그룹. 없으면 `*` 그룹, 그것도 없으면 규칙 없음."""
        tokens = set(_TOKEN_RE.findall(user_agent.lower()))
        for agent, rules in self._groups.items():
            if agent != "*" and agent in tokens:
                return rules
        return self._groups.get("*", [])

    def allowed(self, user_agent: str, path: str) -> bool:
        best: _Rule | None = None
        for rule in self._rules_for(user_agent):
            if not rule.matches(path):
                continue
            # 가장 긴 패턴이 이기고, 길이가 같으면 Allow 가 이긴다 (RFC 9309 §2.2.2)
            if (best is None
                    or len(rule.pattern) > len(best.pattern)
                    or (len(rule.pattern) == len(best.pattern) and rule.allow)):
                best = rule
        return True if best is None else best.allow


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content: bytes
    content_type: str
    elapsed_sec: float

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Fetcher:
    def __init__(self, *, respect_robots: bool = True) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT_SEC,
            follow_redirects=True,
        )
        self._respect_robots = respect_robots
        self._last_hit: dict[str, float] = {}                       # host -> 마지막 요청 시각
        self._robots: dict[str, Robots | None] = {}

    # ------------------------------------------------------------------ robots
    def _robots_for(self, host: str, scheme: str) -> Robots | None:
        if host in self._robots:
            return self._robots[host]
        robots: Robots | None
        try:
            r = self._client.get(f"{scheme}://{host}/robots.txt")
            robots = Robots(r.text) if r.status_code == 200 else None   # 없으면 전부 허용
        except httpx.HTTPError:
            robots = None
        self._robots[host] = robots
        return robots

    def allowed(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        parts = urlsplit(url)
        robots = self._robots_for(parts.netloc, parts.scheme)
        if robots is None:
            return True
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"                           # 쿼리까지 봐야 하는 패턴이 있다
        return robots.allowed(config.USER_AGENT, path)

    # ------------------------------------------------------------------ fetch
    def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = config.REQUEST_DELAY_SEC - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str) -> FetchResult:
        """GET 한 번. 5xx/네트워크 오류는 지수 백오프로 MAX_RETRIES 까지 재시도.
        4xx는 재시도해도 같은 결과라 즉시 반환한다 (호출자가 status 로 판단)."""
        host = urlsplit(url).netloc
        last_exc: Exception | None = None
        for attempt in range(config.MAX_RETRIES):
            self._throttle(host)
            t0 = time.monotonic()
            try:
                r = self._client.get(url)
            except httpx.HTTPError as e:                           # 타임아웃, 연결 실패 등
                last_exc = e
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 500 and attempt < config.MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            return FetchResult(
                url=url,
                final_url=str(r.url),
                status=r.status_code,
                content=r.content,
                content_type=r.headers.get("content-type", ""),
                elapsed_sec=time.monotonic() - t0,
            )
        raise RuntimeError(f"fetch failed after {config.MAX_RETRIES} attempts: {url}") from last_exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
