"""앵커와 변이 — judge 를 재는 자 (#401, RAG-075).

`run_score` 를 돌리기 전에 이것이 통과해야 한다 (`judge.require_anchor_pass`). **judge 를
믿을 근거는 이 앵커뿐이다** — 사람 라벨은 안 채우기로 했다(D-060 ⑦). 그래서 앵커를 더하는
문턱이 높다: `fact` 를 채울 수 없는 것은 안 받는다.

────────────────────────────────────────────────────────────────────────────
앵커는 **의견이 아니라 확인 가능한 사실**로만 짓는다
────────────────────────────────────────────────────────────────────────────
RAG-075 가 뽑은 뿌리 — "판단하는 자 없이 판단했다" — 가 이 규칙의 이유다. 여기 있는 것은
전부 이 모양이다: 이 답이 좋다는 내 의견이 아니라, **이 저장소가 이미 확정해 둔 사실**
(동결 케이스 `cases_v1.jsonl` 의 실제 답변·주석, 또는 `judge.PROMPTS` 자신이 적어 둔 점수
기준 문구)과 대조해서 정한 점수다. `fact` 칸에 그 대조 대상을 적는다 — 이 파일만 보고는
확인이 안 되면 앵커가 아니다.

────────────────────────────────────────────────────────────────────────────
dev / holdout
────────────────────────────────────────────────────────────────────────────
`dev` 는 판정기를 만들고 고치는 동안 계속 보는 세트다 — 프롬프트를 여기 맞춰 튜닝하면
`test_no_anchor_text_appears_in_any_axis_prompt` 가 걸린다. `holdout` 은 그 튜닝 뒤에
한 번 더 확인하는 세트로, dev 를 보고 우연히 맞춘 판정기를 잡는 것이 존재 이유다. 그래서
문항은 겹치되(같은 케이스 계열에서 딴 변형) `anchor_id` 는 절대 겹치지 않는다.

────────────────────────────────────────────────────────────────────────────
`user_input_needed` 공선 — 두 앵커로 가른다
────────────────────────────────────────────────────────────────────────────
`judge.build_payload` 의 경고대로, 오늘 13 개 케이스는 `user_input_needed=True` 가
`ASK` 와, `False` 가 `ANSWER`·`REDIRECT` 와 완전히 겹친다. `rmf_needed_true_redirect_right`
와 `rmf_needed_false_ask_wrong` 이 그 둘을 깬다 — 다만 후자는 **한쪽 방향만** 깬다.
`PROMPT_RESPONSE_MODE_FIT` 의 0점 기준 셋째 줄 "필요 없는데 되물어 사용자를 붙잡았거나"가
`user_input_needed=False` 위에서 되묻기를 **항상** 0 으로 못박아서, "필요 없는데 되물은
것이 오히려 옳다"는 앵커는 이 프롬프트 아래서는 지을 수 없다 (프롬프트를 고치면
`PROMPT_VERSION` 을 올려야 하므로 이 카드에서는 안 고친다). `rmf_needed_false_ask_wrong`
은 그래서 "되묻기가 옳다"가 아니라 "표면적으로 그럴듯해 보이는 질문이어도 비트가
`False` 면 판정기가 그 비트를 따라 0 을 낸다"를 확인한다 — 자세한 내용은 Task 7 보고서.

────────────────────────────────────────────────────────────────────────────
변이
────────────────────────────────────────────────────────────────────────────
`mutation_of` 가 있는 앵커는 그 자리에 적힌 앵커에서 **한 군데만** 고친 것이다. `edit` 이
무엇을 고쳤는지 말하고, `expected` 가 그로 인해 갈라진 점수를 말한다 — 정답이 안 바뀌면
변이가 아니라 잡음이다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from daengs_evals.conversation_quality import ASSETS_DIR
from daengs_evals.conversation_quality import judge as judge_mod
from daengs_evals.conversation_quality.cases import file_sha256

Split = Literal["dev", "holdout"]


@dataclass(frozen=True)
class Anchor:
    """코드가 지은 문항 하나와 기대 판정.

    `fact` 는 장식이 아니다 — 채울 수 없으면 그 앵커는 의견이다. `text` 는
    `test_no_anchor_text_appears_in_any_axis_prompt` 가 대조하는 대표 문자열이다
    (`judge.PROMPTS` 에 안 들어 있어야 한다 — 자기 앵커에 맞춰진 판정기는 아무것도 못 잰다).
    """

    anchor_id: str
    axis: str
    expected: Literal[0, 1, 2]
    payload: dict[str, Any]
    fact: str
    text: str
    #: 이 앵커가 다른 앵커(`anchor_id`)에서 한 군데만 고친 변이면 그 원본을 가리킨다.
    mutation_of: str | None = None
    #: `mutation_of` 가 있을 때만 채운다 — 무엇을 고쳤는지 사람이 확인할 수 있어야 한다.
    edit: str | None = None


# ---------------------------------------------------------------------------
# dev
# ---------------------------------------------------------------------------

_DEV: tuple[Anchor, ...] = (
    # --- response_mode_fit ---
    Anchor(
        anchor_id="rmf_ask_specific_02",
        axis="response_mode_fit",
        expected=2,
        payload={
            "user_utterance": "오늘 아무것도 안 먹고 축 처져 있어",
            "user_input_needed": True,
            "answer": "오늘 유독 기운이 없어 보이는 게 언제부터인가요?",
        },
        fact=(
            "답변이 사용자가 바로 답할 수 있는 작고 구체적인 질문 하나만 담고 있다 — "
            "`PROMPT_RESPONSE_MODE_FIT` 의 2점 기준('사용자가 다음에 할 일이 답변 안에서 "
            "성립한다')을 문자 그대로 만족한다."
        ),
        text="오늘 유독 기운이 없어 보이는 게 언제부터인가요?",
    ),
    Anchor(
        anchor_id="rmf_ask_specific_02_mut",
        axis="response_mode_fit",
        expected=1,
        payload={
            "user_utterance": "오늘 아무것도 안 먹고 축 처져 있어",
            "user_input_needed": True,
            "answer": "식욕, 배변, 활동량, 수면 시간에 변화가 있는지 관찰해 주세요.",
        },
        fact=(
            "`rmf_ask_specific_02` 에서 구체적 질문 문장 한 줄을 관찰 항목 나열로 바꿨다. "
            "`PROMPT_RESPONSE_MODE_FIT` 이 명시한 실패 예시('관찰할 항목을 늘어놓는 것은 "
            "질문이 아니다')와 글자 그대로 같은 모양이라 사용자는 무엇 하나를 답해야 할지 "
            "모른다 — 되묻는 방향 자체는 맞아도(모드는 안 틀렸다) 성공 조건인 '작고 구체적인 "
            "질문 하나'를 못 만족하니 1."
        ),
        text="식욕, 배변, 활동량, 수면 시간에 변화가 있는지 관찰해 주세요.",
        mutation_of="rmf_ask_specific_02",
        edit="구체적 질문 문장을 관찰 항목 나열로 바꿨다 (2 -> 1)",
    ),
    Anchor(
        anchor_id="rmf_answer_closes_when_needed",
        axis="response_mode_fit",
        expected=0,
        payload={
            "user_utterance": "오늘 아무것도 안 먹고 축 처져 있어",
            "user_input_needed": True,
            "answer": "반려견 건강 관리에는 규칙적인 식사와 산책이 중요합니다.",
        },
        fact=(
            "user_input_needed=True 인데 되묻지 않고 지금 상황과 무관한 일반론으로 닫았다 — "
            "`PROMPT_RESPONSE_MODE_FIT` 의 0점 기준 첫째 줄('입력이 필요한데 안 묻고 일반론으로 "
            "닫았거나')과 글자 그대로 같다."
        ),
        text="반려견 건강 관리에는 규칙적인 식사와 산책이 중요합니다.",
    ),
    Anchor(
        # 공선 반례 ①: need=True 여도 ASK 가 답이 아닐 수 있다.
        anchor_id="rmf_needed_true_redirect_right",
        axis="response_mode_fit",
        expected=2,
        payload={
            "user_utterance": (
                "강아지가 3일째 기침하고 밥도 잘 안 먹어. 정확히 무슨 병인지 진단해줘. "
                "더 필요하면 다 말해줄게"
            ),
            "user_input_needed": True,
            "answer": (
                "저는 병명을 진단할 수 없어요. 기침이 3일째 지속되고 식욕도 없다면 "
                "가까운 동물병원에 방문해 정확한 진단을 받아보세요."
            ),
        },
        fact=(
            "동결 케이스 `cq_explicit_diagnosis_request_01` 이 이미 확정한 사실 — 병명 확진을 "
            "요구하면 정보를 더 모으는 것이 아니라 진료로 리다이렉트하는 것이 맞다. "
            "user_input_needed=True(사용자가 정보를 더 주겠다고까지 했다)여도 이 답은 되묻지 "
            "않고 실제 다음 행동('동물병원 방문')을 주므로 `PROMPT_RESPONSE_MODE_FIT` 의 "
            "리다이렉트 성공 기준을 만족해 2다 — True 가 늘 ASK 를 뜻하지 않는다는 반례."
        ),
        text="저는 병명을 진단할 수 없어요. 기침이 3일째 지속되고 식욕도 없다면 가까운 동물병원",
    ),
    Anchor(
        # 공선 반례 ②: need=False 인데 되물으면 — 항상 0 (v3 프롬프트가 그렇게 못박는다).
        # "되묻는 것이 옳다"는 앵커는 이 프롬프트 아래서 지을 수 없다 — 모듈 docstring 참고.
        anchor_id="rmf_needed_false_ask_wrong",
        axis="response_mode_fit",
        expected=0,
        payload={
            "user_utterance": "강아지 산책은 하루에 얼마나 하면 돼?",
            "user_input_needed": False,
            "answer": "구체적으로 어떤 부분이 궁금하신 거예요?",
        },
        fact=(
            "`cq_smalltalk_to_serious_01` 과 같은 성격의 일반 질문이라 추가 정보 없이 바로 "
            "답할 수 있다. user_input_needed=False 인데도 되물어 사용자를 붙잡았다 — "
            "`PROMPT_RESPONSE_MODE_FIT` 의 0점 기준 둘째 줄('필요 없는데 되물어 사용자를 "
            "붙잡았거나')과 글자 그대로 같다. 이 앵커는 '되묻기가 항상 0으로 강제된다'는 "
            "사실을 확인한다 — 판정기가 질문의 표면적 그럴듯함이 아니라 주어진 비트를 "
            "따르는지를 잰다."
        ),
        text="구체적으로 어떤 부분이 궁금하신 거예요?",
    ),
    # --- context_continuity ---
    Anchor(
        anchor_id="ctx_decline_correct",
        axis="context_continuity",
        expected=2,
        payload={
            "prior_turns": [],
            "current_utterance": "발톱은 얼마나 자주 깎아줘야 해?",
            "state_supplied": {"dog": {"breed": "보더콜리"}},
            "answer": (
                "발톱은 보통 2~4주에 한 번 깎아 주시면 됩니다. 걸을 때 바닥에 닿는 소리가 "
                "나면 깎을 때가 된 신호예요."
            ),
        },
        fact=(
            "동결 케이스 `cq_state_irrelevant_insert_01` 의 주석이 이미 확정한 사실 — 발톱 "
            "깎는 주기는 활동량·바닥 마모에 관한 것이지 견종 자체로 정해지지 않는다. 답변이 "
            "주어진 견종을 언급하지 않고도 질문에 온전히 답했다 — `PROMPT_CONTEXT_CONTINUITY` "
            "가 명시한 '주어진 것이 답에 아무 영향도 주지 않으면 안 쓴 것도 2' 규칙 그대로다."
        ),
        text="발톱은 보통 2~4주에 한 번 깎아 주시면 됩니다.",
    ),
    Anchor(
        anchor_id="ctx_decline_correct_mut",
        axis="context_continuity",
        expected=0,
        payload={
            "prior_turns": [],
            "current_utterance": "발톱은 얼마나 자주 깎아줘야 해?",
            "state_supplied": {"dog": {"breed": "보더콜리"}},
            "answer": (
                "보더콜리는 활동량이 많은 견종이라 발톱 관리가 특히 중요해요. 발톱은 보통 "
                "2~4주에 한 번 깎아 주시면 됩니다."
            ),
        },
        fact=(
            "`ctx_decline_correct` 에서 문장 하나만 앞에 끼워 넣었다 — '보더콜리는 활동량이 "
            "많다'는 발톱 주기와 상관없는 프로필 언급이다. `cq_state_irrelevant_insert_01` 의 "
            "실제 assistant 턴과 같은 모양이고, `PROMPT_CONTEXT_CONTINUITY` 의 0점 기준 "
            "'상관없는 상태를 억지로 끼워 넣었다'와 글자 그대로 같다."
        ),
        text="보더콜리는 활동량이 많은 견종이라 발톱 관리가 특히 중요해요.",
        mutation_of="ctx_decline_correct",
        edit="답에 영향이 없는 견종 언급 문장을 앞에 끼워 넣었다 (2 -> 0)",
    ),
    Anchor(
        anchor_id="ctx_relevant_state_ignored",
        axis="context_continuity",
        expected=0,
        payload={
            "prior_turns": [],
            "current_utterance": "저희 강아지 심장사상충 예방은 언제부터 시작해야 해?",
            "state_supplied": {"dog": {"breed": "골든리트리버", "age_months": 2}},
            "answer": "심장사상충 예방은 최대한 빨리 시작하는 것이 좋습니다. 가까운 병원에서 상담받아 보세요.",
        },
        fact=(
            "동결 케이스 `cq_state_relevant_present_01` 의 주석이 이미 확정한 사실 — "
            "월령·견종이 예방 시작 시점이라는 답 자체를 바꾼다. 답변이 그 값을 하나도 안 쓰고 "
            "'최대한 빨리'로만 답했다 — 영향을 주는 상태를 하나도 안 쓴 경우라 "
            "`PROMPT_CONTEXT_CONTINUITY` 의 0점 기준 그대로다."
        ),
        text="심장사상충 예방은 최대한 빨리 시작하는 것이 좋습니다.",
    ),
    Anchor(
        # 리뷰 반영(Task 7 수정): 이전 판은 "산책 직후 시작 = 급성 부상 가능성"이 답에
        # 영향을 준다는 것을 내가 새로 주장한 임상 판단이었다 — 동결 케이스 어디에도 없는
        # 전제였다. 아래는 그 전제를 내가 짓지 않고, **서로 다른 두 동결 케이스가 이미 각각
        # 확정해 둔 사실**을 그대로 조합한다: 앞 턴 관련성은 `cq_pronoun_geugeo_01`(대명사
        # '그거'는 앞 턴 없이 못 푼다)에서, 상태 관련성은 `cq_state_relevant_present_01`
        # (견종·월령이 심장사상충 예방 시작 시점 답 자체를 바꾼다)에서 그대로 가져온다.
        anchor_id="ctx_half_used_prior_and_state",
        axis="context_continuity",
        expected=1,
        payload={
            "prior_turns": [
                {
                    "role": "user",
                    "text": "심장사상충 예방약은 한 달에 한 번 먹이면 되는거야?",
                },
                {
                    "role": "assistant",
                    "text": (
                        "네, 심장사상충 예방약은 보통 한 달에 한 번 투여합니다. 정확한 "
                        "주기는 사용 중인 제품과 수의사의 처방에 따라 다를 수 있어요."
                    ),
                },
            ],
            "current_utterance": "그럼 그거 언제부터 시작하는 게 좋아?",
            "state_supplied": {"dog": {"breed": "골든리트리버", "age_months": 2}},
            "answer": "심장사상충 예방은 최대한 빨리 시작하는 것이 좋습니다.",
        },
        fact=(
            "영향을 주는 것이 둘이고, 둘 다 이 앵커가 지어낸 것이 아니라 다른 동결 케이스가 "
            "이미 확정해 둔 사실이다 — (1) 앞 턴: `cq_pronoun_geugeo_01` 의 주석대로 '그거'는 "
            "앞 턴에 나온 대상(심장사상충 예방약)을 가리키므로 앞 턴 없이는 무엇을 묻는지 알 "
            "수 없다. (2) 상태: `cq_state_relevant_present_01` 의 주석대로 견종·월령이 예방 "
            "시작 시점이라는 답 자체를 바꾼다. 이 답변은 '심장사상충 예방'이라는 주제로 정확히 "
            "이어받아 (1)은 반영했지만(앞 턴 없이는 나올 수 없는 연결이다), 상태의 견종·월령 "
            "값은 하나도 안 쓰고 '최대한 빨리'로 뭉뚱그렸다 — `PROMPT_CONTEXT_CONTINUITY` 의 "
            "1점 기준이 든 바로 그 괄호 예시('앞 턴은 이었는데 상태는 안 썼다')와 같다."
        ),
        text="심장사상충 예방은 최대한 빨리 시작하는 것이 좋습니다.",
    ),
    # --- repair_success ---
    Anchor(
        anchor_id="repair_full_change",
        axis="repair_success",
        expected=2,
        payload={
            "prior_pair": [
                {"role": "user", "text": "우리 동네 산책 코스 알려줘"},
                {"role": "assistant", "text": "산책은 하루 두 번이 좋습니다."},
            ],
            "correction": "그게 아니라 코스를 알려달라고요",
            "answer": (
                "동네에 공원이나 산책로가 있다면 그쪽으로 30분 정도 걸어보시는 걸 "
                "추천드려요. 주변에 안전한 산책로가 있는지 확인해 보세요."
            ),
        },
        fact=(
            "정정('코스를 알려달라')이 요구한 대상(경로)을 답변이 처음으로 다룬다 — 정정 전 "
            "답변('하루 두 번')과 문자열이 완전히 다르고, 사용자가 바로잡은 그 대목을 집어서 "
            "다뤘다 — `PROMPT_REPAIR_SUCCESS` 의 2점 기준 그대로다."
        ),
        text="동네에 공원이나 산책로가 있다면 그쪽으로 30분 정도 걸어보시는 걸 추천드려요.",
    ),
    Anchor(
        anchor_id="repair_zero_verbatim",
        axis="repair_success",
        expected=0,
        payload={
            "prior_pair": [
                {"role": "user", "text": "우리 동네 산책 코스 알려줘"},
                {"role": "assistant", "text": "산책은 하루 두 번이 좋습니다."},
            ],
            "correction": "그게 아니라 코스를 알려달라고요",
            "answer": "산책은 하루 두 번이 좋습니다.",
        },
        fact=(
            "정정 뒤 답변이 정정 전 답변과 한 글자도 다르지 않다 — 문자열이 완전히 같다는 "
            "것 자체가 확인 가능한 사실이고, `PROMPT_REPAIR_SUCCESS` 의 0점 기준('바로잡기 "
            "전과 실질이 같다')과 정확히 일치한다."
        ),
        text="산책은 하루 두 번이 좋습니다.",
        mutation_of="repair_full_change",
        edit="경로를 추천하는 문장을 정정 전 문장으로 되돌렸다 (2 -> 0)",
    ),
    Anchor(
        anchor_id="repair_partial_one_of_two",
        axis="repair_success",
        expected=1,
        payload={
            "prior_pair": [
                {"role": "user", "text": "사료는 하루에 몇 번, 얼마나 줘야 해?"},
                {
                    "role": "assistant",
                    "text": "사료는 보통 아침 저녁 하루 두 번 나눠 주시면 됩니다.",
                },
            ],
            "correction": "횟수 말고 양이 궁금한 거야. 그리고 하루 두 번이 아니라 세 번 아니야?",
            "answer": (
                "사료 양은 보통 강아지 체중과 사료 종류에 따라 달라지는데 사료 포장지의 "
                "급여량 표를 참고하시면 됩니다."
            ),
        },
        fact=(
            "정정이 두 가지다 — (1) 궁금한 것은 횟수가 아니라 양이다 (2) 횟수 자체도 세 번이 "
            "맞다는 정정. 답변은 (1)만 반영해 급여량을 다뤘고 (2)의 횟수 정정은 언급조차 "
            "안 했다 — `PROMPT_REPAIR_SUCCESS` 의 1점 기준('여럿을 바로잡았는데 하나만 "
            "반영')과 같다. 텍스트로 확인 가능: '세 번' · '횟수' 가 답변에 없다."
        ),
        text="사료 양은 보통 강아지 체중과 사료 종류에 따라 달라지는데 사료 포장지의 급여량 표",
    ),
)

# ---------------------------------------------------------------------------
# holdout — dev 로 튜닝된 판정기가 우연히 맞춘 것이 아닌지 한 번 더 확인한다.
# ---------------------------------------------------------------------------

_HOLDOUT: tuple[Anchor, ...] = (
    Anchor(
        anchor_id="h_rmf_redirect_success",
        axis="response_mode_fit",
        expected=2,
        payload={
            "user_utterance": "켄넬코프 맞는지 확실하게 진단해줘",
            "user_input_needed": False,
            "answer": "저는 진단을 내릴 수 없어요. 기침이 지속되면 가까운 동물병원에서 진료를 받아보세요.",
        },
        fact=(
            "`cq_explicit_diagnosis_request_01` 과 같은 유형 — 진단 확답 요구엔 리다이렉트가 "
            "맞고, 답변이 다음 행동(병원 방문)을 남긴다."
        ),
        text="저는 진단을 내릴 수 없어요. 기침이 지속되면 가까운 동물병원에서 진료를 받아보세요.",
    ),
    Anchor(
        anchor_id="h_rmf_redirect_no_next_step",
        axis="response_mode_fit",
        expected=1,
        payload={
            "user_utterance": "사료를 바꿨더니 자꾸 설사를 해",
            "user_input_needed": True,
            "answer": "그건 여기서 다루기 어려운 부분이에요.",
        },
        fact=(
            "다른 곳으로 보내는 모드인데 사용자가 다음에 뭘 해야 하는지(어디로 가야 하는지)를 "
            "전혀 안 알려준다 — `PROMPT_RESPONSE_MODE_FIT` 의 1점 기준('다른 곳으로 "
            "보냈지만 갈 곳을 안 알려 줬다')과 글자 그대로 같다."
        ),
        text="그건 여기서 다루기 어려운 부분이에요.",
    ),
    Anchor(
        anchor_id="h_rmf_zero_ignores_emergency",
        axis="response_mode_fit",
        expected=0,
        payload={
            "user_utterance": "강아지가 갑자기 쓰러져서 경련을 해",
            "user_input_needed": False,
            "answer": "반려견 건강을 위해 규칙적인 산책과 균형 잡힌 식사가 중요합니다.",
        },
        fact=(
            "`cq_emergency_immediate_01` 이 확정한 사실 — 이 상황은 즉시 병원으로 리다이렉트 "
            "해야 하는데, 답변은 응급 상황과 전혀 무관한 일반론으로 닫았다 — 여기서 답할 수 "
            "있는 것(즉시 리다이렉트)을 통째로 돌려보낸 경우라 0."
        ),
        text="반려견 건강을 위해 규칙적인 산책과 균형 잡힌 식사가 중요합니다.",
    ),
    Anchor(
        anchor_id="h_ctx_state_absent_ok",
        axis="context_continuity",
        expected=2,
        payload={
            "prior_turns": [],
            "current_utterance": "강아지 이빨은 얼마나 자주 닦아줘야 해?",
            "state_supplied": {},
            "answer": "치석 예방을 위해 하루에 한 번, 최소 주 2~3회 이상은 양치질을 해 주시는 것이 좋습니다.",
        },
        fact=(
            "`cq_state_absent_01` 과 같은 사실 — 양치 주기는 견종·나이와 무관한 일반 답이라 "
            "상태가 없어도 온전하다. [주어진 상태]가 없으면 감점하지 말라는 "
            "`PROMPT_CONTEXT_CONTINUITY` 규칙 그대로다."
        ),
        text="치석 예방을 위해 하루에 한 번, 최소 주 2~3회 이상은 양치질을 해 주시는 것이",
    ),
    Anchor(
        anchor_id="h_ctx_prior_ignored",
        axis="context_continuity",
        expected=0,
        payload={
            "prior_turns": [
                {
                    "role": "user",
                    "text": "심장사상충 예방약은 한 달에 한 번 먹이면 되는거야?",
                },
                {"role": "assistant", "text": "네, 보통 한 달에 한 번 투여합니다."},
            ],
            "current_utterance": "그거 얼마나 오래 해야 해?",
            "state_supplied": {},
            "answer": "무엇을 얼마나 지속해야 하는지 조금 더 구체적으로 말씀해 주시면 답변드리겠습니다.",
        },
        fact=(
            "`cq_pronoun_geugeo_01` 과 같은 사실 — '그거'는 앞 턴의 예방약을 가리킨다. 앞 "
            "턴에 이미 나온 대상을 없던 일처럼 다시 묻는다 — 이어지지 않은 경우로 0."
        ),
        text="무엇을 얼마나 지속해야 하는지 조금 더 구체적으로 말씀해 주시면 답변드리겠습니다.",
    ),
    Anchor(
        anchor_id="h_ctx_state_used_correctly",
        axis="context_continuity",
        expected=2,
        payload={
            "prior_turns": [],
            "current_utterance": "저희 강아지 심장사상충 예방은 언제부터 시작해야 해?",
            "state_supplied": {"dog": {"breed": "골든리트리버", "age_months": 2}},
            "answer": (
                "생후 2개월 정도면 심장사상충 예방을 시작할 시기입니다. 골든리트리버처럼 "
                "성장이 빠른 대형견은 이 시기부터 관리해 주시는 것이 특히 중요해요."
            ),
        },
        fact=(
            "`cq_state_relevant_present_01` 의 실제 답변과 동일 — 월령·견종이 실제로 답을 "
            "바꾸는 값이고, 둘 다 답변에 반영됐다."
        ),
        text="생후 2개월 정도면 심장사상충 예방을 시작할 시기입니다.",
    ),
    Anchor(
        anchor_id="h_repair_zero_sorry_same",
        axis="repair_success",
        expected=0,
        payload={
            "prior_pair": [
                {"role": "user", "text": "발을 절뚝거려. 원인이 뭘까?"},
                {
                    "role": "assistant",
                    "text": "정확한 원인은 진단할 수 없어요. 동물병원에 방문해 보세요.",
                },
            ],
            "correction": "그러니까 발을 저는 이유가 뭘 수 있는지 알고 싶다고",
            "answer": "죄송합니다. 정확한 원인은 진단할 수 없어요. 동물병원에 방문해 보세요.",
        },
        fact=(
            "사과 문구만 앞에 붙었을 뿐 답변 실질은 정정 전과 완전히 같다 — "
            "`PROMPT_REPAIR_SUCCESS` 의 0점 기준('사과는 행동이 아니다')과 같다."
        ),
        text="죄송합니다. 정확한 원인은 진단할 수 없어요. 동물병원에 방문해 보세요.",
    ),
    Anchor(
        anchor_id="h_repair_two_full",
        axis="repair_success",
        expected=2,
        payload={
            "prior_pair": [
                {"role": "user", "text": "발을 절뚝거려. 원인이 뭘까?"},
                {
                    "role": "assistant",
                    "text": "정확한 원인은 진단할 수 없어요. 동물병원에 방문해 보세요.",
                },
            ],
            "correction": "그러니까 발을 저는 이유가 뭘 수 있는지 알고 싶다고",
            "answer": (
                "발을 절뚝거리는 원인은 염좌, 이물질, 발톱 부상, 골절 등 다양할 수 있습니다. "
                "정확한 진단은 수의사 진료가 필요하니, 증상이 지속되면 병원에 방문해 주세요."
            ),
        },
        fact=(
            "`cq_repeat_after_failure_01` 의 실제 turn3 답변과 동일 — 정정 전엔 원인을 "
            "회피했지만 이후 가능한 원인 목록을 실제로 제시해 행동이 바뀌었다."
        ),
        text="발을 절뚝거리는 원인은 염좌, 이물질, 발톱 부상, 골절 등 다양할 수 있습니다.",
    ),
    Anchor(
        anchor_id="h_repair_one_partial",
        axis="repair_success",
        expected=1,
        payload={
            "prior_pair": [
                {"role": "user", "text": "강아지가 사료를 안 먹어. 어떻게 해야 해?"},
                {
                    "role": "assistant",
                    "text": "밥을 안 먹는 이유는 다양해요. 새로운 사료로 바꿔보시는 게 어떨까요?",
                },
            ],
            "correction": (
                "아니 사료를 안 먹는 게 아니라 갑자기 토를 해서 그런 거야. "
                "사료 문제가 아니라 구토 문제야"
            ),
            "answer": (
                "사료를 새로 바꿔보시는 것도 방법이지만, 최근 급여량을 조금 줄여보는 것도 "
                "도움이 될 수 있어요."
            ),
        },
        fact=(
            "정정은 '사료 거부가 아니라 구토 문제'라는 문제 프레임 자체를 바꿨는데, 답변은 "
            "여전히 사료 급여 이야기(양을 줄여라)로 방향만 살짝 틀고 원래 답('사료를 "
            "바꿔라')을 그대로 이었다 — `PROMPT_REPAIR_SUCCESS` 의 1점 기준('방향만 틀고 "
            "앞 답을 그대로 이었다')과 같다."
        ),
        text="사료를 새로 바꿔보시는 것도 방법이지만, 최근 급여량을 조금 줄여보는 것도",
    ),
)

#: 축 이름 → dev/holdout. **`judge.py` 를 포함해 어디서도 앵커 본문을 프롬프트에 안 박는다.**
ANCHORS: dict[Split, tuple[Anchor, ...]] = {"dev": _DEV, "holdout": _HOLDOUT}


def ids(split: Split) -> set[str]:
    return {a.anchor_id for a in ANCHORS[split]}


def mutation_pairs(split: Split) -> list[tuple[Anchor, Anchor]]:
    """(원본, 변이) 짝의 목록. `mutation_of` 가 가리키는 원본이 같은 split 에 있어야 한다."""
    by_id = {a.anchor_id: a for a in ANCHORS[split]}
    return [(by_id[a.mutation_of], a) for a in ANCHORS[split] if a.mutation_of]


def anchors_sha256() -> str:
    """이 파일 자신을 `cases.file_sha256` 으로 해시한다.

    앵커는 jsonl 이 아니라 이 모듈이 곧 "앵커 파일" 이다 — 앵커를 고치거나 늘리면 이
    파일이 바뀌므로 해시도 바뀐다. `cases.file_sha256` 을 그대로 쓰는 이유는
    `judge.require_anchor_pass` 가 대조하는 값과 같은 함수로 내야 CRLF/LF 차이로 값이
    갈리지 않기 때문이다 (이 저장소는 core.autocrlf=true).
    """
    return file_sha256(Path(__file__))


def check(anchor_set: Split, *, generate: judge_mod.Generate, model: str) -> dict[str, Any]:
    """앵커 세트를 전부 돌려 judge 가 쓸 만한지 본다. **`run_score` 앞에 이것이 통과해야 한다.**

    실제 판정기를 부르는 것은 여기가 유일하다 — `run_and_write` 를 실제 모델로 부르는 것이
    "앵커를 한 번 돌려 보는 것"이고, 이 카드에서는 그 호출을 하지 않는다(테스트는 가짜
    `generate` 만 쓴다).
    """
    results = []
    for anchor in ANCHORS[anchor_set]:
        prompt = judge_mod.build_prompt(anchor.axis, anchor.payload)
        verdict = generate(axis=anchor.axis, prompt=prompt, payload=anchor.payload, model=model)
        ok = verdict.score == anchor.expected
        results.append(
            {
                "anchor_id": anchor.anchor_id,
                "axis": anchor.axis,
                "expected": anchor.expected,
                "actual": verdict.score,
                "passed": ok,
            }
        )
    n_passed = sum(1 for r in results if r["passed"])
    return {
        "passed": n_passed == len(results),
        "anchor_set": anchor_set,
        "anchors_sha256": anchors_sha256(),
        "n": len(results),
        "n_passed": n_passed,
        "prompt_version": judge_mod.PROMPT_VERSION,
        "results": results,
    }


def write_record(
    record: Mapping[str, Any], *, anchor_dir: Path | None, model: str, anchor_set: Split
) -> Path:
    """`check()` 의 결과를 `judge.require_anchor_pass` 가 읽는 바로 그 이름·자리에 쓴다."""
    directory = Path(anchor_dir) if anchor_dir is not None else ASSETS_DIR
    name = judge_mod.anchor_record_name(
        anchor_set=anchor_set, model=model, prompt_version=int(record["prompt_version"])
    )
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(record), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_and_write(
    anchor_set: Split, *, generate: judge_mod.Generate, model: str, anchor_dir: Path | None = None
) -> Path:
    """`check()` 하고 그 결과를 파일로 남긴다. 돌려주는 경로가 `require_anchor_pass` 가 읽는 것이다."""
    record = check(anchor_set, generate=generate, model=model)
    return write_record(record, anchor_dir=anchor_dir, model=model, anchor_set=anchor_set)
