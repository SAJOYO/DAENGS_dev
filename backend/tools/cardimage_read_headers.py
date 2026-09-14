"""카드 12장의 머리띠 글자(제목·배지·부제)를 텍스트 모델로 읽어 JSON 으로 — 틀 만들기 입력 (#496).

`cardimage/N_*.webp` 를 `gemini-3.1-flash-lite` 에 보내 "BLOSSOM NEO" / "NEO-APR25" / "APRIL SPECIAL" 을
읽습니다. 결과는 `cardimage/headers.json`. 사람이 한 번 확인한 뒤 `cardimage_blank_title.py` 에 넣습니다.
비용은 장당 $0.001 미만.

실행 (backend/ 에서):  uv run --with pillow python tools/cardimage_read_headers.py
"""

from __future__ import annotations

import json
import re

from cardimage_try import CARDIMAGE, png_bytes, read_env_key
from PIL import Image

MODEL = "gemini-3.1-flash-lite"
PROMPT = """This is a collectible trading card. Read the three pieces of text in the header at the top and return JSON only:
{"title": "<large title on the dark plate, e.g. BLOSSOM NEO>", "badge": "<code in the small top-right badge, e.g. NEO-APR25>", "subtitle": "<text on the purple strip, e.g. APRIL SPECIAL>"}
Copy the text exactly as printed, including hyphens and capitalization."""


def main() -> None:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=read_env_key())
    out: dict[str, dict[str, str]] = {}
    for p in sorted(CARDIMAGE.glob("*.webp"), key=lambda q: int(re.match(r"(\d+)_", q.name).group(1)) if re.match(r"(\d+)_", q.name) else 99):
        if "template" in p.name:
            continue
        im = Image.open(p).convert("RGB").crop((0, 0, 994, 240))  # 머리띠만
        resp = client.models.generate_content(
            model=MODEL,
            contents=[PROMPT, types.Part.from_bytes(data=png_bytes(im), mime_type="image/png")],
            config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
        )
        data = json.loads(resp.text)
        out[p.name] = {k: str(data.get(k, "")).strip() for k in ("title", "badge", "subtitle")}
        print(f"{p.name:22s} {out[p.name]}")
    (CARDIMAGE / "headers.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(CARDIMAGE / "headers.json")


if __name__ == "__main__":
    main()
