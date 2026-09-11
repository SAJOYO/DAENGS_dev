"""보행 분석 워커 태스크 (D-043, 경계 방식 ⓒ).

**task 정의와 DB 반영은 backend 소유이고, 무거운 분석 함수만 `daengs_gait` 에서
지연 import 합니다.** 방향은 `daengs_backend → daengs_gait` 한쪽뿐입니다 —
`services/training_rag.py` 가 `daengs_training` 을 함수 안에서 부르는 것과 같은
규율이고, `daengs_gait` 는 여전히 backend 를 모릅니다.

워커는 **별도 프로세스**로 뜹니다 (compose 의 `gait-worker`. 옛 `gait-analysis` HTTP
서비스는 D-063 4단계에서 제거됐고, 보행 분석의 실행부는 이제 이 워커 하나입니다):

    celery -A daengs_backend.tasks.gait worker --queues gait --concurrency 1

⚠️ **backend 웹 컨테이너는 이 큐를 먹지 않습니다.** 태스크를 `.delay()` 로 발행만
   하고, 분석은 gait 그룹(torch·rtmlib·onnxruntime)이 설치된 워커에서만 돕니다. 그래서
   아래 지연 import 가 backend 웹 프로세스에서는 절대 실행되지 않습니다 —
   최상단으로 올리면 기본 설치(backend, gait 그룹 없음)가 ImportError 로 죽습니다.

⚠️ **아직 end-to-end 로 돌지 않습니다.** 스토리지 구현이 #78 대기라, 워커가 영상을
   받아올 곳이 없습니다. 태스크는 그 사실을 명확한 실패(FAILED + 사유)로 남깁니다 —
   조용히 성공한 척하는 것보다 낫습니다.
"""

from __future__ import annotations

import os

from celery import Celery

# daengs_life 쪽과 같은 환경변수를 읽지만 **설정 코드를 공유하지는 않습니다** —
# 공유하면 그 import 가 새 접점이 됩니다. REDIS_URL 은 compose 가 넣어 줍니다.
app = Celery(
    "daengs_backend.gait",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=None,  # 결과는 Celery result backend 가 아니라 gait_records 행에 남습니다
)
app.conf.task_default_queue = "gait"
# 워커가 죽으면 태스크를 되살립니다 — 분 단위 작업이라 ack 를 먼저 하면
# 크래시 순간의 작업이 조용히 사라집니다.
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1


@app.task(name="gait.analyze", bind=True, max_retries=0)
def analyze(self, record_id: str) -> None:
    """스토리지에서 영상을 받아 분석하고 결과를 gait_records 에 반영합니다.

    상태 전이의 소유자입니다: PROCESSING → DONE / FAILED.
    (PENDING → UPLOADED 는 confirm 라우터가, 발행은 services/gait.py 가 합니다.)
    """
    # ⚠️ 전부 지연 import 입니다 — 위 모듈 docstring 참고.
    from daengs_backend.services import gait as gait_service

    gait_service.run_analysis_sync(record_id)


@app.task(name="gait.cleanup", bind=True, max_retries=0)
def cleanup(self, record_id: str) -> None:
    """삭제 표시된 기록의 storage object 를 지우고 행을 물리 삭제합니다.

    사용자 직접 삭제 · 탈퇴 · 보관기간 만료 · confirm 안 온 고아 — 파기가 필요한
    모든 경로가 이 태스크로 모입니다 (services.gait 의 발행부 참고).
    """
    from daengs_backend.services import gait as gait_service

    gait_service.run_cleanup_sync(record_id)
