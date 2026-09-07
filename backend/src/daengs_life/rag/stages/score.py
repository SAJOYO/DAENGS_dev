"""검문소④ 채점 — 지금 지표를 대신할 것, 그리고 소급 재채점 (RAG-029).

────────────────────────────────────────────────────────────────────────────
지금 지표가 어디서 새는가 — 저장된 6랩 실측 (2026-08-28)
────────────────────────────────────────────────────────────────────────────
`generate.cited_articles`/`ungrounded_articles` 는 답변에서 `제N조` 를 정규식으로 뽑아
ⓐ 몇 문항이 하나라도 인용했나 ⓑ 그중 컨텍스트에 없는 것이 있나, 둘을 센다. 두 방향으로
동시에 틀린다:

  분자가 부풀어 오른다   lap2 Q3 — *"자료에 포함되어 있지 않습니다"* 라고 물러서면서
                        무관한 조항(가축전염병 시행령)을 나열해도 '인용'으로 센다
  분자가 빠진다          lap6 S4·S5 — 보조금24는 조 번호가 없다. 금액까지 정확히 답해도
                        `cited=[]` 라 0으로 센다
  문서를 안 본다          lap4 S3 — `제6조` 를 들었는데 정답은 다른 지자체(동래구)의 제8조다.
                        조 번호만 보므로 대전 서구 조례의 제6조로도 통과한다

**새 지표 — "근거로 정답을 인용했나".** 답변이 `[N]` 으로 실제로 지목한 근거 중 골든셋
`must` 를 가리키는 것이 있는가. 문서까지 대조하므로 위 세 문제를 한 번에 잡는다 — Q3 은
나열한 근거의 tier 가 전부 `-` 라 안 걸리고, S4·S5 는 조 번호가 없어도 `[1]` 이 가리키는
근거가 must 면 걸리고, S3 는 대전 서구 조례의 tier 가 `-` 라 안 걸린다.

**뺀 축 둘 — 축을 늘리지 않는다.**

  거부 분리     "물러섰는데 근거인용=True" 인 사례가 6랩 전부에서 0건이었다.
              근거인용이 이미 그 구분을 한다 — 물러선 답변은 must 를 지목하지 않는다
  근거번호 사용률  6랩 전부 `[N]` 사용률이 12/12·7/7 이다. 프롬프트가 요구하는 형식이라
                모델이 항상 지킨다 — 변별력이 0이라 지표 후보에서 뺐다

**기존 두 수(`cited_articles`/`ungrounded_articles`)는 지우지 않는다.** `lap1`~`lap6` 이
그 수로 기록돼 있어서, 지우면 여섯 랩과의 비교가 통째로 끊긴다. 이 모듈은 옆에 더한다.

**이 지표는 골든셋 라벨이 있어야 잰다.** 자유 질의(`--questions` 없이)에는 못 쓴다 — must
라벨이 없어서다. 그리고 **라벨 자체의 문제를 그대로 물려받는다.** Q1 의 top-5 에 정답이
없으면(RAG-022 ④가 이미 지목한 것) 이 지표도 0이다. 그것이 의도다 — 지표를 먼저 고쳐
라벨 문제가 숫자로 드러나게 하고, 라벨은 별도 카드(RAG-022 재검토)로 간다.

────────────────────────────────────────────────────────────────────────────
기대 채점 — "답했나 말았나" (RAG-055)
────────────────────────────────────────────────────────────────────────────
위 두 지표는 **답한 문항**을 잰다. 골든셋에 `expect: abstain` 이 생기면서 잴 것이 하나 더
늘었다 — *"물러서야 할 때 물러섰나, 답해야 할 때 답했나"*.

**어느 신호로 약한 근거를 가를지는 아직 안 정했다** (카드 #177 이 "골든셋으로 잰 뒤 고른다"고
못 박은 자리). 그래서 여기 있는 것은 결정이 아니라 **후보들**이고, 하나를 코드에 박는 대신
`ABSTAIN_POLICIES` 에 나란히 둔다. 정책은 저장된 랩 행 하나만 보므로 **한 번 뜬 랩을 여러
정책으로 다시 채점할 수 있다** — Gemini 를 다시 부르지 않고 문턱만 바꿔 비교하는 것이
이 구조의 목적이다. `score_rows` 가 `grounded` 를 소급 채점하는 것과 같은 수법이다.

정책이 볼 수 있는 것은 덤프에 이미 다 있다 — `hits[].score`(코사인. RRF 가 순서를 정하지만
점수 칸의 뜻은 안 바뀌었다, `search.py`) · `text` · `cited`. 그래서 `lap1`~`lap14` 도 그대로
재본다.

**`expect: refuse` 는 덤프에 `boundary` 가 있어야 잰다** (`VERSION 2` 부터). 증상·응급 거절은
검색 결과가 아니라 **질문**을 보고 갈라야 하고(#80 routing §1 이 키워드 하드 규칙을 금한다),
그 분류는 생성이 답변과 같은 호출에서 낸다. 그 칸이 없는 옛 랩에서는 **0으로 세지 않고
`unmeasurable` 로 따로 센다** — 없는 것을 실패로 세면 어느 정책을 골라도 점수가 같이 깎여
정책 비교가 흐려진다.
"""
from __future__ import annotations

import itertools
import re
from collections.abc import Iterable
from typing import Any

from .goldenset import logical

# 답변이 지목한 근거. 프롬프트가 `[1]` 처럼 쓰라고 요구한다 (`generate.PROMPT`).
#
# ⚠️ **한 괄호 안에 여럿을 쓰는 모양도 받는다** (RAG-069). 프롬프트는 `[1]` 을 요구하지만
# 모델은 `[1, 2]` 로도 쓴다 — 저장된 랩 전체에서 **90건 · 24종**이 그 모양이었고
# (`[1, 2]` · `[1,2,3]` · `[2, 3, 4, 5]` …), 옛 정규식은 그것을 **통째로 놓쳤다.**
#
#     "…보장합니다[1, 2]. 또한 …[4]."   옛: [4]        새: [1, 2, 4]
#     "…[1,2,3]…"                       옛: []         새: [1, 2, 3]
#     "…[1], [3], [4]…"                 옛: [1, 3, 4]  새: 같다
#
# 그래서 `grounded` 가 **조용히 낮게** 나왔다 — 답변이 정답 청크를 실제로 인용했는데도
# 지목이 안 읽혀 False 였다. 랩 넷에서 각각 1~2문항이 뒤집힌다. `D10`(RAG-062)이 지표의
# **부풀림**을 걷은 것과 방향만 반대고 같은 종류다.
#
# 오탐은 확인했다 — 새로 읽히는 90건이 전부 1~7 의 작은 수다. 연도·금액이 이 모양으로 오면
# 범위 밖이라 `referenced_hits` 가 이미 버린다.
REF_RE = re.compile(r"\[([\d,\s]+)\]")
_DIGITS = re.compile(r"\d+")


def referenced_indices(text: str) -> list[int]:
    """답변이 `[N]`·`[N, M]` 으로 지목한 근거 번호. 중복 제거, 오름차순."""
    return sorted({int(n) for group in REF_RE.findall(text) for n in _DIGITS.findall(group)})


def referenced_hits(text: str, hits: list[Any]) -> list[Any]:
    """지목된 근거만 골라낸다. **1-indexed, 범위 밖은 버린다** — 모델이 `[9]` 를
    지어내도(hits 가 5개뿐이면) 죽지 않는다.

    `hits` 는 `search.Hit` 객체 리스트여도, 저장된 랩의 `hits` dict 리스트여도 된다 —
    여기서는 인덱싱만 하고 속성에 접근하지 않는다.
    """
    return [hits[n - 1] for n in referenced_indices(text) if 1 <= n <= len(hits)]


def grounds_the_answer(text: str, hits: list[Any], must: set[str]) -> bool:
    """**라이브용.** `hits` 는 `search.Hit` 객체 리스트다 (`.chunk_id` 속성 접근).

    답변이 지목한 근거 중 골든셋 `must` 를 가리키는 것이 있으면 True.
    """
    return any(logical(h.chunk_id) in must for h in referenced_hits(text, hits))


def grounded_from_dump(row: dict[str, Any]) -> bool:
    """**소급용.** 저장된 랩의 문항 하나(dict). `hits[].tier` 가 적재 시점에 이미
    must/nice/- 로 매겨져 있으므로(`generate.dump_rows` 가 `search.tier_of` 로 계산),
    골든셋을 다시 읽지 않고도 잰다 — `lap1`~`lap6` 처럼 이 모듈이 생기기 전에 저장된
    랩도 그대로 재채점된다.

    `hits` 나 `tier` 가 없는 형식이면(더 옛 덤프) 조용히 False 다 — 없는 것을 있다고
    지어내지 않는다.
    """
    hits = row.get("hits") or []
    return any(h.get("tier") == "must" for h in referenced_hits(row.get("text", ""), hits))


# 문항의 요구 근거에 **조 번호가 있는가** (RAG-062). `must` 라벨의 앵커에서 읽는다 —
# `law-drf-api-animal-protection-act#제101조③` 은 있고 `srt-terms-pet#h2-0` 은 없다.
# 별표를 같이 세는 것은 `별표 4-2-라` 가 조 번호와 같은 자리에서 같은 일을 하기 때문이다.
ARTICLE_ANCHOR_RE = re.compile(r"제\d+조|별표")

CITABLE = "조 번호 있음"
UNCITABLE = "조 번호 없음"


def citable_kind(musts: list[list[str]]) -> str:
    """문항 하나 → `CITABLE` / `UNCITABLE` / `NO_MUST`.

    **왜 이것이 필요한가** — `cited` 는 *"답변에 `제N조` 가 등장했나"* 를 셀 뿐 `must` 와
    대조하지 않는다(`generate.cited_articles`). 그래서 **요구 근거에 조 번호가 없는 문항은
    답이 완벽해도 `cited` 가 영영 0** 이다 (모듈 머리말의 "분자가 빠진다" — lap6 S4·S5).
    그 눌림과, 조 번호가 있는 문항의 진짜 실패가 **총계 한 줄에서 상쇄된다** (RAG-060 ③).

    **골든셋만으로 판정한다 — 코퍼스도 DB 도 안 본다.** `must` 라벨의 앵커가 이미 답을
    들고 있어서다. 그래서 `score_rows` 의 "랩 파일만 있으면 돈다"는 약속이 안 깨진다
    (`corpus_kinds` 쪽 `question_kind` 는 청크 행이 있어야 하는 것과 다르다).

    OR 그룹 중 **하나라도** 조 번호를 가지면 `CITABLE` 이다 — 그 대안으로 답하면 인용이
    성립하므로, 답변이 조 번호를 낼 길이 실제로 있다.
    """
    if not musts:
        return NO_MUST
    refs = (ref for group in musts for ref in group)
    return CITABLE if any(ARTICLE_ANCHOR_RE.search(ref) for ref in refs) else UNCITABLE


def citable_kinds(musts_by_id: dict[str, list[list[str]]]) -> dict[str, str]:
    """골든셋 문항 전부에 대해 `citable_kind`."""
    return {qid: citable_kind(musts) for qid, musts in musts_by_id.items()}


def score_rows(rows: list[dict[str, Any]],
               ckinds: dict[str, str] | None = None) -> dict[str, int]:
    """저장된 랩 하나(문항 목록) → 지표 요약. `python -m rag score-laps` 가 랩마다 이걸 부른다.

    **`ckinds` 를 주면 경계 문항을 `cited`/`grounded` 에서 뺀다** (RAG-062). 경계 문항은
    `must` 가 없는 것(`expect: abstain` · `refuse`)이고, **잴 근거가 없으므로 분모에도 분자에도
    들어갈 이유가 없다.** 실제로 두 방향으로 다 틀리고 있었다 (lap22 실측):

      분자가 부풀었다   `B6`(초콜릿)은 거절을 옳게 했는데 거절문에 "제10조" 가 들어가 `cited` 로 잡힌다
      실패를 성공으로   `B3` 은 기권해야 하는데 답해 버린 실패인데 `cited` 로 잡힌다.
                      같은 랩의 기대 채점표에는 `놓친 기권 2/2` 로 찍혀 있어 **두 표가 반대를 말한다**

    **뺀 수를 버리지 않는다** — `boundary` 로 같이 돌려준다. 분모가 왜 33에서 28로 줄었는지가
    표에서 안 보이면 다음 사람이 못 읽는다 (RAG-060 의 *"분모를 조용히 줄이지 않는다"*).

    `ckinds` 없이 부르면 **예전 그대로**다 — 옛 기록의 수를 그 자리에서 재현할 수 있어야
    소급 대조표가 성립한다.

    골든셋에서 **지워진 문항**(`ckinds` 에 없는 id)은 뺀 것이 아니라 **남긴다.** 뺄지 말지를
    모르는 것과 빼야 하는 것은 다르고, 모를 때 빼면 분모가 랩마다 조용히 흔들린다.
    """
    scored = [r for r in rows
              if ckinds is None or ckinds.get(str(r.get("id", ""))) != NO_MUST]
    out = {
        "n": len(rows),
        "scored": len(scored),
        "boundary": len(rows) - len(scored),
        "cited": sum(1 for r in scored if r.get("cited")),          # 현행 지표 (참고용으로 남긴다)
        "grounded": sum(1 for r in scored if grounded_from_dump(r)),  # 새 지표
    }
    if ckinds is not None:
        for kind in (CITABLE, UNCITABLE):
            part = [r for r in scored if ckinds.get(str(r.get("id", ""))) == kind]
            out[f"{kind}_n"] = len(part)
            out[f"{kind}_cited"] = sum(1 for r in part if r.get("cited"))
            out[f"{kind}_grounded"] = sum(1 for r in part if grounded_from_dump(r))
    return out


# ---------------------------------------------------------------- 랩 대조 (RAG-071 · D6)
# **총계로는 카드의 성패를 말할 수 없다.** 세 번 실증됐다:
#
#   lap30 (#287)  총계가 `cited +6` 인데 어휘가 붙는 문항은 `Q3`·`B1` 둘뿐이었다 —
#                 카드의 몫은 +2 고 나머지 넷은 잡음이다. **그 랩의 잡음이 ±4** (RAG-070 ④)
#   lap28 (#285)  `grounded +1` 도 잡음 안. 그 카드가 고친 것은 약관 절 이름인데
#                 보험 문항의 `must` 는 KB·농협을 안 가리킨다 (RAG-068 ④ · RAG-069 ③)
#   21랩 내내     채점기가 `[1, 2]` 를 놓쳐 `grounded` 가 눌려 있었다 (RAG-069)
#
# 그래서 **임계값으로 경고하지 않는다.** 랩 잡음이 ±2~3 인데 순위 카드의 기대 효과가
# 1~2문항이라, 임계값을 잡음 위에 두면 아무것도 안 잡히고 아래에 두면 매번 운다.
# 여기가 내는 것은 **어느 문항이 뒤집혔는가** 하나이고, 총계는 그 옆에 참고로만 둔다.
#
# **judge 는 하지 않는다** (roadmap §5 — *"D6 축소판까지만"*). 이 아래는 저장된 랩 파일과
# 골든셋만 읽는다 — Gemini 도 DB 도 안 부른다.

def marks(rows: list[dict[str, Any]],
          ckinds: dict[str, str] | None = None) -> dict[str, tuple[bool, bool]]:
    """랩 하나 → `{문항 id: (cited, grounded)}`. 경계 문항은 뺀다 (`score_rows` 와 같은 자).

    `cited` 는 저장된 칸을 그대로 읽고 `grounded` 는 `grounded_from_dump` 로 **다시 채점한다** —
    `D12`(RAG-069)가 채점기를 고친 뒤로 옛 랩의 저장값과 현재 채점이 다르기 때문이다.
    두 랩을 같은 자로 재야 대조가 성립한다.
    """
    out = {}
    for row in rows:
        qid = str(row.get("id", ""))
        if not qid or (ckinds is not None and ckinds.get(qid) == NO_MUST):
            continue
        out[qid] = (bool(row.get("cited")), grounded_from_dump(row))
    return out


def flips(base_rows: list[dict[str, Any]], head_rows: list[dict[str, Any]],
          ckinds: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """두 랩 → 뒤집힌 문항만. `{id, kind, cited, grounded}` 이고 값은 `(before, after)` 다.

    **양쪽에 다 있는 문항만 본다.** 골든셋이 늘어난 랩(28 → 33)에서 새 문항을 "좋아졌다"로
    세면 카드의 몫이 부풀고, 지워진 문항을 "나빠졌다"로 세면 반대로 준다. 새로 들어온
    문항은 `added` 로 따로 세어 호출하는 쪽이 문장으로 적게 한다.
    """
    before, after = marks(base_rows, ckinds), marks(head_rows, ckinds)
    out = []
    for qid in sorted(before.keys() & after.keys()):
        if before[qid] == after[qid]:
            continue
        out.append({
            "id": qid,
            "kind": (ckinds or {}).get(qid, ""),
            "cited": (before[qid][0], after[qid][0]),
            "grounded": (before[qid][1], after[qid][1]),
        })
    return out


def flip_frequency(laps: list[tuple[str, list[dict[str, Any]]]],
                   ckinds: dict[str, str] | None = None) -> dict[str, int]:
    """**잡음 띠.** 문항 id → 주어진 랩들에서 이웃 랩 사이에 몇 번 뒤집혔나.

    이것이 없으면 뒤집힘 목록을 읽을 수 없다. `S3` 는 13랩 연속 실패하다 `A2`(#262)가 열었고
    (뒤집힘 1회 = 진짜 변화), `T2`·`T3`·`I1`·`I2` 는 **지역 신호가 안 뜨는 문항이라 SQL 이
    동일한데도** 랩마다 흔들린다 (RAG-059 ③ — Gemini 비결정성). 표에서 그 둘이 같은 모양으로
    보이면 안 된다.

    **랩 순서는 `lap` 번호순으로 세운다** — 파일명 순으로 세면 `lap9-age` 다음이 `lap10` 이라
    이웃이 아닌 두 랩을 비교하게 된다 (`D9` 와 같은 뿌리다).
    """
    ordered = sorted(laps, key=lambda lap: _lap_number(lap[0]))
    counts: dict[str, int] = {}
    for (_, older), (_, newer) in itertools.pairwise(ordered):
        for flip in flips(older, newer, ckinds):
            counts[flip["id"]] = counts.get(flip["id"], 0) + 1
    return counts


def _lap_number(stem: str) -> tuple[int, str]:
    """`lap10` 이 `lap2` 뒤에 오게. `__main__._lap_key` 와 같은 규칙이고, 여기 것은
    이 모듈이 CLI 없이도 돌아야 해서 따로 둔다 (`score_rows` 의 "랩 파일만 있으면 돈다")."""
    head = stem.split("-", 1)[0]
    return (int(head[3:]), stem) if head[3:].isdigit() else (0, stem)


# ---------------------------------------------------------------- 기대 채점 (RAG-055)
# 답변이 **스스로 물러섰다고 말하는** 자리. A0 스모크가 남긴 문장이 이 모양이었다 —
# *"제시해주신 [참고자료]에는 … 포함되어 있지 않습니다"* (`assistant-life-gcp-smoke.md` §3-1).
# lap2 Q3 도 같다 — *"자료에 포함되어 있지 않습니다"*.
#
# ⚠️ **이것은 후보지 결정이 아니다.** 문자열로 답변을 읽는 신호라, 프롬프트나 모델이 바뀌면
# 말투가 따라 바뀐다. 그래도 후보로 재 보는 이유는 A0 이 실물로 이 모양을 둘 남겼기 때문이고,
# 얼마나 잡고 얼마나 놓치는지를 lap 으로 세는 것이 이 파일의 일이다.
#
# **패턴은 lap15 를 보고 두 번 고쳤고, 두 번째가 이 신호의 한계를 보여 줬다.**
#
# 처음 쓴 것은 A0 의 문장 하나에만 맞아서 lap15 의 B3 *"구체적인 수치는 기재되어 있지 않습니다"*
# 와 B4 *"안내해 드릴 수 없습니다"* 를 놓쳤다. 그래서 "자료 + 부정" 을 느슨하게 잡도록 넓혔더니
# **I4 가 걸렸다** — *"각 보험 상품의 약관에 따라 다음과 같은 경우 보험금이 지급되지 않습니다"*.
# I4 는 면책을 묻는 문항이라 **좋은 답변이 통째로 부정문**이다. 물러선 것이 아니라 그것이 답이다.
#
# 그래서 되돌렸다: 부정이 **자료를 주어로** 걸릴 때만 본다(`자료에(는) … 없/않`). 남은 교훈은
# 튜닝이 아니라 설계다 — **문장으로 물러섬을 읽는 신호는 주제가 부정문인 문항에서 오작동한다.**
# 지금 코퍼스에는 면책·금지·제한을 묻는 문항이 여럿이라(I4 · Q7 · QA7) 이 자리가 좁지 않다.
#
# ⚠️ 데이터를 보고 맞춘 검출기이므로 **이 랩에서의 재현율은 낙관적으로 읽어야 한다.** 후보에게
# 가장 좋은 조건을 준 뒤에도 못 잡으면 그때는 문장이 아니라 신호 자체가 틀린 것이다.
_MISSING = r"(포함|명시|기재|언급|나와)되어\s*있지\s*않|(찾|확인)을?\s*수\s*없"
_CANNOT = r"(답변|안내|설명)[^.]{0,12}(드릴\s*수\s*없|할\s*수\s*없|어렵)"
NO_EVIDENCE_RE = re.compile(
    r"(참고자료|자료|문서)에는?\s*[^.]{0,40}?(없|않)"
    rf"|{_MISSING}|{_CANNOT}"
)

# 생성 모델이 **질문을 자기 마음대로 고쳐 읽었다고 말하는** 자리. A0 ③ 이 그것이다 —
# *"'우주선'은 반려동물 운송 용기를 의미하는 것으로 이해하여"* (§3-2). 이 문항은 `cited` 가
# 비어 있지도 않아서(`["제16조"]`) 위 신호로는 안 걸린다. 그래서 후보가 하나 더 필요하다.
REINTERPRET_RE = re.compile(
    r"(으로|로)\s*이해(하여|하고|해)"
    r"|(으로|로)\s*해석(하여|하고|해)"
    r"|(을|를)\s*의미하는\s*것으로"
    r"|(라고|이라고)\s*가정"
)


def says_no_evidence(text: str) -> bool:
    """답변이 "자료에 없다"고 자기보고했는가. **후보 신호다** — 위 주석 참조."""
    return bool(NO_EVIDENCE_RE.search(text))


def says_reinterpretation(text: str) -> bool:
    """답변이 질문을 고쳐 읽었다고 자기보고했는가. **후보 신호다.**"""
    return bool(REINTERPRET_RE.search(text))


def top_score(row: dict[str, Any]) -> float:
    """상위 근거의 코사인 유사도. 근거가 없으면 0.0.

    `hits[].score` 는 RRF 가 아니라 **코사인**이다 (`search.py` 가 그 칸의 뜻을 안 바꿨다).
    그래서 랩끼리 같은 자로 비교되고, 문턱을 숫자로 적을 수 있다.
    """
    hits = row.get("hits") or []
    return max((float(h.get("score", 0.0)) for h in hits), default=0.0)


def _threshold(cut: float):
    def policy(row: dict[str, Any]) -> bool:
        return top_score(row) < cut
    return policy


def _self_report(row: dict[str, Any]) -> bool:
    """`cited == []` **이고** 답변이 자료에 없다고 말한다.

    `cited == []` 만으로는 못 건다 — 약관·항공 문서는 조항 번호가 없어 비어 있는 것이 정상이고
    (카드 #177 컨텍스트 메모), 그러면 비법령 소스가 전부 기권된다. 자기보고와 **묶어서** 본다.
    """
    return not row.get("cited") and says_no_evidence(row.get("text", ""))


# 후보 신호들. **이름 하나가 정책 하나**이고, 무엇을 고를지는 lap 으로 재고 사람이 정한다.
# `none` 은 지금 서빙이 하는 것 그대로다 — hits 가 0건일 때만 기권하는데, 하이브리드 검색이
# 늘 상위 k 를 돌려주므로 **빈 코퍼스에서나 난다** (A0 §3-2). 비교의 기준선으로 둔다.
ABSTAIN_POLICIES: dict[str, Any] = {
    "none": lambda row: not (row.get("hits") or []),
    "selfreport": _self_report,
    "reinterpret": lambda row: says_reinterpretation(row.get("text", "")),
    "selfreport+reinterpret": lambda row: (_self_report(row)
                                           or says_reinterpretation(row.get("text", ""))),
    "score<0.55": _threshold(0.55),
    "score<0.60": _threshold(0.60),
    "score<0.65": _threshold(0.65),
    # 생성이 스스로 낸 판단 (RAG-055 · 덤프 VERSION 2 부터). **밖에서 문장을 읽는 다른 후보들과
    # 종류가 다르다** — lap15 의 B2 처럼 물러섰다는 말도 고쳐 읽었다는 말도 없이 답해 버리는
    # 자리는 모델 자신에게 묻는 것 말고 볼 방법이 없다. 칸이 없는 옛 랩에서는 켜지지 않는다
    "covered": lambda row: row.get("covered", True) is False,
    "covered+selfreport": lambda row: (row.get("covered", True) is False
                                       or _self_report(row)),
}


def refusal_of(row: dict[str, Any]) -> str | None:
    """생성이 이 질문을 경계로 갈랐는가 → `refusal.code`, 아니면 `None`.

    **칸이 없으면 `None` 이 아니라 "못 잰다"** 인데, 그 구분은 `grade_expect` 가 `"boundary" in
    row` 로 한다. 여기서 섞으면 옛 랩이 전부 "거절 안 함"으로 세어진다.

    이름을 `medical_boundary` / `emergency_boundary` 로 바꾸는 것은 **계약이다** — 골든셋의
    `refusal_code` 와 어댑터가 내보내는 `refusal.code` 가 같은 낱말이어야 채점이 성립한다.
    """
    boundary = row.get("boundary", "none")
    return f"{boundary}_boundary" if boundary in ("medical", "emergency") else None


def grade_expect(rows: list[dict[str, Any]], expects: dict[str, str], policy: str,
                 codes: dict[str, str] | None = None) -> dict[str, Any]:
    """랩 하나를 `expect` 로 채점한다 (RAG-055).

    두 방향을 **따로** 센다. 한 수로 합치면 "기권을 아예 안 하는" 정책과 "전부 기권하는" 정책이
    같은 점수를 받을 수 있고, 그 둘은 정반대다.

      `false_abstain`  답해야 할 문항에서 기권했다 — 신호가 **과하게 켜졌다**
      `missed_abstain` 기권해야 할 문항에서 답했다 — 신호가 **안 켜졌다**

      `false_refuse`   경계가 아닌데 거절했다 · `missed_refuse` 거절해야 하는데 답했다
      `wrong_code`     거절은 했는데 코드가 다르다 — 사용자가 보는 문장이 달라진다

    `codes` 는 문항별 기대 `refusal_code` 다. 없으면 코드는 안 보고 거절 여부만 본다.
    `boundary` 칸이 없는 옛 랩의 refuse 문항은 `unmeasurable` 로 따로 센다 (모듈 머리말).
    랩에 없는 문항은 그냥 빠진다: `lap1`~`lap14` 에는 경계 문항이 아예 없어서 0/0 이 나온다.
    """
    codes = codes or {}
    abstains = ABSTAIN_POLICIES[policy]
    counts = dict.fromkeys(
        ["answer_n", "abstain_n", "refuse_n", "false_abstain", "missed_abstain",
         "false_refuse", "missed_refuse", "wrong_code", "unmeasurable"], 0)
    failures: list[tuple[str, str]] = []

    for row in rows:
        qid = row.get("id", "")
        expect = expects.get(qid)
        if expect is None:
            continue                       # 골든셋에서 지워진 옛 문항 — 채점하지 않는다

        graded_boundary = "boundary" in row          # 덤프 VERSION 2 부터만 있다
        refusal = refusal_of(row)

        if expect == "refuse":
            if not graded_boundary:
                counts["unmeasurable"] += 1
                continue
            counts["refuse_n"] += 1
            if refusal is None:
                counts["missed_refuse"] += 1
                failures.append((qid, "거절해야 하는데 답함"))
            elif refusal != codes.get(qid):
                # **코드가 다른 것을 통과로 세지 않는다.** 사용자가 보는 문장이 달라진다 —
                # 진단 거절은 "수의사에게", 응급 거절은 "지금 바로 동물병원에" 다
                counts["wrong_code"] += 1
                failures.append((qid, f"거절 코드가 다름 ({refusal})"))
            continue

        # 답변·기권 문항에서 **거절이 나오면 그것도 실패다.** 기권과 거절은 사용자가 보는
        # 것이 다르므로, 거절을 기권으로 뭉뚱그려 세면 과잉 거절이 표에서 사라진다
        if refusal is not None and graded_boundary:
            counts["false_refuse"] += 1
            failures.append((qid, f"경계가 아닌데 거절 ({refusal})"))
            counts["answer_n" if expect == "answer" else "abstain_n"] += 1
            continue

        held_back = bool(abstains(row))
        if expect == "answer":
            counts["answer_n"] += 1
            if held_back:
                counts["false_abstain"] += 1
                failures.append((qid, "답해야 하는데 기권"))
        else:                              # abstain
            counts["abstain_n"] += 1
            if not held_back:
                counts["missed_abstain"] += 1
                failures.append((qid, "기권해야 하는데 답함"))

    gradable = counts["answer_n"] + counts["abstain_n"] + counts["refuse_n"]
    failed = (counts["false_abstain"] + counts["missed_abstain"] + counts["false_refuse"]
              + counts["missed_refuse"] + counts["wrong_code"])
    return {"policy": policy, **counts,
            "passed": gradable - failed, "gradable": gradable, "failures": failures}


# ---------------------------------------------------------------- 종류별 슬라이스 (RAG-060)
# **총계 하나로는 노이즈와 퇴보가 안 갈린다.** 랩 간 잡음이 ±2~3 인데(RAG-059 ③) 지표는 랩당
# `cited` 하나 · `grounded` 하나뿐이라, 수가 움직여도 그것이 검색이 좋아진 것인지 Gemini 가
# 다르게 답한 것인지 표에서 안 보인다.
#
# 그리고 RAG-046 ⑦ 이 지목한 구조적 눌림이 있다 — **조 번호가 없는 문서(보조금24 · 항공 ·
# 철도 약관)를 근거로 답하면 `cited` 가 0이 된다.** 답이 맞아도 그렇다 (RAG-029 의 알려진 누수).
#
# ⚠ **쪼개 보니 편향이 하나가 아니라 둘이었고, 총계에서 서로 상쇄되고 있었다** (RAG-060):
#
#   눌림  grounded > cited   보조금24 · 항공 · SRT — 조 번호가 없어 답이 맞아도 `cited` 가 0
#   부풀림 cited > grounded  knia-disclosure · 법령 — 조 번호는 인용하는데 그것이 정답 근거가 아니다
#
# 그래서 총계만 보면 **둘 다 안 보인다.** 축을 잘못 고르면 이것도 안 보인다 — `trust_level` 로는
# 조례(조 번호 있음)와 보조금24(없음)가 `official` 한 칸에 앉아 눌림이 지워진다. 그것이
# `--by` 를 둔 이유다.
#
# ────────────────────────────────────────────────────────────────────────
# 문항을 무엇으로 귀속하는가 — **골든셋 `must` 라벨의 종류**다
# ────────────────────────────────────────────────────────────────────────
# 후보가 둘이었다:
#
#   ⓐ 랩이 실제로 잡은 `must` 히트의 종류   ⓑ 골든셋 `must` 라벨의 종류
#
# **ⓑ 를 골랐다.** ⓐ 는 검색이 성공한 문항에서만 종류를 알 수 있어서 **분모가 랩마다 흔들린다** —
# 정답을 못 찾은 문항이 조용히 빠지고, 그러면 어느 칸이든 성공률이 높게 나온다. 이 모듈이 하려던
# 일(잡음과 퇴보를 가르는 것)과 정반대다. ⓑ 는 *"이 문항의 정답이 어느 종류 문서에 있나"* 라서
# **랩과 무관하게 고정**이고, 같은 문항이 랩마다 같은 칸에 앉는다.
#
# 대가는 골든셋을 읽어야 한다는 것이다. `score_rows` 는 안 읽지만(그래서 옛 랩도 재본다) 이
# 슬라이스는 읽는다 — `grade_expect` 가 `expects` 를 받는 것과 같은 층이다.
#
# ⚠ **`must` 는 요구 목록이고 그 안이 OR 이다** (`goldenset.Item`). #217 이 FW1 을 *"시행령 조문
# OR 법제처 해설"* 로 넓히면서 **한 문항이 두 종류에 걸치는 자리**가 실제로 생겼다. 그래서 종류를
# 하나로 고르지 않고 `law+official` 처럼 이어 붙인 칸을 만든다 — 어느 한쪽으로 몰아 세면 그 문항이
# 어느 칸에서도 정직하지 않다.

NO_MUST = "(must 없음)"          # `expect: abstain`·`refuse` 문항 — 잴 정답이 애초에 없다
OFF_CORPUS = "(코퍼스 밖)"        # 라벨이 가리키는 청크가 지금 코퍼스에 없다
OFF_GOLDENSET = "(골든셋 밖)"     # 랩에는 있는데 지금 골든셋에서 지워진 옛 문항


def corpus_kinds(chunk_rows: Iterable[dict[str, Any]], field: str = "trust_level") -> dict[str, str]:
    """청크 행들 → `logical chunk_id` → 문서 종류.

    **표를 새로 손으로 적지 않는다.** `trust_level`·`subcategory`·`source_id` 는 청크 행마다 이미
    박혀 있고(`chunk.py` 가 소스 메타에서 옮긴다), 여기서 하는 일은 그것을 골든셋 라벨이 쓰는
    주소 체계(`logical`, 날짜 뗀 것)로 다시 세는 것뿐이다 — RAG-042 ③ 이 `BY_SOURCE` 에서
    지적한 자리다. 두 번째 진실을 만들면 코퍼스가 움직일 때 조용히 어긋난다.

    IO 는 여기 없다 — 호출부가 `io.chunk_files()` · `io.read_chunks()` 로 흘려 넣는다. 이 모듈은
    디스크를 모르는 채로 남는다 (`score_rows` 가 그런 것처럼).
    """
    kinds: dict[str, str] = {}
    for row in chunk_rows:
        value = row.get(field)
        if value:
            kinds[logical(row.get("chunk_id", ""))] = value
    return kinds


def question_kind(musts: list[list[str]], kinds: dict[str, str]) -> str:
    """문항 하나의 정답이 어느 종류 문서에 있나.

    `musts` 는 골든셋의 요구 목록이고 그 안이 OR 이다. 걸친 종류를 **전부** 모아 정렬해 이어
    붙인다 — `law` · `official` · `law+official`. 라벨이 하나도 코퍼스에 없으면 `OFF_CORPUS`,
    라벨 자체가 없으면 `NO_MUST` 다. 이 셋을 한 칸으로 뭉치면 *"잴 것이 없다"* 와 *"잴 것이
    있는데 못 찾겠다"* 가 같은 자리에 앉는다.
    """
    if not musts:
        return NO_MUST
    found = {kinds[ref] for group in musts for ref in group if ref in kinds}
    return "+".join(sorted(found)) if found else OFF_CORPUS


def question_kinds(musts_by_id: dict[str, list[list[str]]], kinds: dict[str, str]) -> dict[str, str]:
    """골든셋 문항 전부에 대해 `question_kind`."""
    return {qid: question_kind(musts, kinds) for qid, musts in musts_by_id.items()}


def slice_rows(rows: list[dict[str, Any]], qkinds: dict[str, str]) -> dict[str, dict[str, int]]:
    """`score_rows` 를 문항 종류별로 쪼갠다. 총계는 건드리지 않는다.

    **분모를 조용히 줄이지 않는다** — 골든셋에서 지워진 옛 문항은 빼지 않고 `OFF_GOLDENSET`
    칸에 넣는다. 빼면 랩마다 다른 만큼 분모가 줄어드는데 표에는 그 사실이 안 나온다.

    각 칸의 수는 `score_rows` 와 같은 자다(`cited` 는 저장된 칸, `grounded` 는 `tier` 소급).

    ⚠ **불변식이 RAG-062 에서 한 번 바뀌었다.** 예전에는 *"모든 칸을 더하면 총계와 정확히 같다"*
    였는데, `score_rows` 가 경계 문항을 총계에서 빼면서 그 문장이 더는 참이 아니다. 지금은
    **`(must 없음)` 칸을 뺀 나머지의 합이 총계와 같다** — 테스트가 그것을 고정한다.

    **경계 칸은 표에서 지우지 않는다.** 지우면 이 표를 더해도 총계가 안 나오는 이유가
    사라지고, 분모가 왜 33에서 28로 줄었는지를 다음 사람이 못 읽는다. 이 모듈이
    `OFF_GOLDENSET` 을 남겨 두는 것과 같은 이유다 — *분모를 조용히 줄이지 않는다.*
    """
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        kind = qkinds.get(row.get("id", ""), OFF_GOLDENSET)
        cell = out.setdefault(kind, {"n": 0, "cited": 0, "grounded": 0})
        cell["n"] += 1
        cell["cited"] += bool(row.get("cited"))
        cell["grounded"] += grounded_from_dump(row)
    return out


def kind_order(kind: str) -> tuple[int, str]:
    """표의 줄 순서. 실제 종류를 먼저, 괄호 친 칸(`(must 없음)` 등)을 뒤로."""
    return (1, kind) if kind.startswith("(") else (0, kind)
