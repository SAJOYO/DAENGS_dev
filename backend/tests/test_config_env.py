"""설정 로딩 규칙 (RAG-015) — 우선순위, 파싱, 마스킹, 레포 밖 실행.

config 는 import 시점에 값을 확정하므로, 테스트는 Settings 를 직접 만들어 같은 규칙을 확인한다
(모듈을 reload 하면 다른 테스트가 잡고 있는 config.* 참조가 어긋난다).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from daengs_life.crawler.core import config


def _settings_cls(env_files: tuple[Path, ...]) -> type[BaseSettings]:
    """실제 Settings 와 같은 설정으로, env_file 만 바꿔 끼운 클래스."""
    class S(config.Settings):
        model_config = SettingsConfigDict(
            env_file=env_files, env_file_encoding="utf-8", extra="ignore")
    return S


def test_backend_env_wins_over_root_env_and_both_merge(tmp_path: Path) -> None:
    root, backend = tmp_path / "root.env", tmp_path / "backend.env"
    root.write_text("LAW_OC=from_root\nDATA_GO_KR_KEY=root_only\n", encoding="utf-8")
    backend.write_text("LAW_OC=from_backend\n", encoding="utf-8")

    s = _settings_cls((root, backend))()
    assert s.law_oc == "from_backend"          # 겹치면 backend 가 이긴다
    assert s.data_go_kr_key == "root_only"     # 루트에만 있는 값도 병합된다


def test_real_env_beats_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = tmp_path / "backend.env"
    backend.write_text("LAW_OC=from_file\n", encoding="utf-8")
    monkeypatch.setenv("LAW_OC", "from_real_env")

    assert _settings_cls((backend,))().law_oc == "from_real_env"


def test_parses_inline_comment_and_export_prefix(tmp_path: Path) -> None:
    """손으로 짠 파서가 조용히 틀리던 두 가지 (RAG-015)."""
    backend = tmp_path / "backend.env"
    backend.write_text(
        "LAW_OC=abc123 # 2026-08-20 발급\n"
        "export DATA_GO_KR_KEY=exported_value\n"
        'KAKAO_REST_KEY="quoted"\n',
        encoding="utf-8")

    s = _settings_cls((backend,))()
    assert s.law_oc == "abc123"                    # 뒤에 붙은 주석은 값이 아니다
    assert s.data_go_kr_key == "exported_value"    # export 접두사는 키 이름이 아니다
    assert s.kakao_rest_key == "quoted"


def test_missing_env_files_are_fine(tmp_path: Path) -> None:
    assert _settings_cls((tmp_path / "nope.env",))().law_oc == ""


def test_redact_hides_key_by_param_name_and_by_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(config.SECRETS, "DATA_GO_KR_KEY", "SuperSecretServiceKeyValue123")
    assert config.redact("http://x/y?OC=whatever&t=1") == "http://x/y?OC=***&t=1"
    assert config.redact("http://x/y?serviceKey=SuperSecretServiceKeyValue123&n=1") \
        == "http://x/y?serviceKey=***&n=1"
    # 키가 없는 URL 은 그대로 둔다
    plain = "https://www.law.go.kr/LSW/lsInfoR.do?lsiSeq=287795&efYd=20260707"
    assert config.redact(plain) == plain


def test_require_data_dir_explains_what_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATA_DIR", None)
    with pytest.raises(RuntimeError, match="DAENGS_DATA_DIR"):
        config.require_data_dir()


def test_repo_root_is_optional_so_import_survives_outside_the_repo(tmp_path: Path) -> None:
    """백엔드 이미지에는 data/ 가 없다. 여기서 예외가 나면 컨테이너가 안 뜬다 (RAG-014)."""
    assert config._find_repo_root(tmp_path) is None
