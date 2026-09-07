"""운영 지표 API 의 응답 형태 (콘솔 로드맵 B3 · #223).

--------------------------------------------------------------------------------
**여기 원문이 들어갈 칸이 없습니다. 그게 이 파일의 첫째 일입니다.**

D-037 이 관측에 질문 원문을 금지했습니다. `user_content` · `assistant_content` ·
`public_response` 를 담을 칸을 아예 만들지 않으면 실수로도 안 나갑니다 —
`schemas/app_user_admin.py` 가 `*_enc` 에 쓴 것과 같은 방법입니다.

숫자만으로 "사람들이 무엇을 묻나" 가 보이는 것이 이 카드의 주장이고, 그 주장이 틀리면
답은 원문을 담는 것이 아니라 **다른 값을 세는 것**입니다.
--------------------------------------------------------------------------------
"""

from datetime import datetime

from pydantic import BaseModel


class NamedCount(BaseModel):
    """이름 하나와 개수 하나. 분포를 그리는 데 씁니다.

    `dict[str, int]` 대신 목록인 이유는 **순서가 의미를 갖기** 때문입니다 — 많은 순으로
    정렬해서 보내면 화면이 다시 정렬할 필요가 없고, JSON 객체의 키 순서에 기대지도
    않게 됩니다.
    """

    name: str
    count: int


class TurnMetricsOut(BaseModel):
    """대화(turn) 쪽 숫자.

    **`processing` 과 `assistant` 를 한 숫자로 합치지 않았습니다.** `07_chats.sql` 의
    `chat_turns_state_check` 가 둘을 못 겹치게 묶어 두어서, `processing_status='failed'`
    (우리 코드가 죽음)와 `assistant_status='FAILED'`(어시스턴트가 못 답함)는 **다른
    행**입니다. 합치면 고칠 사람이 다른 두 숫자가 섞입니다.
    """

    total: int
    #: `processing` · `completed` · `failed`
    by_processing_status: list[NamedCount]
    #: 8값 (`ANSWERED` … `FAILED`). **`processing`·`failed` 인 행은 안 들어갑니다.**
    by_assistant_status: list[NamedCount]
    #: 처리가 죽은 turn 의 사유 상위 몇 개.
    top_error_codes: list[NamedCount]


class CategoryMetricsOut(BaseModel):
    """능력별 분포.

    ⚠ **`counts` 의 합이 turn 수가 아닙니다.** `agent_categories` 는 배열이라 turn 하나가
    여러 태그를 답니다. 그래서 분모를 따로 실어 보냅니다 — 화면이 turn 수로 나누면
    100%를 넘습니다.
    """

    counts: list[NamedCount]
    #: 위 개수들의 합. 비율을 그린다면 이것이 분모입니다.
    tagged_total: int


class ChatMetricsOut(BaseModel):
    """`GET /admin/metrics/chats` 응답.

    **기간을 응답에 실어 보냅니다.** 화면이 "왜 이 숫자냐" 를 말할 수 있어야 하고,
    기본값을 서버가 정하므로 클라이언트가 되짚어 계산하면 어긋납니다.
    """

    since: datetime
    days: int

    sessions: int
    turns: TurnMetricsOut
    categories: CategoryMetricsOut
    #: `processing` · `completed` · `failed`
    summaries: list[NamedCount]
