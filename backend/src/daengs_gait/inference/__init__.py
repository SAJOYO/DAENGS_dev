"""v4 pose 추론(SuperAnimal ssdlite + RTMPose AP-10K) — 자식 프로세스 전용 (D-063 5B).

`daengs_gait.engines.subprocess_bridge` 가 `python -m daengs_gait.inference analyze ...` 로
이 패키지를 **서브프로세스로만** 부릅니다 — torch·rtmlib·onnxruntime 이 워커 자신의
프로세스에는 안 올라옵니다.

⚠️ **이 파일은 가벼워야 합니다.** 하위 모듈(`pose`·`ssdlite_detector`·`analyze`)은 여기서
   eager import 하지 않습니다 — `daengs_gait.engines.__init__` 과 같은 규율입니다. backend
   웹 프로세스가 실수로 `daengs_gait.inference` 를 import 해도 torch 가 따라오면 안 됩니다.
"""

__all__: list[str] = []
