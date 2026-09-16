"""Offline space architecture preview: admitted facts -> scene -> actual tool inputs.

Never imports a provider or calls a model/public API. Examples are authored drafts,
not generated answers. Existing outputs are never overwritten.
"""

import argparse
import html
import json
from pathlib import Path

from run_diary_route_scenario import configure, dump, prepare, read

PURPOSES = {
    "background_basis": "현재 위치의 배경 근거",
    "location_context": "상위 위치 참조",
    "independent_surrounding": "기록 위치 주변의 독립 정보",
    "area_context": "조회 영역 전체의 통계",
    "environment_context": "지역 환경 맥락",
}
EXAMPLES = {
    "풀밭": "보리와 함께한 산책의 이곳에는 풀밭이 있었다.",
    "길": "함께한 산책의 이곳은 길이었고, 가까이에 공원이 있었다.",
    "숲": "보리와 함께한 이곳의 배경은 숲이었다.",
}


def preview(source, previous, output):
    from daengs_backend.services.walk_diary import space_details
    from daengs_backend.services.walk_diary.preparation.board import with_scene_backgrounds
    from daengs_backend.services.walk_diary.writing import policy
    from daengs_backend.services.walk_diary.writing.context import get_space_context
    from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
    from daengs_walk.diary.contracts.input import digest

    raw = read(source / "input.json")
    snapshot = SceneBackgroundSnapshot.model_validate(read(source / "backgrounds.json"))
    before = read(previous / "input.json")
    assert (digest(raw), digest(snapshot)) == (
        before["input_sha256"],
        before["backgrounds_sha256"],
    )
    base = with_scene_backgrounds(prepare(raw), snapshot)
    cases = []
    for old in before["cases"]:
        context = get_space_context(base, old["card_id"])
        wire = context.llm_input
        assert {k: v for k, v in wire.items() if k != "space_scene"} == old["inputs"]["space"]
        initial = space_details.initial_input(wire)
        # All alternatives are shown for inspection, never attributed to a model decision.
        options = [
            {
                "candidate": candidate,
                "returned": space_details.lookup(
                    wire,
                    {"material_ids": [candidate["id"]]},
                ),
            }
            for candidate in initial.get("available_details", [])
        ]
        facts = {m["id"]: m for m in wire["materials"]}
        basis = wire["space_scene"]["background"]["basis_ids"]
        cover = facts[basis[0]]["material"]["피복"]
        assert cover in EXAMPLES and wire["narration"]["companions"] == [{"name": "보리"}]
        park = next(m for m in wire["materials"] if m.get("material", {}).get("배경") == "공원")
        alias_by_id = {
            m["id"]: projected["id"]
            for m, projected in zip(
                context.request["materials"],
                wire["materials"],
                strict=True,
            )
        }
        cases.append(
            {
                "index": old["index"],
                "card_id": old["card_id"],
                "label": old["label"],
                "before_input": old["inputs"]["space"],
                "admitted_facts": [
                    {**m, "alias": alias_by_id[m["id"]]} for m in context.request["materials"]
                ],
                "compiled_scene": context.request["space_scene"],
                "model_materials": wire["materials"],
                "projected_scene": wire["space_scene"],
                "initial_input": initial,
                "detail_options": options,
                "authored_example": {
                    "text": EXAMPLES[cover],
                    "used_material_ids": basis + ([park["id"]] if cover == "길" else []),
                    "choice": "현재 위치의 배경을 중심으로 표현한다. 동 이름은 위치 참조로 남긴다. "
                    + (
                        "공원은 기록 위치와의 근접만 덧붙인다."
                        if cover == "길"
                        else "배경과의 관계가 확인되지 않은 공원은 이번 예시에서 생략한다."
                    ),
                    "not_confirmed": "피복의 실제 통과, 숲·풀밭·길과 공원의 포함 관계, 감각·정지",
                },
            }
        )
    value = {
        "mode": "offline-space-scene",
        "model_calls": 0,
        "public_api_calls": 0,
        "generated_parts": 0,
        "input_sha256": digest(raw),
        "backgrounds_sha256": digest(snapshot),
        "writer": policy.writing_version(),
        "prompt": policy.PROMPTS["space"] + space_details.INSTRUCTION,
        "cases": cases,
    }
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "preview.json", value)
    render(value, output)
    print(json.dumps({"scenes": len(cases), "model_calls": 0, "public_api_calls": 0}))


def pretty(value):
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2))


def render(value, output):
    parts = []
    for case in value["cases"]:
        materials = {m["id"]: m for m in case["model_materials"]}
        rows = []
        for binding in case["projected_scene"]["bindings"]:
            item = materials[binding["material_id"]]
            facts = item.get("material", {"temperature_c": item.get("temperature_c")})
            relation = item["relation"]
            if binding.get("background_link") == "unconfirmed":
                relation += " / 현재 배경과의 연결 관계는 미확인"
            rows.append(
                "<tr><td>"
                + html.escape(json.dumps(facts, ensure_ascii=False))
                + "</td><td>"
                + PURPOSES[binding["purpose"]]
                + "</td><td>"
                + html.escape(relation)
                + "</td></tr>"
            )
        example = case["authored_example"]
        parts.append(
            f"<section><h2>{case['index']:02d} · {html.escape(case['label'])}</h2>"
            "<table><thead><tr><th>선정된 재료</th><th>코드가 부여한 역할</th><th>관계와 범위</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>"
            "<article><b>작성 방향 예시 · 사람이 작성한 초안</b><p>"
            + html.escape(example["text"])
            + "</p><small>"
            + html.escape(example["choice"])
            + "</small></article><div class='pair'><details><summary>이전 · 모델용 재료 목록</summary><pre>"
            + pretty(case["before_input"])
            + "</pre></details><details><summary>변경 · 실제 첫 모델 입력</summary><pre>"
            + pretty(case["initial_input"])
            + "</pre></details></div>"
            "<details><summary>상세 후보별 반환 예시 · 모델이 선택한 결과 아님</summary><pre>"
            + pretty(case["detail_options"])
            + "</pre></details>"
            "<details><summary>개발자용 · 선정 근거와 조립 결과</summary><pre>"
            + pretty({"admitted": case["admitted_facts"], "compiled": case["compiled_scene"]})
            + "</pre></details></section>"
        )
    page = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>공간 맥락 조립 · 입력 비교</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f4f3ef;color:#26372d;font-family:system-ui,sans-serif}
main{max-width:1200px;margin:auto;padding:40px 24px}header p{line-height:1.8;max-width:950px}
h2{font-size:22px;margin-top:45px}table{width:100%;border-collapse:collapse;background:white;font-size:14px}
td,th{border:1px solid #d9ded6;padding:14px;text-align:left;line-height:1.7}th{background:#e5ece2}
article{background:white;border-left:4px solid #73966f;border-radius:5px;padding:24px;margin:20px 0}
article p{font-size:20px;line-height:1.8}b,small,summary{font-size:13px}small{line-height:1.8}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}details{margin-top:18px;min-width:0}
summary{cursor:pointer;color:#44634b}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.6;
background:#fff;padding:18px;border:1px solid #d9ded6}
@media(max-width:760px){.pair{grid-template-columns:1fr}main{padding:24px 12px}td,th{padding:8px}}</style>
<main><header><h1>선정 근거에서 공간 맥락으로</h1>
<p>저장된 세 장면의 공간 재료와 동행은 이전 실험 그대로다. 코드가 설명 대상·범위·서술 역할을 조립하고,
그 결과를 실제 첫 입력과 상세 반환에 보존한다. 지점 피복과 근처 공원은 서로의 포함 관계를 뜻하지 않는다.</p>
<p><strong>Gemini 0회 · 공공 API 0회 · 새 생성 문장 없음.</strong>
아래 문장은 작성 방향을 검토하기 위한 초안이다. 입력 연결의 확인이며 서술 품질 검증이 아니다.
공공자료는 저장된 실제 응답, GPS와 행동 핀은 합성 시나리오다.</p></header>"""
    page += "".join(parts)
    page += "<details><summary>실제 공간 프롬프트와 공통 작성 예시</summary><pre>"
    page += pretty(value["prompt"]) + "</pre></details></main></html>"
    (output / "preview.html").write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    if args.render_only:
        render(read(args.output / "preview.json"), args.output)
        return
    if not args.source or not args.previous:
        parser.error("source and previous are required")
    if args.output.exists():
        parser.error("existing output is preserved; choose a new directory")
    configure(None)
    preview(args.source, args.previous, args.output)


if __name__ == "__main__":
    main()
