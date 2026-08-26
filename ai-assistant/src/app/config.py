# config.py = 앱 전체에서 쓰는 "설정값 모음".
# DB 주소, Ollama 주소, 사용할 모델 이름 등을 하드코딩하지 않고 여기 한 곳에 모아둡니다.

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


# BaseSettings를 상속하면(pydantic-settings 라이브러리 기능),
# 아래 각 필드를 "환경 변수" 또는 ".env 파일"에서 자동으로 읽어옵니다.
# 예: DATABASE_URL 환경변수가 있으면 database_url에 그 값이 들어가고,
#     없으면 아래 적힌 기본값(=default)이 사용됩니다.
class Settings(BaseSettings):
    # env_file=".env" : 프로젝트 루트의 .env 파일을 읽어서 값을 채워라는 뜻.
    # extra="ignore" : .env에 여기 정의 안 된 값이 더 있어도 에러 내지 말고 무시.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "변수명: 타입 = 기본값" 형태 (파이썬 타입 힌트).
    # str = 문자열, int = 정수, float = 소수. 이 타입은 강제는 아니지만
    # pydantic이 실제로 값이 이 타입에 맞는지 검증/변환해줍니다.
    database_url: str = "postgresql://postgres:postgres@localhost:5432/dogai"
    ollama_host: str = "http://localhost:11434"
    embedding_model: str = "bge-m3"          # 질문/문서를 벡터로 바꿀 때 쓰는 임베딩 모델
    generation_model: str = "qwen2.5:7b-instruct"  # 실제 답변 문장을 생성하는 LLM
    top_k: int = 3                # LLM에게 최종적으로 넘겨줄 문서 개수
    # 이 값 이상 유사해야 "관련 문서 있음"으로 인정 (BR2).
    # 0.6으로 재보정(2026-08-20): documents_test가 1,551건으로 늘어난 뒤 scripts/
    # calibrate_similarity_threshold.py로 실측한 결과, 기존 0.5에서는 무관한 질문의 83%가
    # 오탐(false positive)으로 grounded 처리됐음. 정답 질의 top-1 점수(0.624~0.811)와
    # 무관 질의 top-1 점수(0.486~0.575) 사이에 뚜렷한 간격이 있어 0.6으로 올리면 오탐률 0%,
    # 정답 grounded율은 100% 그대로 유지됨.
    similarity_threshold: float = 0.6
    retrieval_pool_size: int = 10      # 하이브리드 검색(벡터/키워드) 1차 후보를 각각 몇 개씩 넓게 가져올지
    # 위 후보를 RRF로 합친 뒤, 리랭커에 넘길 후보 개수 (top_k보다 커야 함).
    # 2026-08-24: 리랭커를 후보당 LLM 호출 -> 질의당 1회 배치 호출로 바꾸면서(reranker.py)
    # 8->5로 줄여봤더니 id 일치율(Hit@1/MRR)이 눈에 띄게 떨어져 8로 되돌림 — 배치화 자체의
    # 정확도 손실과 풀 축소로 인한 손실을 분리해서 측정하기 위함 (evaluate_law_qa_hit_rate.py).
    rerank_pool_size: int = 8
    rda_petfood_api_key: str = ""      # 농촌진흥청 반려동물 사료정보 Open API 서비스키 (미설정 시 해당 소스만 스킵)


# @lru_cache 는 "함수 결과를 캐싱해주는 데코레이터"입니다.
# (데코레이터 = 함수 위에 @이름 을 붙여서 그 함수의 동작을 감싸/확장하는 문법)
# 즉 get_settings()를 여러 번 호출해도 .env 파일을 매번 다시 읽지 않고,
# 최초 1번만 Settings()를 만들고 그 다음부터는 같은 객체를 재사용합니다.
@lru_cache
def get_settings() -> Settings:
    return Settings()
