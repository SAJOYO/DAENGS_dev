"""The display boundary: bounded user language, never operational identifiers.

Facts and completion are decided by the receipt renderer. This layer only accepts its
wording or uses an equally grounded fallback; it never asks a model to rewrite facts.
"""

import re
import unicodedata

_INTERNAL = re.compile(
    r"(?<![a-z])(?:session|revision|receipt|snapshot|traceback|timeout|http|json|sql|llm|api)(?![a-z])"
    r"|[a-z]+_[a-z_]+|[0-9a-f]{8}-[0-9a-f-]{27,}"
    r"|세션|리비전|스냅샷|오케스트레|클라이언트|토큰|서버|원천\s*정보|위도|경도|[{}<>`]",
    re.IGNORECASE,
)


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def user_text_allowed(text, *, limit=160):
    text = normalized(text)
    return (
        bool(text)
        and len(text) <= limit
        and not _INTERNAL.search(text)
        and len(re.split(r"(?<=[.!?])\s+", text)) <= 2
    )


def user_text(text, fallback, *, limit=160):
    return normalized(text) if user_text_allowed(text, limit=limit) else fallback
