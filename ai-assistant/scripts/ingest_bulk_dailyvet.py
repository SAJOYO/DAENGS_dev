"""dailyvet.co.kr(수의사 신문) 기사를 대량으로 실제 수집해 documents_test에 적재하는 스크립트.

지금까지(ingest_real_sources.py 등)는 사람이 사실 하나하나를 골라 한국어로 옮겨 적는
방식이었지만, 800건 규모로는 현실적이지 않습니다. 대신 이미 만들어둔 실제 수집 파이프라인
(services/web_ingest.py의 fetch_html/extract_main_text + services/chunking.py의 chunk_text)을
그대로 재사용해서, 기사 원문을 실제로 가져와 슬라이딩 윈도우로 청킹한 뒤 그대로 적재합니다
(사람이 사실을 재작성하지 않음 — web_ingest.py의 "번역하지 않는다" 원칙과 동일하게, 원문을
그대로 저장). dailyvet은 한국어 매체라 documents_test 콘텐츠가 계속 한국어로 유지됩니다.

기사가 사라졌거나(404) 일시적으로 응답이 없는 URL은 건너뛰고 계속 진행합니다.

사용법: PYTHONPATH=src python scripts/ingest_bulk_dailyvet.py
"""

import sys
import time

from app.repository_documents_test import insert_document_test
from app.services.chunking import chunk_text
from app.services.embedding import embed_text
from app.services.web_ingest import extract_main_text, fetch_html

COLLECTED_DATE = "2026-08-19"
SOURCE = "dailyvet"
SOURCE_TYPE = "veterinary-news"

# (url, category, subcategory, document_title)
ARTICLES: list[tuple[str, str, str, str]] = [
    ("https://www.dailyvet.co.kr/news/academy/252132", "nutrition-guideline", "supplement-safety",
     "반려동물 영양제·보조제, 성분 목록 및 전문가 검토 체계 필요"),
    ("https://www.dailyvet.co.kr/news/industry/232094", "nutrition-guideline", "feed-classification",
     "펫푸드, 반려동물완전사료·기타사료로 나눈다..처방사료 분류는 없어"),
    ("https://www.dailyvet.co.kr/news/practice/companion-animal/155573", "nutrition-guideline", "longevity-diet",
     "[기고] 반려동물 장수의 비결 : 영양과 식단"),
    ("https://www.dailyvet.co.kr/news/industry/170253", "nutrition-guideline", "feed-safety-regulation",
     "[2022 국감이슈] 반려동물 사료, 안전·품질관리 별도로 관리해야"),
    ("https://www.dailyvet.co.kr/news/policy/190469", "nutrition-guideline", "feed-classification",
     "가축용 사료와 펫푸드 분리, 영양 가이드라인 제정..펫사료협회 '환영'"),
    ("https://www.dailyvet.co.kr/news/industry/232561", "nutrition-guideline", "prescription-diet",
     "국산 반려동물 처방사료·영양제도 과학적 근거로 경쟁력 갖춰야"),
    ("https://www.dailyvet.co.kr/news/policy/223614", "nutrition-guideline", "feed-classification",
     "처방식이 기타 반려동물사료? 사료 유형에서 '처방식' 제외"),
    ("https://www.dailyvet.co.kr/news/industry/92859", "nutrition-guideline", "novel-ingredient",
     "[기고] 곤충단백질과 반려동물 그리고 사람"),
    ("https://www.dailyvet.co.kr/news/college/80880", "nutrition-guideline", "veterinary-nutrition-science",
     "[인터뷰] 반려동물도 장수시대…오원석 박사에게 수의영양학에 대해 묻다"),
    ("https://www.dailyvet.co.kr/news/industry/249425", "nutrition-guideline", "industry-context",
     "마크 에드워즈 수의사 '한국은 세계에서 가장 수준 높은 펫푸드 시장 중 하나'"),
    ("https://www.dailyvet.co.kr/news/academy/280919", "routine-care", "digestive-health",
     "하루 종일 장질환·마이크로바이옴만 공부했다..수의영양학회 심화세미나 개최"),
    ("https://www.dailyvet.co.kr/news/industry/283594", "routine-care", "digestive-health",
     "'개 만성 염증성 장질환, 식이 관리가 첫 번째 선택지' 힐스, CIE 주제로 심포지엄 열어"),
    ("https://www.dailyvet.co.kr/news/policy/163238", "nutrition-guideline", "functional-feed-research",
     "반려동물 기능성 사료 특허출원 많아졌지만‥효용은 `글쎄`"),
    ("https://www.dailyvet.co.kr/news/practice/companion-animal/162580", "nutrition-guideline", "weight-management",
     "'반려견·반려묘 어느새 비만？' 체중관리 위한 3번의 골든타임은"),
    ("https://www.dailyvet.co.kr/news/practice/companion-animal/203401", "nutrition-guideline", "weight-management",
     "BCS 8~9단계 비만 반려동물 증가...4가지 비만 관리 방법은?"),
    ("https://www.dailyvet.co.kr/news/practice/companion-animal/109897", "nutrition-guideline", "weight-management",
     "비만 반려동물，다이어트 성공 이후 '체중 유지관리' 더 중요하다"),
    ("https://www.dailyvet.co.kr/news/practice/companion-animal/162126", "nutrition-guideline", "weight-management",
     "반려동물 비만, 의료비 증가로 이어진다‥어릴 때부터 관리해야"),
    ("https://www.dailyvet.co.kr/news/practice/companion-animal/253985", "nutrition-guideline", "weight-management",
     "[위클리벳 463회] 반려동물 비만 진단 비율과 비만 관리 방법"),
    ("https://www.dailyvet.co.kr/news/industry/265761", "routine-care", "senior-care",
     "BBB 통과 성분으로 노령 반려견 인지기능·소화·관절 동시 케어"),
    ("https://www.dailyvet.co.kr/news/etc/144864", "routine-care", "senior-care",
     "반려인들 '10살부터 노령견，동물병원에서 얻은 정보 가장 신뢰'"),
    ("https://www.dailyvet.co.kr/news/academy/193550", "routine-care", "allergy-management",
     "알레르기 피부 질환, 반려동물·보호자·수의사 모두가 편안해 지려면?"),
    ("https://www.dailyvet.co.kr/news/academy/264940", "routine-care", "allergy-management",
     "'피부 진료가 경쟁력' 1차 동물병원, 아포퀠·사이토포인트로 알러지 치료 패러다임 바꾼다"),
    ("https://www.dailyvet.co.kr/news/practice/companion-animal/203190", "routine-care", "parasite-prevention",
     "[칼럼] 개심장사상충의 예방에 왜 검사가 필수적인가?"),
    ("https://www.dailyvet.co.kr/news/practice/companion-animal/60970", "routine-care", "allergy-management",
     "강아지 아토피 피부염의 종합적인 관리, 버박의 아토피 3 STEP!"),
    ("https://www.dailyvet.co.kr/news/practice/companion-animal/75061", "nutrition-guideline", "weight-management",
     "로얄캐닌 체중관리 클리닉이 알려주는 반려묘·반려견 비만 관리의 중요성"),
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    total_inserted = 0
    for i, (url, category, subcategory, title) in enumerate(ARTICLES, start=1):
        print(f"\n=== [{i}/{len(ARTICLES)}] {title} ({url}) ===")
        try:
            html = fetch_html(url)
            text = extract_main_text(html)
            chunks = chunk_text(text, max_sentences=2, overlap_sentences=1)
        except Exception as exc:  # 404, 타임아웃 등 - 이 기사만 건너뛰고 계속 진행
            print(f"  건너뜀 (수집 실패): {exc!r}")
            continue

        if not chunks:
            print("  건너뜀 (본문 추출 결과 없음)")
            continue

        print(f"  {len(chunks)}개 청크 적재 중...")
        for chunk in chunks:
            embedding = embed_text(chunk)
            insert_document_test(
                content=chunk,
                embedding=embedding,
                category=category,
                subcategory=subcategory,
                source=SOURCE,
                source_type=SOURCE_TYPE,
                document_title=title,
                source_url=url,
                metadata={"source_url": url, "collected_date": COLLECTED_DATE, "ingest_method": "auto-chunked"},
            )
            total_inserted += 1
        print(f"  누적 적재: {total_inserted}건")
        time.sleep(0.2)  # 사이트에 너무 빠르게 연속 요청하지 않기 위한 최소한의 딜레이

    print(f"\n총 {total_inserted}건 적재 완료.")


if __name__ == "__main__":
    main()
