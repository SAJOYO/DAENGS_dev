"""Portable, factual scene candidates. No credentials, raw provider rows, or user edits."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=80)
    captured_at: AwareDatetime | None
    url: str | None = Field(max_length=500)


class Fact(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    kind: Literal["walk", "action", "note", "movement", "environment", "coverage"]
    text: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(max_length=20)


class RouteRange(StrictModel):
    start_m: float = Field(ge=0)
    end_m: float = Field(ge=0)
    block_id: int | None = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_m < self.start_m:
            raise ValueError("Reversed route range")
        return self


class Scene(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    revision: str = Field(min_length=1, max_length=100)
    started_at: AwareDatetime
    ended_at: AwareDatetime
    route: RouteRange | None
    reasons: list[
        Literal[
            "session_boundary",
            "action",
            "note",
            "profile_change",
            "session_speed",
            "distance_fill",
            "observation_gap",
        ]
    ] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=80)
    facts: list[Fact] = Field(min_length=1, max_length=40)
    sources: list[Source] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def references(self):
        if self.ended_at < self.started_at:
            raise ValueError("Reversed time range")
        ids = {s.id for s in self.sources}
        if len(ids) != len(self.sources) or len({f.id for f in self.facts}) != len(self.facts):
            raise ValueError("Duplicate evidence ids")
        if any(not set(f.source_ids) <= ids for f in self.facts):
            raise ValueError("Unknown source reference")
        return self


class StoryboardBundle(StrictModel):
    format: Literal["walk-storyboard-candidates-v1"] = "walk-storyboard-candidates-v1"
    session_id: str = Field(min_length=1, max_length=128)
    source_revision: str = Field(min_length=1, max_length=100)
    synthetic: bool
    scenes: list[Scene] = Field(min_length=1, max_length=250)

    @model_validator(mode="after")
    def ordered(self):
        if len({s.id for s in self.scenes}) != len(self.scenes):
            raise ValueError("Duplicate scene ids")
        if [s.started_at for s in self.scenes] != sorted(s.started_at for s in self.scenes):
            raise ValueError("Scenes must be chronological")
        return self


def build_storyboard(
    session_id, start, end, distance_m, entries, selection, contexts, gaps=(), *, synthetic=False
):
    local_source = {
        "id": "local-walk",
        "provider": "walk-observation",
        "status": "known",
        "captured_at": None,
        "url": None,
    }
    scenes = []

    def append(
        identity,
        at_s,
        title,
        kind,
        text,
        reasons,
        route=None,
        context=None,
        end_s=None,
        movement=(),
    ):
        scene_id = "scene-" + fingerprint({"session": session_id, "identity": identity})[:24]
        if not synthetic and (identity in {"start", "end"} or identity.startswith("entry:")):
            scene_id = identity
        sources = [local_source]
        facts = [
            {"id": scene_id + ":fact", "kind": kind, "text": text, "source_ids": ["local-walk"]}
        ]
        if context:
            for source in context.get("sources", []):
                provider = source.get("source") or "unknown"
                sources.append(
                    {
                        "id": "environment-" + provider,
                        "provider": provider,
                        "status": source.get("status") or "unknown",
                        "captured_at": source.get("captured_at"),
                        "url": source.get("source_url"),
                    }
                )
            source_ids = [s["id"] for s in sources[1:]]
            for i, value in enumerate(context.get("facts", [])):
                provider = (
                    "commerce"
                    if value.startswith("좌표 반경")
                    else "parks"
                    if "대표점" in value
                    else "rivers"
                    if "시점 좌표" in value
                    else None
                )
                refs = ["environment-" + provider] if provider else source_ids
                facts.append(
                    {
                        "id": scene_id + f":environment:{i}",
                        "kind": "environment",
                        "text": value,
                        "source_ids": refs,
                    }
                )
            if not context.get("facts"):
                facts.append(
                    {
                        "id": scene_id + ":coverage",
                        "kind": "coverage",
                        "text": "이 구간을 설명할 환경 자료를 확인하지 못했어요. 시설이 없다는 뜻은 아니에요.",
                        "source_ids": source_ids,
                    }
                )
        for i, m in enumerate(movement):
            history_ids = []
            if m.get("reason") == "profile_change":
                for walk_id in selection.get("reference_walk_ids", []):
                    source_id = "history-" + walk_id
                    history_ids.append(source_id)
                    if not any(source["id"] == source_id for source in sources):
                        sources.append(
                            {
                                "id": source_id,
                                "provider": "walk-history",
                                "status": "known",
                                "captured_at": None,
                                "url": None,
                            }
                        )
            facts.append(
                {
                    "id": scene_id + f":movement:{i}",
                    "kind": "movement",
                    "text": f"관측 구간 {m['start_s']:.0f}~{m['end_s']:.0f}초의 평균 속도 "
                    f"{m['mean_mps']:.2f}m/s · 비교 기준 {m['baseline_mps']:.2f}m/s",
                    "source_ids": ["local-walk", *history_ids],
                }
            )
        content = {
            "id": scene_id,
            "started_at": start + timedelta(seconds=at_s),
            "ended_at": start + timedelta(seconds=end_s if end_s is not None else at_s),
            "route": route,
            "reasons": reasons,
            "title": title,
            "facts": facts,
            "sources": sources,
        }
        revision = fingerprint(
            {
                **content,
                "started_at": content["started_at"].isoformat(),
                "ended_at": content["ended_at"].isoformat(),
            }
        )
        scenes.append(Scene(**content, revision=revision))

    append("start", 0, "산책 시작", "walk", "산책 시작 기록", ["session_boundary"])
    for entry in entries:
        if not entry["accepted"]:
            continue
        append(
            "entry:" + entry["id"],
            entry["elapsed_s"],
            entry["label"],
            "note" if entry["kind"] == "note" else "action",
            entry["note"] or entry["label"] + " 기록을 남겼어요.",
            ["note" if entry["kind"] == "note" else "action"],
            {
                "start_m": entry["accepted_distance_m"],
                "end_m": entry["accepted_distance_m"],
                "block_id": None,
            }
            if entry["location"] is not None and entry.get("route_known", True)
            else None,
        )
    for anchor in selection["anchors"]:
        # Identity comes from the observed anchor, never its ordinal selection position.
        identity = f"anchor:{anchor['block']}:{anchor['elapsed_s']:.6f}"
        title = (
            "평소와 다른 이동 구간"
            if "profile_change" in anchor["reasons"]
            else "이동 속도가 달라진 구간"
            if "session_speed" in anchor["reasons"]
            else f"경로 {round(anchor['route_m'])}m 주변"
        )
        append(
            identity,
            anchor["elapsed_s"],
            title,
            "walk",
            f"산책 경로 {round(anchor['route_m'])}m 부근의 관측 지점이에요.",
            anchor["reasons"],
            {"start_m": anchor["route_m"], "end_m": anchor["route_m"], "block_id": anchor["block"]},
            contexts.get(anchor["id"]),
            movement=anchor["movement_evidence"],
        )
    for gap in gaps:
        # Gap fields are checked through the canonical model, not inferred from a straight line.
        a = (gap.a.at - start).total_seconds()
        b = (gap.b.at - start).total_seconds()
        append(
            f"gap:{a}:{b}",
            a,
            "위치 기록이 비어 있는 구간",
            "coverage",
            "이 사이의 이동 경로는 확인할 수 없어요.",
            ["observation_gap"],
            end_s=b,
        )
    append(
        "end",
        (end - start).total_seconds(),
        "산책 마무리",
        "walk",
        f"수용 이동거리 {round(distance_m)}m",
        ["session_boundary"],
    )
    scenes.sort(key=lambda s: (s.started_at, s.id))
    payload = [s.model_dump(mode="json") for s in scenes]
    return StoryboardBundle(
        session_id=session_id,
        synthetic=synthetic,
        source_revision=fingerprint(payload),
        scenes=scenes,
    )
