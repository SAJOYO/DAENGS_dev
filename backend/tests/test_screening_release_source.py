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


def test_폴더를_직접_정하면_그것을_본다(monkeypatch):
    """로컬에서 직접 만든 릴리스로 돌려 볼 때. **기본값은 없습니다.**"""
    monkeypatch.setattr(service, "RELEASE_REPO", "")
    monkeypatch.setattr(service, "RELEASE_DIR", "/tmp/rel")
    assert service._release_path(download=True) == "/tmp/rel"
    assert service._release_path(download=False) == "/tmp/rel"


def test_둘_다_비면_왜인지_말한다(monkeypatch):
    """★ 마운트를 없앤 뒤로는 **조용히 빈 폴더를 보는 일이 없어야** 합니다.

    예전에는 `/models/release` 가 기본값이라, 마운트가 빠져도 "없는 폴더를 보는"
    상태로 조용히 굴러갔습니다. 지금은 어디서 가져올지 안 정하면 말을 합니다.
    """
    monkeypatch.setattr(service, "RELEASE_REPO", "")
    monkeypatch.setattr(service, "RELEASE_DIR", "")

    # 헬스체크는 죽으면 안 됩니다 — 조용히 0팔로 보입니다.
    assert service._release_path(download=False) is None
    assert service._arms_on_disk() == []

    with pytest.raises(RuntimeError, match="SCREENING_RELEASE_REPO"):
        service._release_path(download=True)


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
    """안 정했으면 **리포에서 받는 구성일 때만** 3 을 기대합니다.

    폴더를 쓰는 개발 PC 는 1팔짜리 릴리스로도 돌아야 합니다 — 거기서 막으면
    `/screen/` 이 통째로 안 뜨고, 그건 "가중치가 없어도 backend 는 뜬다" 는
    지금 설계와 어긋납니다.

    ⚠️ 이 값을 `docker-compose.yml` 의 `environment:` 에 두지 않습니다.
       `environment` 가 `env_file` 을 이겨서 `backend/.env` 값이 덮입니다.
    """
    monkeypatch.delenv("SCREENING_EXPECT_STAGE2_ARMS", raising=False)

    monkeypatch.setattr(service, "RELEASE_REPO", "")
    assert service._expect_arms() is None, "폴더 구성에서는 검사하지 않습니다"

    monkeypatch.setattr(service, "RELEASE_REPO", "org/repo")
    assert service._expect_arms() == 3, "리포 구성이면 기본 3"

    monkeypatch.setenv("SCREENING_EXPECT_STAGE2_ARMS", "2")
    assert service._expect_arms() == 2, "적어 두면 그 값이 이깁니다"

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
