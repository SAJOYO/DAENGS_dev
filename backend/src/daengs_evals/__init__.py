"""평가·벤치마크 도구.

다른 daengs_* 패키지를 import 해도 되지만 반대 방향은 금지입니다 —
운영 코드(`daengs_backend` 등)가 이 패키지를 import 하면 안 됩니다.
결과 데이터는 `backend/evals/` 아래에 쌓입니다.
"""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/
EVALS_DIR = BACKEND_DIR / "evals"
