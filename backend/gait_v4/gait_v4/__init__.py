"""gait_v4 — walk_demo v4 보행 분석(direct ssdlite + RTMPose AP-10K) 이식 패키지. README.md 참조."""
from .analyze import analyze_video
from .compare import compare_records
from .config import GAIT_FILTER_VERSION, MODEL_ID, model_meta

__all__ = ["analyze_video", "compare_records", "model_meta", "MODEL_ID", "GAIT_FILTER_VERSION"]
__version__ = "0.1.0"
