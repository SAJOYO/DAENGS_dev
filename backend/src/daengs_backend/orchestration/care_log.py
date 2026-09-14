"""케어 기록 쓰기의 **결정론 부분** — 무엇을 기록할지와, 사용자가 승낙했는지 (#331 후속).

`emergency.py` 와 같은 층·같은 성격입니다: 모델을 안 태우고 원문을 직접 봅니다. 그 자리에
있는 이유가 여기서는 더 강합니다 — 이 판정의 결과는 답변이 아니라 **DB 에 남는 행**입니다.

## 쓰기 경로에는 모델 호출이 **0회** 입니다

| 누가 | 무엇을 |
| --- | --- |
| 이 파일 | 기록하겠다는 말인가(`is_care_log_statement`) · 무엇을(`kind_of`) · 승낙했나(`confirmation_of`) |
| `planner` | 언제(`occurred_at`)·누구(`pet_id`)·멱등키 — 신뢰된 context 와 **서버 시계**만 |

**의미 라우터를 안 쓰는 것이 이 설계의 핵심입니다.** 라우터에 목적지를 하나 더하는 판본을
먼저 그렸다가 물렸습니다. 이유 둘:

1. **쓰기라서요.** `emergency.py` 가 모델 앞에 선 것과 같은 이유이고, 여기서는 결과가
   답변이 아니라 DB 행이라 그 이유가 더 큽니다. 프롬프트 한 줄의 회귀가 남의 강아지
   기록에 밥 한 줄을 더하게 되는 자리를 안 만듭니다.
2. **프롬프트를 고치면 `PROMPT_VERSION` 이 올라갑니다.** 그 상수는 동결된 골드 세트·러너·
   평가 랩 행이 함께 가리키는 핀이라(`semantic.py` 머리말), 올리는 것은 코드 변경이 아니라
   **측정 사건**입니다 — 러너 한 벌과 사람 판정이 따라옵니다. 기능 하나를 붙이려고 그것을
   끌고 오지 않습니다.

대가는 어휘가 모르는 말투를 놓치는 것이고, 놓치면 **오늘과 똑같이** 답합니다 (일반 답변).
못 알아듣는 실패는 조용하지만 해가 없고, 잘못 알아듣는 실패는 DB 에 남습니다.

## 애매하면 안 씁니다

`kind_of` 와 `confirmation_of` 는 둘 다 **모르면 None** 입니다. 그리고 None 의 처리는
"추측" 이 아니라 "기록 화면으로 안내" 입니다 (`planner._care_log_clarify` · `docs/care-events.md`).
오기록은 조용합니다 — 잘못 적힌 밥 한 줄은 아무 오류도 안 내고, 다음 날 "어제 두 번 먹였네" 로
읽힙니다. 그래서 이 파일의 모든 모호함은 **안 쓰는 쪽**으로 떨어집니다. #331 이 채팅 쓰기를
처음부터 안 하겠다고 한 근거 셋 중 "오기록이 로그를 조용히 망친다" 가 이것이고, 확인 단계와
이 규칙이 그 근거에 답하는 자리입니다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal

from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CareLogKind,
    CareLogProposal,
    RouteTrace,
)

__all__ = [
    "KIND_LABELS",
    "MAX_STATEMENT_CHARS",
    "PROPOSAL_TTL",
    "build_care_log_declined_response",
    "confirmation_of",
    "is_care_log_statement",
    "is_proposal_fresh",
    "kind_of",
    "proposal_question",
]

#: 제안이 살아 있는 시간. 이 뒤의 "네" 는 **안 씁니다** (`is_proposal_fresh`).
#:
#: `care_events.occurred_at` 은 앱이 보낸 시각이라 과거를 적을 수 있고 하한이 없습니다
#: (`schemas/care_event.FUTURE_GRACE` 는 앞날만 막습니다). 그래서 어제 받은 제안에 오늘
#: "응" 이라고 답하면 **어제 시각의 밥 한 줄**이 조용히 들어갑니다 — 사용자는 오늘 뭔가를
#: 승낙했다고 생각합니다. `pending_clarification_of` 가 "가장 최근 완료 turn" 만 보므로
#: 대기는 한 턴짜리지만, 그 한 턴이 **며칠 전**일 수 있습니다: 대화를 열어 두고 다음 날
#: 이어 말하면 그 turn 이 여전히 마지막입니다.
#:
#: 1시간인 이유는 이 기능이 "방금 먹였어" 를 받는 자리이기 때문입니다. 지난 기록을 적는 것은
#: 기록 화면의 일이고, 그쪽은 시각을 손으로 고를 수 있습니다.
PROPOSAL_TTL = timedelta(hours=1)

#: 종류별 어휘. **한 종류만 잡혀야 기록합니다** (`kind_of`).
#:
#: `먹였`·`줬` 같은 동사는 **일부러 없습니다** — 그것은 "무엇을" 이 아니라 "했다" 라서, 넣으면
#: 종류를 모르는 발화가 밥으로 떨어집니다. 동사만 있는 발화(`"방금 줬어"`)는 여기서 None 이 되고
#: 기록 화면으로 갑니다.
_KIND_PATTERNS: dict[CareLogKind, re.Pattern[str]] = {
    # ⚠️ `약` 은 **낱말 하나로 섰을 때만** 약입니다 (조사는 붙어도 됩니다). 처음에는 못 쓸
    # 글자를 하나씩 빼는 식(`약(?!간|속|국…)`)으로 썼는데, 골드 세트 391,634건을 훑는
    # 테스트가 `제약`·`약관` 을 투약으로 읽는 것을 잡았습니다 — 뺄 글자를 세는 방식은
    # 셀 수 없는 쪽이 훨씬 많아서 늘 집니다(계약 · 절약 · 조약 · 약도 · 약국 · 예약 …).
    # 그래서 방향을 뒤집어, **앞뒤에 다른 한글이 붙으면 약이 아니라고** 봅니다.
    #
    # 오탐 비용이 셋 중 가장 큰 종류라 이렇게 합니다: 투약은 중복 확인이 걸린 유일한
    # 종류이고(`care_event.CONFIRM_KINDS`), 하지 않은 투약이 기록되면 다음 사람이 약을
    # 건너뜁니다. `한약` 처럼 실제 약인 합성어는 아래 목록에 따로 적습니다.
    CareLogKind.MEDICATION: re.compile(
        r"(?<![가-힣])약(?:을|은|도|만|이|과|이랑|랑|하고|까지|부터|요)?(?![가-힣])"
        r"|투약|알약|물약|안약|가루약|처방약|한약|구충|심장사상충|항생제|진통제|영양제|유산균"
    ),
    CareLogKind.SNACK: re.compile(r"간식|츄르|트릿|개껌|덴탈|육포|저키|비스켓|비스킷"),
    # `캔` 한 글자는 일부러 없습니다 — `사료`·`습식` 이 이미 덮고, 한 글자는 `캔슬` 같은
    # 말에 걸립니다. `급여` 는 남겨 두지만 사람의 월급을 뜻할 수도 있는 말이라, 위의 `약`
    # 처럼 좁혀야 할 날이 오면 같은 방식을 씁니다.
    CareLogKind.MEAL: re.compile(r"밥|사료|식사|끼니|습식|화식|자연식|급여"),
}

#: 승낙. **거절이 먼저 걸립니다** (`confirmation_of`) — `"아니 응 그거 말고"` 는 승낙이 아닙니다.
#:
#: ⚠️ **문장 맨 앞에 고정돼 있습니다** (`re.match`). 안 고정했다가 `"병원 어디가 좋아"` 가
#: `좋아` 때문에 승낙으로 읽히는 것을 테스트가 잡았습니다 — 앞 턴에 제안이 떠 있는 채로 다른
#: 것을 물으면 그 질문이 밥 한 줄을 만들었다는 뜻입니다. 사람은 승낙을 맨 앞에 둡니다
#: (`"네"` · `"응 기록해줘"` · `"그래"`), 그래서 고정이 정밀도를 크게 올리고 잃는 것이 거의 없습니다.
_AFFIRM_PREFIX = re.compile(
    r"(?:네|넵|넸|예{1,2}|응|웅|엉|그래|그럼|맞아|맞어|좋아|좋습|오케이|오키|콜|yes|ok(?:ay)?)"
)
#: 어디에 있어도 승낙인 말 — **"기록해" 라고 직접 말하는 것**뿐입니다. 위와 달리 고정하지
#: 않는 이유는 `"그거 기록해줘"` 처럼 앞에 다른 말이 붙기 때문이고, 그래도 안전한 이유는
#: 이 낱말들이 다른 뜻으로 쓰이지 않기 때문입니다. `"해줘"` 는 일부러 없습니다 — 그 두 글자는
#: 무엇이든 부탁하는 말이라 `"병원 알려줘"` 류를 승낙으로 만듭니다.
_AFFIRM_ANYWHERE = re.compile(r"기록해|기록 ?좀|기록 ?부탁|저장해|남겨 ?줘|남겨 ?주")
#: 전체가 이것일 때만 승낙으로 읽는 짧은 답.
_SHORT_AFFIRM = frozenset({"ㅇ", "ㅇㅇ", "ㅇㅋ", "어", "o", "y"})
#: 거절. 승낙보다 **먼저** 봅니다.
_DECLINE = re.compile(
    r"아니|아냐|아뇨|안 ?해|안 ?했|하지 ?마|말아|말고|취소|됐어|됐고|괜찮아|"
    r"아직|나중|싫어|틀렸|틀려|잘못|no|nope"
)
#: 문장 부호·공백을 떼고 봅니다 — `"네!"` 와 `"네"` 가 달라야 할 이유가 없습니다.
_PUNCTUATION = re.compile(r"[\s.!?~,。！？]+")

#: 종류별 사람 말. `care_events.kind` 는 내부 값이라 문장에 그대로 안 씁니다
#: (`aggregate._WALK_VERDICTS` 와 같은 규칙).
KIND_LABELS: dict[CareLogKind, str] = {
    CareLogKind.MEAL: "밥",
    CareLogKind.MEDICATION: "약",
    CareLogKind.SNACK: "간식",
}

#: "했다" 를 말하는 동사. 이것이 없으면 기록 진술이 아닙니다 — `"밥 언제 줘야 해?"` 와
#: `"밥 줬어"` 를 가르는 것이 이 목록입니다.
_DID_IT = re.compile(
    r"먹였|먹임|먹이고|줬|줫|줘써|주었|드렸|챙겼|급여했|"
    r"했어|했다|했음|완료|끝냈"
)

#: 이것이 하나라도 있으면 **기록 진술이 아닙니다.** 질문이거나, 기록에 걱정이 붙은 말입니다.
#:
#: `는데`·`근데` 가 여기 있는 것이 이 게이트에서 가장 중요한 한 줄입니다 —
#: `"밥 먹였는데 계속 낑낑거려"` 는 기록하겠다는 말이 아니라 **걱정**이고, 그것을 기록
#: 확인으로 가로채면 사용자가 물은 것에 아무도 답하지 않습니다. 오늘은 일반 답변이 그
#: 걱정에 답하고 있고, 이 기능이 그것을 뺏어서는 안 됩니다.
_NOT_A_STATEMENT = re.compile(
    r"[?？]|왜|어떻|어떡|괜찮|이상|정상|해야|하나|할까|될까|볼까|나요|가요|까요|"
    r"건가|는데|은데|지만|근데|그런데|얼마|몇|무엇|뭐|언제|어디|추천|알려|"
    r"토하|설사|안 ?먹|못 ?먹|거부|낑낑|절뚝|아파|아픈|힘들어"
)

#: 진술로 받아 주는 길이 상한. 넘으면 기록이 아니라 **이야기**입니다.
#:
#: 긴 발화에는 거의 늘 다른 요청이 섞여 있고, 섞인 발화를 배타적인 확인 되묻기로 가로채면
#: 나머지가 조용히 사라집니다. 어휘로 다 잡을 수 없는 그 위험을 길이로 한 번 더 막습니다.
MAX_STATEMENT_CHARS = 40


def is_care_log_statement(query: str) -> bool:
    """"챙겼다" 고 **말하고 있나**. 질문·걱정·긴 발화는 전부 거짓입니다.

    조건 셋을 모두 넘겨야 참입니다 — 종류 어휘 하나(`_KIND_PATTERNS`), "했다" 동사
    하나(`_DID_IT`), 그리고 질문·걱정 표지가 **없음**(`_NOT_A_STATEMENT`).

    **셋 다 요구하는 것이 의도입니다.** 하나라도 느슨하게 하면 기록 확인이 다른 답을
    가로채기 시작합니다 — 이 게이트가 거짓이면 오늘과 똑같이 라우팅되므로, 놓치는 비용은
    "기능이 안 걸린다" 이고 잘못 잡는 비용은 "물은 것에 답이 없다" 입니다.
    """
    if len(query.strip()) > MAX_STATEMENT_CHARS:
        return False
    if _NOT_A_STATEMENT.search(query):
        return False
    if not _DID_IT.search(query):
        return False
    return any(pattern.search(query) for pattern in _KIND_PATTERNS.values())


def kind_of(query: str) -> CareLogKind | None:
    """발화에서 **한 종류만** 읽힐 때 그 종류. 애매하면 None.

    `"밥이랑 약 먹였어"` 는 None 입니다 — 두 줄을 자동으로 쓰지 않습니다. 한 번의 확인으로
    두 기록을 만들면 사용자가 승낙한 것과 들어간 것이 달라지고, 되돌리려면 기록 화면에서
    둘을 따로 지워야 합니다. 그런 발화는 기록 화면이 답입니다.
    """
    matched = [kind for kind, pattern in _KIND_PATTERNS.items() if pattern.search(query)]
    if len(matched) != 1:
        return None
    return matched[0]


def confirmation_of(query: str) -> Literal["affirm", "decline", "unrelated"]:
    """`"affirm"` · `"decline"` · `"unrelated"` 중 하나.

    **거절이 승낙을 이깁니다.** `"아니 그거 말고"` 에는 승낙 어휘가 없지만, `"응 아니야"`
    처럼 섞인 발화에서 승낙을 골라 쓰면 안 되는 쪽으로 실패합니다.

    **`"unrelated"` 는 거절이 아닙니다.** 제안을 흘리고 평소대로 라우팅할 뿐이라, 사용자가
    이어서 다른 것을 물으면 그 답이 나갑니다 — 대기 되묻기를 안 이어받는 기존 동작과 같습니다.
    """
    normalized = _PUNCTUATION.sub(" ", query).strip()
    if not normalized:
        return "unrelated"
    if normalized.replace(" ", "").lower() in _SHORT_AFFIRM:
        return "affirm"
    if _DECLINE.search(normalized):
        return "decline"
    if _AFFIRM_PREFIX.match(normalized) or _AFFIRM_ANYWHERE.search(normalized):
        return "affirm"
    return "unrelated"


def is_proposal_fresh(proposal: CareLogProposal, *, now: datetime) -> bool:
    """제안이 아직 살아 있나 (`PROPOSAL_TTL`). 앞날이면 거짓입니다."""
    age = now - proposal.occurred_at
    return timedelta(0) <= age <= PROPOSAL_TTL


def proposal_question(proposal: CareLogProposal, *, clock: str) -> str:
    """확인 문장. **시각을 반드시 보여 줍니다** — 그것이 확인 단계의 전부입니다.

    `clock` 은 서울 기준 `HH:MM` 이고 부르는 쪽이 만듭니다 (`planner`). 여기서 시간대를
    다시 해석하지 않는 이유는, 이 문장이 사용자가 검사할 유일한 값이라 계산하는 자리가
    하나여야 하기 때문입니다.
    """
    return f"{clock}에 {KIND_LABELS[proposal.kind]} 먹인 걸로 기록할까요?"


def build_care_log_declined_response(
    *, request_id: str, route: RouteTrace | None = None
) -> AssistantResponse:
    """거절 응답 — 고정 문구, 능력 0개 (`social.build_social_response` 와 같은 꼴).

    상태가 `ANSWERED` 인 이유: 요청은 "기록하지 마" 였고 그대로 했습니다. `REFUSED` 는
    **우리가** 거절한 것이라는 뜻이라 여기 쓰면 계약이 거짓말을 합니다.
    """
    return AssistantResponse(
        request_id=request_id,
        status=AssistantStatus.ANSWERED,
        message="기록하지 않았어요. 기록 화면에서 직접 남기실 수도 있어요.",
        results=[],
        handoffs=[],
        clarify=None,
        route=route,
    )
