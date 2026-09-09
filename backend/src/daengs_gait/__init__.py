"""강아지 보행 영상 분석 — 관절 움직임 기록과 같은 개체의 시간 변화 비교.

**진단이 아닙니다.** 같은 개체의 이전 기록과 견주는 관찰 도구입니다.

## 이 패키지의 위치 (D-038)

`backend/src/` 밑에 있지만 **`daengs_backend` 프로세스에 붙지 않습니다.**
소스와 의존성만 backend 로 통합했고, 런타임은 갈라 둡니다 —
compose 의 `gait-analysis` 서비스가 `daengs_gait.service` 를 자기 프로세스로 띄웁니다.

    uv sync --frozen --group gait
    uv run --no-sync gait-serve --host 0.0.0.0 --port 8000

⚠️ **여기서 `daengs_backend` 를 import 하지 마세요.** 그 순간 격리가 깨집니다.
   D-029 가 지키려던 것은 "영상 분석(분 단위 CPU)이 넘어질 때 로그인과 `/life/ask` 까지
   같이 넘어지지 않는 것"이고, D-038 은 그 취지를 그대로 유지합니다.
   반대 방향(`daengs_backend` → `daengs_gait`)은 **한 곳, 함수 안 지연 import 로만** 있습니다
   (D-043 ⓒ · D-063): 워커가 `daengs_gait.engines.get_engine(...)` 으로 엔진을 고르고, 웹이
   `daengs_gait.compare`·`contract` 를 비교할 때 부릅니다. backend 설정값은 인자로 넘어옵니다.
   최상단 import 는 여전히 금지이고, 이 접점을 늘려야 할 것 같으면 D-063 을 먼저 보세요.

⚠️ 무거운 의존성(torch·ultralytics·opencv)은 `gait` 그룹에만 있습니다.
   **이 패키지의 모듈을 `daengs_backend` 쪽에서 최상단 import 하면** 기본 설치
   (`uv sync`, gait 그룹 없음)의 backend 가 ImportError 로 죽습니다.

무엇을 건드리면 안 되는지는 `CLAUDE.md`, 운영 절차는 `README.md` 에 있습니다.
"""
