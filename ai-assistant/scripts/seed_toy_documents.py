"""toy 지식베이스 시드 스크립트.

requirement-verification-questions.md Q5에 따라 AI가 조사한 데이터 소스
(ASPCA Animal Poison Control, Merck Veterinary Manual, NIAS RER 공식, AKC 가이드 등)를
참고하여 직접 작성한 한국어 toy 문장입니다 (원문 그대로 복사하지 않음).

사용법: PYTHONPATH=src python scripts/seed_toy_documents.py  (Windows PowerShell은 README 참고)
"""

from app.repository import insert_document
from app.services.embedding import embed_text

TOY_DOCUMENTS = [
    {
        "content": "포도와 건포도는 소량이라도 강아지에게 급성 신부전을 유발할 수 있어 절대 급여해서는 안 됩니다.",
        "source_tag": "food-safety",
    },
    {
        "content": "초콜릿에 들어있는 테오브로민 성분은 강아지에게 독성이 있으며, 카카오 함량이 높을수록 더 위험합니다.",
        "source_tag": "food-safety",
    },
    {
        "content": "양파, 마늘, 파류는 강아지의 적혈구를 손상시켜 빈혈을 유발할 수 있어 조리된 형태라도 급여하면 안 됩니다.",
        "source_tag": "food-safety",
    },
    {
        "content": "성견의 하루 적정 급여량은 체중과 활동량에 따라 달라지며, 안정시 필요 에너지량(RER)은 대략 70 x 체중(kg)의 0.75제곱으로 추정할 수 있습니다.",
        "source_tag": "feeding-amount",
    },
    {
        "content": "소형견은 하루 20~30분, 중대형견은 하루 30~60분 정도의 산책이 일반적으로 권장되며, 목욕은 2~4주에 한 번 정도가 무난합니다.",
        "source_tag": "exercise",
    },
]


def main() -> None:
    for doc in TOY_DOCUMENTS:
        embedding = embed_text(doc["content"])
        record = insert_document(doc["content"], embedding, doc["source_tag"])
        print(f"등록됨: {record.id} - {doc['content'][:24]}...")


if __name__ == "__main__":
    main()
