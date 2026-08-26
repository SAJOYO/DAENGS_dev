# chat.py = "/chat" API의 입구(진짜 로직은 없음, 요청/응답 형태만 다룸).
# 실제 두뇌 역할(임베딩 -> 검색 -> 생성 -> 가드레일)은 services/rag.py의 answer_query()가 담당합니다.
# 이 파일은 "HTTP 요청을 받아서 answer_query를 부르고, 결과를 HTTP 응답으로 포장"하는 역할만 함.

from fastapi import APIRouter, HTTPException

from app.schemas import ChatRequest, ChatResponse, SourceSchema
from app.services.rag import answer_query

router = APIRouter()


# request_model=ChatRequest : 요청 body가 {"query": "..."} 형태여야 한다는 것을
#   FastAPI가 자동으로 검증해줌 (schemas.py의 ChatRequest 참고).
# response_model=ChatResponse : 이 함수가 리턴하는 값을 ChatResponse 형태의 JSON으로 변환.
@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        # 실제 RAG(검색+생성) 로직은 전부 answer_query 안에 있음. 여기선 그냥 호출만.
        result = answer_query(request.query)
    except Exception as exc:  # BR6: Ollama/DB 연결 실패 등 외부 서비스 장애
        # DB가 안 켜져 있거나 Ollama가 안 떠 있으면 answer_query 안에서 예외가 발생함.
        # 그걸 그냥 500 에러로 흘려보내지 않고, 사용자가 이해할 수 있는 메시지와 함께
        # 503(Service Unavailable) 에러로 바꿔서 응답한다.
        # "raise ... from exc" 는 원래 에러(exc)를 원인으로 남겨서 디버깅 시 추적 가능하게 함.
        raise HTTPException(
            status_code=503,
            detail="로컬 모델 서비스에 연결할 수 없습니다. Ollama가 실행 중인지 확인해주세요.",
        ) from exc

    # result는 services/rag.py의 ChatResult(dataclass) 객체.
    # 이걸 그대로 리턴하지 않고 schemas.py의 ChatResponse(pydantic 모델)로 다시 감싸는 이유:
    # response_model=ChatResponse로 FastAPI에게 "이 모양으로 응답한다"고 선언했기 때문.
    # [SourceSchema(...) for s in result.sources] 는 "리스트 컴프리헨션"이라는 문법으로,
    # for 반복문을 한 줄로 줄여서 새 리스트를 만드는 것 (result.sources의 각 항목 s를
    # SourceSchema로 하나씩 변환해서 새 리스트를 만듦).
    return ChatResponse(
        answer=result.answer,
        sources=[
            SourceSchema(content=s.content, score=s.score, source_url=s.source_url, section=s.section)
            for s in result.sources
        ],
        grounded=result.grounded,
    )
