"""장면 + 배경 타일을 뷰어 템플릿에 구워 실행 가능한 HTML 하나를 만든다.

    uv run python -m scripts.spikes.territory_paint.layer_viewer \\
        --scenes layer-scenes.json --basemap layer-basemap.json --out layer-lab.html

## 왜 레포에 있나

이 실험의 결론("복구한 맥락이 지도에서 읽힌다")을 만드는 것은 **렌더러**다. 그런데 처음엔
장면 생성기만 커밋하고 화면은 게시된 Artifact 에만 뒀다 — 그러면 재현 사슬이 여기서 끊긴다.

    scenes.json → ??? → 화면 → 픽셀 측정 → 400/400 · 0.649/0.367

`???` 가 커밋에 없으면 3 개월 뒤 그 숫자가 어디서 나왔는지 아무도 못 되짚는다. 평가기를
커밋한 `persona_experiment` 와 같은 이유로 뷰어도 레포에 있어야 한다.

제품 코드가 아니라 **연구 재현물**이다. 템플릿은 `scripts/spikes/territory_paint/lab/layer_viewer.html`.

## 측정도 페이지 안에 있다

뷰어에 자가 점검 패널이 있어서 문서가 인용하는 숫자를 페이지가 스스로 다시 잰다. 두 판
비교는 밝기 합이 아니라 **픽셀 단위 평균 절대차**다 — 밝기만 보면 "같은 총량, 다른 그림" 을
놓치고, 실제로 차이 모드에서 그 함정을 밟았다(밝기 416 대 421 인데 색은 정반대였다).
"""

import argparse
import json
import os
import sys

TEMPLATE = os.path.join(os.path.dirname(__file__), "lab", "layer_viewer.html")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenes", required=True, help="layer_scenes 산출물")
    parser.add_argument("--basemap", required=True, help="basemap 산출물")
    parser.add_argument("--template", default=TEMPLATE)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    with open(args.template, encoding="utf-8") as handle:
        html = handle.read()
    for token in ("__SCENES__", "__BASE__"):
        if token not in html:
            print(f"템플릿에 {token} 자리표시자가 없다: {args.template}")
            return 1

    with open(args.scenes, encoding="utf-8") as handle:
        scenes = handle.read()
    with open(args.basemap, encoding="utf-8") as handle:
        basemap = handle.read()

    html = html.replace("__SCENES__", scenes).replace("__BASE__", basemap)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(html)

    size = os.path.getsize(args.out) / 1024 / 1024
    payload = json.loads(scenes)
    print(f"페르소나 {len(payload['personas'])}명 · 장면 {len(payload['scenarios'])}종 "
          f"· 타일 {len(json.loads(basemap)['tiles'])}장 → {args.out} ({size:.2f}MB)")
    print("브라우저로 열면 자가 점검이 자동으로 한 번 돈다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
