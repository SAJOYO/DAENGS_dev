from app.geo.polyline import decode, encode


def test_roundtrip():
    pts = [(37.4979, 127.0276), (37.4990, 127.0290), (37.5145, 127.0316), (37.4900, 127.0100)]
    enc = encode(pts)
    assert isinstance(enc, str) and len(enc) < 60
    dec = decode(enc)
    assert all(abs(a[0] - b[0]) < 1e-5 and abs(a[1] - b[1]) < 1e-5 for a, b in zip(pts, dec, strict=True))


def test_known_google_example():
    # Google 문서 예제
    assert encode([(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
