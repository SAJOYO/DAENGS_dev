"""HTTP 공통 계층: UA, 호스트별 요청 간격, 재시도, robots.txt.

소스 모듈은 이 모듈의 Fetcher 하나만 쓴다. 예절 규칙이 여기 한 곳에만 있어야
소스가 23개가 되어도 전부 같은 방식으로 동작한다.
"""
from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from . import config


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
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # ------------------------------------------------------------------ robots
    def _robots_for(self, host: str, scheme: str) -> urllib.robotparser.RobotFileParser | None:
        if host in self._robots:
            return self._robots[host]
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = self._client.get(f"{scheme}://{host}/robots.txt")
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                rp = None                                           # robots 없음 = 전부 허용
        except httpx.HTTPError:
            rp = None
        self._robots[host] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        parts = urlsplit(url)
        rp = self._robots_for(parts.netloc, parts.scheme)
        return True if rp is None else rp.can_fetch(config.USER_AGENT, url)

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
