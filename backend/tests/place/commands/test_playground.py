from uuid import uuid4

import httpx

from daengs_evals.facility_tools.playground import create_app


async def test_ui_and_chat_use_the_same_command_port_and_session():
    class Provider:
        def __init__(self, key, model):
            self.round = 0

        async def _call(self, payload):
            self.round += 1
            if self.round == 1:
                return {
                    "status": "requires_action",
                    "steps": [
                        {
                            "type": "function_call",
                            "id": "search",
                            "name": "search_places",
                            "arguments": {
                                "category": {"operation": "set", "values": ["cafe"]},
                                "parking": "required",
                            },
                        }
                    ],
                }
            return {
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "주차 가능한 카페를 찾았어멍 🐾"}],
                    }
                ],
            }

    app = create_app("not-a-real-key", provider_factory=Provider)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8769",
        headers={"origin": "http://127.0.0.1:8769"},
    ) as client:
        initial = (await client.get("/api/state")).json()
        assert len(initial["ui"]["cards"]) == 6
        request = {
            "request_id": str(uuid4()),
            "expected_revision": initial["ui"]["revision"],
            "query": "주차되는 카페만 보여줘",
        }
        response = await client.post("/api/chat", json=request)
        assert response.status_code == 200
        answer = response.json()
        assert answer["turn"]["status"] == "ready"
        assert answer["ui"]["cards"][0]["name"] == "평가 장소 A"
        assert len(answer["ui"]["cards"]) == 1
        repeated = (await client.post("/api/chat", json=request)).json()
        assert repeated["ui"]["revision"] == answer["ui"]["revision"]
        manual = (
            await client.post(
                "/api/command",
                json={
                    "request_id": str(uuid4()),
                    "expected_revision": answer["ui"]["revision"],
                    "name": "search_places",
                    "arguments": {"category": {"operation": "add", "values": ["restaurant"]}},
                },
            )
        ).json()
        assert {p["name"] for p in manual["ui"]["cards"]} == {"평가 장소 A", "평가 장소 E"}
        assert manual["ui"]["filters"]["required"] == answer["ui"]["filters"]["required"]
        trace = (await client.get("/api/trace/" + request["request_id"])).json()
        assert "not-a-real-key" not in str(trace)
        assert trace["calls"][1]["request"]["input"][-1]["type"] == "function_result"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8769"
    ) as other:
        assert (await other.get("/api/trace/" + request["request_id"])).status_code == 404
        assert (await other.post("/api/chat", json=request)).status_code == 403


async def test_loopback_host_and_request_size_are_checked():
    app = create_app("")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://attacker.test"
    ) as client:
        assert (await client.get("/api/state")).status_code == 403
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8769",
        headers={"origin": "http://127.0.0.1:8769", "content-type": "application/json"},
    ) as client:
        assert (await client.post("/api/chat", content="x" * 17000)).status_code == 413
