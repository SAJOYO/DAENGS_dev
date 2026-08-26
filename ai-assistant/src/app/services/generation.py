# generation.py = 실제 "답변 문장"을 만들어내는 곳 (LLM한테 질문을 던지고 글을 받아옴).
# embedding.py가 "검색을 위한 벡터"를 만든다면, 여기는 "사람이 읽을 최종 문장"을 만듭니다.

import ollama

from app.config import get_settings
from app.services.language import is_korean_dominant

# BR5: 시스템 프롬프트 방어선 (1차 방어). 후처리 필터(guardrail.py)와 함께 2중 방어를 구성한다.
# "시스템 프롬프트"란 사용자가 안 보는, LLM에게 미리 주는 규칙/역할 설명입니다.
# 매 요청마다 이 문구를 먼저 보내서 모델의 답변 스타일/제약을 잡아줍니다.
GUARDRAIL_SYSTEM_PROMPT = (
    "당신은 반려견 생활 관리를 돕는 AI 비서입니다. "
    "다음 규칙을 반드시 지키세요:\n"
    "1. 의학적 진단명이나 확정적 처방을 절대 언급하지 마세요 (예: '~병입니다', '병원에 가세요' 등 금지).\n"
    "2. 제공된 참고 문서가 있다면 그 내용을 근거로 답변하세요.\n"
    "3. 확신할 수 없는 내용은 추측하지 말고 모른다고 솔직히 답하세요.\n"
    "4. 답변은 반드시 한국어로만 작성하세요. 영어, 중국어 등 다른 언어 단어를 절대 섞지 마세요."
)

# 로컬 생성 모델(qwen2.5:7b-instruct)이 간헐적으로 영어/중국어로 코드스위칭하는 문제(guardrail.py 주석
# 참고)에 대한 2차 방어. 1차 재시도에도 한국어로 안 돌아오면, 틀린 언어 답변을 그대로 보여주는 대신
# 이 고정 안내 문구로 대체한다 (guardrail.py의 SAFE_FALLBACK_NOTICE와 같은 "안전한 실패" 철학).
_LANGUAGE_RETRY_NOTICE = "방금 답변이 한국어가 아니었습니다. 반드시 한국어로만 다시 답변해주세요."
_NON_KOREAN_FALLBACK = "죄송합니다, 답변을 한국어로 생성하는 데 문제가 발생했습니다. 다시 질문해 주세요."


def _chat(messages: list[dict[str, str]]) -> str:
    settings = get_settings()
    client = ollama.Client(host=settings.ollama_host)
    response = client.chat(model=settings.generation_model, messages=messages)
    # 응답은 중첩된 딕셔너리 구조: {"message": {"content": "실제 답변 텍스트", ...}, ...}
    return response["message"]["content"]


# context_snippets: 검색으로 찾은 참고 문서 내용들 (없으면 빈 리스트 []).
def generate_answer(query: str, context_snippets: list[str]) -> str:
    # 참고 문서가 있는지(grounded) 없는지에 따라 LLM에게 보낼 프롬프트(질문 문구)를 다르게 구성.
    if context_snippets:
        # "\n".join(...) : 리스트의 각 항목을 줄바꿈으로 이어붙여 하나의 문자열로 만듦.
        # f"- {snippet}" 처럼 문자열 앞에 f를 붙이면 "f-string"이라 하고, {} 안에 변수 값을 끼워넣을 수 있음.
        context_text = "\n".join(f"- {snippet}" for snippet in context_snippets)
        user_prompt = (
            f"다음은 참고할 수 있는 문서입니다:\n{context_text}\n\n"
            f"위 문서를 근거로 다음 질문에 답변하세요: {query}"
        )
    else:
        # 참고 문서를 못 찾은 경우(US-02): 일반 지식으로 답하되, 모르면 모른다고 하도록 유도.
        user_prompt = (
            "참고할 수 있는 문서를 찾지 못했습니다. "
            "일반적인 지식으로 답변을 시도하되, 확신이 없으면 정확히 모른다고 답하세요.\n\n"
            f"질문: {query}"
        )

    # client.chat(): Ollama에게 "system(규칙) + user(실제 질문)" 메시지를 보내고 답변을 받음.
    # 이 구조는 ChatGPT API 등 다른 LLM들도 거의 동일하게 쓰는 표준적인 방식입니다.
    messages = [
        {"role": "system", "content": GUARDRAIL_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    answer = _chat(messages)
    if is_korean_dominant(answer):
        return answer

    # 1차 재시도: 방금 답변을 대화 기록에 남긴 채, 한국어로만 다시 답하라고 강하게 재요청.
    retry_messages = messages + [
        {"role": "assistant", "content": answer},
        {"role": "user", "content": _LANGUAGE_RETRY_NOTICE},
    ]
    retry_answer = _chat(retry_messages)
    if is_korean_dominant(retry_answer):
        return retry_answer

    # 재시도까지 실패하면, 틀린 언어의 답변을 그대로 노출하지 않고 안전한 한국어 안내로 대체.
    return _NON_KOREAN_FALLBACK
