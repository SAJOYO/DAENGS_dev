# guardrail.py = LLM이 만든 답변을 "문장 단위로 검사"해서, 위험한 표현(진단/처방/병원 가라 등)이
# 있으면 그 문장만 지워버리는 안전장치. generation.py의 시스템 프롬프트가 1차 방어라면,
# 여기는 "그래도 모델이 규칙을 어길 때"를 대비한 2차 방어입니다 (기계적 후처리 필터).

import re

# re.compile(패턴) : 정규표현식(regex)을 미리 "컴파일"해서 재사용 가능한 객체로 만들어둠.
# 정규표현식은 "문자열이 특정 패턴에 맞는지" 검사하는 미니 언어입니다. 예를 들어:
#   r"병원에\s*가"  -> "병원에" 다음에 공백(\s*, 0개 이상)이 오고 "가"로 시작하는 부분을 찾음
#                      ("병원에 가", "병원에가", "병원에  가세요" 등에 모두 매치)
#   r"[가-힣A-Za-z]*병입니다" -> 한글/영문 글자가 0개 이상(*) 온 다음 "병입니다"로 끝나는 부분
# r"..." 처럼 문자열 앞에 r을 붙이면 "raw string"이라 해서 \를 특수문자로 취급하지 않고
# 정규식 문법에 있는 그대로 넘겨줍니다 (정규식 쓸 땐 거의 항상 r"..." 를 씁니다).

# BR3: 가드레일 금지 표현 목록 (MVP 최소 버전). 정교화는 로드맵 ⑯ Guardrail 단계에서 분류기 기반으로 대체 예정.
# 생성 모델이 간헐적으로 중국어/영어로 코드스위칭하는 것이 관찰되어(build-and-test Scenario 4),
# 한국어 패턴을 우회하지 못하도록 최소한의 중국어/영어 대응 패턴을 임시로 추가한다.
# (참고: build-and-test 검증 결과, 정규식만으로는 LLM의 무수한 표현 변형을 다 못 막는다는 한계가
#  실제로 확인됨. 근본적인 해결은 로드맵 ⑯의 분류기 기반 가드레일로 예정되어 있음.)
_BANNED_PATTERNS = [
    # 한국어
    re.compile(r"[가-힣A-Za-z]*병입니다"),
    re.compile(r"[가-힣A-Za-z]*병일\s*수\s*있습니다"),
    re.compile(r"[가-힣A-Za-z]*증상입니다"),
    re.compile(r"질환으로\s*보입니다"),
    re.compile(r"(동물\s*)?병원에\s*가"),
    re.compile(r"(동물\s*)?병원을?\s*방문"),
    re.compile(r"수의사(와|랑)?\s*상담"),
    re.compile(r"진료를?\s*받으세요"),
    re.compile(r"진찰을?\s*받"),
    re.compile(r"약을?\s*처방"),
    re.compile(r"(의사|전문가)(에게|의)\s*(검진|진찰|조언|자문)"),
    # 중국어 (임시 대응)
    re.compile(r"去看?\s*兽医"),
    re.compile(r"兽医.*?(检查|诊断|治疗)"),
    re.compile(r"诊断"),
    re.compile(r"处方|开药"),
    # 영어 (임시 대응)
    # re.IGNORECASE : 대소문자 구분 없이 매치 (Vet/vet/VET 다 잡힘)
    re.compile(r"\bsee\s+(a|your)\s+vet\b", re.IGNORECASE),
    re.compile(r"\bconsult\s+(a|your)\s+vet(erinarian)?\b", re.IGNORECASE),
    re.compile(r"\bveterinar(y|ian)\b.*\b(diagnos|treatment)", re.IGNORECASE),
    re.compile(r"\bprescri(be|ption)\b", re.IGNORECASE),
    re.compile(r"\bdiagnos(is|e|ed)\b", re.IGNORECASE),
]

# 위반 문장을 지운 뒤, 결과가 이상하지 않도록 뒤에 붙여주는 안내 문구.
SAFE_FALLBACK_NOTICE = "이 답변은 일반적인 생활 관리 참고 정보이며, 의학적 진단이나 처방을 포함하지 않습니다."


def apply_guardrail(answer: str) -> str:
    # 1) 답변 전체를 "문장 단위"로 쪼갠다.
    # re.split(패턴, 문자열) : 문자열을 패턴에 맞는 부분마다 잘라서 리스트로 반환.
    # (?<=[.!?다요])\s+ 은 "lookbehind"라는 정규식 기법으로,
    #   ". ! ? 다 요" 중 하나로 끝난 "직후"의 공백(\s+)에서 자른다는 뜻.
    #   즉 문장 끝(마침표, "~다", "~요")을 기준으로 나눔.
    sentences = re.split(r"(?<=[.!?다요])\s+", answer.strip())

    kept_sentences = []       # 문제 없다고 판단해서 살아남는 문장들
    violation_found = False   # 하나라도 걸린 문장이 있었는지 표시

    # 2) 문장을 하나씩 검사하면서, 금지 패턴에 걸리면 버리고(continue) 아니면 kept_sentences에 저장.
    for sentence in sentences:
        # any(...) : 괄호 안 조건들 중 하나라도 True면 전체가 True.
        # pattern.search(sentence) : 이 문장 "어딘가에" 패턴이 있으면 True 비슷한 매치 객체를 반환.
        # 즉 "_BANNED_PATTERNS 중 하나라도 이 문장에서 발견되면" 이라는 뜻.
        if any(pattern.search(sentence) for pattern in _BANNED_PATTERNS):
            violation_found = True
            continue  # 이 문장은 버리고 다음 문장으로 (kept_sentences에 추가 안 함)
        kept_sentences.append(sentence)

    # 3) 위반이 하나도 없었다면 원본 답변을 그대로 반환 (수정할 필요 없음).
    if not violation_found:
        return answer

    # 4) 위반이 있었다면, 살아남은 문장들만 다시 이어붙이고 안내 문구를 추가.
    #    s for s in kept_sentences if s : 빈 문자열("")은 걸러내고 이어붙임.
    cleaned = " ".join(s for s in kept_sentences if s).strip()
    if cleaned:
        return f"{cleaned} {SAFE_FALLBACK_NOTICE}"
    # 모든 문장이 다 걸려서 남은 게 없으면, 안내 문구만 단독으로 반환.
    return SAFE_FALLBACK_NOTICE
