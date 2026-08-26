# health.py = 서버가 살아있는지 확인용 엔드포인트.
# Docker/배포 환경에서 "이 서버 정상 응답하나?" 체크할 때 흔히 씀.

from fastapi import APIRouter

# APIRouter() : "이 파일에서 처리할 URL들을 담는 그릇" 정도로 생각하면 됩니다.
# main.py에서 app.include_router(health.router)로 이 router를 앱 전체에 합칩니다.
router = APIRouter()


# @router.get("/health") 데코레이터: "GET /health 요청이 오면 아래 함수를 실행해라"는 뜻.
# 함수 이름(health)은 아무거나 상관없고, 데코레이터에 적힌 경로("/health")가 실제 URL입니다.
@router.get("/health")
def health() -> dict[str, str]:
    # dict[str, str] : {"문자열 키": "문자열 값"} 형태의 딕셔너리라는 타입 힌트.
    # 이 함수가 return한 딕셔너리는 FastAPI가 자동으로 JSON으로 변환해서 응답합니다.
    return {"status": "ok"}
