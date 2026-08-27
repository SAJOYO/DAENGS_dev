"""seed_sources.yaml 로드 + (domain, id) → Source 클래스 매핑.

규약: yaml 의 domain 'law' + id 'easylaw-pet'
      → 모듈 crawler.sources.law.easylaw_pet → 그 안의 Source 서브클래스 하나.
모듈이 없으면 '아직 구현 안 된 소스'다 (list 명령에서 표시).

domain 을 폴더로 쓰는 이유 — 소스가 23개가 되면 sources/ 가 평평한 .py 더미가 된다.
domain 은 yaml 에 이미 있고 `data/raw/{domain}/` 저장 위치와도 같은 축이라, 새 규약을 만들지 않고
폴더를 나눌 수 있다. 소스를 찾을 때 스캔하지 않고 경로를 바로 계산하는 것도 그대로 유지된다.
"""
from __future__ import annotations

import importlib
import inspect
from typing import Any

import yaml

from . import config
from ..sources.base import Source


def load_seeds() -> dict[str, dict[str, Any]]:
    config.require_data_dir()               # data/ 를 처음 건드리는 지점
    with config.SEED_FILE.open(encoding="utf-8") as f:
        items = yaml.safe_load(f)
    return {it["id"]: it for it in items}


def module_name(seed: dict[str, Any]) -> str:
    """yaml 항목 → 모듈 경로. `-` 는 파이썬 식별자에 못 쓰므로 `_` 로 바꾼다."""
    return (f"daengs_life.crawler.sources.{seed['domain'].replace('-', '_')}"
            f".{seed['id'].replace('-', '_')}")


def resolve(seed: dict[str, Any]) -> type[Source] | None:
    modname = module_name(seed)
    try:
        mod = importlib.import_module(modname)
    except ModuleNotFoundError as e:
        # 도메인 폴더가 통째로 없으면 e.name 이 상위 패키지
        # ('daengs_life.crawler.sources.subsidy')다.
        # 그것도 '아직 구현 안 됨'이지만, bs4 같은 외부 의존성 누락은 숨기지 않는다.
        if e.name and modname.startswith(e.name):
            return None
        raise
    for _, obj in inspect.getmembers(mod, inspect.isclass):
        # 베이스 클래스(_lawgokr.LawGoKrSource 등)를 import 해 와도 __module__ 이 달라 걸러진다
        if issubclass(obj, Source) and obj is not Source and obj.__module__ == modname:
            return obj
    return None


def build(source_id: str) -> Source:
    seeds = load_seeds()
    if source_id not in seeds:
        raise KeyError(f"unknown source id: {source_id} (seed_sources.yaml 에 없음)")
    seed = seeds[source_id]
    cls = resolve(seed)
    if cls is None:
        raise KeyError(f"source '{source_id}' 는 yaml 에 있지만 모듈이 없음: {module_name(seed)}.py")
    return cls(seed)
