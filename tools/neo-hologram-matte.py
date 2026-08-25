"""네오 채소 카드에서 주인공만 알파로 따 낸다 (이머시브 레이어용).

    uv run --no-project --python 3.12 --with pillow --with numpy --with scipy       python tools/neo-hologram-matte.py frontend/public/neo-hologram/art/cabbage-subject.webp

전역에 아무것도 설치하지 않는다 — uv 가 임시 환경을 만들고 끝나면 지운다.

원리: 아트 창 배경이 흰 은색 홀로 광선(아주 밝음)이고 주인공은 채도 높은 초록 +
따뜻한 갈색이라 색으로 가를 수 있다. 완벽한 누끼가 아니라 **패럴랙스 레이어로
쓸 만한 수준**이 목표다. 무지개 반짝이 조각이 몇 개 남는다.

다른 카드에 쓰려면 SRC 와 BOX(아트 창 좌표)를 그 카드에 맞게 바꿔야 한다.
카드마다 프레임 두께가 달라서 BOX 는 눈으로 맞추는 게 빠르다 — 미리보기를 보면 된다.
"""
import sys
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

SRC = r"C:\Users\choiy\Documents\VSC\git_study\DAENGS_dev\frontend\public\neo-hologram\art\cabbage.webp"
OUT = sys.argv[1] if len(sys.argv) > 1 else "subject.png"

# 아트 창(그림 영역). 밖은 프레임·제목바·스탯바라 통째로 버린다.
BOX = (32, 175, 778, 892)   # left, top, right, bottom

img = Image.open(SRC).convert("RGB")
W, H = img.size
a = np.asarray(img).astype(np.float32) / 255.0
r, g, b = a[..., 0], a[..., 1], a[..., 2]

mx = a.max(2)
mn = a.min(2)
d = mx - mn
v = mx
s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)

h = np.zeros_like(mx)
m = d > 1e-6
i = m & (mx == r); h[i] = ((g[i] - b[i]) / d[i]) % 6
i = m & (mx == g) & (mx != r); h[i] = (b[i] - r[i]) / d[i] + 2
i = m & (mx == b) & (mx != r) & (mx != g); h[i] = (r[i] - g[i]) / d[i] + 4
h *= 60.0

# 배경 판정은 채도가 아니라 **밝기**로 한다. 홀로 광선은 파스텔 무지개라 채도가
# 제법 있어서 채도만으로는 안 걸린다. 대신 어느 색이든 아주 밝다.
# 이 기준이면 배추의 하이라이트도 배경으로 찍히지만, 그건 덩어리 **안쪽**이라
# 뒤의 구멍 메우기가 되살린다.
lum = (mx + mn) / 2
bg = lum > 0.78

green = (h > 55) & (h < 175) & (s > 0.30)
warm = ((h < 52) | (h > 330)) & (s > 0.16) & (v > 0.12)
dark = v < 0.30            # 눈·코 등 아주 어두운 곳은 채도가 죽어서 따로 잡는다

sub = (green | warm | dark) & ~bg

# 아트 창 밖은 전부 버린다
win = np.zeros_like(sub)
win[BOX[1]:BOX[3], BOX[0]:BOX[2]] = True
sub &= win

print(f"raw subject px: {sub.sum():,}")

# 순서가 중요하다. 얇은 광선을 먼저 털어내고(opening) **그다음에** 덩어리를 고른다.
# 닫기(closing)를 먼저 하면 옆에 있던 무지개 조각이 본체에 붙어버려서 같이 살아남는다.
sub = ndimage.binary_opening(sub, np.ones((3, 3)), iterations=3)

lab, n = ndimage.label(sub)
if n:
    sizes = ndimage.sum(sub, lab, range(1, n + 1))
    keep = [j + 1 for j, sz in enumerate(sizes) if sz > 6000]
    print(f"components: {n}, kept: {len(keep)}, sizes: {sorted(sizes)[-6:]}")
    sub = np.isin(lab, keep)

sub = ndimage.binary_closing(sub, np.ones((5, 5)), iterations=2)
sub = ndimage.binary_fill_holes(sub)
print(f"final subject px: {sub.sum():,}  ({sub.sum() / (W * H):.1%} of canvas)")

# 가장자리를 살짝 안으로 깎고(배경 흰 테두리 방지) 부드럽게 번지게 한다
alpha = Image.fromarray((sub * 255).astype(np.uint8), "L")
alpha = alpha.filter(ImageFilter.MinFilter(3))
alpha = alpha.filter(ImageFilter.GaussianBlur(1.6))

# 배추 밑동은 원본에서 이미 "LEAFY LOOK" 패널에 가려져 잘려 있다. 그대로 두면
# 레이어로 띄웠을 때 바닥이 자로 자른 듯 직선으로 끊긴다. 살릴 수는 없으니
# 마지막 몇십 줄을 서서히 지워서 흐려지며 사라지게 한다.
FADE_TO, FADE_FROM = BOX[3], BOX[3] - 46
av = np.asarray(alpha).astype(np.float32)
ramp = np.clip((FADE_TO - np.arange(H)) / (FADE_TO - FADE_FROM), 0, 1)
av *= ramp[:, None]
alpha = Image.fromarray(av.astype(np.uint8), "L")

out = img.convert("RGBA")
out.putalpha(alpha)
out.save(OUT, lossless=False, quality=92, method=6)
print("wrote", OUT)

# 실제로 쓸 모습에 가까운 미리보기 — 같은 그림을 크게 흐린 배경 위에 올린다
bgim = img.resize((int(W * 1.6), int(H * 1.6))).filter(ImageFilter.GaussianBlur(38))
bgim = bgim.crop(((bgim.width - W) // 2, (bgim.height - H) // 2,
                  (bgim.width - W) // 2 + W, (bgim.height - H) // 2 + H))
bgim = Image.blend(bgim, Image.new("RGB", (W, H), (8, 18, 10)), 0.45)
bgim.paste(out, (0, 0), out)
bgim.save("preview_scene.png")
print("wrote preview_scene.png")
