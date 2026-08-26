"""유튜브 영상(자막) + 댓글을 documents_test에 적재하는 스크립트.

이 스크립트가 다루는 두 영상은 모두 2026-08-19에 youtube_transcript_api(자막)와
yt-dlp(댓글, getcomments=True — 공식 YouTube Data API 키 불필요)로 실제 수집한
내용입니다. 아래 VIDEO_FACTS/VIDEO_COMMENTS는 그 실제 자막·댓글 원문을 사람이
직접 읽고 옮긴 것이며(로컬 LLM 번역/요약 없음), 지어낸 내용은 없습니다.

영상 vs 댓글을 다른 신뢰 등급으로 분리해 적재하는 이유
--------------------------------------------------------
영상(수의사/채널 운영자가 말한 내용)은 ASPCA/WSAVA/Merck/VCA와 같은 성격의
"출처가 명확한 사실"로 category="nutrition-guideline"에 넣습니다. 반면 댓글은
일반 시청자가 검증 없이 쓴 글이라 category="community-signal"로 명확히 분리합니다.

실제로 이 분리가 왜 필요한지 두 번째 영상(개알남 수의사 이세원, "체중(kg) x 15 =
하루 사료량(g)" 공식)에서 그대로 드러납니다 — 이 영상의 상위 댓글들(좋아요 22/20/16/11개)은
"동물학대 수준", "잘못된 정보" 라며 이 공식이 급여량을 지나치게 적게 잡는다고 정면으로
반박합니다. 영상 내용과 댓글 반응이 서로 모순되는 실제 사례이므로, 댓글을 영상과 같은
"검증된 사실" 취급으로 섞어 넣으면 안 됩니다. 두 카테고리로 나눠 적재해두면 나중에 이
테이블을 참조하는 기능이 "영상은 이렇게 말하지만, 시청자들은 이렇게 반박한다"를 그대로
보여줄 수 있습니다.

사용법: PYTHONPATH=src python scripts/ingest_youtube_sources.py
"""

import sys

from app.repository_documents_test import insert_document_test
from app.services.embedding import embed_text

COLLECTED_DATE = "2026-08-19"

VIDEO_META: dict[str, dict[str, str]] = {
    "TZnD0DGKHyo": {
        "title": "반려견 사료와 간식의 급여 적정량, 아주 쉽게 판단하는 법",
        "channel": "와벳TV-동물병원사용설명서",
        "url": "https://www.youtube.com/watch?v=TZnD0DGKHyo",
        "upload_date": "2023-05-18",
    },
    "I_DLx44qv6o": {
        "title": "강아지 사료량 계산 10초만에 하는법!",
        "channel": "개알남 수의사 이세원",
        "url": "https://www.youtube.com/shorts/I_DLx44qv6o",
        "upload_date": "2025-10-21",
    },
}

# --- 영상 자막에서 사람이 직접 뽑은 사실 (지어낸 내용 없음) ---
VIDEO_FACTS: list[dict[str, str]] = [
    # TZnD0DGKHyo (와벳TV) — RER/DER 기반 계산법
    {
        "video_id": "TZnD0DGKHyo",
        "content": "강아지의 적정 사료 급여량을 계산하려면 먼저 휴식 시 필요한 에너지량인 RER(기초대사량)과 하루에 필요한 에너지량인 DER을 구해야 합니다.",
    },
    {
        "video_id": "TZnD0DGKHyo",
        "content": "RER은 '체중(kg) × 30 + 70' 공식으로 계산합니다. 예를 들어 체중 5kg인 강아지의 RER은 5×30+70=220kcal입니다.",
    },
    {
        "video_id": "TZnD0DGKHyo",
        "content": "DER은 RER에 상태별 계수를 곱해 구합니다. 이 영상이 제시하는 계수는 중성화한 성견 1.6, 중성화하지 않은 성견 1.8, 과체중 1.4, 비만 1.0, 중성화한 노령견 1.4, 중성화하지 않은 노령견 1.4입니다.",
    },
    {
        "video_id": "TZnD0DGKHyo",
        "content": "간식의 적정 비율은 하루 총 섭취 칼로리(DER)의 10%이며, 나머지 90%는 주식 사료로 채우는 것이 권장됩니다.",
    },
    {
        "video_id": "TZnD0DGKHyo",
        "content": "현실적인 급여량 관리 방법은 사료 포장지의 급여표를 기준으로 주되, 매주 같은 요일·시간에 체중을 재서 체중이 늘면 5~10% 감량, 줄면 5~10% 증량하는 것입니다.",
    },
    {
        "video_id": "TZnD0DGKHyo",
        "content": "이 영상에 따르면 동물병원에 내원하는 반려견 중에는 영양 결핍보다 영양 과잉으로 인한 질병이 훨씬 더 많이 발생합니다.",
    },
    {
        "video_id": "TZnD0DGKHyo",
        "content": "간식을 주는 목적은 영양 공급이 아니라 칭찬과 보상이므로, 최소한의 크기로 규칙을 정해 급여하는 것이 권장됩니다.",
    },
    # I_DLx44qv6o (개알남 수의사 이세원) — 간이 공식 (아래 댓글에서 반박 다수, VIDEO_COMMENTS 참고)
    {
        "video_id": "I_DLx44qv6o",
        "content": "이 영상은 하루 사료 급여량을 간단히 추정하는 방법으로 '체중(kg) × 15 = 하루 사료량(g)' 공식을 제시합니다 (예: 체중 5kg → 하루 약 75g). 평균적인 활동량의 정상 체중 강아지를 기준으로 한 대략적인 값이며, 사료 종류·밀도·비만 여부·나이에 따라 달라질 수 있다고 영상에서도 언급합니다.",
    },
    {
        "video_id": "I_DLx44qv6o",
        "content": "이 영상은 종이컵으로 어림잡을 때 종이컵 한 컵에 사료를 가득 담으면 보통 약 80g 정도라고 안내하며, 정확한 급여를 위해서는 종이컵보다 전자저울로 계량할 것을 권장합니다.",
    },
]

# --- 실제 상위 댓글 (yt-dlp getcomments=True로 수집, 좋아요순, 번역/재작성 없이 원문 그대로) ---
VIDEO_COMMENTS: list[dict[str, object]] = [
    # TZnD0DGKHyo
    {"video_id": "TZnD0DGKHyo", "author": "@효인이네", "like_count": 6,
     "content": "뭔말인지 난 모르겠는..😅 차라리 1일 종이컵하나가 더 쉬운..😢"},
    {"video_id": "TZnD0DGKHyo", "author": "@lilysonny", "like_count": 5,
     "content": "사료를 이번에 바꿨는데 적정양을 확인하고 주고있어요.\n잘보고 갑니다~♡"},
    {"video_id": "TZnD0DGKHyo", "author": "@태풍이네-o5d", "like_count": 4,
     "content": "감사해요 도움이 많이 되었어요! 간식은 10%만 줘야 한다는 걸 알고는 있었지만 저렇게 적은 양일 줄은 몰랐네요 ㅜ 그리고 대부분의 간식이 칼로리가 안적혀있어서 조절하기가 어렵네요 흑 ㅜ"},
    {"video_id": "TZnD0DGKHyo", "author": "@ahh7500", "like_count": 3,
     "content": "똘이는 1.4를 곱해야... 비만 경향😅"},
    {"video_id": "TZnD0DGKHyo", "author": "@wise_vet", "like_count": 2,
     "content": "사료 포장지에 나왔는 급여표대로 먹이시고, 일주일 단위로 체중을 재면서, 체중이 늘면 사료양을 5~10%감량, 줄면 5~10% 증량 해주시면 됩니다."},
    {"video_id": "TZnD0DGKHyo", "author": "@user-nacho0504", "like_count": 0,
     "content": "3개월 된 1kg 중성화 안 한 아기 강아지 RER100, DER200이 나오는데 맞게 계산한 걸까요? 맞다면 Kcal는 그램(g)으로는 어떻게 계산 하나요? 사료를 저울에 올려서 적정량을 맞춰주고 싶은데…."},
    # I_DLx44qv6o — 공식 자체를 정면 반박하는 상위 댓글들 (좋아요 순위 1~4위)
    {"video_id": "I_DLx44qv6o", "author": "@하이텐션-u7b", "like_count": 22,
     "content": "1.2키론데 그럼 하루에 18그람을 먹이라는말? 무슨 강아지 기아 만들일 있나"},
    {"video_id": "I_DLx44qv6o", "author": "@azaz파이팅", "like_count": 20,
     "content": "2키로인데 그럼 30그램을 주란 말인가요\n말도 안됩니다\n강아지 키워보신 분 맞나요?\n거의 동물학대 수준입니다.\n잘못된 정보로 강아지들\n괴롭히지 마세요"},
    {"video_id": "I_DLx44qv6o", "author": "@gguya99", "like_count": 16,
     "content": "1키로 아이한테 15그람을 하루에 주라구요?\n학대같은데"},
    {"video_id": "I_DLx44qv6o", "author": "@JSPARK-jz6wo", "like_count": 11,
     "content": "잘못된정보입니다 1키로 좀 넘는데 하루 15g~20g이면 말이안되는데요?!"},
    {"video_id": "I_DLx44qv6o", "author": "@donghyukk", "like_count": 11,
     "content": "우리 개들은 하루 동안 먹을 거를 한 끼에 먹어 치우는군...."},
    {"video_id": "I_DLx44qv6o", "author": "@하나-l7d7o", "like_count": 5,
     "content": "6.5kg이라면 15×6.5=97.5g이군요\n하루 총 사료양..알고 싶었는데 감사합니다🫶"},
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    total = len(VIDEO_FACTS) + len(VIDEO_COMMENTS)
    print(f"{len(VIDEO_FACTS)}건의 영상 사실 + {len(VIDEO_COMMENTS)}건의 댓글, 총 {total}건을 적재합니다...\n")

    n = 0
    for fact in VIDEO_FACTS:
        meta = VIDEO_META[fact["video_id"]]
        n += 1
        embedding = embed_text(fact["content"])
        record = insert_document_test(
            content=fact["content"],
            embedding=embedding,
            category="nutrition-guideline",
            subcategory="feeding-schedule",
            source=meta["channel"],
            source_type="youtube-video",
            document_title=meta["title"],
            source_url=meta["url"],
            metadata={
                "video_url": meta["url"],
                "upload_date": meta["upload_date"],
                "collected_date": COLLECTED_DATE,
            },
        )
        print(f"[{n}/{total}] video  :: {record.id} :: {fact['content'][:30]}...")

    for comment in VIDEO_COMMENTS:
        meta = VIDEO_META[comment["video_id"]]
        n += 1
        embedding = embed_text(comment["content"])
        record = insert_document_test(
            content=comment["content"],
            embedding=embedding,
            category="community-signal",
            subcategory="youtube-comment",
            source=meta["channel"],
            source_type="youtube-comment",
            document_title=meta["title"],
            source_url=meta["url"],
            metadata={
                "video_url": meta["url"],
                "author": comment["author"],
                "like_count": comment["like_count"],
                "verified": False,
                "collected_date": COLLECTED_DATE,
            },
        )
        print(f"[{n}/{total}] comment:: {record.id} :: {str(comment['content'])[:30]}...")


if __name__ == "__main__":
    main()
