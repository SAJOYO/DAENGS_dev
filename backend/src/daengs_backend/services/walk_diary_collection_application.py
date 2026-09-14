"""Keep acquisition evidence independently of which backgrounds can enter scene slots."""

from daengs_backend.services.walk_diary_base_board import with_scene_backgrounds
from daengs_backend.services.walk_diary_card_contracts import CollectionReceipt
from daengs_backend.services.walk_diary_collection_progress import (
    CollectionProgress,
    active_collection,
)


async def collect_for_writing(base, collector, executor, deadline):
    progress = CollectionProgress(base.board, deadline)
    failure = None

    async def acquire():
        nonlocal failure
        token = active_collection.set(progress)
        try:
            snapshot = await collector(base.board)
            try:
                progress.accept(snapshot)
            except (ValueError, TypeError, AttributeError):
                failure = "invalid_snapshot"
                raise
        finally:
            active_collection.reset(token)

    outcome = await executor.run("diary:space_collection", acquire, deadline=deadline)
    snapshot = progress.freeze()
    prepared, excluded, application = apply_collection(base, snapshot)
    timed_out = any(
        b.reason in {"collection_timeout", "collection_not_started"} for b in snapshot.backgrounds
    )
    receipt = CollectionReceipt(
        snapshot=snapshot,
        status=("timeout" if timed_out else "completed")
        if outcome.status == "ok"
        else outcome.status,
        failure_code=(failure or "collector_failed") if outcome.status == "error" else None,
        application_status=application,
        application_failures=excluded,
    )
    return prepared, receipt


def apply_collection(base, snapshot):
    snapshot = snapshot.validate_board(base.board)
    try:
        return with_scene_backgrounds(base, snapshot), (), "applied"
    except Exception:  # noqa: BLE001 - isolate projection errors without logging source payloads
        return _apply_individually(base, snapshot)


def _apply_individually(base, snapshot):
    # The normal path projects once. Only an invalid combination needs source-by-source isolation.
    kept, failed = [], []
    prepared = base
    for row in snapshot.backgrounds:
        candidate = snapshot.model_copy(update={"backgrounds": (*kept, row)})
        try:
            projected = with_scene_backgrounds(base, candidate)
        except Exception:  # noqa: BLE001 - the receipt retains this source and its exclusion
            failed.append(row.id)
        else:
            kept.append(row)
            prepared = projected
    return prepared, tuple(failed), ("partial" if failed else "applied") if kept else "failed"
