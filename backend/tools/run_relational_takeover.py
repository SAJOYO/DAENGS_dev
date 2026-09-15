"""Inspect a saved v6 preparation, or explicitly make paced live model calls.

No public spatial API lookup, route synthesis, model substitution or note rewriting.
A v4/v5 archive is evidence, not a valid v6 request: prepare it using the current planner.
"""

import argparse
import asyncio
import gzip
import json
from pathlib import Path


def read_json(path):
    content = path.read_bytes()
    return json.loads(gzip.decompress(content) if path.suffix == '.gz' else content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live', action='store_true', help='make paid provider calls; default is inspection only')
    parser.add_argument('--env', type=Path)
    parser.add_argument('--max-calls', type=int, default=64)
    parser.add_argument('--minimum-interval', type=float, default=10.0)
    args = parser.parse_args()
    if args.max_calls < 0 or args.minimum_interval < 0:
        parser.error('call budget and interval must be nonnegative')
    args.output.mkdir(parents=True, exist_ok=False)
    from daengs_backend.services.walk_diary.writing.relational import validate_prepared
    prepared = read_json(args.prepared)
    tasks = validate_prepared(prepared)
    manifest = {
        'source_file': str(args.prepared), 'prepared_revision': prepared['revision'],
        'live_calls_requested': args.live, 'planned_body_tasks': len(tasks),
        'space_tasks': sum(t.stage == 'space' for t in tasks),
        'action_tasks': sum(t.stage == 'action' for t in tasks),
        'semantic_review': 'one separate review per returned valid body/title',
        'automatic_retries': 0, 'max_model_calls': args.max_calls,
        'minimum_interval_s': args.minimum_interval,
        'limits': 'Inspection does not evaluate prose. A model review is not proof of correctness.',
    }
    (args.output/'inspection.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if not args.live:
        print(json.dumps(manifest,ensure_ascii=False,indent=2))
        return
    if args.env:
        from run_diary_route_scenario import configure
        configure(args.env)
    from daengs_backend.orchestration.relational_diary import generate_prepared_relational_diary
    from daengs_backend.services.walk_diary.storage.relational import save_skeleton, read_skeleton
    result = asyncio.run(generate_prepared_relational_diary(
        prepared, max_calls=args.max_calls, minimum_interval_s=args.minimum_interval))
    save_skeleton(args.output/'receipt.json', result)
    if read_skeleton(args.output/'receipt.json') != result['receipt']:
        raise ValueError('saved receipt differs from in-memory publication')
    print(json.dumps(result['receipt']['execution'],ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
