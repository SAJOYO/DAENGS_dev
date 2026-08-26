"""실제 원문 "단락"을 진짜 청킹 파이프라인(services/chunking.py)에 태워 적재하는 스크립트.

ingest_real_sources.py와의 차이:
  - ingest_real_sources.py: 사람이 요약한 "사실 1개 = 문서 1개" (원자적 사실 단위)
  - 이 스크립트: 실제 웹페이지의 "원문 단락"(여러 문장)을 통째로 가져온 뒤,
    chunking.chunk_text()로 슬라이딩 윈도우(문장 2개씩, 1개씩 겹침) 청크를 만들어 각각 저장.
    한 단락에서 여러 개의(서로 겹치는) 청크가 나오는 게 정상입니다 — 이게 실제 RAG에서
    "청킹"이 의미하는 바입니다. 벡터 검색이 이 여러 청크 후보 중 질문과 가장 가까운 것을
    골라내는 실질적인 일을 하게 됩니다.

RAW_PARAGRAPHS의 text_en은 2026-08-13 WebFetch로 각 페이지에서 그대로(verbatim) 인용한
실제 원문입니다. content_ko는 사람이 검증한 번역이며(로컬 7B 모델 번역의 신뢰성 문제는
ingest_real_sources.py 참고), 청크 단위로 영어 원문과 동일한 슬라이딩 윈도우 구조를 그대로
한국어로 옮겨 적었습니다.

사용법: PYTHONPATH=src python scripts/ingest_chunked_sources.py
"""

import sys

from app.repository import insert_document
from app.services.chunking import chunk_text
from app.services.embedding import embed_text

RAW_PARAGRAPHS: list[dict[str, str]] = [
    {
        "text_en": (
            "ASPCA Poison Control experts have put together a handy list of common people foods "
            "to avoid feeding your pet. As always, if you suspect your pet has eaten any of the "
            "following foods, please note the amount ingested and contact your veterinarian or "
            "poison control at (888) 426-4435."
        ),
        "chunks_ko": [
            "ASPCA 동물 중독 관리 전문가들이 반려동물에게 먹이면 안 되는 흔한 사람 음식 목록을 정리했습니다. "
            "만약 반려동물이 이런 음식을 먹은 것 같다면, 먹은 양을 기록해두고 수의사나 동물 중독 상담 기관에 "
            "바로 연락하는 것이 좋습니다.",
        ],
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "text_en": (
            "Chocolate, coffee, and caffeine are similar in that their toxicity concerns stem from "
            "their methylxanthine concentrations. When ingested by pets, methylxanthines can cause "
            "vomiting and diarrhea, panting, excessive thirst and urination, hyperactivity, abnormal "
            "heart rhythm, tremors, seizures and even death. The darker (higher cacao percentage) the "
            "chocolate, or the higher the caffeine content, the greater the risk for toxicity. White "
            "chocolate has the lowest methylxanthine content while baking chocolate and cocoa powder "
            "have the highest concentrations."
        ),
        "chunks_ko": [
            "초콜릿, 커피, 카페인은 모두 메틸잔틴이라는 성분 때문에 위험하다는 공통점이 있습니다. "
            "반려동물이 메틸잔틴을 섭취하면 구토와 설사, 헐떡임, 과도한 갈증과 배뇨, 과잉행동, 심장 부정맥, "
            "떨림, 발작을 일으킬 수 있고 심하면 사망에 이를 수 있습니다.",

            "메틸잔틴을 섭취하면 구토와 설사, 헐떡임, 과도한 갈증과 배뇨, 과잉행동, 심장 부정맥, 떨림, "
            "발작을 일으킬 수 있고 심하면 사망에 이를 수 있습니다. 초콜릿 색이 진할수록(카카오 함량이 "
            "높을수록), 또는 카페인 함량이 높을수록 중독 위험이 커집니다.",

            "초콜릿 색이 진할수록(카카오 함량이 높을수록), 또는 카페인 함량이 높을수록 중독 위험이 커집니다. "
            "화이트 초콜릿은 메틸잔틴 함량이 가장 낮고, 베이킹용 초콜릿과 코코아 파우더가 가장 높습니다.",
        ],
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "text_en": (
            "Xylitol is used as a sweetener in many products, including gum, candy, baked goods and "
            "toothpaste. Xylitol can cause low blood sugar (hypoglycemia) and potentially liver damage "
            "depending on the amount ingested. Initial signs of toxicosis include vomiting, lethargy and "
            "loss of coordination, which can progress to seizures. Liver damage can occur within 12-24 "
            "hours, which can also cause secondary issues with abnormal bleeding."
        ),
        "chunks_ko": [
            "자일리톨은 껌, 사탕, 구운 과자, 치약 등 다양한 제품에 감미료로 쓰입니다. 자일리톨은 섭취량에 "
            "따라 저혈당을 일으키고 간 손상으로 이어질 수 있습니다.",

            "자일리톨은 섭취량에 따라 저혈당을 일으키고 간 손상으로 이어질 수 있습니다. 중독 초기 증상은 "
            "구토, 무기력, 운동실조이며 심해지면 발작으로 진행될 수 있습니다.",

            "중독 초기 증상은 구토, 무기력, 운동실조이며 심해지면 발작으로 진행될 수 있습니다. 간 손상은 "
            "12~24시간 이내에 나타날 수 있고, 이로 인해 비정상적인 출혈 같은 2차 문제도 생길 수 있습니다.",
        ],
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "text_en": (
            "Puppies retain maternal immunity at birth that gradually fades, requiring frequent "
            "vaccination. Vaccines won't work well until this immunity fades, so puppies need to be "
            "vaccinated frequently (every 2-3 weeks) until they are about 4 months old. Until fully "
            "vaccinated, puppies should avoid unvaccinated dogs or those with unknown vaccination status."
        ),
        "chunks_ko": [
            "강아지는 태어날 때 모체에서 받은 면역력을 갖고 있지만 이는 점점 사라지기 때문에 백신 접종이 "
            "자주 필요합니다. 이 모체 면역이 남아있는 동안에는 백신이 잘 듣지 않기 때문에, 생후 약 4개월이 "
            "될 때까지 2~3주 간격으로 자주 접종해야 합니다.",

            "이 모체 면역이 남아있는 동안에는 백신이 잘 듣지 않기 때문에, 생후 약 4개월이 될 때까지 2~3주 "
            "간격으로 자주 접종해야 합니다. 접종이 완료되기 전까지는 백신을 맞지 않았거나 접종 여부를 알 수 "
            "없는 다른 개와의 접촉을 피하는 것이 좋습니다.",
        ],
        "source_tag": "puppy-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/puppy-care",
    },
    {
        "text_en": (
            "Intestinal worms commonly pass from mother to puppy. These are typically addressed with "
            "preventative deworming shortly after birth, followed by regular monitoring. Your vet will "
            "check your puppy's poop for worms every 2-4 weeks until it has two negative tests in a row."
        ),
        "chunks_ko": [
            "장내 기생충은 어미에게서 새끼에게 흔히 옮겨집니다. 이 때문에 보통 태어난 직후 예방적으로 "
            "구충을 하고, 이후에도 정기적으로 확인합니다.",

            "이 때문에 보통 태어난 직후 예방적으로 구충을 하고, 이후에도 정기적으로 확인합니다. 수의사는 "
            "연속 두 번 음성이 나올 때까지 2~4주마다 강아지의 대변에서 기생충 여부를 검사합니다.",
        ],
        "source_tag": "puppy-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/puppy-care",
    },
    {
        "text_en": (
            "Puppies require specialized food supporting growth. Look for products with AAFCO labeling. "
            "Feeding frequency decreases with age: 4 meals daily at 6-12 weeks, 3 meals at 3-6 months, "
            "2 meals at 6-12 months, then transitioning to adult food after 12 months."
        ),
        "chunks_ko": [
            "강아지(퍼피)는 성장을 뒷받침하는 전용 사료가 필요합니다. 미국사료관리협회(AAFCO) 표시가 "
            "있는 제품을 고르는 것이 좋습니다.",

            "미국사료관리협회(AAFCO) 표시가 있는 제품을 고르는 것이 좋습니다. 급여 횟수는 나이가 들수록 "
            "줄어드는데, 생후 6~12주에는 하루 4번, 3~6개월에는 3번, 6~12개월에는 2번 급여하고, 생후 "
            "12개월 이후에는 성견용 사료로 전환합니다.",
        ],
        "source_tag": "puppy-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/puppy-care",
    },
    {
        "text_en": (
            "Begin early with consistent outdoor bathroom breaks. Key times include mornings, after "
            "meals, post-napping, and before bedtime. With patience and consistency, this usually takes "
            "just a few weeks."
        ),
        "chunks_ko": [
            "집안 배변 훈련은 일찍부터 규칙적으로 밖에서 배변할 기회를 주는 것으로 시작합니다. 아침, "
            "식사 후, 낮잠 후, 잠자리에 들기 전이 특히 중요한 시간대입니다.",

            "아침, 식사 후, 낮잠 후, 잠자리에 들기 전이 특히 중요한 시간대입니다. 인내심을 갖고 꾸준히 "
            "하면 보통 몇 주 안에 자리를 잡습니다.",
        ],
        "source_tag": "puppy-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/puppy-care",
    },
    {
        "text_en": (
            "Between 2-4 months, interaction with people and animals is particularly important, "
            "helping prevent future behavioral issues."
        ),
        "chunks_ko": [
            "생후 2~4개월 사이에 사람 및 다른 동물과의 교류는 특히 중요하며, 이는 이후의 행동 문제를 "
            "예방하는 데 도움이 됩니다.",
        ],
        "source_tag": "puppy-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/puppy-care",
    },
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    total_chunks = sum(len(p["chunks_ko"]) for p in RAW_PARAGRAPHS)
    print(f"{len(RAW_PARAGRAPHS)}개 원문 단락 -> {total_chunks}개 청크로 적재합니다...\n")

    n = 0
    for para in RAW_PARAGRAPHS:
        # 검증: 영어 원문에 실제로 chunk_text()를 돌려서, 우리가 손으로 쓴 한국어 청크 개수가
        # 실제 슬라이딩 윈도우 청커가 만들어내는 청크 개수와 일치하는지 확인 (구조 일관성 체크).
        expected_chunk_count = len(chunk_text(para["text_en"], max_sentences=2, overlap_sentences=1))
        assert expected_chunk_count == len(para["chunks_ko"]), (
            f"청크 개수 불일치: chunk_text()는 {expected_chunk_count}개, "
            f"손으로 쓴 번역은 {len(para['chunks_ko'])}개입니다."
        )

        for chunk_ko in para["chunks_ko"]:
            n += 1
            embedding = embed_text(chunk_ko)
            record = insert_document(chunk_ko, embedding, para["source_tag"], para["source_url"])
            print(f"[{n}/{total_chunks}] {record.id} :: {chunk_ko[:40]}...")


if __name__ == "__main__":
    main()
