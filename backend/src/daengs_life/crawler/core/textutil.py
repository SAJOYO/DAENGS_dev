"""소스 공통 텍스트 처리 — 블록 단위 본문 추출과 법령 조항 인용 파싱.

두 가지 모두 "특정 사이트 지식"이 아니라 "한국 법령 문서 공통 지식"이라 core/ 에 둔다
(RAG-009: site-specific 만 소스 모듈, 나머지는 core/ 한 곳).

block_text() 가 필요한 이유:
  조문 인용이 <a> 로 감싸여 있어 get_text("\n") 을 쓰면 "제16조" 와 "제2항" 이 다른 줄로
  쪼개져 인용 파싱이 깨진다. 블록 태그 경계에서만 줄을 나누고 인라인은 공백으로 잇는다.
"""
from __future__ import annotations

import re
from typing import Any

WS = re.compile(r"\s+")

_BLOCK_TAGS = ["p", "div", "li", "ul", "ol", "dl", "dt", "dd", "tr", "table",
               "h1", "h2", "h3", "h4", "h5", "h6"]

# 「법령명」 뒤에 이어지는 조항 나열 (예: 「동물보호법」 제2조 제5호, 제23조 제1항, 101조제2항제5호)
#
# 줄바꿈에서 끊는 이유 — 인용은 한 문장 안에서 끝난다. 줄을 넘게 두면 종결부호가 없는 줄
# ("… 제10조제2항에 따라 …" 뒤 줄바꿈, 다음 줄이 "제15조(등록대상동물의 등록 등)") 에서
# 다음 조문 번호까지 앞 법령의 인용으로 빨아들인다. 법령 원문 6건에서 이런 허위 인용이 19건 나왔다.
# "출처 링크 + 조항 번호 인용" 이 KPI 라 없는 인용을 만드는 것이 못 찾는 것보다 나쁘다.
_RE_LAW = re.compile(r"「([^」\n]{2,40})」\s*((?:제?\s*\d+조(?:의\d+)?[^「。\n]*?)(?=[。.\n]|「|$))")
_RE_ART = re.compile(r"제?\s*(\d+조(?:의\d+)?(?:\s*제\s*\d+항)?(?:\s*제\s*\d+호)?)")


def block_text(box: Any) -> str:
    """블록 요소 경계만 줄바꿈으로, 인라인(a/span 등)은 공백으로 잇는다.

    주의: box 를 제자리에서 수정한다 (br 치환, 블록 태그에 개행 추가).
    """
    for br in box.find_all("br"):
        br.replace_with("\n")
    for tag in box.find_all(_BLOCK_TAGS):
        tag.append("\n")
    lines = [WS.sub(" ", ln).strip() for ln in box.get_text(" ").split("\n")]
    return "\n".join(ln for ln in lines if ln)


def squeeze(text: str) -> str:
    """줄 앞뒤 공백·중복 공백·빈 줄 정리. 노이즈 제거를 끝낸 뒤 마지막에 부른다."""
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r" {2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def cites(text: str) -> list[str]:
    """본문이 「」로 인용한 타 법령의 조항. RAG-004 section 형식(제16조제2항제1호)으로 정규화.

    법령명이 없는 자기 참조("제10조제2항")는 뽑지 않는다 — 어느 법의 조항인지 확정할 수
    없고, 법령 원문에서는 문장마다 나와 노이즈가 된다.
    """
    out: list[str] = []
    for law, tail in _RE_LAW.findall(text):
        for art in _RE_ART.findall(tail):
            c = f"{law} 제{WS.sub('', art)}"
            if c not in out:
                out.append(c)
    return out
