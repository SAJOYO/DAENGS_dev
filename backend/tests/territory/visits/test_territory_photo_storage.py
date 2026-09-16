"""Real files: a territory photo becomes visible only after a complete, exclusive PUT."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier

import pytest

from daengs_backend.core.storage import LocalBridgeStorage, StorageNotConfiguredError

KEY = "territory/user/attempt/capture.jpg"
PHOTO = b"abcdefghijklmnopqrstuvwxyz!"


@pytest.mark.parametrize("failure", ["write", "short_write", "close"])
def test_failed_upload_is_invisible_and_same_key_can_be_retried(tmp_path, monkeypatch, failure):
    storage = LocalBridgeStorage(str(tmp_path))
    original_open = Path.open

    class FailingStream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()
            if failure == "close":
                raise OSError("injected close failure")

        def write(self, data):
            if failure == "close":
                return self.stream.write(data)
            written = self.stream.write(data[:4])
            self.stream.flush()
            if failure == "write":
                raise OSError("injected write failure")
            return written

    def failing_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return FailingStream(stream) if mode == "xb" else stream

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", failing_open)
        with pytest.raises(OSError):
            storage.write_if_absent(KEY, PHOTO)

    assert storage.stat(KEY) is None
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
    storage.write_if_absent(KEY, PHOTO)
    stored = storage.stat(KEY)
    assert stored.size_bytes == len(PHOTO)
    assert stored.generation == sha256(PHOTO).hexdigest()
    assert storage.read_bytes(KEY, generation=stored.generation, max_bytes=len(PHOTO)) == PHOTO


@pytest.mark.parametrize("redacted", [False, True])
def test_existing_photo_or_tombstone_cannot_be_replaced(tmp_path, redacted):
    storage = LocalBridgeStorage(str(tmp_path))
    storage.write_if_absent(KEY, PHOTO)
    if redacted:
        storage.redact(KEY, generation=storage.stat(KEY).generation)
    original = storage.stat(KEY)

    with pytest.raises(FileExistsError):
        storage.write_if_absent(KEY, b"another photo")

    assert storage.stat(KEY) == original
    assert storage.local_path(KEY).read_bytes() == (b"" if redacted else PHOTO)
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [storage.local_path(KEY)]


def test_concurrent_writers_publish_exactly_one_complete_photo(tmp_path, monkeypatch):
    original_open = Path.open
    both_writing = Barrier(2, timeout=5)

    def simultaneous_open(path, mode="r", *args, **kwargs):
        # Both calls reach the exclusive-create operation before either proceeds.
        if mode == "xb":
            both_writing.wait()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", simultaneous_open)

    def upload(photo):
        # Independent adapters, as with two backend processes sharing the volume.
        storage = LocalBridgeStorage(str(tmp_path))
        try:
            storage.write_if_absent(KEY, photo)
        except FileExistsError:
            return None
        return photo

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, [PHOTO, b"different complete photo"]))

    winners = [photo for photo in results if photo is not None]
    assert len(winners) == 1
    storage = LocalBridgeStorage(str(tmp_path))
    assert storage.local_path(KEY).read_bytes() == winners[0]
    assert storage.stat(KEY).generation == sha256(winners[0]).hexdigest()
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [storage.local_path(KEY)]


@pytest.mark.parametrize(
    "escape",
    [
        "../escape.jpg",
        "territory/../../escape.jpg",
        "territory/user/../../../../escape.jpg",
        "territory/./../../escape.jpg",
    ],
)
def test_storage_key_cannot_leave_the_root(tmp_path, escape):
    """키가 루트를 벗어나면 거부합니다 — `_path()` 가 `resolve()` 를 안 쓰게 된 뒤에도 (#566 ⓒ).

    동시 `mkdir` 중에 `resolve()` 가 흔들려서 멀쩡한 업로드가 503 을 받던 것을 고치며
    검사를 사전식 정규화로 바꿨습니다. 그때 **같이 사라지면 안 되는 것**이 이 성질이라
    여기서 못 박습니다. 이 방어는 그전까지 테스트가 없었습니다.
    """
    storage = LocalBridgeStorage(str(tmp_path))
    with pytest.raises(StorageNotConfiguredError):
        storage.local_path(escape)


def test_absolute_storage_key_cannot_replace_the_root(tmp_path):
    # pathlib 은 `root / "/절대경로"` 에서 루트를 통째로 **갈아치웁니다**. 그것도 막습니다.
    storage = LocalBridgeStorage(str(tmp_path))
    with pytest.raises(StorageNotConfiguredError):
        storage.local_path(str(Path(tmp_path.anchor, "escape.jpg")))


def test_ordinary_storage_key_stays_under_the_root(tmp_path):
    storage = LocalBridgeStorage(str(tmp_path))
    assert storage.local_path(KEY).is_relative_to(Path(tmp_path).resolve())


@pytest.mark.parametrize("operation", ["fsync", "link"])
def test_failed_file_completion_or_publication_can_be_retried(tmp_path, monkeypatch, operation):
    storage = LocalBridgeStorage(str(tmp_path))

    def fail(*args):
        raise OSError(f"injected {operation} failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, operation, fail)
        with pytest.raises(OSError, match=f"injected {operation} failure"):
            storage.write_if_absent(KEY, PHOTO)

    assert storage.stat(KEY) is None
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
    storage.write_if_absent(KEY, PHOTO)
    assert storage.local_path(KEY).read_bytes() == PHOTO


@pytest.mark.parametrize("published", [False, True])
def test_temporary_cleanup_failure_does_not_change_upload_outcome(
    tmp_path, monkeypatch, caplog, published
):
    storage = LocalBridgeStorage(str(tmp_path))

    def cleanup_fails(*args, **kwargs):
        raise PermissionError("injected cleanup failure")

    def publication_fails(*args):
        raise OSError("injected publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", cleanup_fails)
        if published:
            storage.write_if_absent(KEY, PHOTO)
        else:
            patch.setattr(os, "link", publication_fails)
            with pytest.raises(OSError, match="injected publication failure"):
                storage.write_if_absent(KEY, PHOTO)

    assert "injected cleanup failure" in caplog.text
    if published:
        assert storage.local_path(KEY).read_bytes() == PHOTO
        # A leftover hard link shares the redaction; it must not retain original bytes.
        storage.redact(KEY, generation=storage.stat(KEY).generation)
        assert all(path.read_bytes() == b"" for path in tmp_path.rglob("*") if path.is_file())
    else:
        assert storage.stat(KEY) is None
        storage.write_if_absent(KEY, PHOTO)
        assert storage.local_path(KEY).read_bytes() == PHOTO


@pytest.mark.parametrize("phase", ["partial", "published"])
def test_process_exit_never_leaves_a_partial_final_photo(tmp_path, phase):
    # os._exit bypasses context managers and finally blocks, like an abrupt worker exit.
    script = """
import os
import sys
from pathlib import Path
from daengs_backend.core.storage import LocalBridgeStorage

original_open = Path.open
original_link = os.link

class InterruptedWrite:
    def __init__(self, stream):
        self.stream = stream
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.stream.close()
    def write(self, data):
        self.stream.write(data[:4])
        self.stream.flush()
        os._exit(17)

def interrupted_open(path, mode='r', *args, **kwargs):
    stream = original_open(path, mode, *args, **kwargs)
    return InterruptedWrite(stream) if mode == 'xb' else stream

def exit_after_publication(source, destination):
    original_link(source, destination)
    os._exit(17)

if sys.argv[2] == 'partial':
    Path.open = interrupted_open
else:
    os.link = exit_after_publication
LocalBridgeStorage(sys.argv[1]).write_if_absent(sys.argv[3], sys.argv[4].encode())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), phase, KEY, PHOTO.decode()],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 17, result.stderr
    storage = LocalBridgeStorage(str(tmp_path))
    if phase == "partial":
        assert storage.stat(KEY) is None
        storage.write_if_absent(KEY, PHOTO)
    else:
        with pytest.raises(FileExistsError):
            storage.write_if_absent(KEY, b"replacement")
    assert storage.local_path(KEY).read_bytes() == PHOTO
    assert storage.stat(KEY).generation == sha256(PHOTO).hexdigest()
