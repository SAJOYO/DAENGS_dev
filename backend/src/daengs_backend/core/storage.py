"""파일 저장소 경계 — GCS 확정, provider-neutral 계약 (D-043, #78).

앱이 보행 영상과 점령지 사진을 **backend 를 거치지 않고** 저장소에 직접 올리는 구조입니다
(Signed URL). backend 가 저장소에 요구하는 것만 Protocol 로 좁혀 둡니다.

provider 는 **GCS 로 확정**(2026-09-02). 다만 bucket·location·만료·보관 정책은
#78 이 정할 값이라 전부 `settings` 로 뺐습니다 — 여기 하드코딩하지 않습니다.

구현체 셋 (`settings.gait_storage` 로 고름 — 이름은 보행 저장소에서 시작한 역사적 이름):
  none  — 미설정. 모든 호출이 503. (`NotConfiguredStorage`)
  local — **임시 bridge.** GCS 자격증명 없이 왕복을 검증하려고 로컬 디렉터리에
          둡니다. 프로덕션이 아닙니다. (`LocalBridgeStorage`)
  gcs   — 진짜. Signed URL. (`GcsStorage`)

⚠️ **object key 는 backend 가 만듭니다** (원칙 6). 앱이 임의 키를 지정하면 남의
   경로를 덮어쓰거나 훔쳐볼 수 있습니다. 도메인별 key builder에서만 만듭니다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


class StorageNotConfiguredError(RuntimeError):
    """저장소가 아직 설정되지 않았습니다 (#78 대기). 라우터가 503 으로 옮깁니다."""


@dataclass
class UploadTicket:
    """앱이 저장소에 직접 올릴 때 필요한 것 전부."""

    storage_key: str
    upload_url: str
    headers: dict[str, str]
    expires_in_seconds: int


class StoragePort(Protocol):
    def create_upload_ticket(
        self,
        *,
        object_key: str,
        content_type: str,
        bridge_upload_path: str = "/app/gait/_bridge/upload",
    ) -> UploadTicket:
        ...

    def exists(self, storage_key: str) -> bool:
        ...

    def download_url(self, storage_key: str, *, expires_in_seconds: int) -> str:
        ...

    def delete(self, storage_key: str) -> None:
        ...


def build_object_key(pet_id: uuid.UUID, *, kind: str, source_file: str) -> str:
    """저장소 object key. **backend 만 만듭니다** (원칙 6).

    `kind` 는 "original" | "overlay". 확장자는 원본 이름에서 따되 경로 조작을 막으려
    basename 의 suffix 만 씁니다 — 앱이 준 이름을 경로로 쓰지 않습니다.
    """
    from pathlib import PurePosixPath

    suffix = PurePosixPath(source_file).suffix.lower()[:10] or ".bin"
    return f"gait/{pet_id}/{kind}/{uuid.uuid4().hex}{suffix}"


def build_overlay_object_key(pet_id: uuid.UUID, record_id: uuid.UUID) -> str:
    """워커 overlay 의 결정적 키.

    업로드는 성공했지만 마지막 DB commit 이 실패해도 pet_id 와 record_id 만으로 정리
    대상을 다시 계산할 수 있어야 합니다. 원본 티켓처럼 임의 UUID 를 새로 만들면 그
    실패 창에서 키가 DB 에 남지 않아 object 가 영구 고아가 됩니다.
    """
    return f"gait/{pet_id}/overlay/{record_id.hex}.mp4"


def build_territory_photo_key(
    app_user_id: uuid.UUID,
    attempt_id: uuid.UUID,
    *,
    content_type: str,
) -> str:
    """점령지 촬영 원본의 결정적 키. 파일명 대신 검증된 MIME으로 확장자를 정합니다."""
    suffixes = {"image/jpeg": ".jpg", "image/webp": ".webp"}
    try:
        suffix = suffixes[content_type]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 점령지 사진 형식: {content_type}") from exc
    return f"territory/{app_user_id}/{attempt_id}/capture{suffix}"


# ── none: 미설정 ────────────────────────────────────────────────────────
class NotConfiguredStorage:
    """자리 지킴이 — 모든 호출이 명확하게 실패합니다. 조용히 no-op 하지 않습니다."""

    _MSG = "파일 저장소가 아직 설정되지 않았습니다 — provider·정책이 정해지면(#78) 열립니다."

    def create_upload_ticket(
        self,
        *,
        object_key,
        content_type,
        bridge_upload_path="/app/gait/_bridge/upload",
    ):
        raise StorageNotConfiguredError(self._MSG)

    def exists(self, storage_key):
        raise StorageNotConfiguredError(self._MSG)

    def download_url(self, storage_key, *, expires_in_seconds):
        raise StorageNotConfiguredError(self._MSG)

    def delete(self, storage_key):
        raise StorageNotConfiguredError(self._MSG)


# ── local: 임시 bridge ──────────────────────────────────────────────────
class LocalBridgeStorage:
    """**임시 dev/검증용.** 로컬 디렉터리에 두고, 업로드는 backend 의 bridge 엔드포인트로
    받습니다. GCS 자격증명 없이 `/app/gait/*` 왕복을 검증하려는 것뿐입니다.

    ⚠️ **프로덕션 경로가 아닙니다.** 여기서는 영상이 bridge 엔드포인트(backend)를 지나
       갑니다 — GCS 경로(원칙 1: backend 를 통과하지 않음)와 다릅니다. 그래서
       `settings.gait_storage="local"` 일 때만 켜지고, 배포에서는 절대 안 씁니다.

    backend 와 gait 워커가 **같은 디렉터리를 봐야** 합니다 (compose 에서 한 볼륨을
    양쪽에 마운트, 또는 단일 머신 검증에서 같은 경로).
    """

    def __init__(self, root: str, *, base_url: str = "") -> None:
        from pathlib import Path

        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        # bridge 업로드/다운로드 URL 의 앞부분. 앱 기준이라 nginx 접두사가 붙습니다.
        self._base_url = base_url.rstrip("/")

    def _path(self, storage_key: str):

        # key 는 backend 가 만든 `gait/<uuid>/...` 라 조작 위험이 없지만, 방어적으로
        # 루트 밖으로 못 나가게 확인합니다.
        root = self._root.resolve()
        p = (root / storage_key).resolve()
        if not p.is_relative_to(root):
            raise StorageNotConfiguredError("잘못된 storage_key")
        return p

    def create_upload_ticket(
        self,
        *,
        object_key,
        content_type,
        bridge_upload_path="/app/gait/_bridge/upload",
    ):
        if not bridge_upload_path.startswith("/") or bridge_upload_path.endswith("/"):
            raise StorageNotConfiguredError("잘못된 bridge upload path")
        return UploadTicket(
            storage_key=object_key,
            upload_url=f"{self._base_url}{bridge_upload_path}/{object_key}",
            headers={"Content-Type": content_type},
            expires_in_seconds=15 * 60,
        )

    def exists(self, storage_key):
        return self._path(storage_key).exists()

    def download_url(self, storage_key, *, expires_in_seconds):
        return f"{self._base_url}/app/gait/_bridge/download/{storage_key}"

    def delete(self, storage_key):
        p = self._path(storage_key)
        if p.exists():
            p.unlink()

    # bridge 엔드포인트가 직접 쓰는 헬퍼 (StoragePort 계약 밖 — local 전용).
    def write(self, storage_key: str, data: bytes) -> None:
        p = self._path(storage_key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def local_path(self, storage_key: str):
        return self._path(storage_key)


# ── gcs: 진짜 ───────────────────────────────────────────────────────────
class GcsStorage:
    """Google Cloud Storage. Signed URL 로 앱이 직접 올리고 받습니다 (원칙 1·7).

    ⚠️ **`google-cloud-storage` 를 지연 import 합니다** — backend 웹 프로세스의 `main`
       import 를 가볍게 유지하는 규율이고(D-021), local/none 으로 도는 개발 PC 가
       이 패키지 없이도 뜨게 합니다.

    자격증명은 GCP 표준(ADC: `GOOGLE_APPLICATION_CREDENTIALS` 또는 워크로드 아이덴티티)을
    따릅니다 — 코드에 키를 두지 않습니다. bucket·location 은 settings 에서 옵니다.
    """

    def __init__(self, *, bucket: str, location: str) -> None:
        if not bucket:
            raise StorageNotConfiguredError(
                "GAIT_GCS_BUCKET 이 비어 있습니다 — 버킷이 생기면(#78) 채웁니다."
            )
        self._bucket_name = bucket
        self._location = location
        self._client = None

    def _bucket(self):
        if self._client is None:
            from google.cloud import storage  # 지연 — 위 docstring 참고

            self._client = storage.Client()
        return self._client.bucket(self._bucket_name)

    def create_upload_ticket(
        self,
        *,
        object_key,
        content_type,
        bridge_upload_path="/app/gait/_bridge/upload",
    ):
        from datetime import timedelta

        blob = self._bucket().blob(object_key)
        url = blob.generate_signed_url(
            version="v4",
            method="PUT",
            expiration=timedelta(seconds=self._upload_ttl()),
            content_type=content_type,
        )
        return UploadTicket(
            storage_key=object_key,
            upload_url=url,
            headers={"Content-Type": content_type},
            expires_in_seconds=self._upload_ttl(),
        )

    def exists(self, storage_key):
        return self._bucket().blob(storage_key).exists()

    def download_url(self, storage_key, *, expires_in_seconds):
        from datetime import timedelta

        return self._bucket().blob(storage_key).generate_signed_url(
            version="v4", method="GET",
            expiration=timedelta(seconds=expires_in_seconds),
        )

    def delete(self, storage_key):
        # 없는 것을 지워도 실패로 보지 않습니다 (idempotent — 재시도·중복 정리 대비).
        try:
            self._bucket().blob(storage_key).delete(if_generation_match=None)
        except Exception as exc:
            # google.api_core.exceptions.NotFound 를 모듈 import 없이 판별합니다. storage.py
            # 의 지연-import 경계를 유지하고, google 모듈을 대역으로 쓰는 테스트도 GCS
            # 패키지 전체를 올리지 않게 하기 위해서입니다.
            if exc.__class__.__name__ == "NotFound" and getattr(exc, "code", 404) == 404:
                return
            raise

    def upload_bytes(self, storage_key: str, data: bytes, *, content_type: str) -> None:
        """워커가 overlay 를 올릴 때 씁니다 (앱이 아니라 서버 쪽 업로드라 Signed URL 이
        아니라 직접 씁니다)."""
        self._bucket().blob(storage_key).upload_from_string(data, content_type=content_type)

    @staticmethod
    def _upload_ttl() -> int:
        from daengs_backend.config import settings

        return settings.gait_upload_url_ttl_seconds


def get_storage() -> StoragePort:
    """`settings.gait_storage` 로 구현을 고릅니다.

    기본은 none — 아무것도 설정 안 하면 안전하게 503 입니다. local/gcs 는 **명시적으로**
    켜야 합니다.
    """
    from daengs_backend.config import settings

    kind = settings.gait_storage
    if kind == "gcs":
        return GcsStorage(
            bucket=settings.gait_gcs_bucket, location=settings.gait_gcs_location
        )
    if kind == "local":
        if not settings.gait_local_storage_dir:
            raise StorageNotConfiguredError(
                "GAIT_LOCAL_STORAGE_DIR 이 비어 있습니다 (gait_storage=local)."
            )
        _check_bridge_base_url(settings.gait_bridge_base_url)
        return LocalBridgeStorage(
            settings.gait_local_storage_dir, base_url=settings.gait_bridge_base_url
        )
    return NotConfiguredStorage()


def _check_bridge_base_url(base_url: str) -> None:
    """bridge 의 공개 주소를 발급 **전에** 검사합니다 (local 모드 전용).

    ⚠️ **앱은 티켓의 `upload_url` 을 그대로 씁니다 — 보정할 수 없습니다.** 그래서 잘못된
       값은 앱에서야 드러나고, 그때는 원인이 서버 설정이라는 것이 안 보입니다. 여기서
       막아야 `/app/gait/analyze` 가 503 과 함께 이유를 말해 줍니다.

    막는 것은 **조용히 깨지는 모양**입니다:

    - 빈 값 → `"/app/gait/_bridge/..."` 라는 **호스트 없는 상대경로**가 나갑니다.
      앱의 `URL(ticket.uploadUrl)` 이 거기서 예외를 냅니다.
    - 스킴 없음(`daengback.weareithero.cloud`) → 같은 이유로 앱이 절대 URL 로 못 씁니다.
    - 경로가 붙음(`http://host/gait`) → `"/gait/app/gait/_bridge/..."` 가 되어 **404**.
      옛 주소를 그대로 옮겨 적을 때 나오는 실수입니다.

    ⚠️ **`http` 를 막지는 않습니다.** 지금 운영 중인 `daengback` 이 평문 http 라서,
       막으면 돌아가는 서버가 죽습니다. 대신 경고를 남깁니다.

    ⚠️ **"맞는 호스트인지"는 여기서 못 봅니다.** GCP 에 집 서버 주소를 넣어도 형식은
       정상입니다 — 그러면 **앱이 영상을 엉뚱한 서버로 올립니다.** 그건 배포 절차가
       지킬 몫이고(`docs/deploy/runbook.md`), 그래서 아래 경고에 호스트를 찍습니다.
    """
    from urllib.parse import urlparse

    if not base_url:
        raise StorageNotConfiguredError(
            "GAIT_BRIDGE_BASE_URL 이 비어 있습니다 (gait_storage=local). "
            "앱이 영상을 올릴 공개 주소라, 비면 상대경로가 나가 앱에서 실패합니다."
        )

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise StorageNotConfiguredError(
            f"GAIT_BRIDGE_BASE_URL 이 절대 주소가 아닙니다: {base_url!r} — "
            "http(s)://호스트 형태여야 합니다."
        )
    if parsed.path.strip("/"):
        raise StorageNotConfiguredError(
            f"GAIT_BRIDGE_BASE_URL 에 경로가 붙어 있습니다: {base_url!r} — "
            "오리진만 넣으세요. 경로를 붙이면 업로드 주소가 어긋납니다."
        )
    if parsed.scheme == "http":
        log.warning(
            "GAIT_BRIDGE_BASE_URL 이 평문 http 입니다 (%s). 앱이 이 주소로 영상을 "
            "올립니다 — 배포 환경의 공개 주소가 맞는지 확인하세요.",
            parsed.netloc,
        )
