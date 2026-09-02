"""파일 저장소 경계 — provider-neutral 계약 (D-043, #78 대기).

앱이 영상을 **backend 를 거치지 않고** 클라우드에 직접 올리는 구조(#78 의 presigned
방식)를 전제로, backend 가 저장소에 요구하는 것만 Protocol 로 좁혀 둡니다.

⚠️ **실구현이 아직 없습니다.** provider(S3/GCS) · 버킷 · 리전 · 보관/파기 정책이
   #78 에서 사람이 정할 일이라, 여기서는 계약과 "미설정" 구현체까지만 둡니다.
   임시 local-upload 폴백도 **일부러 만들지 않습니다** — 폴백이 있으면 그것이
   사실상의 저장 정책이 되어 #78 의 결정을 앞질러 버립니다.

키(`storage_key`)는 불투명 문자열입니다. S3 든 GCS 든 키 모양만 다르고 이 계약은
같습니다 — DB(`gait_records.original_storage_key`)도 text 로 받는 이유입니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class StorageNotConfiguredError(RuntimeError):
    """저장소 provider 가 아직 설정되지 않았습니다 (#78 대기).

    라우터는 이것을 503 으로 옮깁니다 — 요청이 틀린 게 아니라 환경이 덜 갖춰진
    것입니다 (`/ask` 가 ml 그룹 없을 때 503 을 내는 것과 같은 규칙).
    """


@dataclass
class UploadTicket:
    """앱이 스토리지에 직접 올릴 때 필요한 것 전부.

    `upload_url` 로 PUT/POST 하고, 성공하면 backend 에 confirm 을 보냅니다.
    `storage_key` 는 그 confirm 과 이후 워커가 파일을 찾는 열쇠입니다.
    """

    storage_key: str
    upload_url: str
    # provider 마다 요구 헤더가 다릅니다 (S3 의 Content-Type 강제 등).
    headers: dict[str, str]
    expires_in_seconds: int


class StoragePort(Protocol):
    """backend 가 저장소에 요구하는 것 전부. 이 넷을 넘는 요구가 생기면
    provider 종속이 새는 것이니 여기서 막습니다."""

    def create_upload_ticket(self, *, key_hint: str, content_type: str) -> UploadTicket:
        """앱이 직접 올릴 자리를 만듭니다."""
        ...

    def exists(self, storage_key: str) -> bool:
        """confirm 때 실제로 올라왔는지 확인합니다 — 앱의 말만 믿으면
        빈 기록이 PROCESSING 으로 넘어갑니다."""
        ...

    def download_url(self, storage_key: str, *, expires_in_seconds: int) -> str:
        """재생용 임시 URL. 응답에 storage_key 를 그대로 내보내지 않는 이유입니다."""
        ...

    def delete(self, storage_key: str) -> None:
        """soft delete 된 기록의 파일 정리."""
        ...


class NotConfiguredStorage:
    """#78 이 정해지기 전의 자리 지킴이 — 모든 호출이 명확하게 실패합니다.

    조용히 no-op 하지 않습니다. no-op 이면 confirm 이 "올라왔다"고 거짓말하게 되고,
    워커가 없는 파일을 받으러 갑니다.
    """

    _MSG = (
        "파일 저장소가 아직 설정되지 않았습니다 — provider·보관 정책이 정해지면(#78) "
        "열립니다."
    )

    def create_upload_ticket(self, *, key_hint: str, content_type: str) -> UploadTicket:
        raise StorageNotConfiguredError(self._MSG)

    def exists(self, storage_key: str) -> bool:
        raise StorageNotConfiguredError(self._MSG)

    def download_url(self, storage_key: str, *, expires_in_seconds: int) -> str:
        raise StorageNotConfiguredError(self._MSG)

    def delete(self, storage_key: str) -> None:
        raise StorageNotConfiguredError(self._MSG)


def get_storage() -> StoragePort:
    """지금은 항상 미설정입니다. #78 이 정해지면 여기가 provider 구현체를 고릅니다
    (환경변수로 갈라 taps — settings 에 넣는 것은 그때 일입니다)."""
    return NotConfiguredStorage()
