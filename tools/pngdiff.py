#!/usr/bin/env python
"""PNG 두 장의 픽셀 차이를 잰다. 의존성 0 (zlib 만 쓴다).

    uv run --no-project --python 3.12 python tools/pngdiff.py base.png other.png
    → 평균차 0.29 / 255    눈에 띄는 픽셀  0.35%

## 왜 있나

"이 효과를 빼면 화면이 얼마나 달라지나" 를 눈이 아니라 숫자로 정하려고 만들었다.
성능을 깎는 작업은 늘 "이거 빼도 티 안 나겠지?" 에서 갈리는데, 눈으로는 두 스크린샷을
번갈아 보는 게 최선이라 미묘한 손실을 놓친다. 실제로 이걸로 갈린 판단이 여럿이다:

  will-change 를 안 움직이는 평면에서 뺀다      0.00%  → 그냥 한다
  하늘 평면을 .dio 배경으로 내린다              0.35%  → 한다
  누끼 글로우 반경을 64 → 30px                  0.22%  → 한다
  누끼 글로우를 원형 그라디언트로 대체          12.71% → **안 한다**
                                                (잎사귀를 따라 도는 글로우라 원으로는
                                                 흉내가 안 된다는 걸 이 숫자가 잡아냈다)

평면을 하나씩 `display: none` 으로 끄고 기준 화면과 비교하면 **어느 레이어가 실제로
화면에 기여하는지**도 나온다. 그렇게 재 보니 .dio-sky 는 0.01%(사실상 안 보임)였고
겉잎(.dio-rind)은 17.9% 여서 남겼다.

**정지 화면이라 움직임 효과는 과소평가된다.** 먼지·이슬이 0.15% / 0.53% 로 낮게 나오는데
그 숫자로 지우면 안 된다 — 그것들은 흐르고 반짝이는 게 본체다.

## 짝이 되는 도구

스크린샷은 헤드리스 크롬으로 찍는다 (`--dump-dom` 은 윈도에서 stdout 이 안 잡히지만
`--screenshot` 은 된다. 파일이 몇 초 늦게 쓰이니 기다렸다 확인할 것):

    chrome.exe --headless=new --disable-gpu --window-size=430,900       --virtual-time-budget=12000 --screenshot=<경로>       "http://localhost:3000/neo-hologram/index.html?im=cabbage"

`?im=<카드 id>` 가 이머시브를 바로 연다. 끝나면 **크롬 프로세스를 반드시 죽일 것** —
안 죽이면 크롬 업데이트가 완료되지 않는다.

## 제약

이 PC 에 PIL 이 없어서 직접 디코딩한다. 스크린샷 비교용이라 8비트 RGB/RGBA
논인터레이스만 다룬다. 크기가 다르면 거부한다.
"""
import sys, zlib, struct


def load(path):
    d = open(path, "rb").read()
    assert d[:8] == b"\x89PNG\r\n\x1a\n", f"PNG 아님: {path}"
    i, idat, meta = 8, [], None
    while i < len(d):
        n = int.from_bytes(d[i:i + 4], "big")
        tag = d[i + 4:i + 8]
        body = d[i + 8:i + 8 + n]
        if tag == b"IHDR":
            w, h, bit, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert bit == 8 and interlace == 0 and ctype in (2, 6), \
                f"8비트 RGB/RGBA 논인터레이스만: bit={bit} ctype={ctype}"
            meta = (w, h, 4 if ctype == 6 else 3)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        i += 12 + n

    w, h, ch = meta
    raw = zlib.decompress(b"".join(idat))
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        f = raw[pos]; pos += 1
        line = bytearray(raw[pos:pos + stride]); pos += stride
        if f == 1:
            for x in range(ch, stride):
                line[x] = (line[x] + line[x - ch]) & 255
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                b = prev[x]
                c = prev[x - ch] if x >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, bytes(out)


def main():
    w1, h1, c1, a = load(sys.argv[1])
    w2, h2, c2, b = load(sys.argv[2])
    assert (w1, h1) == (w2, h2), f"크기 다름 {w1}x{h1} vs {w2}x{h2}"

    total = 0
    hits = 0
    n = w1 * h1
    for p in range(n):
        i, j = p * c1, p * c2
        d = (abs(a[i] - b[j]) + abs(a[i + 1] - b[j + 1]) + abs(a[i + 2] - b[j + 2])) // 3
        total += d
        if d > 4:
            hits += 1
    print(f"평균차 {total / n:6.2f} / 255    눈에 띄는 픽셀 {hits * 100 / n:5.2f}%")


main()
