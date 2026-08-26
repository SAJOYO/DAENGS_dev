"""`uv run kakao-token` — 앱 없이 카카오 `id_token` 을 받아 우리 검증을 돌려 봅니다.

앱이 아직 없을 때 **콘솔 설정이 실제로 맞는지** 확인하는 용도입니다.
브라우저를 열어 카카오 로그인을 하고, 돌아온 인가 코드를 토큰으로 바꾼 뒤,
그 `id_token` 을 `core/kakao.py` 의 진짜 검증기에 그대로 넣어 봅니다.

    uv run kakao-token

**토큰이 이 PC 밖으로 나가지 않습니다.** 기본적으로 화면에도 찍지 않고 검증 결과만
보여 줍니다. `id_token` 은 실제 카카오 계정의 신원 증명이고 안에 이메일이 들어 있어서,
채팅이나 PR 에 붙여넣을 물건이 아닙니다. 정말 필요하면 `--print-token` 을 쓰세요.

## 미리 해 둘 것

이 스크립트는 **REST API 키**로 도는 웹 로그인 흐름을 씁니다. 그래서 콘솔에
Redirect URI 등록이 필요합니다.

    카카오 개발자 콘솔 → 내 애플리케이션 → 카카오 로그인 → Redirect URI
    http://localhost:8910/callback

**운영 흐름(네이티브 앱 SDK)에는 이 등록이 필요 없습니다** — 앱은 커스텀 스킴을 씁니다.
이건 앱 없이 테스트하려고 REST 키를 쓰기 때문에 필요한 것입니다 (D-017).

콘솔에서 **OpenID Connect 를 켜 두어야** 합니다. 안 켜져 있으면 `scope` 에
`openid` 를 넣어도 `id_token` 이 오지 않고, 이 스크립트가 그렇다고 알려 줍니다.

## KOE010 (invalid_client) 이 뜬다면

인가 코드는 받았는데 토큰 교환만 실패한 것입니다. 그 단계까지 왔다는 것은
REST API 키가 맞다는 뜻이므로, 원인은 거의 항상 **Client Secret** 입니다.
콘솔에서 켜 두었다면 `--client-secret` 을 붙여 다시 돌리세요.

**이건 이 스크립트만의 문제입니다.** 우리 서버는 인가 코드를 토큰으로 교환하지
않습니다 — 앱이 받아 온 id_token 을 검증만 하므로 Client Secret 과 무관합니다.

## KOE205 가 뜬다면

"설정하지 않은 동의 항목" 오류입니다. **켜지 않은 동의 항목을 요청**했다는 뜻이고,
사용자가 거절한 것과는 다릅니다 (로그인 화면이 아예 안 뜹니다).

기본 `scope` 는 `openid` 하나뿐이라 OIDC 만 켜져 있으면 걸리지 않습니다.
`--scope` 로 항목을 추가했다면 그 항목을 콘솔의 [카카오 로그인] → [동의항목]에서
먼저 켜세요. `openid` 만으로도 KOE205 가 뜬다면 **OIDC 가 안 켜진 것**입니다.
"""

import argparse
import asyncio
import getpass
import http.server
import sys
import threading
import urllib.parse
import webbrowser

import httpx

from daengs_backend.config import settings
from daengs_backend.core.kakao import KakaoIdTokenError, verify_id_token

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"

DEFAULT_PORT = 8910

# **콘솔에서 켜 둔 동의 항목만 요청할 수 있습니다.** 안 켠 것을 넣으면 로그인 화면
# 대신 KOE205("설정하지 않은 동의 항목")가 뜹니다 — 사용자가 거절하는 것과 다릅니다.
#
# 그래서 기본은 `openid` 하나입니다. id_token 을 받는 데 필요한 최소이고,
# OIDC 를 켰다면 별도 동의 항목 설정 없이 바로 됩니다.
#
# 이메일을 받으려면 콘솔의 [카카오 로그인] → [동의항목]에서 '카카오계정(이메일)'을
# 선택 동의로 켠 **뒤에** `--scope openid,account_email` 로 돌리세요.
# 안 켜도 됩니다 — 이메일 없이 가입되고 `email_enc`/`email_hash` 는 NULL 입니다.
DEFAULT_SCOPE = "openid"

# bytes 리터럴에는 한글을 못 넣습니다. str 로 두고 내보낼 때 인코딩합니다.
_DONE_PAGE = """<!doctype html><meta charset="utf-8">
<body style="font-family:sans-serif;padding:3rem">
<h2>받았습니다</h2><p>터미널로 돌아가세요. 이 창은 닫아도 됩니다.</p></body>""".encode()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """인가 코드를 한 번만 받고 끝나는 핸들러."""

    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802  (BaseHTTPRequestHandler 규약)
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        _CallbackHandler.code = (params.get("code") or [None])[0]
        _CallbackHandler.error = (
            params.get("error_description") or params.get("error") or [None]
        )[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_DONE_PAGE)

    def log_message(self, *args: object) -> None:
        """접근 로그를 끕니다. 여기에 인가 코드가 그대로 찍힙니다."""


def _wait_for_code(port: int, timeout: float) -> str:
    """로컬에 한 번만 받는 서버를 띄우고 인가 코드를 기다립니다."""
    _CallbackHandler.code = None
    _CallbackHandler.error = None

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    except OSError as exc:
        sys.exit(
            f"127.0.0.1:{port} 를 열 수 없습니다 ({exc}).\n"
            "다른 프로그램이 쓰고 있으면 --port 로 바꾸고, "
            "콘솔의 Redirect URI 도 같은 포트로 등록하세요."
        )

    server.timeout = timeout
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()

    if _CallbackHandler.error:
        sys.exit(f"카카오가 거절했습니다: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        sys.exit(
            f"{timeout:.0f}초 안에 인가 코드가 오지 않았습니다.\n"
            "브라우저에서 로그인을 끝냈는지, Redirect URI 가 콘솔에 등록되어 있는지 "
            "확인하세요."
        )
    return _CallbackHandler.code


def _exchange(code: str, redirect_uri: str, client_secret: str | None) -> dict:
    """인가 코드를 토큰으로 바꿉니다."""
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.kakao_rest_api_key,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    if client_secret:
        # 콘솔의 '보안 → Client Secret' 을 켰다면 필요합니다.
        data["client_secret"] = client_secret

    response = httpx.post(TOKEN_URL, data=data, timeout=10.0)
    if response.status_code == 200:
        return response.json()

    # 카카오의 오류 본문에는 우리 앱 키가 들어 있지 않습니다. 그대로 보여 줍니다.
    body = response.text
    hint = ""
    if "KOE010" in body and not client_secret:
        # 인가 코드까지 받았는데 교환만 실패했다는 것은 client_id 는 맞다는 뜻입니다.
        # 그 상태의 invalid_client 는 거의 항상 Client Secret 누락입니다.
        hint = """

인가 코드는 받았으니 REST API 키 자체는 맞습니다.
콘솔에서 **Client Secret 이 켜져 있을 때** 이 오류가 납니다:
  내 애플리케이션 → 카카오 로그인 → 보안 → Client Secret
켜져 있다면 `--client-secret` 을 붙여 다시 돌리세요 (값은 따로 물어봅니다)."""
    sys.exit(f"토큰 교환 실패 ({response.status_code}): {body}{hint}")


def _describe(token: str, *, print_token: bool) -> None:
    """우리 검증기를 그대로 돌려 결과를 보여 줍니다."""
    try:
        identity = asyncio.run(verify_id_token(token))
    except KakaoIdTokenError as exc:
        print(f"\n[실패] 우리 검증기가 거부했습니다: {exc}")
        print(
            "\n**바로 위에 찍힌 경고 줄에 진짜 이유가 있습니다.**"
            "\n응답 메시지는 일부러 뭉뚱그려 둡니다 — 무엇이 틀렸는지 알려 주면"
            "\n검증을 통과하는 조건을 알려 주는 셈이라서요."
            "\n"
            "\n자주 나오는 것:"
            "\n  aud 불일치      backend/.env 의 DAENGS_KAKAO_REST_API_KEY 가"
            "\n                  방금 로그인한 앱의 REST API 키인지 확인하세요."
            "\n  issued in the future / expired"
            "\n                  이 PC 시계가 어긋나 있습니다. 60초까지는 봐주지만"
            "\n                  그보다 크면 Windows 시간 동기화를 돌리세요."
            "\n  signature       공개키 문제입니다. 거의 나오지 않습니다."
        )
        raise SystemExit(1) from None

    print("\n[성공] 우리 검증기를 통과했습니다.")
    print(f"  회원번호(kakao_id) : {identity.kakao_id}")
    print(
        "  이메일             : "
        + (identity.email if identity.email else "(동의 안 함 — NULL 로 저장됩니다)")
    )
    print(f"  nonce              : {identity.nonce or '(없음)'}")
    print(
        "\n이제 이 회원번호로 POST /auth/app/kakao 가 회원을 만듭니다.\n"
        "실제로 찔러 보려면 --print-token 으로 토큰을 꺼내 쓰세요 "
        "(수명이 짧습니다)."
    )
    if print_token:
        print(f"\nid_token:\n{token}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="카카오 id_token 을 받아 우리 검증기로 확인합니다 (앱 없이).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"콜백을 받을 로컬 포트 (기본 {DEFAULT_PORT}). "
        "바꾸면 콘솔의 Redirect URI 도 같이 바꿔야 합니다.",
    )
    parser.add_argument(
        "--client-secret",
        action="store_true",
        help="콘솔에서 Client Secret 을 켰다면 붙이세요. **값은 여기 적지 않습니다** — "
        "붙이면 따로 물어봅니다 (셸 히스토리에 남지 않게).",
    )
    parser.add_argument(
        "--print-token",
        action="store_true",
        help="검증 결과와 함께 id_token 원문도 찍습니다. "
        "**실제 계정의 신원 증명입니다** — 어디에 붙여넣을지 생각하고 쓰세요.",
    )
    parser.add_argument(
        "--scope",
        default=DEFAULT_SCOPE,
        help=f"요청할 동의 항목 (기본 '{DEFAULT_SCOPE}'). "
        "**콘솔에서 켜 둔 것만** 넣을 수 있습니다. 이메일까지 받으려면 "
        "동의항목에서 켠 뒤 'openid,account_email'.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="브라우저 로그인을 기다릴 시간(초). 기본 180.",
    )
    args = parser.parse_args()

    # 값을 인자로 받지 않습니다. seed_admin.py 와 같은 이유입니다 — 인자로 받는 순간
    # 셸 히스토리와 프로세스 목록에 평문이 남습니다.
    client_secret = (
        getpass.getpass("Client Secret: ") if args.client_secret else None
    )

    redirect_uri = f"http://localhost:{args.port}/callback"
    query = urllib.parse.urlencode(
        {
            "client_id": settings.kakao_rest_api_key,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": args.scope,
        }
    )
    url = f"{AUTHORIZE_URL}?{query}"

    print(f"Redirect URI : {redirect_uri}")
    print(f"scope        : {args.scope}")
    print("  → 이 값이 카카오 콘솔에 **그대로** 등록되어 있어야 합니다.\n")
    print("브라우저를 엽니다. 열리지 않으면 아래 주소를 직접 여세요.\n")
    print(url + "\n")

    try:
        webbrowser.open(url)
    except (webbrowser.Error, OSError):
        pass  # 주소를 이미 찍었으니 손으로 열면 됩니다.

    print(f"로그인을 기다립니다... (최대 {args.timeout:.0f}초)")
    code = _wait_for_code(args.port, args.timeout)

    tokens = _exchange(code, redirect_uri, client_secret)
    id_token = tokens.get("id_token")
    if not id_token:
        sys.exit(
            "응답에 id_token 이 없습니다.\n\n"
            "카카오 콘솔에서 **OpenID Connect 가 꺼져 있을 때** 이렇게 됩니다.\n"
            "  내 애플리케이션 → 카카오 로그인 → OpenID Connect → 활성화\n"
            f"(scope 는 '{args.scope}' 로 보냈습니다)"
        )

    _describe(id_token, print_token=args.print_token)


if __name__ == "__main__":
    main()
