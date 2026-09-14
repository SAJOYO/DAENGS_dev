"""사용자 사진 검증과 축소. 앱이 프로필 사진을 보내는 크기(긴 변 1600, JPEG q90)와 같다 — 실험 1·2 로 원본과 유사도 차이 없음."""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

# 폰 원본 JPEG 이 그대로 온다는 가정이다 — `cardimage/test/` 의 실제 사진 13장 중 3장이 9.7~10.2MB 라
# 프로필 사진 상한(8 MiB, `services/pet.py`)으로는 413 이 났다(09-14 콘솔 실측). nginx 의
# `/api/admin/cardimage/` 블록 `client_max_body_size` 와 같은 값이어야 한다.
MAX_PHOTO_BYTES = 20 * 1024 * 1024
# 폰 원본 화소는 대개 5000만 이하다. 압축이 잘 먹는 단색·PNG 는 파일 바이트가 작아도
# 픽셀 수는 커서(압축 폭탄) 디코드 때 메모리를 크게 먹을 수 있다. 어차피 아래에서
# max_side 로 줄이므로, 바이트 크기와 별개로 여유를 둔 6000만 화소에서 막는다.
MAX_PIXELS = 60_000_000
ALLOWED_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})


class PhotoError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def prepare_photo(data: bytes, content_type: str, *, max_side: int = 1600) -> bytes:
    """사진을 검증하고 축소한 JPEG q90 바이트로 돌려준다.

    검사 순서가 중요하다. MIME 을 가장 먼저 보는 이유는 공짜이기 때문이고(디코드가
    전혀 안 든다), 바이트 크기를 그 다음에 보는 이유도 같다. 선언된 해상도(가로×세로)는
    실제 디코드 **전에** 본다 — `Image.open` 은 헤더만 읽으므로 여기서 막으면 압축
    폭탄(작은 파일이 거대한 픽셀 수로 풀리는 것)이 메모리를 먹기 전에 끊긴다. 알파(RGBA·
    LA·투명 팔레트)는 검게 눌러 찍지 않고 흰 배경에 합성한다 — 카드 틀이 밝은 배경이라
    검정 대신 흰색이 자연스럽다.
    """
    if content_type not in ALLOWED_MIME:
        raise PhotoError("bad_mime", f"사진은 JPEG·PNG·WebP 만 받습니다 (받은 것: {content_type})")
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise PhotoError("too_large", f"사진은 비어 있지 않은 {MAX_PHOTO_BYTES // (1024 * 1024)} MiB 이하여야 합니다")
    try:
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > MAX_PIXELS:
            raise PhotoError("too_large", f"사진 해상도가 너무 큽니다 ({im.width}x{im.height})")
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, rgba)
        im = im.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PhotoError("undecodable", "사진을 읽을 수 없습니다") from exc
    im.thumbnail((max_side, max_side))  # 작은 사진은 키우지 않는다
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()
