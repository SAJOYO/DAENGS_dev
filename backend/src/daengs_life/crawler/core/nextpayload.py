"""Next.js RSC 페이로드에서 i18n 사전 읽기 — **서버 HTML 에 없는 본문을 꺼낸다.**

`jsobject` 와 같은 자리에 두는 이유도 같다: "특정 사이트 지식"이 아니라 **포맷 지식**이고,
**수집과 파싱이 같은 것을 읽어야 하기 때문**이다. 이 리더를 파서 쪽에 복사하면 이스케이프
처리가 두 벌이 되고, 한쪽만 고쳐지는 날 크롤러는 요금을 보는데 파서는 못 보는 상태가 된다.
`jsobject` 가 `rag` 의 허용 목록에 들어간 것과 같은 판단이다 (RAG-018 · RAG-046).

**무엇을 푸는가** — App Router 를 쓰는 사이트는 화면에 보이는 값이 DOM 에 없고 이런 모양의
스크립트 안에 있다:

    self.__next_f.push([1,"...{\\"need_pet_fare_32kg_nea_kr\\":\\"KRW 130,000\\"}..."])

**JSON 이 JS 문자열 안에 한 번 더 들어 있어 이스케이프가 두 겹**이다. 그래서 파일에는
`\\"key\\":\\"value\\"` 로 보이고, 값 안의 태그는 `\\u003cbr /\\u003e` 로 접혀 있다.
청크 전체를 `json.loads` 로 열려는 시도는 실패한다 — 한 청크가 여러 조각으로 잘려 오고
문서마다 조각 수가 달라, 이어 붙이는 규칙이 사이트 판마다 바뀐다.

**그래서 사전 전체를 복원하지 않고 필요한 접두사의 키만 뽑는다.** 목적이 "문서 본문 얻기"라
구조가 아니라 값이 필요하고, 부분 파싱은 조각이 잘려도 나머지를 그대로 읽는다.

에어프레미아(`airlines-pet-pages`)에서 실측 — `need_pet` 접두사로 **97키**. 요금·케이지 규격·
단두종/맹견 목록·미국 입국 규정이 전부 여기 있고 DOM 에는 표의 머리만 있다.
"""
from __future__ import annotations

import re

from . import textutil

# `\"key\":\"value\"` — 값 안의 `\"` 는 이스케이프된 따옴표라 경계가 아니다.
_PAIR_TEMPLATE = r'\\"({prefix}[A-Za-z0-9_]*)\\":\\"((?:[^\\]|\\[^"])*)\\"'
_UNI_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_TAG = re.compile(r"<[^>]+>")


def _unescape(value: str) -> str:
    """`\\u003cbr /\\u003e` · `\\"` · `\\n` 을 풀고 남은 태그를 지운다."""
    out = _UNI_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), value)
    out = out.replace('\\"', '"').replace("\\n", " ").replace("\\/", "/")
    return textutil.squeeze(_TAG.sub(" ", out)).strip()


def mapping(html: bytes | str, prefix: str) -> dict[str, str]:
    """`prefix` 로 시작하는 키 → 값. **문서 순서를 유지한다** (dict 가 삽입 순서를 지킨다).

    **같은 키가 두 번 나오면 첫 것만 쓴다.** 페이로드에는 화면 상태에 따라 갱신된 값이 뒤에
    다시 실리는 경우가 있는데, 뒤엣것을 쓰면 같은 페이지에서 실행 시점에 따라 결과가 달라진다.

    순서를 정렬하지 않는 이유는 **지문(sha256)** 때문이다 — 정렬하거나 set 으로 돌리면
    사이트가 그대로여도 파이썬 판이 바뀔 때 지문이 흔들려 `changed` 가 뜬다.
    """
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    pattern = re.compile(_PAIR_TEMPLATE.format(prefix=re.escape(prefix)))
    out: dict[str, str] = {}
    for key, value in pattern.findall(html):
        if key in out:
            continue
        cleaned = _unescape(value)
        if cleaned:
            out[key] = cleaned
    return out


def values(html: bytes | str, prefix: str) -> list[str]:
    """`mapping()` 의 값만. 지문·미리보기처럼 구조가 필요 없는 자리에서 쓴다."""
    return list(mapping(html, prefix).values())


__all__ = ["mapping", "values"]
