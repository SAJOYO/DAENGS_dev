"""Android App Links 서명 지문 설정 — `DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS`.

**선택 기능이라, 값이 틀려도 백엔드를 세우지 않는다.** 이 값이 쓰이는 곳은
`/.well-known/assetlinks.json` 하나뿐이고, 비어 있으면 초대 링크가 앱 대신 웹 안내로
떨어질 뿐이다. 오타 하나로 인증·산책·케어 API 전체가 부팅에 실패하는 것은 과하다 —
카카오 앱 키·DB·암호화 키 같은 필수 보안 설정의 fail-fast(`config.py`)와 다르게 둔다.

그렇다고 조용히 넘기지도 않는다: 틀리면 **전부 버리고**(일부만 골라 싣지 않는다 — 어느
지문이 빠졌는지 모르는 채 검증이 반쯤 되는 편이 더 찾기 어렵다) 부팅 로그에 오류를 남기고
관리자 상태 페이지(`/admin/status`)에 항목으로 보인다.

사유 문장에는 **값을 싣지 않는다** — 몇 번째 원소인지만 말한다. 지문 자체는 공개값이지만,
이 자리에 다른 비밀을 잘못 붙여 넣었을 때 로그로 새면 안 된다.
"""

import json
import re
from dataclasses import dataclass

ENV_NAME = "DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS"

# `keytool -list -v` 가 찍는 SHA-256 인증서 지문 모양 — 대문자 16진수 32쌍, 콜론 구분.
# `assetlinks.json` 의 `sha256_cert_fingerprints` 도 이 모양이다.
_SHA256_FINGERPRINT = re.compile(r"^(?:[0-9A-F]{2}:){31}[0-9A-F]{2}$")


@dataclass(frozen=True)
class PlaySigningFingerprints:
    """읽은 결과. `error` 가 있으면 `fingerprints` 는 늘 비어 있다."""

    fingerprints: tuple[str, ...] = ()
    error: str | None = None


def parse_play_signing_fingerprints(raw: str | None) -> PlaySigningFingerprints:
    """원문 문자열을 읽는다. **예외를 던지지 않는다.**

    비어 있거나 `[]` 면 오류 없이 빈 결과다(설정 안 한 환경). JSON 이 아니거나, 배열이
    아니거나, 문자열이 아닌 원소가 있거나, 지문 모양이 아닌 원소가 **하나라도** 있으면
    전부 버리고 사유를 돌려준다. 공백은 버리고 소문자는 대문자로 맞춘다.
    """
    if raw is None or not raw.strip():
        return PlaySigningFingerprints()
    try:
        values = json.loads(raw)
    except ValueError:
        return _invalid("JSON 으로 읽을 수 없습니다")
    if not isinstance(values, list):
        return _invalid("JSON 배열이 아닙니다")

    cleaned: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str):
            return _invalid(f"{index}번째 원소가 문자열이 아닙니다")
        normalized = value.strip().upper()
        if not _SHA256_FINGERPRINT.fullmatch(normalized):
            return _invalid(
                # 예시를 'XX:XX:…' 로 둔다 — 'AA:BB' 같은 진짜 16진수 예시는 로그에서 입력값이 샌 것처럼 읽힌다.
                f"{index}번째 원소가 SHA-256 인증서 지문 모양('XX:XX:…' 콜론 구분 16진수 32쌍)이 아닙니다"
            )
        cleaned.append(normalized)
    return PlaySigningFingerprints(tuple(cleaned))


def _invalid(reason: str) -> PlaySigningFingerprints:
    return PlaySigningFingerprints(
        error=f"{ENV_NAME} 설정이 잘못돼 전부 무시합니다 — {reason}. assetlinks.json 은 빈 배열을 돌려줍니다."
    )
