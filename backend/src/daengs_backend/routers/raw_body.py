"""요청 본문 원시 바이트를 크기 상한과 함께 받는다 — 사진 한 장을 쿼리 메타와 같이 받는 경로용.

이 저장소는 multipart 를 쓰지 않는다(`python-multipart` 없음). `/admin/cardimage/generate` 와
`/app/ai-cards` 가 같이 쓴다.

`Content-Length` 를 먼저 보고 넘으면 즉시 끊는다 — 다만 그 헤더는 클라이언트가 주는 값이라
**믿지 않고**, 청크마다 누적 크기를 다시 검사해 본문을 끝까지 받기 전에도 413 으로 끊는다.
`await request.body()` 로 통째로 받았다가 검사하면 큰 업로드가 메모리를 다 채운 뒤에야 거절된다.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status


def _too_large() -> HTTPException:
    return HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE, detail={"code": "too_large", "message": "사진이 너무 큽니다"}
    )


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"code": "bad_length", "message": "Content-Length가 올바르지 않습니다"},
            ) from None
        if declared_size > max_bytes:
            raise _too_large()

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise _too_large()
    return bytes(body)
