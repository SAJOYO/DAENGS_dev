"""The architecture preview must prepare actual inputs without any generation or network."""

import json
import subprocess
import sys

from tests.walk.support.paths import REPO


def test_cli_prepares_three_saved_scenes_without_model_or_network(tmp_path):
    script = """
import importlib.abc
import runpy
import socket
import sys

class NoWriter(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = (
            "google.genai", "langgraph",
            "daengs_backend.services.walk_diary.writing.provider",
            "daengs_backend.services.walk_diary.runtime",
            "daengs_backend.orchestration.diary",
        )
        if any(fullname == p or fullname.startswith(p + ".") for p in blocked):
            raise AssertionError("preview imported writer: " + fullname)

def no_network(*args, **kwargs):
    raise AssertionError("preview tried network")

sys.meta_path.insert(0, NoWriter())
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
sys.path.insert(0, "tools")
sys.argv = ["tools/preview_diary_space_scene.py",
    "--source", "evals/diary_route_scenario/public-02",
    "--previous", "evals/diary_route_scenario/narration-gemini-01",
    "--output", sys.argv[1]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
    output = tmp_path / "scene"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script, str(output)],
        cwd=REPO / "backend",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads((output / "preview.json").read_text(encoding="utf-8"))
    assert value["model_calls"] == value["public_api_calls"] == value["generated_parts"] == 0
    assert len(value["cases"]) == 3
    for case in value["cases"]:
        assert case["initial_input"]["space_scene"]["background"]["basis_ids"]
        assert all("space_scene" in option["returned"] for option in case["detail_options"])
    page = (output / "preview.html").read_text(encoding="utf-8")
    assert "사람이 작성한 초안" in page
    assert "모델이 선택한 결과 아님" in page
