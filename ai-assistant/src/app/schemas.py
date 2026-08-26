# schemas.py = API의 "요청/응답 형태(모양)"를 정의하는 파일.
# 여기 클래스들은 pydantic의 BaseModel을 상속합니다. 이렇게 하면:
#   1. 요청이 들어올 때: JSON을 이 클래스 형태로 자동 변환 + 검증 (형식 틀리면 자동으로 422 에러)
#   2. 응답을 보낼 때: 이 클래스 객체를 자동으로 JSON으로 변환
# FastAPI는 이 파일의 클래스들과 routers/*.py를 연결해서 이 모든 걸 자동으로 처리해줍니다.

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.services.language import is_korean_dominant


# 클라이언트가 POST /chat 할 때 보내는 요청 body: {"query": "포도 먹여도 돼?"}
class ChatRequest(BaseModel):
    # Field(..., min_length=1) : "..."은 필수값이라는 뜻(기본값 없음), min_length=1은 빈 문자열 금지.
    query: str = Field(..., min_length=1)

    # 이 비서는 한국어 전용(지식베이스도 한국어)이므로, 질문도 한국어 위주여야 한다고 강제.
    # ValueError를 던지면 FastAPI/pydantic이 자동으로 422 응답으로 변환해줌.
    @field_validator("query")
    @classmethod
    def query_must_be_korean(cls, value: str) -> str:
        if not is_korean_dominant(value):
            raise ValueError("질문은 한국어로 입력해주세요.")
        return value


# ChatResponse 안에 들어가는 "참고 문서 1개"를 표현하는 작은 모델.
class SourceSchema(BaseModel):
    content: str   # 문서 내용
    score: float   # 질문과 얼마나 유사한지 (0~1에 가까울수록 유사)
    source_url: str | None = None  # 실제 출처 URL (있으면 답변에서 인용 가능)
    section: str | None = None     # 인용 문자열 (예: "동물보호법 제15조제2항") — 법령 등 구조화 문서만 값이 있음


# POST /chat 의 응답 body 형태.
class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceSchema]  # SourceSchema 여러 개를 담은 리스트
    grounded: bool                # 답변이 실제 문서에 근거했는지 여부 (True/False)


# POST /documents 요청 body: 새 문서를 등록할 때 보내는 내용.
class DocumentCreate(BaseModel):
    content: str = Field(..., min_length=1)
    # `str | None`은 "문자열이거나 None(값 없음)"이라는 뜻 (파이썬 3.10+ 문법).
    # = None 은 기본값이 None이라는 뜻 -> 즉 source_tag는 안 보내도 되는 선택 항목.
    source_tag: str | None = None
    source_url: str | None = None


# GET /documents 응답에 쓰이는 문서 1건의 모양.
class DocumentResponse(BaseModel):
    id: str
    content: str
    source_tag: str | None
    source_url: str | None
    created_at: datetime


# POST /ingest 요청 body: 자동 수집할 URL.
class IngestRequest(BaseModel):
    url: str = Field(..., min_length=1)
    source_tag: str | None = None


# POST /ingest 응답: 몇 개의 청크가 새로 저장됐는지 + 미리보기.
class IngestResponse(BaseModel):
    url: str
    chunks_created: int
    documents: list[DocumentResponse]
