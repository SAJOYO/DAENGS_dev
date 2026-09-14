"""Inspect actual writer inputs from saved route inputs, without generating prose.

Reads input.json and backgrounds.json (or .json.gz). Reuses the service's admitted
slots and context projection. No acquisition, model call, database or publication.
"""

import argparse
import json
from pathlib import Path

from run_diary_route_scenario import configure, dump, prepare, read


def preview(source, output):
    from daengs_backend.services.walk_diary.preparation.board import with_scene_backgrounds
    from daengs_backend.services.walk_diary.writing.context import (
        get_action_context,
        get_space_context,
    )
    from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
    from daengs_walk.diary.contracts.input import digest

    raw = read(source / "input.json")
    snapshot = SceneBackgroundSnapshot.model_validate(read(source / "backgrounds.json"))
    base = with_scene_backgrounds(prepare(raw), snapshot)
    cards = []
    for scene in base.board.scenes:
        space = get_space_context(base, scene.id)
        action = get_action_context(base, scene.id)
        cards.append(
            {
                "card_id": scene.id,
                "space": space.llm_input,
                "action": action.llm_input if action is not None else None,
            }
        )
    summary = {
        "mode": "context-only",
        "external_calls": 0,
        "source": source.name,
        "input_sha256": digest(raw),
        "backgrounds_sha256": digest(snapshot),
        "slot_revision": base.slots.revision(),
        "cards": len(cards),
        "space_contexts": len(cards),
        "action_contexts": sum(c["action"] is not None for c in cards),
        "generated_parts": 0,
    }
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "contexts.json", cards)
    dump(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("choose a new output directory; existing results are not overwritten")
    configure(None)
    preview(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
