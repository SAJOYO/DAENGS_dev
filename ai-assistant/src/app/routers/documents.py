# documents.py = 지식베이스(참고 문서)를 등록/조회하는 API.
# /chat이 "질문"을 다루는 쪽이라면, 여기는 "질문에 답할 때 근거로 쓸 문서"를 관리하는 쪽입니다.

from fastapi import APIRouter

from app.repository import insert_document, list_documents
from app.schemas import DocumentCreate, DocumentResponse
from app.services.embedding import embed_text


router = APIRouter()


# status_code=201 : 성공 시 HTTP 201(Created)로 응답하라는 뜻 (REST 관례: 새로 만들었을 때 201).
@router.post("/documents", response_model=DocumentResponse, status_code=201)
def create_document(request: DocumentCreate) -> DocumentResponse:
    # 1) 문서 텍스트를 숫자 벡터(임베딩)로 변환 (Ollama 호출, services/embedding.py 참고)
    embedding = embed_text(request.content)
    # 2) 원문 + 벡터를 DB에 저장 (repository.py -> Postgres/pgvector)
    record = insert_document(request.content, embedding, request.source_tag, request.source_url)
    # 3) DB에서 돌려받은 record(dataclass)를 API 응답 형태(pydantic 모델)로 변환해서 리턴
    return DocumentResponse(
        id=str(record.id),          # UUID 타입을 문자열로 변환 (JSON엔 UUID 타입이 없어서)
        content=record.content,
        source_tag=record.source_tag,
        source_url=record.source_url,
        created_at=record.created_at,
    )


@router.get("/documents", response_model=list[DocumentResponse])
def get_documents() -> list[DocumentResponse]:
    records = list_documents()
    # 아래는 for 문을 한 줄로 쓴 "리스트 컴프리헨션".
    # 풀어서 쓰면 이런 뜻:
    #   result = []
    #   for r in records:
    #       result.append(DocumentResponse(id=str(r.id), content=r.content, ...))
    #   return result
    return [
        DocumentResponse(
            id=str(r.id),
            content=r.content,
            source_tag=r.source_tag,
            source_url=r.source_url,
            created_at=r.created_at,
        )
        for r in records
    ]
