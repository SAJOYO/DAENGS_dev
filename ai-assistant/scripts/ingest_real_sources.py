"""실제 웹사이트 3곳에서 수집한 사실을 지식베이스로 적재하는 스크립트 (로드맵 ⑧ 착수).

기존 seed_toy_documents.py는 AI가 손으로 쓴 toy 문장이었습니다. 이 스크립트는 그 대신:
  1. 아래 각 항목의 fact_en은 실제로 HTTP 요청을 보내 살아있는 페이지에서 확인한 사실입니다
     (수집 시점: 2026-08-13). source_url에 실제 출처를 그대로 저장해서, /chat 응답이
     실제 출처를 인용할 수 있게 합니다.
  2. content_ko는 fact_en을 사실 관계 그대로 옮긴 한국어 번역입니다. 처음에는 로컬 LLM
     (qwen2.5:7b-instruct)에게 번역을 맡겨봤으나, 검수 중 다음 문제가 발견되어 폐기했습니다:
       - 답변 도중 중국어로 코드스위칭 (guardrail.py에서 이미 확인된 것과 동일한 패턴)
       - 실제 사실 오역: "distemper"(개홍역)를 "독감"으로, "rabies"(광견병)를
         존재하지 않는 "렉스바이러스"로 잘못 옮김
     건강/안전 정보를 다루는 지식베이스에 검증되지 않은 소형 모델 번역을 넣는 것은
     위험하다고 판단해, content_ko는 사람이 사실관계를 검증한 번역으로 대체했습니다.
     (참고: guardrail.py의 정규식 우회 사례와 함께, 로컬 7B 양자화 모델을 "검증 없이"
     신뢰하기 어렵다는 근거가 하나 더 쌓인 셈입니다.)

사용법: PYTHONPATH=src python scripts/ingest_real_sources.py
"""

import sys

from app.repository import insert_document
from app.services.embedding import embed_text

RAW_FACTS: list[dict[str, str]] = [
    # --- ASPCA Animal Poison Control: "People Foods to Avoid Feeding Your Pets" ---
    {
        "fact_en": "Alcohol can cause vomiting, diarrhea, incoordination, depression, difficulty breathing, tremors, changes in blood pH, coma and even death in dogs.",
        "content_ko": "술(알코올)은 소량이라도 강아지에게 구토, 설사, 운동실조, 무기력, 호흡곤란, 떨림, 혈액 pH 변화를 일으킬 수 있으며 심하면 혼수상태나 사망에 이를 수 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Yeast dough can cause gas accumulation and stomach bloating in a dog's digestive system, with the potential for the stomach to twist, plus alcohol toxicity as the dough ferments.",
        "content_ko": "이스트가 들어간 반죽은 강아지의 위 속에서 가스가 차고 부풀어 오르게 하며, 위가 뒤틀리거나(위확장염전) 발효 과정에서 생기는 알코올로 인한 중독을 일으킬 위험이 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Chocolate can cause vomiting and diarrhea, panting, excessive thirst and urination, hyperactivity, abnormal heart rhythm, tremors, seizures and even death in dogs; the higher the cacao content, the greater the risk.",
        "content_ko": "초콜릿은 강아지에게 구토와 설사, 헐떡임, 과도한 갈증과 배뇨, 과잉행동, 심장 부정맥, 떨림, 발작을 일으킬 수 있고 심하면 사망에 이를 수 있으며, 카카오 함량이 높을수록 위험이 커집니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Coffee and other caffeinated beverages contain methylxanthines, the same toxic substances found in chocolate, and pose a similar risk to dogs.",
        "content_ko": "커피 등 카페인이 든 음료에는 초콜릿과 같은 계열의 독성 물질(메틸잔틴)이 들어 있어 강아지에게 초콜릿과 비슷한 위험을 줍니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Exposure to grapes or raisins, even in small amounts, can lead to kidney damage in dogs.",
        "content_ko": "포도나 건포도는 소량만 먹여도 강아지에게 신장 손상을 일으킬 수 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Onions, garlic, and chives can cause gastrointestinal irritation and red blood cell damage in dogs, which can lead to anemia, even when cooked.",
        "content_ko": "양파, 마늘, 파(차이브 포함)는 조리된 상태라도 강아지의 위장을 자극하고 적혈구를 손상시켜 빈혈을 유발할 수 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Milk and other dairy products can cause diarrhea or other digestive upset in dogs.",
        "content_ko": "우유 등 유제품은 강아지에게 설사나 소화불량 등 소화기 문제를 일으킬 수 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Macadamia nuts can cause weakness, incoordination, depression, vomiting, tremors and hyperthermia in dogs, with symptoms appearing within 12 hours of ingestion.",
        "content_ko": "마카다미아 너트를 먹으면 강아지에게 섭취 후 12시간 이내에 무기력, 운동실조, 우울감, 구토, 떨림, 고체온 증상이 나타날 수 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Almonds, pecans, and walnuts can cause vomiting and diarrhea in dogs, and potentially pancreatitis.",
        "content_ko": "아몬드, 피칸, 호두는 강아지에게 구토와 설사를 일으킬 수 있고, 췌장염으로 이어질 가능성도 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Coconut and coconut oil can cause stomach upset, loose stools and diarrhea in dogs.",
        "content_ko": "코코넛과 코코넛 오일은 강아지에게 속쓰림, 무른 변, 설사를 일으킬 수 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Raw meat and raw eggs can contain harmful bacteria; raw eggs also interfere with a dog's vitamin absorption.",
        "content_ko": "날고기와 날달걀에는 해로운 세균이 들어있을 수 있고, 특히 날달걀은 강아지의 비타민 흡수를 방해합니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Bones pose a risk of injury or obstruction to a dog's gastrointestinal tract.",
        "content_ko": "뼈는 강아지의 위장관에 상처를 내거나 막히게 할 위험이 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Xylitol can cause low blood sugar (hypoglycemia) and potentially liver damage in dogs; symptoms include vomiting, lethargy and loss of coordination, which can progress to seizures.",
        "content_ko": "자일리톨은 강아지에게 저혈당을 일으키고 간 손상으로 이어질 수 있으며, 구토·무기력·운동실조로 시작해 심하면 발작까지 진행될 수 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    {
        "fact_en": "Excessive salt intake can cause increased thirst and urination in dogs, and in more severe cases vomiting, diarrhea, depression, tremors, seizures and even death.",
        "content_ko": "과도한 염분 섭취는 강아지에게 갈증과 배뇨 증가를 일으키고, 심한 경우 구토, 설사, 무기력, 떨림, 발작을 일으켜 사망에 이를 수도 있습니다.",
        "source_tag": "food-safety",
        "source_url": "https://www.aspca.org/pet-care/animal-poison-control/people-foods-avoid-feeding-your-pets",
    },
    # --- WSAVA Global Nutrition Guidelines ---
    {
        "fact_en": "Veterinary teams should evaluate a dog's fat stores at every visit using a standardized Body Condition Score chart to track weight management.",
        "content_ko": "동물병원에서는 매 진료마다 표준화된 체형 점수(Body Condition Score) 차트를 이용해 강아지의 체지방을 평가하는 것이 권장됩니다.",
        "source_tag": "nutrition-guideline",
        "source_url": "https://wsava.org/global-guidelines/global-nutrition-guidelines/",
    },
    {
        "fact_en": "Beyond body fat, clinicians should separately assess a dog's muscle condition, since muscle status can be affected by disease or aging independent of body fat.",
        "content_ko": "체지방과는 별도로, 질병이나 노화로 영향을 받을 수 있는 근육 상태도 따로 평가하는 것이 권장됩니다.",
        "source_tag": "nutrition-guideline",
        "source_url": "https://wsava.org/global-guidelines/global-nutrition-guidelines/",
    },
    {
        "fact_en": "Veterinary practices should collect a full diet history from owners, using a structured diet history form, to understand a dog's current feeding practices.",
        "content_ko": "동물병원은 구조화된 문진표를 이용해 보호자로부터 반려견의 평소 식습관 정보를 수집하는 것이 권장됩니다.",
        "source_tag": "nutrition-guideline",
        "source_url": "https://wsava.org/global-guidelines/global-nutrition-guidelines/",
    },
    {
        "fact_en": "Quick-reference calorie charts provide veterinarians with starting points for estimating the daily calorie needs of a healthy dog.",
        "content_ko": "빠르게 참고할 수 있는 칼로리 표는 건강한 성견의 하루 필요 칼로리를 추정하는 출발점을 제공합니다.",
        "source_tag": "nutrition-guideline",
        "source_url": "https://wsava.org/global-guidelines/global-nutrition-guidelines/",
    },
    {
        "fact_en": "For hospitalized dogs, veterinary teams use specialized feeding guides to quickly determine appropriate calorie goals for nutritional support.",
        "content_ko": "입원한 반려견의 경우, 전용 급여 가이드를 이용해 적절한 칼로리 목표를 신속하게 정하는 것이 권장됩니다.",
        "source_tag": "nutrition-guideline",
        "source_url": "https://wsava.org/global-guidelines/global-nutrition-guidelines/",
    },
    {
        "fact_en": "A quick nutritional screening should be performed on every dog at every veterinary visit, not just when a problem is suspected.",
        "content_ko": "문제가 의심될 때만이 아니라 모든 진료 방문마다 간단한 영양 상태 스크리닝을 실시하는 것이 권장됩니다.",
        "source_tag": "nutrition-guideline",
        "source_url": "https://wsava.org/global-guidelines/global-nutrition-guidelines/",
    },
    {
        "fact_en": "Veterinary guidelines recommend using specific communication strategies to support owner compliance with a prescribed nutrition plan.",
        "content_ko": "수의학 가이드라인은 보호자가 처방된 영양 계획을 잘 따르도록 돕는 구체적인 소통 전략을 권장합니다.",
        "source_tag": "nutrition-guideline",
        "source_url": "https://wsava.org/global-guidelines/global-nutrition-guidelines/",
    },
    {
        "fact_en": "Veterinarians are advised to inform owners about the potential risks associated with raw meat-based diets for dogs.",
        "content_ko": "수의사는 보호자에게 생고기 기반 식단(raw diet)과 관련된 잠재적 위험을 안내하는 것이 권장됩니다.",
        "source_tag": "nutrition-guideline",
        "source_url": "https://wsava.org/global-guidelines/global-nutrition-guidelines/",
    },
    # --- Merck Veterinary Manual: "Routine Health Care of Dogs" ---
    {
        "fact_en": "Healthy adult dogs should see a veterinarian at least once a year for a full checkup.",
        "content_ko": "건강한 성견은 최소 연 1회 정기 건강검진을 위해 동물병원을 방문하는 것이 권장됩니다.",
        "source_tag": "routine-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs",
    },
    {
        "fact_en": "Puppies should be seen by a veterinarian every 3 to 4 weeks until they are 4 months old.",
        "content_ko": "강아지(퍼피)는 생후 4개월이 될 때까지 3~4주마다 동물병원 검진을 받는 것이 권장됩니다.",
        "source_tag": "routine-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs",
    },
    {
        "fact_en": "Senior dogs, generally 7 to 8 years and older, should be seen by a veterinarian twice a year or more, sometimes including blood tests or x-rays.",
        "content_ko": "대략 7~8세 이상의 노령견은 연 2회 이상, 필요하면 혈액검사나 엑스레이를 포함해 검진받는 것이 권장됩니다.",
        "source_tag": "routine-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs",
    },
    {
        "fact_en": "Core vaccines protect dogs against distemper, parvovirus, and rabies, while non-core vaccines such as those for Lyme disease or Bordetella depend on the dog's location and lifestyle.",
        "content_ko": "핵심 백신은 강아지를 디스템퍼(개홍역), 파보바이러스, 광견병으로부터 보호하며, 라임병이나 보르데텔라 같은 비핵심 백신 접종 여부는 사는 지역과 생활 환경에 따라 결정됩니다.",
        "source_tag": "routine-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs",
    },
    {
        "fact_en": "Annual fecal testing is recommended to screen dogs for intestinal parasites.",
        "content_ko": "장내 기생충 검사를 위해 연 1회 분변 검사를 받는 것이 권장됩니다.",
        "source_tag": "routine-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs",
    },
    {
        "fact_en": "Heartworm prevention requires year-round medication along with annual heartworm testing, and many heartworm preventives also protect against roundworms and hookworms.",
        "content_ko": "심장사상충 예방은 연중 내내 예방약을 투여하고 연 1회 검사를 병행해야 하며, 많은 심장사상충 예방약이 회충·구충 예방도 함께 해줍니다.",
        "source_tag": "routine-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs",
    },
    {
        "fact_en": "A dog's teeth should be brushed regularly with a dog-specific toothbrush and toothpaste, with professional veterinary cleanings as needed.",
        "content_ko": "반려견 전용 칫솔과 치약으로 정기적으로 양치를 시켜주고, 필요에 따라 동물병원에서 전문 스케일링을 받는 것이 권장됩니다.",
        "source_tag": "routine-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs",
    },
    {
        "fact_en": "Female dogs are typically spayed around 6 months old, before their first heat cycle.",
        "content_ko": "암컷 강아지는 보통 첫 발정기가 오기 전인 생후 6개월 무렵에 중성화 수술(난소자궁적출)을 하는 경우가 많습니다.",
        "source_tag": "routine-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs",
    },
    {
        "fact_en": "Male dogs are typically neutered between 5 and 10 months old.",
        "content_ko": "수컷 강아지는 보통 생후 5~10개월 사이에 중성화 수술(거세)을 하는 경우가 많습니다.",
        "source_tag": "routine-care",
        "source_url": "https://www.merckvetmanual.com/dog-owners/routine-care-of-dogs/routine-health-care-of-dogs",
    },
]


def main() -> None:
    # Windows 콘솔(cp949)이 일부 문자를 못 그려서 죽는 걸 방지 (실제 저장 데이터엔 영향 없음).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"{len(RAW_FACTS)}건의 실제 수집 사실을 적재합니다 (사람이 검증한 한국어 번역 사용)...\n")
    for i, fact in enumerate(RAW_FACTS, start=1):
        embedding = embed_text(fact["content_ko"])
        record = insert_document(
            fact["content_ko"], embedding, fact["source_tag"], fact["source_url"]
        )
        print(f"[{i}/{len(RAW_FACTS)}] {record.id} :: {fact['content_ko'][:40]}...")


if __name__ == "__main__":
    main()
