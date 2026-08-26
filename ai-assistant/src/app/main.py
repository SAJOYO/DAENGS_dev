# main.py = 이 애플리케이션의 "진입점"(entry point).
# `uvicorn app.main:app` 으로 서버를 켜면, uvicorn이 이 파일에서 `app` 변수를 찾아 실행합니다.
#
# 전체 요청 흐름 (예: 브라우저에서 "포도 먹여도 돼?"를 물어봤을 때):
#   1. 브라우저 -> POST /chat  (여기서 라우팅 시작)
#   2. routers/chat.py 가 요청을 받아서
#   3. services/rag.py 가 "임베딩 -> 검색 -> 생성 -> 가드레일" 순서로 조합하고
#   4. 그 안에서 repository.py(DB), services/embedding.py, services/generation.py 를 호출합니다.
# 이 파일(main.py)은 그 라우터들을 FastAPI 앱에 "등록"만 하는 역할입니다.

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# routers 폴더 안의 각 파일에는 `router = APIRouter()` 가 정의되어 있고,
# 그 안에 `/health`, `/documents`, `/chat` 같은 개별 URL 처리 함수들이 들어있습니다.
from app.routers import chat, documents, health, ingest

# FastAPI() 로 앱 객체를 하나 만듭니다. 이 app이 웹 서버의 "본체"입니다.
app = FastAPI(title="반려견 생활 관리 AI 비서 - MVP", version="0.1.0")

# 각 라우터를 앱에 연결(등록)합니다. 이렇게 해야 예를 들어 routers/chat.py 안의
# `@router.post("/chat")` 함수가 실제로 "POST /chat" 요청에 반응하게 됩니다.
# tags=[...] 는 /docs (Swagger UI) 화면에서 그룹으로 묶어 보여주기 위한 이름표일 뿐, 동작에는 영향 없음.
app.include_router(health.router, tags=["health"])
app.include_router(documents.router, tags=["documents"])
app.include_router(chat.router, tags=["chat"])
app.include_router(ingest.router, tags=["ingest"])

# 정적 파일(HTML/CSS/JS) 서빙 설정.
# Path(__file__).parent 는 "이 main.py 파일이 들어있는 폴더"를 의미 (즉 src/app/).
# 그 밑의 static/ 폴더에 index.html이 있고, StaticFiles(html=True) 덕분에
# "/" 로 접속하면 자동으로 static/index.html을 내려줍니다 (테스트용 채팅 UI).
# 주의: 이 mount("/") 는 반드시 위의 include_router들보다 "뒤"에 와야 합니다.
# 그래야 /chat, /health 같은 API 경로가 먼저 매칭되고, 나머지 경로만 정적 파일로 처리됩니다.
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
