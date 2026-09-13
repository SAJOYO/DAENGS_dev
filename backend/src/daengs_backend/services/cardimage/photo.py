"""사용자 사진 검증과 축소. 앱이 프로필 사진을 보내는 크기(긴 변 1600, JPEG q90)와 같다 — 실험 1·2 로 원본과 유사도 차이 없음."""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PHOTO_BYTES = 8 * 1024 * 1024
ALLOWED_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})


class PhotoError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def prepare_photo(data: bytes, content_type: str, *, max_side: int = 1600) -> bytes:
    if content_type not in ALLOWED_MIME:
        raise PhotoError("bad_mime", f"사진은 JPEG·PNG·WebP 만 받습니다 (받은 것: {content_type})")
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise PhotoError("too_large", f"사진은 비어 있지 않은 {MAX_PHOTO_BYTES // (1024 * 1024)} MiB 이하여야 합니다")
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise PhotoError("undecodable", "사진을 읽을 수 없습니다") from exc
    im.thumbnail((max_side, max_side))  # 작은 사진은 키우지 않는다
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()
