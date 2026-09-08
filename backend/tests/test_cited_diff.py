"""#318 — 인용 조항 비교.

API 를 안 부릅니다. 여기서 보는 것은 **세는 규칙**입니다 — 답을 안 한 자리를 "같다" 로 세지
않는가, 어느 능력의 인용을 읽는가, 분모가 0 일 때 0 으로 안 내리는가.

세 번째가 특히 중요합니다. 답을 안 할수록 일치율이 올라가는 지표는 최적화하면 침묵하는 쪽으로
갑니다 — 이 카드가 재려는 것과 정확히 반대입니다.
"""

from tools.answer_quality.cited_diff import compare


def _row(question_id, *, stratum="pet_insurance_skin__polite", status="ANSWERED", cited=(),
         capability="life", result_status="OK"):
    data = {"citations": [{"label": c} for c in cited]} if cited else {}
    return {
        "kind": "answer",
        "question_id": question_id,
        "stratum": stratum,
        "status": status,
        "results": [{"capability": capability, "status": result_status, "data": data}],
    }


def test_같은_조항을_인용하면_동일이다() -> None:
    a = [_row("q1", cited=("제1조", "제2조"))]
    b = [_row("q1", cited=("제2조", "제1조"))]  # 순서는 뜻이 없다 — 집합으로 본다
    result = compare(a, b)
    assert result["identical"] == 1
    assert result["different"] == 0
    assert result["identical_rate"] == 1.0


def test_다른_조항이_나오면_어느_쪽_전용인지_적는다() -> None:
    a = [_row("q1", cited=("제1조", "제2조"))]
    b = [_row("q1", cited=("제2조", "제3조"))]
    (detail,) = compare(a, b)["details"]
    assert not detail["identical"]
    assert detail["shared"] == 1
    assert detail["a_only"] == ["제1조"]
    assert detail["b_only"] == ["제3조"]


def test_양쪽_다_인용이_없으면_분모에서_뺀다() -> None:
    """**이 파일에서 제일 중요한 테스트입니다.** 답을 안 한 자리를 "같다" 로 세면 답을
    안 할수록 일치율이 올라갑니다 — #314 실측에서 14건 중 5건이 이 자리였습니다.
    """
    a = [_row("q1", status="REFUSED"), _row("q2", cited=("제1조",))]
    b = [_row("q1", status="REFUSED"), _row("q2", cited=("제1조",))]
    result = compare(a, b)
    assert result["questions"] == 2
    assert result["comparable"] == 1  # q1 은 빠진다
    assert result["identical"] == 1
    assert len(result["details"]) == 1


def test_한쪽만_인용이_없으면_다름이다() -> None:
    """답을 안 하게 된 것도 "인용이 달라진 것" 이다 — 실제로 #314 의 유일한 DIFF 가 이것이었다."""
    a = [_row("q1", cited=("제1조",))]
    b = [_row("q1", status="REFUSED")]
    result = compare(a, b)
    assert result["comparable"] == 1
    assert result["different"] == 1
    (detail,) = result["details"]
    assert detail["a_count"] == 1 and detail["b_count"] == 0
    assert detail["b_status"] == "REFUSED"


def test_다른_능력의_인용은_안_읽는다() -> None:
    """Place 의 출처는 조항이 아닙니다. 섞으면 이 지표가 다른 것을 재게 됩니다."""
    a = [_row("q1", cited=("장소A",), capability="place")]
    b = [_row("q1", cited=("장소B",), capability="place")]
    assert compare(a, b)["comparable"] == 0


def test_multi_intent_는_기본으로_빠진다() -> None:
    """place 까지 라우팅돼 Life 결과가 안 남습니다 (#314 에서 2/2). **문체를 지우지 않고
    여기서 빼는 것이 의도입니다** — 지우면 복합 발화 관찰을 잃습니다.
    """
    a = [_row("q1", stratum="pet_insurance_skin__multi_intent", cited=("제1조",))]
    b = [_row("q1", stratum="pet_insurance_skin__multi_intent", cited=("제2조",))]
    result = compare(a, b)
    assert result["questions"] == 0
    assert result["comparable"] == 0
    assert result["excluded_styles"] == ["multi_intent"]


def test_제외를_끄면_다시_센다() -> None:
    a = [_row("q1", stratum="pet_insurance_skin__multi_intent", cited=("제1조",))]
    b = [_row("q1", stratum="pet_insurance_skin__multi_intent", cited=("제2조",))]
    assert compare(a, b, exclude_styles=())["different"] == 1


def test_비교할_것이_없으면_비율이_None_이다() -> None:
    """0 으로 내리면 "다 달랐다" 로 읽힙니다 — 실제로는 잴 대상이 없는 것입니다."""
    result = compare([_row("q1", status="FAILED")], [_row("q1", status="FAILED")])
    assert result["comparable"] == 0
    assert result["identical_rate"] is None


def test_한쪽에만_있는_문항은_안_센다() -> None:
    """짝이 아닌 것을 세면 두 수집의 질문 파일이 달랐다는 사실이 숨습니다."""
    a = [_row("q1", cited=("제1조",)), _row("q2", cited=("제2조",))]
    b = [_row("q1", cited=("제1조",))]
    result = compare(a, b)
    assert result["questions"] == 1
    assert result["comparable"] == 1


def test_인용을_실은_거절은_비교에_들어온다() -> None:
    """#328 뒤로는 `REFUSED` 도 `data.citations` 를 가질 수 있습니다 (RAG-077) — 경계 답변이
    조항을 짚고 `[N]` 을 단 채로 거절이 된 자리입니다. 그 인용은 생성이 실제로 한 인용이므로
    **상태가 아니라 인용의 유무**로 비교 대상을 정합니다. 옛 수집(`data` 없음)은 그대로 빠집니다.
    """
    a = [_row("q1", cited=("제1조",))]
    b = [_row("q1", status="REFUSED", cited=("제1조",), result_status="REFUSED")]
    result = compare(a, b)
    assert result["comparable"] == 1
    assert result["identical"] == 1
    (detail,) = result["details"]
    assert detail["b_status"] == "REFUSED"
    assert detail["b_count"] == 1
