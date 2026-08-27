"""Google encoded polyline. 클라이언트(Android/Leaflet) 디코더가 흔하다. 좌표 60개 → 문자열 수백 바이트."""


def encode(points: list[tuple[float, float]], precision: int = 5) -> str:
    factor = 10 ** precision
    out: list[str] = []
    prev_lat = prev_lng = 0
    for lat, lng in points:
        ilat, ilng = round(lat * factor), round(lng * factor)
        for v in (ilat - prev_lat, ilng - prev_lng):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lng = ilat, ilng
    return "".join(out)


def decode(s: str, precision: int = 5) -> list[tuple[float, float]]:
    factor = 10 ** precision
    pts: list[tuple[float, float]] = []
    i = lat = lng = 0
    while i < len(s):
        for which in (0, 1):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else (result >> 1)
            if which == 0:
                lat += d
            else:
                lng += d
        pts.append((lat / factor, lng / factor))
    return pts
