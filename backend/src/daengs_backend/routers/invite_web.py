"""공동 돌봄 초대 링크의 웹 폴백 — `daengapi.weareithero.cloud` 가 서빙한다.

⚠️ **여기서 새 익명 초대 미리보기 API 를 만들지 않는다.** `/app/pet-invites/preview` 는
로그인이 필요해서(`CurrentAppUser`, `pet_member.py`) 이 페이지는 보낸 사람·강아지
이름을 알 방법이 없다. 그래서 정적 안내만 하고, 실제 초대 내용은 앱에서 로그인한
뒤에만(`InviteAcceptScreen`) 보여준다 — 이 라우터는 `/app/pet-invites/*` 계약을
전혀 건드리지 않는다.

토큰은 링크의 프래그먼트(`#TOKEN`)에만 있고 서버로 오지 않는다 — 앱 쪽
`InviteLink.kt` 와 같은 이유다. 이 페이지도 토큰을 읽거나 로그에 남기지 않는다.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from daengs_backend.config import settings

router = APIRouter(tags=["invite-web"], include_in_schema=False)

# 앱 쪽 `app/build.gradle.kts` 의 `applicationId`(release, 접미사 없음)와 반드시 같다.
# `assetlinks.json` 의 package_name 도 이 값이다.
_APP_PACKAGE = "com.daengs.app"
_PLAY_STORE_URL = f"https://play.google.com/store/apps/details?id={_APP_PACKAGE}"

# 앱 쪽 `InviteLink.HOST`·`PATH` 와 반드시 같다 — 갈라지면 이 페이지가 만드는
# `intent://` 폴백 링크가 앱의 intent-filter 와 안 맞는다.
_INVITE_HOST = "daengapi.weareithero.cloud"
_INVITE_PATH = "/invite"

# 「앱에서 초대 열기」가 토큰을 싣는 Intent extra 이름. 앱 쪽 `InviteLink.WEB_FALLBACK_EXTRA`
# 와 반드시 같다 — 갈라지면 앱이 열리기는 하는데 토큰을 못 찾아 빈 붙여넣기 화면이 뜬다.
# 토큰을 URL(쿼리·경로)에 두지 않으려고 extra 로 싣는다 — 아래 페이지 스크립트 주석 참고.
_WEB_FALLBACK_EXTRA = "com.daengs.app.extra.INVITE_TOKEN"

_INVITE_HTML = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>댕스 공동 돌봄 초대</title>
<style>
  :root {{
    color-scheme: light;
    --pink: #ff8fab;
    --pink-deep: #e26a8a;
    --cream: #fff7f2;
    --card: #ffffff;
    --text-dark: #3a2c2c;
    --text-muted: #8a7a7a;
    --pink-faint: #ffe7ee;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    min-height: 100vh;
    background: var(--cream);
    color: var(--text-dark);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo",
      "Noto Sans KR", sans-serif;
    display: flex;
    justify-content: center;
    padding: 32px 16px;
  }}
  main {{
    width: 100%;
    max-width: 420px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }}
  h1 {{
    font-size: 19px;
    margin: 8px 0 0;
  }}
  p.lead {{
    color: var(--text-muted);
    font-size: 14px;
    margin: 0 0 6px;
    line-height: 1.5;
  }}
  .card {{
    background: var(--card);
    border-radius: 16px;
    padding: 16px;
  }}
  .guidance {{
    background: var(--pink-faint);
    border-radius: 12px;
    padding: 14px;
    font-size: 13px;
    color: var(--text-dark);
    line-height: 1.6;
  }}
  button, a.button {{
    display: block;
    width: 100%;
    border: none;
    border-radius: 14px;
    padding: 14px 16px;
    font-size: 15px;
    font-weight: 700;
    text-align: center;
    text-decoration: none;
    cursor: pointer;
    box-sizing: border-box;
  }}
  .primary {{
    background: var(--pink);
    color: #fff;
  }}
  .secondary {{
    background: var(--card);
    color: var(--pink-deep);
    border: 1.5px solid var(--pink);
  }}
  .ghost {{
    background: transparent;
    color: var(--text-muted);
    font-weight: 600;
    font-size: 13px;
    padding: 8px;
  }}
  details {{
    font-size: 13px;
    color: var(--text-muted);
  }}
  summary {{
    cursor: pointer;
    font-weight: 600;
    padding: 6px 0;
  }}
  #full-link {{
    width: 100%;
    word-break: break-all;
    background: var(--card);
    border: 1px solid var(--pink-faint);
    border-radius: 10px;
    padding: 10px;
    font-size: 12px;
    color: var(--text-dark);
    user-select: all;
    -webkit-user-select: all;
  }}
  #status {{
    font-size: 12px;
    color: var(--pink-deep);
    min-height: 16px;
  }}
  #bad-link {{
    display: none;
    font-size: 13px;
    color: var(--text-muted);
  }}
  [hidden] {{ display: none !important; }}
</style>
</head>
<body>
<main>
  <h1>🐶 댕스 공동 돌봄 초대</h1>
  <p class="lead">강아지의 공동 보호자로 초대받았어요. 앱에서 초대 내용을 확인하고 수락할 수 있어요.</p>

  <div id="bad-link" class="card">
    <p style="margin:0;color:var(--text-muted);font-size:13px;">
      이 페이지만으로는 초대 링크를 확인할 수 없어요. 카카오톡에서 받은 초대장을 다시 눌러 주세요.
    </p>
  </div>

  <div id="actions" class="card" style="display:flex;flex-direction:column;gap:10px;">
    <a id="install-btn" class="button primary" href="{_PLAY_STORE_URL}">앱 설치하기</a>
    <button id="open-app-btn" class="button secondary" type="button">이미 설치했나요? 앱에서 초대 열기</button>
    <button id="copy-btn" class="button ghost" type="button">초대 링크 복사</button>
    <div id="status" role="status" aria-live="polite"></div>
    <details id="manual-link">
      <summary>복사가 안 되나요? 링크 직접 보기</summary>
      <p style="margin:6px 0 4px;">아래 링크 전체를 손으로 선택해 복사해 주세요.</p>
      <div id="full-link"></div>
    </details>
  </div>

  <div class="guidance">
    <strong>설치를 막 마쳤다면</strong><br>
    스토어의 "열기"만으로는 이 초대가 이어지지 않아요. <strong>카카오톡의 초대 메시지를
    다시 눌러</strong> 이 페이지로 돌아온 뒤 위의 "이미 설치했나요? 앱에서 초대 열기"를
    눌러 주세요.<br><br>
    <strong>앱이 열리지 않나요?</strong><br>
    카카오톡 안에서 연 화면에서는 버튼으로 앱이 안 열릴 수 있어요. 그때는 "초대 링크
    복사"로 링크를 복사한 뒤, 댕스 앱의 <strong>내 화면 → 받은 초대 링크 넣기</strong>에
    붙여넣어 주세요.
  </div>
</main>

<script>
(function () {{
  "use strict";

  // 토큰은 프래그먼트에만 있고 서버로 온 적이 없다 — 여기서도 어디로도 보내지 않는다.
  var TOKEN_RE = /^[A-Za-z0-9_-]{{1,200}}$/;
  var hash = window.location.hash || "";
  var token = hash.length > 1 ? hash.slice(1) : "";
  var valid = TOKEN_RE.test(token);

  var actions = document.getElementById("actions");
  var badLink = document.getElementById("bad-link");
  var openAppBtn = document.getElementById("open-app-btn");
  var copyBtn = document.getElementById("copy-btn");
  var manualLink = document.getElementById("manual-link");
  var fullLinkEl = document.getElementById("full-link");
  var statusEl = document.getElementById("status");

  if (!valid) {{
    // 누락·잘못된 토큰에는 초대 열기 동작을 활성화하지 않는다.
    actions.hidden = true;
    badLink.style.display = "block";
    return;
  }}

  var fullLink = "https://{_INVITE_HOST}{_INVITE_PATH}#" + token;
  fullLinkEl.textContent = fullLink;

  // "이미 설치했나요? 앱에서 초대 열기" — Chrome 의 intent:// 문법으로 패키지를 못박아
  // 연다 (https://developer.chrome.com/docs/android/intents).
  //
  // ⚠️ 토큰은 URL 이 아니라 Intent extra(`S.<이름>=`)에 싣는다. intent:// 는
  // `#Intent;...;end` 를 자기 문법으로 쓰기 때문에 진짜 URL 프래그먼트(토큰)를 실을
  // 자리가 없고, 쿼리·경로에 두면 토큰이 URL 의 일부가 된다. extra 로 실으면 데이터
  // URI 는 토큰 없는 `https://…/invite` 그대로라 앱의 intent-filter 와 맞고, 앱은
  // 그 이름의 extra 에서만 토큰을 읽는다. Chrome 이 앱에 안 넘기는 extra 는
  // browser_fallback_url 하나뿐이다(같은 문서).
  //
  // 네트워크로는 안 나간다: intent:// 는 브라우저가 로컬에서 안드로이드 Intent 로
  // 바꾸는 문자열일 뿐이고, 리졸브에 실패했을 때만(앱이 없을 때) 별도의 **토큰 없는**
  // S.browser_fallback_url(스토어)로 실제 요청이 나간다.
  //
  // ⚠️ 열린다고 단정하지 않는다. Chrome(과 그 기반 브라우저)이 사용자 제스처 안에서만
  // 처리하고, 카카오톡 인앱 브라우저 같은 WebView 는 이 문법을 아예 못 알아들을 수
  // 있다 — 그때는 이 버튼이 조용히 아무 일도 안 한다. 그래서 복사·직접 보기 경로를
  // 늘 같이 보여 주고, 아래 안내도 그 길을 말한다.
  openAppBtn.addEventListener("click", function () {{
    var fallback = encodeURIComponent("{_PLAY_STORE_URL}");
    var intentUrl = "intent://{_INVITE_HOST}{_INVITE_PATH}#Intent;scheme=https;package={_APP_PACKAGE};" +
      "S.{_WEB_FALLBACK_EXTRA}=" + encodeURIComponent(token) +
      ";S.browser_fallback_url=" + fallback + ";end";
    window.location.href = intentUrl;
  }});

  // 클립보드는 사용자가 이 버튼을 눌렀을 때만 쓴다 — 자동으로 읽거나 쓰지 않는다.
  copyBtn.addEventListener("click", function () {{
    function done(ok) {{
      statusEl.textContent = ok ? "복사했어요." : "복사에 실패했어요. 아래에서 직접 복사해 주세요.";
      if (!ok) manualLink.open = true;
    }}
    if (navigator.clipboard && window.isSecureContext) {{
      navigator.clipboard.writeText(fullLink).then(function () {{ done(true); }}, function () {{ done(false); }});
    }} else {{
      done(false);
    }}
  }});
}})();
</script>
</body>
</html>
"""


@router.get("/invite", response_class=HTMLResponse)
async def invite_web() -> HTMLResponse:
    """초대 링크의 웹 폴백 — 정적 페이지다. 서버는 토큰을 보지 않는다(프래그먼트).

    `text/html`. 캐시하지 않는다 — 페이지 자체는 안 바뀌지만, 프록시·브라우저가
    구버전을 오래 들고 있으면 버튼 문구를 고쳐도 안 보인다.
    """
    return HTMLResponse(content=_INVITE_HTML, headers={"Cache-Control": "no-store"})


@router.get("/.well-known/assetlinks.json")
async def assetlinks() -> JSONResponse:
    """Android App Links 검증. `DAENGS_PLAY_SIGNING_SHA256_FINGERPRINTS` 가 비어 있으면
    빈 배열을 준다 — **검증은 그냥 실패하고, 링크는 이 웹 페이지로 떨어진다.** 가짜
    지문을 채워 넣지 않는다 (`config.py` 의 설정 주석 참고).

    ⚠️ 이 응답 하나로 "열린다"가 되지 않는다 — 안드로이드가 **설치 시점에** 이 파일을
    받아 APK 의 서명 지문과 대조해야 하고, 그 지문이 스토어 배포본의 서명(Play App
    Signing 키)이어야 한다. 검증 상태는 기기에서 `adb shell pm get-app-links
    com.daengs.app` 으로 본다.
    """
    if not settings.play_signing_sha256_fingerprints:
        return JSONResponse(content=[])

    statements = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": _APP_PACKAGE,
                "sha256_cert_fingerprints": settings.play_signing_sha256_fingerprints,
            },
        }
    ]
    return JSONResponse(content=statements)
