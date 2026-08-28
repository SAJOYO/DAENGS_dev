"""JS 객체 리터럴 읽기 — `json.loads` 로 안 열리는 응답을 위한 최소 리더.

`textutil` 과 같은 자리에 두는 이유도 같다: "특정 사이트 지식"이 아니라 **포맷 지식**이다.
손해보험협회 공시(`knia-disclosure`)가 `Content-Type: text/plain` 으로 주는 본문이
JSON 이 아니라 **자바스크립트 객체 리터럴**이다.

    {list:[{TP_NAME:'(무) 펫퍼민트…',TP_W_BILL:34365,MIN_BILL:'-'}]}

JSON 과 다른 점 셋 — 키에 따옴표가 없고, 문자열이 홑따옴표이며, 숫자가 섞여 온다.
그래서 `json.loads` 는 `Expecting property name enclosed in double quotes` 로 죽는다.

**따옴표를 바꿔치기하는 정규식으로 때우지 않는다.** 값 안에 `'` 와 `"` 가 다 들어 있어
(`ㆍ예시보험료는 …`, `2~3마리 시 5%`) 치환이 문자열 경계를 깬다. 문자 단위로 읽는 편이
짧고 안전하다.

지원 범위는 **실제로 오는 것까지만**이다 — 객체 / 배열 / 홑따옴표 문자열 / 숫자 /
`true`·`false`·`null`. 함수·정규식·주석은 안 받고 `ValueError` 로 죽인다. 조용히 넘기면
응답 규격이 바뀐 것을 못 알아채기 때문이다.

**후행 쉼표(`{a:1,}`)는 받는다.** 엄격함의 값은 "규격이 바뀐 것을 알아채는 것" 인데 후행 쉼표는
값을 하나도 잃게 하지 않는다 — 거절해서 얻는 것이 없고, 사이트가 하나 붙이는 날 수집이 통째로
멈춘다. 반대로 **키 중복은 거절한다**: 뒤엣것이 앞엣것을 덮으면 값이 실제로 사라진다.
"""
from __future__ import annotations

import re

__all__ = ["loads"]

_KEY = re.compile(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*:")
_WS = re.compile(r"\s*")
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")
_CONST = {"true": True, "false": False, "null": None}


def loads(text: str) -> object:
    """JS 객체 리터럴 문자열 → 파이썬 값. 남는 문자가 있으면 실패시킨다."""
    reader = _Reader(text)
    value = reader.value()
    reader.skip_ws()
    if reader.i != len(text):
        raise ValueError(f"뒤에 남은 문자가 있다 @{reader.i}: {text[reader.i:reader.i + 40]!r}")
    return value


class _Reader:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def skip_ws(self) -> None:
        self.i = _WS.match(self.s, self.i).end()

    def _at(self) -> str:
        if self.i >= len(self.s):
            raise ValueError("입력이 도중에 끝났다 — 응답이 잘렸을 수 있다")
        return self.s[self.i]

    def value(self) -> object:
        self.skip_ws()
        c = self._at()
        if c == "{":
            return self._object()
        if c == "[":
            return self._array()
        if c in "'\"":
            return self._string(c)
        m = _NUM.match(self.s, self.i)
        if not m:
            raise ValueError(f"값이 아니다 @{self.i}: {self.s[self.i:self.i + 40]!r}")
        self.i = m.end()
        token = m.group(0)
        if token in _CONST:
            return _CONST[token]
        return float(token) if re.search(r"[.eE]", token) else int(token)

    def _object(self) -> dict[str, object]:
        self.i += 1                                   # '{'
        out: dict[str, object] = {}
        while True:
            self.skip_ws()
            if self._at() == "}":
                self.i += 1
                return out
            c = self._at()
            if c in "'\"":                            # 따옴표 붙은 키도 받는다
                key = self._string(c)
                self.skip_ws()
                if self._at() != ":":
                    raise ValueError(f"키 뒤에 ':' 이 없다 @{self.i}")
                self.i += 1
            else:
                m = _KEY.match(self.s, self.i)
                if not m:
                    raise ValueError(f"키가 아니다 @{self.i}: {self.s[self.i:self.i + 40]!r}")
                key, self.i = m.group(1), m.end()
            if key in out:
                # 같은 키가 두 번 오면 뒤엣것이 앞엣것을 덮는다. 조용히 덮으면 값이 하나
                # 사라진 것을 아무도 모른다
                raise ValueError(f"키가 중복됐다: {key!r}")
            out[key] = self.value()
            self.skip_ws()
            if self._at() == ",":
                self.i += 1

    def _array(self) -> list[object]:
        self.i += 1                                   # '['
        out: list[object] = []
        while True:
            self.skip_ws()
            if self._at() == "]":
                self.i += 1
                return out
            out.append(self.value())
            self.skip_ws()
            if self._at() == ",":
                self.i += 1

    def _string(self, quote: str) -> str:
        self.i += 1
        buf: list[str] = []
        while True:
            if self.i >= len(self.s):
                raise ValueError("문자열이 닫히지 않았다 — 응답이 잘렸을 수 있다")
            c = self.s[self.i]
            if c == quote:
                self.i += 1
                return "".join(buf)
            if c == "\\":
                nxt = self.s[self.i + 1:self.i + 2]
                if not nxt:
                    raise ValueError("역슬래시로 끝났다")
                buf.append(_ESCAPES.get(nxt, nxt))
                self.i += 2
                continue
            buf.append(c)                             # 줄바꿈도 값에 그대로 들어온다
            self.i += 1


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}
