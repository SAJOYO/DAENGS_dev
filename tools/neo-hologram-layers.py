"""네오 채소 카드에서 이머시브 레이어를 뽑아낸다.

두 가지를 한다.

    subject  주인공만 알파로 따 내고 알파 경계에 딱 맞게 자른다
    back     배경 원화에서 카드 프레임(제목바·스탯바·은색 테두리)을 잘라내 그림만 남긴다

둘 다 **아트 창 좌표를 손으로 준다.** 카드마다 프레임 두께가 달라서 자동으로 찾는 것보다
미리보기를 보고 맞추는 게 빠르다. 실행하면 `*_preview.png` 가 같이 나오니 그걸 보면 된다.

    uv run --no-project --python 3.12 --with pillow --with numpy --with scipy \\
      python tools/neo-hologram-layers.py <subject|back> <src> <out> <l> <t> <r> <b>

전역에 아무것도 설치하지 않는다 — uv 가 임시 환경을 만들고 끝나면 지운다.

원본 레이어 원화는 tools/art-src/ 에 둔다. public/ 에 두면 그대로 서빙돼서 쓰지도 않는
수 MB 짜리 원본이 바깥에 열린다 — 결과물(art/*.webp)만 public 으로 나간다.

배추(No.01) 에 쓴 값:

    subject  frontend/public/neo-hologram/art/cabbage.webp   32 175 778 892   (810x1125 기준)
    back     tools/art-src/cabbage_neo_back.png              60 235 815 905   (875x1216 기준)

subject 원리: 아트 창 배경이 흰 은색 홀로 광선(어느 색이든 아주 밝다)이고 주인공은 채도
높은 초록 + 따뜻한 갈색이라 밝기로 가를 수 있다. 완벽한 누끼가 아니라 **패럴랙스 레이어로
쓸 만한 수준**이 목표다 — 무지개 반짝이 조각이 몇 개 남는다.

순서에 주의. 얇은 광선을 먼저 털어내고(opening) **그다음에** 큰 덩어리를 고른다. 닫기를
먼저 하면 옆에 있던 무지개 조각이 본체에 붙어 같이 살아남는다.
"""
import sys

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage


def matte(img, box):
    """아트 창 안에서 주인공만 남긴 불리언 마스크."""
    W, H = img.size
    a = np.asarray(img).astype(np.float32) / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]

    mx, mn = a.max(2), a.min(2)
    d = mx - mn
    v = mx
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)

    h = np.zeros_like(mx)
    m = d > 1e-6
    i = m & (mx == r); h[i] = ((g[i] - b[i]) / d[i]) % 6
    i = m & (mx == g) & (mx != r); h[i] = (b[i] - r[i]) / d[i] + 2
    i = m & (mx == b) & (mx != r) & (mx != g); h[i] = (r[i] - g[i]) / d[i] + 4
    h *= 60.0

    # 배경 판정은 채도가 아니라 밝기로. 홀로 광선은 파스텔 무지개라 채도가 제법 있다.
    # 이 기준이면 배추 하이라이트도 배경으로 찍히지만 덩어리 안쪽이라 구멍 메우기가 되살린다.
    bg = (mx + mn) / 2 > 0.78

    green = (h > 55) & (h < 175) & (s > 0.30)
    warm = ((h < 52) | (h > 330)) & (s > 0.16) & (v > 0.12)
    dark = v < 0.30    # 눈·코처럼 아주 어두운 곳은 채도가 죽어서 따로 잡는다

    sub = (green | warm | dark) & ~bg

    win = np.zeros_like(sub)
    win[box[1]:box[3], box[0]:box[2]] = True
    sub &= win

    sub = ndimage.binary_opening(sub, np.ones((3, 3)), iterations=3)

    lab, n = ndimage.label(sub)
    if n:
        sizes = ndimage.sum(sub, lab, range(1, n + 1))
        keep = [j + 1 for j, sz in enumerate(sizes) if sz > 6000]
        print(f"  덩어리 {n} 개 중 {len(keep)} 개 남김 (상위 {sorted(sizes)[-3:]})")
        sub = np.isin(lab, keep)

    sub = ndimage.binary_closing(sub, np.ones((5, 5)), iterations=2)
    return ndimage.binary_fill_holes(sub)


def do_subject(src, out, box):
    img = Image.open(src).convert("RGB")
    H = img.size[1]
    sub = matte(img, box)
    print(f"  주인공 {sub.sum():,} px")

    alpha = Image.fromarray((sub * 255).astype(np.uint8), "L")
    alpha = alpha.filter(ImageFilter.MinFilter(3))     # 배경 흰 테두리 방지로 한 겹 깎기
    alpha = alpha.filter(ImageFilter.GaussianBlur(1.6))

    # 배추 밑동은 원본에서 이미 "LEAFY LOOK" 패널에 가려 잘려 있다. 살릴 수 없으니
    # 마지막 몇십 줄을 서서히 지워 흐려지며 사라지게 한다. 안 그러면 자로 자른 듯 끊긴다.
    fade_to, fade_from = box[3], box[3] - 46
    av = np.asarray(alpha).astype(np.float32)
    av *= np.clip((fade_to - np.arange(H)) / (fade_to - fade_from), 0, 1)[:, None]
    alpha = Image.fromarray(av.astype(np.uint8), "L")

    rgba = img.convert("RGBA")
    rgba.putalpha(alpha)

    # 알파가 있는 만큼만 잘라낸다 — 여백을 남기면 CSS 에서 크기를 잡을 수가 없다
    bbox = rgba.getbbox()
    rgba = rgba.crop(bbox)
    print(f"  알파 경계로 자름 {bbox} -> {rgba.size}")

    rgba.save(out, lossless=False, quality=92, method=6)

    prev = Image.new("RGB", rgba.size, (255, 0, 190))
    prev.paste(rgba, (0, 0), rgba)
    prev.save(str(out).rsplit(".", 1)[0] + "_preview.png")


def do_back(src, out, box):
    img = Image.open(src).convert("RGB").crop(box)
    print(f"  프레임 잘라냄 -> {img.size} (가로세로 {img.size[0] / img.size[1]:.2f})")
    img.save(out, lossless=False, quality=88, method=6)
    img.save(str(out).rsplit(".", 1)[0] + "_preview.png")


if __name__ == "__main__":
    mode, src, out, *nums = sys.argv[1:]
    box = tuple(int(x) for x in nums)
    print(f"{mode}: {src} -> {out}  box={box}")
    (do_subject if mode == "subject" else do_back)(src, out, box)
    print("  됐다")
