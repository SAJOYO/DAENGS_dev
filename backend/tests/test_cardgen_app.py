"""GPU 서비스의 HTTP 계약 (D-078). 실제 모델은 부르지 않는다 — 가짜 모델을 주입한다."""

import base64
import io
import threading
import time

from fastapi.testclient import TestClient
from PIL import Image

from daengs_cardgen.app import create_app
from daengs_cardgen.models import EditRequest, snap


class FakeModel:
    name = "fake"

    def __init__(self) -> None:
        self.requests: list[EditRequest] = []

    def load(self) -> None:
        raise AssertionError("주입한 모델은 lifespan 이 다시 올리지 않는다")

    def edit(self, req: EditRequest) -> Image.Image:
        self.requests.append(req)
        return Image.new("RGB", (req.width, req.height), (1, 2, 3))


def _b64(size=(64, 64), fmt="PNG") -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (9, 9, 9)).save(buf, fmt)
    return base64.b64encode(buf.getvalue()).decode()


def _body(**over) -> dict:
    body = {"images_b64": [_b64(), _b64(fmt="JPEG")], "prompt": "p", "seed": 7, "width": 994, "height": 1582}
    body.update(over)
    return body


def test_snap_rounds_to_multiple_of_16() -> None:
    assert snap(994) == 992
    assert snap(1582) == 1584
    assert snap(1024) == 1024
    assert snap(3) == 16


def test_health_reports_injected_model() -> None:
    with TestClient(create_app(model=FakeModel())) as client:
        assert client.get("/health").json() == {"model": "fake", "ready": True, "load_seconds": None, "error": None}


def test_health_path_is_not_healthz() -> None:
    paths = {getattr(route, "path", None) for route in create_app(model=FakeModel()).routes}
    assert "/health" in paths
    assert "/healthz" not in paths  # Cloud Run 앞 구글 프런트엔드가 가로챈다 (D-070)


def test_generate_returns_png_at_snapped_size_and_passes_request() -> None:
    fake = FakeModel()
    with TestClient(create_app(model=fake)) as client:
        response = client.post("/generate", json=_body())
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["X-Cardgen-Model"] == "fake"
    assert response.headers["X-Cardgen-Size"] == "992x1584"
    assert float(response.headers["X-Cardgen-Seconds"]) >= 0
    assert Image.open(io.BytesIO(response.content)).size == (992, 1584)
    req = fake.requests[0]
    assert (req.seed, req.prompt, len(req.images), req.steps, req.guidance) == (7, "p", 2, None, None)


def test_generate_rejects_bytes_that_are_not_an_image() -> None:
    with TestClient(create_app(model=FakeModel())) as client:
        response = client.post("/generate", json=_body(images_b64=[base64.b64encode(b"nope").decode()]))
    assert response.status_code == 400
    assert response.json()["code"] == "bad_image"


def test_generate_validates_seed_and_image_count() -> None:
    with TestClient(create_app(model=FakeModel())) as client:
        assert client.post("/generate", json=_body(seed=-1)).status_code == 422
        assert client.post("/generate", json=_body(images_b64=[_b64()] * 5)).status_code == 422


def test_load_failure_is_reported_by_health_and_generate(capsys) -> None:
    def broken() -> FakeModel:
        raise RuntimeError("가중치가 없다")

    with TestClient(create_app(loader=broken, ready_timeout_s=5)) as client:
        response = client.post("/generate", json=_body())
        health = client.get("/health").json()
    assert response.status_code == 503
    assert response.json() == {"code": "load_failed", "message": "RuntimeError: 가중치가 없다"}
    assert health["ready"] is False
    assert health["error"] == "RuntimeError: 가중치가 없다"
    assert "Traceback" in capsys.readouterr().err


def test_generate_waits_for_loading_then_succeeds() -> None:
    release = threading.Event()
    fake = FakeModel()

    def slow() -> FakeModel:
        release.wait(10)
        return fake

    try:
        with TestClient(create_app(loader=slow, ready_timeout_s=0.05)) as client:
            started = time.monotonic()
            response = client.post("/generate", json=_body())
            assert response.status_code == 503
            assert response.json()["code"] == "not_ready"
            assert time.monotonic() - started < 5
            assert client.get("/health").json() == {"model": None, "ready": False, "load_seconds": None, "error": None}

            release.set()
            deadline = time.monotonic() + 5
            while not client.get("/health").json()["ready"] and time.monotonic() < deadline:
                time.sleep(0.01)
            assert client.post("/generate", json=_body()).status_code == 200
            health = client.get("/health").json()
            assert health["model"] == "fake"
            assert health["load_seconds"] is not None
    finally:
        release.set()
