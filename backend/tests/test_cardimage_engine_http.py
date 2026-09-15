"""`HttpCardImageEngine` — GPU 카드 생성 서비스(D-078)를 부르는 엔진. 네트워크 없이 MockTransport 로."""

import base64
import io
import json

import httpx
import pytest
from cardimage_fakes import png
from PIL import Image

from daengs_cardimage.engine import GEN_SIZE, EngineError, HttpCardImageEngine


def _engine(handler, **over) -> HttpCardImageEngine:
    kwargs = {"base_url": "https://cardgen.example/", "timeout_s": 5, "seed": 11,
              "auth": lambda audience: f"tok:{audience}", "transport": httpx.MockTransport(handler)}
    kwargs.update(over)
    return HttpCardImageEngine(**kwargs)


def test_posts_template_then_photo_and_fits_card_size() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=png(*GEN_SIZE),
                              headers={"X-Cardgen-Seconds": "3.2", "X-Cardgen-Model": "fake"})

    engine = _engine(handler)
    template = png(994, 1582, (1, 1, 1))
    out = engine.generate(template_png=template, photo_jpeg=b"jpeg-bytes", prompt="P")

    assert Image.open(io.BytesIO(out)).size == (994, 1582)
    assert seen["url"] == "https://cardgen.example/generate"
    assert seen["auth"] == "Bearer tok:https://cardgen.example"
    body = seen["body"]
    assert (body["seed"], body["prompt"], body["width"], body["height"]) == (11, "P", 1024, 1632)
    assert base64.b64decode(body["images_b64"][0]) == template
    assert base64.b64decode(body["images_b64"][1]) == b"jpeg-bytes"
    assert engine.last_meta == {"seed": 11, "seconds": "3.2", "model": "fake"}


def test_without_auth_sends_no_authorization_header() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=png(*GEN_SIZE))

    _engine(handler, auth=None).generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert seen["auth"] is None


def test_random_seed_when_not_fixed() -> None:
    seeds = []

    def handler(request: httpx.Request) -> httpx.Response:
        seeds.append(json.loads(request.content)["seed"])
        return httpx.Response(200, content=png(*GEN_SIZE))

    engine = _engine(handler, seed=None)
    engine.generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert 0 <= seeds[0] < 2**31
    assert engine.last_meta["seed"] == seeds[0]


def test_non_200_is_upstream_error() -> None:
    engine = _engine(lambda request: httpx.Response(503, json={"code": "not_ready"}))
    with pytest.raises(EngineError) as info:
        engine.generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert info.value.code == "upstream"
    assert "503" in info.value.detail


def test_transport_failure_is_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(EngineError) as info:
        _engine(handler).generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert info.value.code == "upstream"


def test_empty_url_is_no_key() -> None:
    engine = _engine(lambda request: httpx.Response(200), base_url="  ")
    with pytest.raises(EngineError) as info:
        engine.generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert info.value.code == "no_key"


def test_non_image_body_is_no_image() -> None:
    engine = _engine(lambda request: httpx.Response(200, content=b"not a png"))
    with pytest.raises(EngineError) as info:
        engine.generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert info.value.code == "no_image"
