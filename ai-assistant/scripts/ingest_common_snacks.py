"""흔히 물어보지만 ASPCA 위험음식 목록엔 없는 사람 간식들을 documents_test에 추가하는 스크립트.

계기(2026-08-20): "솜사탕 먹여도 돼?" 질문이 지식베이스에 grounding할 사실이 전혀 없어서
(솜사탕은 독성이 아니라 "당분 과다" 문제라 ASPCA 목록엔 없음), 로컬 LLM이 근거 없이 답하다가
"솜사탕"이라는 단어 자체를 "pson사탕"/"psona-sajang" 등으로 헛짚어내는 hallucination이
반복 확인됨. 언어 순수성 검사(language.py)로는 못 잡는 종류의 문제라, 실제 사실을 채워서
grounded 답변이 나오게 하는 것으로 해결한다.

사용법: PYTHONPATH=src python scripts/ingest_common_snacks.py
"""

import sys

from app.repository_documents_test import insert_document_test
from app.services.embedding import embed_text

COLLECTED_DATE = "2026-08-20"
_SOURCE_URL = "https://www.dogster.com/dog-nutrition/can-dogs-eat-cotton-candy"

FACTS: list[dict[str, str]] = [
    {
        "fact_en": "Traditional cotton candy contains just sugar and coloring/flavoring — one ounce has less sugar than a soda can and only 100 calories; while not toxic, it isn't good for dogs either.",
        "content_ko": "일반 솜사탕은 설탕과 색소·향료로만 이루어져 있으며 28g당 약 100kcal입니다. 독성은 없지만 칼로리가 높은 편이라 반려견에게 좋은 간식은 아닙니다.",
    },
    {
        "fact_en": "Excess sugar intake over time contributes to weight gain, diabetes, and obesity, which in turn increase risk of heart disease, hypertension, osteoarthritis, and cancer.",
        "content_ko": "설탕이 많은 간식을 자주 주면 체중 증가, 당뇨병, 비만으로 이어질 수 있고, 이는 다시 심장질환·고혈압·관절염 위험을 높입니다. 특히 소형견은 적은 양으로도 영향을 크게 받습니다.",
    },
    {
        "fact_en": "Sugar-free cotton candy varieties may contain xylitol, which is extremely toxic to dogs — it triggers insulin release causing dangerous blood sugar drops, and can lead to hypoglycemia, seizures, liver failure, or death even in small amounts.",
        "content_ko": "무설탕(슈가프리) 솜사탕에는 자일리톨이 들어있을 수 있습니다. 자일리톨은 강아지에게 매우 위험한 성분으로, 소량만 섭취해도 저혈당·발작·간부전을 일으키고 심하면 사망에 이를 수 있습니다.",
    },
    {
        "fact_en": "Chocolate-flavored cotton candy contains theobromine, which dogs cannot metabolize as well as humans, posing a separate chocolate-toxicity risk (vomiting, increased heart rate, and in severe cases seizures).",
        "content_ko": "초콜릿 맛 솜사탕에는 테오브로민이 들어있어, 당분 문제와는 별개로 초콜릿 중독 위험(구토, 심박수 증가, 심한 경우 발작)도 함께 있습니다.",
    },
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"{len(FACTS)}건을 documents_test에 적재합니다...\n")
    for i, fact in enumerate(FACTS, start=1):
        embedding = embed_text(fact["content_ko"])
        record = insert_document_test(
            content=fact["content_ko"],
            embedding=embedding,
            category="food-safety",
            subcategory="toxic-food",
            source="Dogster",
            source_type="veterinary-editorial",
            document_title="Can Dogs Eat Cotton Candy? Vet-Verified Facts & Safety Guide",
            source_url=_SOURCE_URL,
            metadata={
                "source_url": _SOURCE_URL,
                "fact_en": fact["fact_en"],
                "collected_date": COLLECTED_DATE,
            },
        )
        print(f"[{i}/{len(FACTS)}] {record.id} :: {fact['content_ko'][:30]}...")


if __name__ == "__main__":
    main()
