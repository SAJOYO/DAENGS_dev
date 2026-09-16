"""Expose prepared-snapshot validation without crossing writing package ownership."""


def validate_scene_snapshot_bindings(prepared_snapshot):
    from daengs_backend.services.walk_diary.preparation import scene_snapshot

    return scene_snapshot.validate_scene_snapshot_bindings(prepared_snapshot)
