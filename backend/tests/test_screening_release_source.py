"""가중치를 **어디서** 가져오는가 — 허깅페이스 자동 다운로드 (#카드).

배포에서 앙상블이 조용히 1팔로 줄어든 적이 있습니다 (2026-09-07). 응답은 200 이었고
에러도 경고도 없었으며 유일한 단서가 **성공 로그의 부재**였습니다. 커버리지가
67.9% 에서 58.4% 로 떨어진 것을 한참 뒤에 알았습니다.

그래서 이 파일이 지키는 것은 "다운로드가 되나" 가 아니라 **조용히 틀리지 않는가** 입니다:

    ① 리포를 안 정했으면 예전 폴더를 그대로 본다 (되돌리기가 변수 하나)
    ② `local_dir` 을 주지 않는다 — `/models/release` 는 compose 가 `:ro` 로 물립니다
    ③ `/healthz` 는 1.2GB 를 끌어오지 않는다
    ④ 팔 수가 다르면 **모델을 안 올리고 죽는다**
    ⑤ 첫 요청이 둘 동시에 와도 한 번만 받는다

⚠️ `RELEASE_REPO` 등은 import 시점에 읽는 모듈 상수라, 테스트는 환경 변수가 아니라
   **모듈 속성**을 monkeypatch 합니다. 환경 변수를 바꿔도 이미 읽은 뒤라 안 바뀝니다.
"""

import sys
import threading
import types

import pytest

from daengs_screening import service


@pytest.fixture(autouse=True)
def _reset():
    """모듈 전역 캐시를 테스트마다 비웁니다 — 안 그러면 앞 테스트가 뒤를 통과시킵니다."""
    service._downloaded = None
    yield
    service._downloaded = None


def _fake_hub(monkeypatch, path="/cache/snap", boom_offline=True):
    """`huggingface_hub` 을 통째로 바꿔치기합니다 — 패키지가 없어도 돌게."""
    calls = []

    def snapshot_download(**kw):
        calls.append(kw)
        if kw.get("local_files_only") and boom_offline:
            raise OSError("아직 안 받았습니다")
        return path

    mod = types.ModuleType("huggingface_hub")
    mod.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", mod)
    return calls


def test_리포가_없으면_예전_폴더를_본다(monkeypatch):
    """되돌리기가 환경 변수 하나여야 합니다. 토큰 없는 개발 PC 도 그대로 돌아야 하고요."""
    monkeypatch.setattr(service, "RELEASE_REPO", "")
    assert service._release_path(download=True) == service.RELEASE_DIR
    assert service._release_path(download=False) == service.RELEASE_DIR


def test_local_dir_을_주지_않는다(monkeypatch):
    """★ `/models/release` 는 compose 가 `:ro` 로 물립니다 — 거기 쓰면 실패합니다.

    `local_dir` 없이 부르면 이미 쓰기 가능하게 물려 있는 `hf-cache` 볼륨에 받고
    스냅샷 경로를 돌려줍니다. 그래서 compose 를 안 고쳐도 됩니다.
    """
    calls = _fake_hub(monkeypatch)
    monkeypatch.setattr(service, "RELEASE_REPO", "org/repo")
    monkeypatch.setattr(service, "RELEASE_REVISION", "v1")

    assert service._release_path(download=True) == "/cache/snap"
    assert calls == [{"repo_id": "org/repo", "revision": "v1"}], calls
    assert "local_dir" not in calls[0]


def test_헬스체크는_받지_않는다(monkeypatch):
    """`/healthz` 가 1.2GB 를 끌어오면 안 됩니다 — 아직이면 그냥 없다고 답합니다."""
    calls = _fake_hub(monkeypatch)
    monkeypatch.setattr(service, "RELEASE_REPO", "org/repo")
    monkeypatch.setattr(service, "RELEASE_REVISION", "v1")

    assert service._release_path(download=False) is None
    assert calls[0]["local_files_only"] is True
    assert service._arms_on_disk() == []  # 못 받았으면 0팔로 보고합니다


def test_한_번만_받는다(monkeypatch):
    """`_agent()` 는 `run_in_threadpool` 로 불립니다 — 첫 요청 둘이 겹칠 수 있습니다.

    `lru_cache` 는 이 경우를 막아 주지 않아서 락이 따로 필요합니다.
    """
    calls = _fake_hub(monkeypatch)
    monkeypatch.setattr(service, "RELEASE_REPO", "org/repo")
    monkeypatch.setattr(service, "RELEASE_REVISION", "v1")

    done = []
    threads = [
        threading.Thread(target=lambda: done.append(service._release_path(True))) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert done == ["/cache/snap"] * 8
    assert len(calls) == 1, f"{len(calls)}번 받았습니다 — 락이 없습니다"


def test_기대하는_팔_수(monkeypatch):
    """비어 있거나 숫자가 아니면 검사하지 않습니다 (1팔짜리로 개발할 때)."""
    monkeypatch.delenv("SCREENING_EXPECT_STAGE2_ARMS", raising=False)
    assert service._expect_arms() is None
    monkeypatch.setenv("SCREENING_EXPECT_STAGE2_ARMS", "3")
    assert service._expect_arms() == 3
    monkeypatch.setenv("SCREENING_EXPECT_STAGE2_ARMS", "어쩌구")
    assert service._expect_arms() is None


def test_팔이_모자라면_모델을_안_올린다(monkeypatch):
    """★ 이게 이 PR 의 핵심입니다.

    팔이 줄어도 이름·경로·응답은 전부 멀쩡합니다. 개수를 세어 **여기서** 막지 않으면
    다음에 또 조용히 지나갑니다.
    """
    _fake_hub(monkeypatch)
    monkeypatch.setattr(service, "RELEASE_REPO", "org/repo")
    monkeypatch.setattr(service, "RELEASE_REVISION", "v1")
    monkeypatch.setenv("SCREENING_EXPECT_STAGE2_ARMS", "3")
    monkeypatch.setattr(service, "_arms_on_disk", lambda: ["stage2_a"])

    loaded = []
    fake_agent = types.ModuleType("daengs_screening.agent")
    fake_agent.ScreeningAgent = type(
        "X", (), {"from_release": staticmethod(lambda p: loaded.append(p))}
    )
    fake_agent.CONTRACT_VERSION = "1.0"
    monkeypatch.setitem(sys.modules, "daengs_screening.agent", fake_agent)

    service._agent.cache_clear()
    with pytest.raises(RuntimeError, match="1개"):
        service._agent()
    assert loaded == [], "팔이 모자란데 모델을 올렸습니다"
    service._agent.cache_clear()
