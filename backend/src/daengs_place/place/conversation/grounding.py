"""Conservative Korean edit grounding; ambiguity never authorizes a place mutation."""

import re


def compact(text):
    return re.sub(r"\s+", "", text).casefold()


def literal_span(query, text):
    # Keep word boundaries: '다' in '달나라', '여기' in a business name, etc. is not a reference.
    pattern = re.escape(text.strip()).replace(r"\ ", r"\s*")
    return bool(
        re.search(r"(?<!\w)" + pattern + r"(?=$|[\s.,!?]|[은는을를이가와과도])", query, re.IGNORECASE)
    )


def assert_direct_request(query):
    # Deliberately decline negated, quoted, conditional and reported instructions.
    # This is a bounded mutation gate, not a general Korean intent classifier.
    text = compact(query)
    if re.search(
        r"""["'“”‘’]|(?:빼|제외하|제거하|숨기|넣|포함하|풀|해제하|초기화하)지"""
        r"|(?:안|못)(?:빼|제외|제거|숨|넣|포함|풀|해제|초기화)"
        r"|말(?:아|라고|라는)|라고(?:했|하|말)|라면|다면|면어떻게|면뭐|필요없|하지마",
        text,
    ):
        raise ValueError("ambiguous or negated place instruction")


def assert_operation(query, edit):
    assert_direct_request(query)
    if compact(edit.operation_quote) not in compact(query):
        raise ValueError("operation evidence absent from latest query")
    pattern = (
        r"빼(?:줘|주|고|달|버|줄)|(?:제외|제거)(?:해|하)|숨겨"
        if edit.operation == "exclude"
        else r"풀(?:어|고|자)|해제|(?:다시)?넣(?:어|고|자)|포함(?:해|하)|제외취소"
    )
    if not re.search(pattern, compact(edit.operation_quote)):
        raise ValueError("no supported explicit place operation")


def assert_restart(query):
    assert_direct_request(query)
    text = compact(query)
    if not ("초기화" in text or re.search(r"제외.*(?:풀|해제|취소).*처음부터", text)):
        raise ValueError("restart needs explicit exploration reset")


def resolve_target(query, target, places, selected):
    if not literal_span(query, target.text):
        raise ValueError("target evidence absent from latest query")
    text = compact(target.text)
    if target.kind == "name":
        matches = tuple(p for p in places if compact(p.name) == text)
    elif target.kind == "selected":
        if text not in {"여기", "거기", "이곳", "그곳", "이장소", "그장소"}:
            raise ValueError("unsupported selected reference")
        matches = tuple(p for p in places if p.key == selected)
    elif target.kind == "ordinal":
        ordinals = {
            "첫": 1,
            "첫번째": 1,
            "두번째": 2,
            "세번째": 3,
            "네번째": 4,
            "다섯번째": 5,
            "여섯번째": 6,
            "일곱번째": 7,
            "여덟번째": 8,
            "아홉번째": 9,
            "열번째": 10,
            "마지막": len(places),
        }
        numeric = re.fullmatch(r"([1-9][0-9]{0,2})(?:번째|번)", text)
        index = int(numeric[1]) if numeric else ordinals.get(text, 0)
        matches = (places[index - 1],) if 1 <= index <= len(places) else ()
    else:
        if text not in {"전체", "전부", "모두", "다"}:
            raise ValueError("unsupported all reference")
        return places
    if len(matches) != 1:
        raise ValueError("missing or ambiguous place target")
    return matches
