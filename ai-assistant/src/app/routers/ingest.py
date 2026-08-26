# ingest.py = "URL 하나 던지면 자동으로 지식베이스에 저장" API.
# 실제 로직은 services/web_ingest.py의 ingest_url()이 전부 처리하고, 여기는 HTTP 껍데기만.

from fastapi import APIRouter, HTTPException

from app.schemas import DocumentResponse, IngestRequest, IngestResponse
from app.services.web_ingest import ingest_url

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse, status_code=201)
def ingest(request: IngestRequest) -> IngestResponse:
    try:
        records = ingest_url(request.url, request.source_tag)
    except Exception as exc:  # URL 접속 실패, HTML 파싱 실패, Ollama/DB 장애 등
        raise HTTPException(
            status_code=502,
            detail=f"URL을 수집하는 중 오류가 발생했습니다: {exc}",
        ) from exc

    return IngestResponse(
        url=request.url,
        chunks_created=len(records),
        documents=[
            DocumentResponse(
                id=str(r.id),
                content=r.content,
                source_tag=r.source_tag,
                source_url=r.source_url,
                created_at=r.created_at,
            )
            for r in records
        ],
    )
