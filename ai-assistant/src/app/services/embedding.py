# embedding.py = 텍스트를 "숫자 벡터"로 바꿔주는 역할.
#
# 왜 필요한가?
#   컴퓨터는 "포도 먹여도 돼?"와 "포도는 위험해요" 같은 문장이 서로 비슷한 의미인지
#   글자만 봐서는 알기 어렵습니다. 그래서 문장을 수백~수천 개의 숫자로 이루어진
#   벡터(임베딩)로 변환하면, 의미가 비슷한 문장끼리는 벡터도 서로 가까워집니다.
#   이 벡터들 사이의 거리(코사인 유사도)를 계산해서 "관련 문서 찾기"를 하는 것이 RAG의 핵심.

import ollama

from app.config import get_settings


def embed_text(text: str) -> list[float]:
    settings = get_settings()
    # ollama.Client : 로컬에서 실행 중인 Ollama 서버(LLM 런타임)에 접속하는 클라이언트.
    client = ollama.Client(host=settings.ollama_host)
    # settings.embedding_model (예: "bge-m3") 모델에게 이 텍스트의 임베딩을 요청.
    response = client.embeddings(model=settings.embedding_model, prompt=text)
    # 응답은 {"embedding": [0.123, -0.045, ...]} 같은 딕셔너리라서, 그 안의 리스트만 꺼내 반환.
    return response["embedding"]
