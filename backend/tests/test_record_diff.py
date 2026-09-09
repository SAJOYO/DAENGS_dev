"""#330 — 두 수집의 레코드별 대조. API 를 안 부른다. 보는 것은 **축을 가르는 규칙**이다."""

from daengs_evals.answer_quality.record_diff import diff


def _row(qid, *, life_status="OK", text="답", cited=("제1조",), code=None, plan=None,
         stratum="pet_insurance_skin__polite", extra_results=()):
    life = {"capability": "life", "status": life_status}
    if life_status == "OK":
        life["data"] = {"answer": text, "citations": [{"label": c} for c in cited]}
    elif life_status == "REFUSED":
        life["refusal"] = {"code": code or "medical_boundary", "message": text}
        if cited:
            life["data"] = {"citations": [{"label": c} for c in cited]}
    else:
        life["abstention"] = {"code": code or "no_evidence", "message": text}
    return {
        "kind": "answer", "question_id": qid, "stratum": stratum, "status": "ANSWERED",
        "plan": plan or {"requests": ["life"], "handoffs": [], "clarify": None,
                         "model": "m", "prompt_version": "v9", "router": "llm"},
        "results": [life, *extra_results],
    }


def test_거절의_인용_유무는_Life_축이_아니라_refusal_evidence_다() -> None:
    """RAG-077 이전 수집은 거절에 `data` 가 없다 — 그 차이를 Life 가 움직인 것으로 세면 안 된다."""
    a = {"q": _row("q", life_status="REFUSED", text="같은 문장", cited=())}
    b = {"q": _row("q", life_status="REFUSED", text="같은 문장", cited=("제1조",))}
    r = diff(a, b)
    assert r["life_moved"] == 0
    assert r["mismatches"]["refusal_evidence"] == 1


def test_plan_의_모델_프롬프트버전_라우터는_비교에서_뺀다() -> None:
    """그것이 다른 것은 meta 행이 이미 말한다. 요청·핸드오프가 같으면 계획은 같다."""
    a = {"q": _row("q", plan={"requests": ["life"], "handoffs": ["skin"], "model": "m1", "prompt_version": "v9"})}
    b = {"q": _row("q", plan={"requests": ["life"], "handoffs": ["skin"], "model": "m2", "prompt_version": "v10"})}
    assert diff(a, b)["routing_moved"] == 0
    c = {"q": _row("q", plan={"requests": ["life"], "handoffs": [], "prompt_version": "v10"})}
    assert diff(a, c)["mismatches"]["plan"] == 1


def test_Life_본문이_한_글자라도_다르면_Life_축이_움직인다() -> None:
    a = {"q": _row("q", text="30일 이내에 발생한 질병")}
    b = {"q": _row("q", text="30일 이내에 발생한 질환")}
    r = diff(a, b)
    assert r["mismatches"]["life_text"] == 1 and r["mismatches"]["life_text_normalized"] == 1
    assert r["life_moved"] == 1 and r["where"]["life_text"] == ["q"]


def test_공백만_다르면_정규화_칸은_같다() -> None:
    a = {"q": _row("q", text="가 나")}
    b = {"q": _row("q", text="가  나")}
    r = diff(a, b)
    assert r["mismatches"]["life_text"] == 1 and r["mismatches"]["life_text_normalized"] == 0


def test_제외한_문체는_안_센다_그리고_한쪽에만_있는_문항은_적는다() -> None:
    a = {"m": _row("m", stratum="x__multi_intent", text="a"), "p": _row("p")}
    b = {"m": _row("m", stratum="x__multi_intent", text="b"), "p": _row("p"), "z": _row("z")}
    r = diff(a, b, exclude_styles=("multi_intent",))
    assert r["compared"] == 1 and r["life_moved"] == 0
    assert r["only_b"] == ["z"]
