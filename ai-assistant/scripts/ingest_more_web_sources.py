"""추가 웹 소스 3곳(PetMD, Tufts Petfoodology, dailyvet.co.kr)을 documents_test에 적재.

기존 ingest_real_sources.py / ingest_documents_test.py와 동일한 human-verified 패턴입니다.
아래 FACTS의 content_ko는 2026-08-19에 각 페이지를 직접 fetch해 실제로 확인한 내용을
사람이 읽고 한국어로 옮긴 것이며(로컬 LLM 번역 없음), 지어낸 내용은 없습니다.
스키마는 건드리지 않고 기존 documents_test 컬럼(category/subcategory 등)에 값만 채웁니다.

사용법: PYTHONPATH=src python scripts/ingest_more_web_sources.py
"""

import sys

from app.repository_documents_test import insert_document_test
from app.services.embedding import embed_text

COLLECTED_DATE = "2026-08-19"

_PETMD_URL = "https://www.petmd.com/dog/nutrition/are-you-feeding-your-dog-right-amount"
_TUFTS_GROWTH_URL = "https://sites.tufts.edu/petfoodology/2018/09/growth-guide-keeping-your-puppy-on-the-right-track/"
_TUFTS_PUPPY_FOOD_URL = "https://sites.tufts.edu/petfoodology/2019/02/whats-the-best-food-for-your-new-puppy/"
_DAILYVET_URL = "https://www.dailyvet.co.kr/news/policy/226461"

FACTS: list[dict[str, str]] = [
    # --- PetMD: Dog Feeding Chart (vet-verified) ---
    {
        "content": "사료 급여량을 정할 때 가장 먼저 참고해야 할 것은 사료 포장지에 적힌 급여표이며, 이는 그 사료의 칼로리 밀도에 맞춰 반려견 체중별 권장량을 제시합니다.",
        "category": "nutrition-guideline", "subcategory": "feeding-schedule",
        "source": "PetMD", "source_type": "veterinary-editorial",
        "document_title": "Dog Feeding Chart: How Much Food Should I Feed My Dog?", "source_url": _PETMD_URL,
    },
    {
        "content": "급여 횟수는 견종 크기에 따라 다릅니다. 대형·초대형견은 생후 4개월까지 하루 3끼, 이후 성견·노령견은 하루 2~3끼가 권장됩니다. 소형·중형견은 생후 4개월까지 하루 3끼, 이후 성견은 하루 2끼가 권장됩니다. 토이 품종은 생후 4개월까지 하루 4~5끼, 이후 성견은 하루 2끼가 권장됩니다.",
        "category": "nutrition-guideline", "subcategory": "feeding-schedule",
        "source": "PetMD", "source_type": "veterinary-editorial",
        "document_title": "Dog Feeding Chart: How Much Food Should I Feed My Dog?", "source_url": _PETMD_URL,
    },
    {
        "content": "급여량에 영향을 주는 요인은 생애주기(나이), 현재 체중과 이상 체중의 차이, 활동량, 중성화 여부, 사료의 칼로리 밀도, 건강 상태 등입니다.",
        "category": "nutrition-guideline", "subcategory": "feeding-schedule",
        "source": "PetMD", "source_type": "veterinary-editorial",
        "document_title": "Dog Feeding Chart: How Much Food Should I Feed My Dog?", "source_url": _PETMD_URL,
    },
    {
        "content": "적정하게 급여되고 있는 반려견은 허리 라인이 보이고, 갈비뼈가 만져지되 눈에 띄지 않으며, 에너지가 안정적이고, 대변 상태가 양호한 특징을 보입니다.",
        "category": "nutrition-guideline", "subcategory": "feeding-schedule",
        "source": "PetMD", "source_type": "veterinary-editorial",
        "document_title": "Dog Feeding Chart: How Much Food Should I Feed My Dog?", "source_url": _PETMD_URL,
    },
    {
        "content": "미국 반려견의 약 59%가 과체중이거나 비만이며, 이는 관절 질환, 심장 문제, 당뇨병, 암 위험 증가와 수명 단축으로 이어질 수 있습니다.",
        "category": "nutrition-guideline", "subcategory": "feeding-schedule",
        "source": "PetMD", "source_type": "veterinary-editorial",
        "document_title": "Dog Feeding Chart: How Much Food Should I Feed My Dog?", "source_url": _PETMD_URL,
    },
    # --- Tufts Petfoodology: Growth Guide ---
    {
        "content": "월썸(WALTHAM) 강아지 성장 차트는 건강한 어린 개 5만 마리의 데이터를 바탕으로 만들어졌으며, 성별과 예상 성견 체중에 따라 개별 강아지의 성장을 표준 성장 곡선과 비교해 볼 수 있습니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "Growth Guide: Keeping your Puppy on the Right Track", "source_url": _TUFTS_GROWTH_URL,
    },
    {
        "content": "강아지는 생후 6개월까지는 매달, 그 이후에는 2~3개월마다 체중을 재는 것이 권장됩니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "Growth Guide: Keeping your Puppy on the Right Track", "source_url": _TUFTS_GROWTH_URL,
    },
    {
        "content": "강아지는 9점 만점 체형점수(BCS) 기준 4~5점을 유지하는 것이 좋으며, 다소 낮은 편(4점 이하)이 오히려 더 안전한 쪽에 가깝다고 권장됩니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "Growth Guide: Keeping your Puppy on the Right Track", "source_url": _TUFTS_GROWTH_URL,
    },
    {
        "content": "과도한 칼로리 섭취는 강아지의 급격한 성장을 유발해 대형견의 골관절 질환 위험을 높이고, 평생 영향을 줄 수 있습니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "Growth Guide: Keeping your Puppy on the Right Track", "source_url": _TUFTS_GROWTH_URL,
    },
    {
        "content": "간식은 하루 총 칼로리의 10%를 넘지 않아야 하며, 개껌·트레이닝 보상·생가죽·불리스틱·덴탈껌·사람 음식까지 모두 포함해 계산해야 합니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "Growth Guide: Keeping your Puppy on the Right Track", "source_url": _TUFTS_GROWTH_URL,
    },
    {
        "content": "생후 12개월(대형견은 18개월) 이전에 성견용 사료로 전환하면 영양 불균형으로 인해 뼈 기형·골절, 빈혈, 성장 저하, 피부 문제, 심지어 심장 질환까지 일으킬 수 있습니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "Growth Guide: Keeping your Puppy on the Right Track", "source_url": _TUFTS_GROWTH_URL,
    },
    # --- Tufts Petfoodology: What's the Best Food for your New Puppy? ---
    {
        "content": "강아지는 생후 12개월(초대형견은 18개월)까지는 전용 사료가 필요한, 발달상 중요한 시기입니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "What's the Best Food for your New Puppy?", "source_url": _TUFTS_PUPPY_FOOD_URL,
    },
    {
        "content": "사료를 고를 때는 세계소동물수의사회(WSAVA) 가이드라인을 충족하는, 영양학적 전문성과 품질관리 역량을 갖춘 제조사의 제품을 우선 고려하는 것이 권장됩니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "What's the Best Food for your New Puppy?", "source_url": _TUFTS_PUPPY_FOOD_URL,
    },
    {
        "content": "사료 라벨의 영양 적정성 문구를 확인해, 미국사료관리협회(AAFCO)의 성장기 강아지 최소 영양 기준을 충족하는지 확인해야 합니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "What's the Best Food for your New Puppy?", "source_url": _TUFTS_PUPPY_FOOD_URL,
    },
    {
        "content": "성견 예상 체중이 50파운드(약 23kg)를 넘는 대형견 강아지는, 칼슘 함량이 낮고 칼로리가 상대적으로 적은 대형견 전용 성장기 사료를 선택하는 것이 권장됩니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "What's the Best Food for your New Puppy?", "source_url": _TUFTS_PUPPY_FOOD_URL,
    },
    {
        "content": "실제 급여시험(feeding trial)을 거친 사료를 우선하는 것이 좋지만, 영양학적으로 적절히 배합(formulated)된 사료도 허용됩니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "What's the Best Food for your New Puppy?", "source_url": _TUFTS_PUPPY_FOOD_URL,
    },
    {
        "content": "중성화 수술 후에는 사료량을 약 30% 줄이는 것이 권장됩니다.",
        "category": "nutrition-guideline", "subcategory": "puppy-nutrition",
        "source": "Tufts Petfoodology", "source_type": "veterinary-academic",
        "document_title": "What's the Best Food for your New Puppy?", "source_url": _TUFTS_PUPPY_FOOD_URL,
    },
    # --- dailyvet.co.kr: NIAS 영양표준 세부 보완 ---
    {
        "content": "이 영양표준을 충족하는 제품만 2026년 1월 1일부터 '반려동물완전사료'로 표시할 수 있으며, 충족하지 못하면 '기타 반려동물사료'로 분류됩니다.",
        "category": "nutrition-guideline", "subcategory": "life-stage-nutrient-standard",
        "source": "dailyvet", "source_type": "veterinary-news",
        "document_title": "반려동물 사료 영양표준 마련..기준 충족해야 '완전사료'", "source_url": _DAILYVET_URL,
    },
    {
        "content": "이 영양표준은 1992년 제정된 미국 AAFCO 기준과 2008년 설립된 유럽 FEDIAF 기준을 참고해 국내 실정에 맞게 개발되었습니다.",
        "category": "nutrition-guideline", "subcategory": "life-stage-nutrient-standard",
        "source": "dailyvet", "source_type": "veterinary-news",
        "document_title": "반려동물 사료 영양표준 마련..기준 충족해야 '완전사료'", "source_url": _DAILYVET_URL,
    },
    {
        "content": "간식, 습식사료, 영양보조제, 처방식, 펫밀크는 이 완전사료 기준 충족 여부와 무관하게 모두 '기타 반려동물사료'로 분류됩니다.",
        "category": "nutrition-guideline", "subcategory": "life-stage-nutrient-standard",
        "source": "dailyvet", "source_type": "veterinary-news",
        "document_title": "반려동물 사료 영양표준 마련..기준 충족해야 '완전사료'", "source_url": _DAILYVET_URL,
    },
    {
        "content": "이 영양표준은 국립축산과학원과 한국축산학회 반려동물영양연구회가 함께 개발했으며, 2024년 7월 국제 학술 심포지엄을 거쳤습니다.",
        "category": "nutrition-guideline", "subcategory": "life-stage-nutrient-standard",
        "source": "dailyvet", "source_type": "veterinary-news",
        "document_title": "반려동물 사료 영양표준 마련..기준 충족해야 '완전사료'", "source_url": _DAILYVET_URL,
    },
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"{len(FACTS)}건을 documents_test에 적재합니다...\n")
    for i, fact in enumerate(FACTS, start=1):
        embedding = embed_text(fact["content"])
        record = insert_document_test(
            content=fact["content"],
            embedding=embedding,
            category=fact["category"],
            subcategory=fact["subcategory"],
            source=fact["source"],
            source_type=fact["source_type"],
            document_title=fact["document_title"],
            source_url=fact["source_url"],
            metadata={
                "source_url": fact["source_url"],
                "collected_date": COLLECTED_DATE,
            },
        )
        print(f"[{i}/{len(FACTS)}] {record.id} :: {fact['category']}/{fact['subcategory']} :: {fact['content'][:30]}...")


if __name__ == "__main__":
    main()
