"""documents_test(신규 category/subcategory/source 스키마) 적재 스크립트.

두 가지 일을 합니다:
  1. 기존 documents 테이블에 이미 들어간 자료(ingest_real_sources.py의 RAW_FACTS,
     ingest_chunked_sources.py의 RAW_PARAGRAPHS)를 그대로 재사용해 새 컬럼 구조로
     재태깅한 뒤 documents_test에 이관합니다. (원본 파일들은 전혀 수정하지 않고 import만 함)
  2. 반려견 영양/급여 관련 신규 소스 3곳(NIAS, VCA, WSAVA)의 사실을 새로 추가합니다.
     - NIAS, VCA: 2026-08-19 WebFetch로 실제 페이지에서 확인한 사실입니다.
     - RDA 농촌진흥청 Open API(data.go.kr, item 15059270)는 서비스키 발급(사용자 본인 계정
       가입 필요)이 안 되어 있어 이번 실행에서는 건너뜁니다. RDA_PETFOOD_API_KEY를 .env에
       채워 넣으면 시도하지만, 실제 엔드포인트/응답 필드명은 발급 후 데이터 포털 문서를 보고
       fetch_rda_petfood_facts()의 TODO 부분을 검증해야 합니다 (키 없이는 확인 불가능했음).
     - WSAVA 툴킷은 이번 수집 시도에서 wsava.org가 WebFetch 요청을 403으로 차단해 신규 사실을
       추가하지 못했습니다 (기존 7건은 그대로 이관됨). 필요하면 브라우저 기반 수집 도구로 재시도하세요.

사용법: PYTHONPATH=src python scripts/ingest_documents_test.py
"""

import sys
from typing import Any

from app.config import get_settings
from app.repository_documents_test import insert_document_test
from app.services.chunking import chunk_text
from app.services.embedding import embed_text

from ingest_real_sources import RAW_FACTS
from ingest_chunked_sources import RAW_PARAGRAPHS

COLLECTED_DATE = "2026-08-19"

# 기존 documents 테이블의 source_tag -> 새 (category, subcategory) 매핑.
_TAG_TO_CATEGORY: dict[str, tuple[str, str]] = {
    "food-safety": ("food-safety", "toxic-food"),
    "nutrition-guideline": ("nutrition-guideline", "body-condition-calorie"),
    "routine-care": ("routine-care", "checkup-vaccination-grooming"),
    "puppy-care": ("routine-care", "puppy-care"),
}

# source_url -> (source, source_type, document_title) 매핑 (이관 대상 3개 사이트).
_URL_TO_SOURCE_META: dict[str, tuple[str, str, str]] = {
    "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets": (
        "ASPCA",
        "poison-control",
        "People Foods to Avoid Feeding Your Pets",
    ),
    "https://wsava.org/global-guidelines/global-nutrition-guidelines/": (
        "WSAVA",
        "veterinary-guideline",
        "WSAVA Global Nutrition Guidelines",
    ),
    "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs": (
        "Merck Veterinary Manual",
        "veterinary-manual",
        "Routine Health Care of Dogs",
    ),
    "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/puppy-care": (
        "Merck Veterinary Manual",
        "veterinary-manual",
        "Puppy Care",
    ),
}


def _migrate_real_facts() -> list[dict[str, Any]]:
    """ingest_real_sources.py의 RAW_FACTS를 새 스키마 형태로 변환."""
    items = []
    for fact in RAW_FACTS:
        category, subcategory = _TAG_TO_CATEGORY[fact["source_tag"]]
        source, source_type, document_title = _URL_TO_SOURCE_META[fact["source_url"]]
        items.append(
            {
                "content": fact["content_ko"],
                "category": category,
                "subcategory": subcategory,
                "source": source,
                "source_type": source_type,
                "document_title": document_title,
                "section": None,
                "metadata": {
                    "source_url": fact["source_url"],
                    "fact_en": fact["fact_en"],
                    "migrated_from": "ingest_real_sources.py",
                },
            }
        )
    return items


def _migrate_chunked_paragraphs() -> list[dict[str, Any]]:
    """ingest_chunked_sources.py의 RAW_PARAGRAPHS(청크 단위)를 새 스키마 형태로 변환."""
    items = []
    for para in RAW_PARAGRAPHS:
        category, subcategory = _TAG_TO_CATEGORY[para["source_tag"]]
        source, source_type, document_title = _URL_TO_SOURCE_META[para["source_url"]]
        # 원본 스크립트와 동일하게, 실제 청커가 만드는 청크 개수와 손으로 쓴 번역 개수가
        # 일치하는지 다시 한번 검증 (구조 일관성 체크를 이관 과정에서도 유지).
        expected = len(chunk_text(para["text_en"], max_sentences=2, overlap_sentences=1))
        assert expected == len(para["chunks_ko"]), (
            f"청크 개수 불일치: {para['source_url']}"
        )
        for i, chunk_ko in enumerate(para["chunks_ko"]):
            items.append(
                {
                    "content": chunk_ko,
                    "category": category,
                    "subcategory": subcategory,
                    "source": source,
                    "source_type": source_type,
                    "document_title": document_title,
                    "section": None,
                    "metadata": {
                        "source_url": para["source_url"],
                        "text_en": para["text_en"],
                        "chunk_index": i,
                        "migrated_from": "ingest_chunked_sources.py",
                    },
                }
            )
    return items


# --- 신규 소스 1: 국립축산과학원(NIAS) 반려동물 사료 영양표준 ---
# 원문 PDF는 스캔 이미지라 텍스트 추출이 불가능해(확인됨), 텍스트로 읽히는 정책 뉴스 페이지를
# source_url로 사용. 2026-08-19 WebFetch로 실제 페이지에서 확인한 사실.
_NIAS_SOURCE_URL = "https://www.korea.kr/news/policyNewsView.do?newsId=156656220"

_NIAS_FACTS: list[dict[str, str]] = [
    {
        "fact_en": "NIAS (National Institute of Animal Science) has developed a nutrient standard for domestic pet (dog and cat) food, based on research conducted since 2010.",
        "content_ko": "국립축산과학원(NIAS)은 2010년부터 연구를 추진해 국내 실정에 맞는 반려동물(개·고양이) 사료 영양표준을 개발했습니다.",
    },
    {
        "fact_en": "The standard specifies recommended levels for 38 nutrients for adult dogs, and 40 nutrients for puppies and breeding female dogs.",
        "content_ko": "이 영양표준은 다 자란 개(성견) 기준 38종, 강아지와 번식기 암캐 기준 40종의 권장 영양소 함량을 제시합니다.",
    },
    {
        "fact_en": "The standard is composed of purpose/scope, nutrients and energy, recommended nutrient levels, feed metabolizable energy calculation methods, digestibility prediction test methods, and appendices.",
        "content_ko": "영양표준은 목적·범위, 영양소와 에너지, 권장 영양 수준, 사료 대사에너지 산출법, 소화율 예측 시험법과 부록으로 구성됩니다.",
    },
    {
        "fact_en": "The standard is a guideline presenting the minimum recommended levels of essential nutrients needed for pets to maintain a healthy life and normal physiological state.",
        "content_ko": "이 영양표준은 반려동물이 건강한 생활과 정상적인 생리 상태를 유지하는 데 필요한 필수 영양소의 최소 권장 수준을 제시하는 지침입니다.",
    },
    {
        "fact_en": "The standard is designed for feed manufacturers to use when labeling products that meet the 'complete feed' (완전사료) criteria.",
        "content_ko": "이 영양표준은 사료 제조업체가 '완전사료' 표시 기준을 충족하는 제품을 만드는 데 활용하도록 설계되었습니다.",
    },
]

# --- 신규 소스 2: VCA Animal Hospitals 급여 가이드 ---
# 2026-08-19 WebFetch로 실제 페이지에서 확인한 사실.
_VCA_SOURCE_URL = "https://vcahospitals.com/know-your-pet/nutrition-general-feeding-guidelines-for-dogs"

_VCA_FACTS: list[dict[str, str]] = [
    {
        "fact_en": "For most pet dogs, feeding once or twice per day is recommended; many dogs benefit from eating equally divided meals two to three times per day.",
        "content_ko": "대부분의 반려견에게는 하루 한두 번 급여가 권장되며, 많은 개들이 하루 2~3회로 나누어 먹었을 때 더 좋은 효과를 봅니다.",
    },
    {
        "fact_en": "Younger puppies with a larger energy requirement may need the total daily amount divided into three or four meals.",
        "content_ko": "에너지 요구량이 큰 어린 강아지는 하루 급여량을 3~4번으로 나누어 줄 필요가 있을 수 있습니다.",
    },
    {
        "fact_en": "All pet food packages should include feeding guidelines with a suggested serving size based on the dog's weight and, sometimes, activity level.",
        "content_ko": "모든 사료 포장에는 체중(경우에 따라 활동량)을 기준으로 한 권장 급여량 가이드라인이 표시되어 있어야 합니다.",
    },
    {
        "fact_en": "A veterinary team can perform an energy calculation based on a dog's weight, activity, and other factors to give a more individualized daily calorie recommendation.",
        "content_ko": "수의사는 체중, 활동량 등을 반영한 에너지 계산을 통해 더 개별화된 하루 칼로리 급여량을 알려줄 수 있습니다.",
    },
    {
        "fact_en": "Puppies should be fed growth-formulated food and generally transition to adult food around 10 months of age, while large-breed puppies should continue puppy food until 18-24 months of age.",
        "content_ko": "강아지는 성장기용 사료를 먹이다가 보통 생후 10개월 무렵 성견용 사료로 전환하며, 대형견은 생후 18~24개월까지 강아지용 사료를 유지하는 것이 좋습니다.",
    },
    {
        "fact_en": "Obesity is prevalent in dogs and often results from excess calorie intake; once a dog becomes overweight it can be challenging to lose the excess weight, so prevention is the better approach.",
        "content_ko": "개의 비만은 흔하며 대개 칼로리 과다 섭취에서 비롯됩니다. 한번 과체중이 되면 감량이 쉽지 않기 때문에, 애초에 과체중이 되지 않도록 예방하는 것이 더 좋은 접근입니다.",
    },
    {
        "fact_en": "Fresh, clean water should always be available; for dogs needing increased hydration, canned food or soaked dry kibble can help.",
        "content_ko": "신선하고 깨끗한 물을 항상 마실 수 있게 해주어야 하며, 수분 섭취를 늘려야 하는 개에게는 캔 사료나 물에 불린 사료가 도움이 될 수 있습니다.",
    },
]


def _build_new_source_items() -> list[dict[str, Any]]:
    items = []
    for fact in _NIAS_FACTS:
        items.append(
            {
                "content": fact["content_ko"],
                "category": "nutrition-guideline",
                "subcategory": "life-stage-nutrient-standard",
                "source": "NIAS",
                "source_type": "government-standard",
                "document_title": "반려동물(개·고양이) 사료 영양표준",
                "section": None,
                "metadata": {
                    "source_url": _NIAS_SOURCE_URL,
                    "fact_en": fact["fact_en"],
                    "collected_date": COLLECTED_DATE,
                },
            }
        )
    for fact in _VCA_FACTS:
        items.append(
            {
                "content": fact["content_ko"],
                "category": "nutrition-guideline",
                "subcategory": "feeding-schedule",
                "source": "VCA Animal Hospitals",
                "source_type": "animal-hospital",
                "document_title": "Nutrition - General Feeding Guidelines for Dogs",
                "section": None,
                "metadata": {
                    "source_url": _VCA_SOURCE_URL,
                    "fact_en": fact["fact_en"],
                    "collected_date": COLLECTED_DATE,
                },
            }
        )
    return items


def fetch_rda_petfood_facts(api_key: str) -> list[dict[str, Any]]:
    """농촌진흥청 반려동물 사료정보 Open API(data.go.kr, item 15059270) 수집.

    주의: 이 함수는 서비스키가 없어 실제 응답을 확인하지 못한 상태로 작성되었습니다.
    data.go.kr 활용신청 승인 후 제공되는 End Point/응답 필드명 문서를 보고 아래 TODO를
    실제 값으로 채운 뒤 사용하세요. 검증 전까지는 호출하지 않고 빈 리스트를 반환합니다.
    """
    if not api_key:
        print("  RDA_PETFOOD_API_KEY가 비어 있어 이 소스는 건너뜁니다.")
        return []

    # TODO: data.go.kr 활용신청 승인 후 제공되는 실제 End Point/파라미터/응답 스키마로 교체.
    # 키 없이는 정확한 필드명을 확인할 수 없어, 검증되지 않은 상태로 실제 API를 호출하지 않습니다.
    print(
        "  RDA_PETFOOD_API_KEY는 설정되어 있지만, 실제 API 엔드포인트/응답 스키마가 "
        "아직 검증되지 않아 이 소스는 건너뜁니다. fetch_rda_petfood_facts()의 TODO를 "
        "data.go.kr 문서에 맞춰 채운 뒤 다시 실행하세요."
    )
    return []


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()

    items = (
        _migrate_real_facts()
        + _migrate_chunked_paragraphs()
        + _build_new_source_items()
        + fetch_rda_petfood_facts(settings.rda_petfood_api_key)
    )

    print(f"{len(items)}건을 documents_test에 적재합니다...\n")
    for i, item in enumerate(items, start=1):
        embedding = embed_text(item["content"])
        record = insert_document_test(
            content=item["content"],
            embedding=embedding,
            category=item["category"],
            subcategory=item["subcategory"],
            metadata=item["metadata"],
            source=item["source"],
            source_type=item["source_type"],
            document_title=item["document_title"],
            section=item["section"],
            source_url=item["metadata"]["source_url"],
        )
        print(f"[{i}/{len(items)}] {record.id} :: {item['category']}/{item['subcategory']} :: {item['content'][:30]}...")


if __name__ == "__main__":
    main()
