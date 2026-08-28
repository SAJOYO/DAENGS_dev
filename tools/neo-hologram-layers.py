"""네오 채소 카드에서 이머시브 레이어를 뽑아낸다.

두 가지를 한다.

    subject  카드 그림에서 주인공을 **색으로 갈라** 알파를 만든다 (알파가 없는 원본용)
    trim     **이미 알파가 있는** 원화를 다듬는다 — 먼지 털고 딱 맞게 자른다 (손누끼용)
    back     배경 원화에서 카드 프레임을 잘라내고, 좌우를 거울로 넓혀 가로 화면에 맞춘다
    card     카드 그림의 둥근 모서리 바깥(검정)을 알파로 지운다 — 확대할 때 귀퉁이가 검게 남는다

둘 다 **아트 창 좌표를 손으로 준다.** 카드마다 프레임 두께가 달라서 자동으로 찾는 것보다
미리보기를 보고 맞추는 게 빠르다. 실행하면 `*_preview.png` 가 같이 나오니 그걸 보면 된다.

    uv run --no-project --python 3.12 --with pillow --with numpy --with scipy \\
      python tools/neo-hologram-layers.py <subject|back> <src> <out> <l> <t> <r> <b>

전역에 아무것도 설치하지 않는다 — uv 가 임시 환경을 만들고 끝나면 지운다.

원본 레이어 원화는 tools/art-src/ 에 둔다. 서빙되는 곳에 두면 쓰지도 않는 수 MB 짜리
원본이 그대로 바깥에 열린다 — 결과물(art/*.webp)만 내보낸다.
**art-src 는 git 에 없다** (.gitignore). 수 MB 짜리라 팀 드라이브에 두고, 이 스크립트를
다시 돌릴 때만 내려받는다. 그래서 갓 클론한 저장소에서는 입력이 비어 있는 게 정상이다.

**결과물의 목적지는 이제 이 저장소가 아니다.** 도감은 `SAJOYO/DAENGS_CARDS` 로 나갔고
(D-025) `art/*.webp` 는 그쪽 루트의 `art/` 다. 이 스크립트가 여기 남은 것은 입력인
`tools/art-src/` 가 여기 있기 때문이다. webp 변환기(convert-one.mjs · convert-server.mjs)는
개인 저장소 `choiyc05/gohome` 에만 있으니, 원화를 새로 받으면 이 셋의 위치부터 확인할 것.

배추(No.01) 에 쓴 값:

    trim     tools/art-src/cabbage_neo_subject_2.png                          (좌표 불필요)
    back     tools/art-src/cabbage_neo_back.png              60 235 815 905   (875x1216 기준)

subject 원리: 아트 창 배경이 흰 은색 홀로 광선(어느 색이든 아주 밝다)이고 주인공은 채도
높은 초록 + 따뜻한 갈색이라 밝기로 가를 수 있다. 완벽한 누끼가 아니라 **패럴랙스 레이어로
쓸 만한 수준**이 목표다 — 무지개 반짝이 조각이 몇 개 남는다.

순서에 주의. 얇은 광선을 먼저 털어내고(opening) **그다음에** 큰 덩어리를 고른다. 닫기를
먼저 하면 옆에 있던 무지개 조각이 본체에 붙어 같이 살아남는다.
"""
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
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


def do_trim(src, out, _box=None):
    """이미 알파가 있는 원화를 다듬는다 — 먼지 같은 반투명 점을 털고 딱 맞게 자른다.

    손으로 딴 누끼를 받았을 때 쓴다. 눈에 안 보이는 알파 1~2 짜리 픽셀이 구석에 남아
    있으면 경계 상자가 캔버스 전체가 돼서 CSS 에서 크기를 못 잡는다.
    """
    img = Image.open(src).convert("RGBA")
    al = np.asarray(img.getchannel("A")).astype(int)
    print(f"  원본 {img.size}, 경계 상자 {img.getbbox()}")

    solid = al > 10
    solid = ndimage.binary_opening(solid, np.ones((3, 3)))
    lab, n = ndimage.label(solid)
    if n:
        sizes = ndimage.sum(solid, lab, range(1, n + 1))
        keep = [j + 1 for j, sz in enumerate(sizes) if sz > 4000]
        print(f"  덩어리 {n} 개 중 {len(keep)} 개 남김 (상위 {sorted(sizes)[-3:]})")
        solid = np.isin(lab, keep)

    # 살릴 덩어리 언저리는 원래 알파를 그대로 둔다 — 가장자리 반투명이 이 원화의 장점이다
    grow = ndimage.binary_dilation(solid, np.ones((7, 7)), iterations=2)
    al = np.where(grow, al, 0)

    img.putalpha(Image.fromarray(al.astype(np.uint8), "L"))
    bbox = img.getbbox()
    img = img.crop(bbox)
    print(f"  다듬은 뒤 {bbox} -> {img.size}")

    img.save(out, lossless=False, quality=92, method=6)
    prev = Image.new("RGB", img.size, (255, 0, 190))
    prev.paste(img, (0, 0), img)
    prev.save(str(out).rsplit(".", 1)[0] + "_preview.png")


def do_back(src, out, box, ratio=2.0):
    """프레임을 잘라내고, 화면 비율에 맞게 좌우로 넓힌다.

    원화(아트 창)는 세로형인데 PC 화면은 가로형이다. 그대로 background:cover 로 깔면
    좌우를 맞추느라 위아래가 잘려서 **하늘이 사라지고 텃밭만** 남는다. 그래서 좌우
    날개를 붙여 2:1 로 만든다 — 날개는 가장자리를 거울처럼 뒤집은 것이다.

    잎사귀처럼 반복되는 무늬라 거울 이음매는 거의 안 보인다. 좌우 대칭이 눈에 띌 수는
    있는데, 그 자리는 어차피 비네팅으로 어두워지는 구석이다.
    """
    img = Image.open(src).convert("RGB").crop(box)
    w, hgt = img.size
    print(f"  프레임 잘라냄 -> {img.size} (가로세로 {w / hgt:.2f})")

    target_w = int(round(hgt * ratio))
    wing = max(0, (target_w - w) // 2)
    if wing:
        left = img.crop((0, 0, wing, hgt)).transpose(Image.FLIP_LEFT_RIGHT)
        right = img.crop((w - wing, 0, w, hgt)).transpose(Image.FLIP_LEFT_RIGHT)
        wide = Image.new("RGB", (w + wing * 2, hgt))
        wide.paste(left, (0, 0))
        wide.paste(img, (wing, 0))
        wide.paste(right, (w + wing, 0))
        img = wide
        print(f"  거울 날개 {wing}px 씩 -> {img.size} (가로세로 {img.size[0] / img.size[1]:.2f})")

        # 거울 자체는 티가 안 나는데 **좌우 대칭**이 눈에 띈다 (특히 배추처럼 큰 덩어리가
        # 나비 날개처럼 짝을 이룬다). 바깥으로 갈수록 흐리고 어둡게 해서 주변시야로
        # 밀어낸다 — 대칭이 안 읽히고, 덤으로 피사계심도가 생겨 가운데가 앞으로 나온다.
        w2, h2 = img.size
        x = np.abs(np.linspace(-1, 1, w2))
        t = np.clip((x - 0.40) / 0.60, 0, 1) ** 1.4
        mask = Image.fromarray((np.tile(t, (h2, 1)) * 255).astype(np.uint8), "L")
        img = Image.composite(img.filter(ImageFilter.GaussianBlur(9)), img, mask)
        shade = (1 - 0.42 * np.tile(t, (h2, 1)))[..., None]
        img = Image.fromarray((np.asarray(img) * shade).astype(np.uint8))
        print(f"  바깥 {int((1 - 0.40) * 50)}% 를 흐리고 어둡게")

    img.save(out, lossless=False, quality=88, method=6)
    img.save(str(out).rsplit(".", 1)[0] + "_preview.png")


def do_card(src, out, nums=None):
    """카드 그림의 둥근 모서리 **바깥**을 알파로 지운다.

    원본 파일은 직사각형인데 인쇄된 카드는 모서리가 둥글어서, 그 바깥이 검정으로
    채워져 있다. 도감 그리드에서는 .card 의 border-radius 가 잘라 주니 안 보이지만,
    이머시브 진입에서 카드를 화면만 하게 확대하면 네 귀퉁이에 검은 삼각형이 남는다.

    반지름은 눈으로 재면 된다 — 맨 윗줄에서 검정이 어디까지 이어지는지가 곧 반지름이다.
    배추 카드(810x1125)는 30px 쯤이라 32 로 잡았다. inset 은 모서리를 두른 검은
    실선 한 겹까지 같이 걷어내려고 안쪽으로 더 깎는 양이다.
    """
    r, inset = (tuple(nums) + (32, 1))[:2] if nums else (32, 1)
    img = Image.open(src).convert("RGBA")
    w, h = img.size

    # 4배로 그렸다가 줄인다 — 안 그러면 둥근 모서리가 계단으로 남는다
    s = 4
    m = Image.new("L", (w * s, h * s), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (inset * s, inset * s, (w - inset) * s - 1, (h - inset) * s - 1),
        radius=r * s, fill=255)
    img.putalpha(m.resize((w, h), Image.LANCZOS))
    print(f"  {img.size}, 반지름 {r}px, 안쪽으로 {inset}px")

    img.save(out, lossless=False, quality=92, method=6)
    prev = Image.new("RGB", img.size, (255, 0, 190))
    prev.paste(img, (0, 0), img)
    prev.save(str(out).rsplit(".", 1)[0] + "_preview.png")


MODES = {"subject": do_subject, "back": do_back, "trim": do_trim, "card": do_card}

if __name__ == "__main__":
    mode, src, out, *nums = sys.argv[1:]
    box = tuple(int(x) for x in nums) if nums else None
    print(f"{mode}: {src} -> {out}  box={box}")
    MODES[mode](src, out, box)
    print("  됐다")
