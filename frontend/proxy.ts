import { NextResponse, type NextRequest } from "next/server";

import { CONSOLE_HOME, SESSION_COOKIE } from "@/lib/auth";

/**
 * 로그인하지 않은 사람이 콘솔 화면을 여는 것을 막는 **1차 필터**.
 *
 * 파일 이름이 `middleware.ts` 가 아닌 이유: Next 16 에서 그 규약이 deprecated 되고
 * `proxy.ts` + `proxy` 라는 이름의 export 로 바뀌었습니다. 하는 일은 같습니다.
 *
 * 여기서 하는 일은 쿠키가 있느냐뿐입니다. access 는 JWE 라 여기서 열어 볼 수
 * 없고(키도 DB 도 여기 없습니다), refresh 는 애초에 불투명 토큰입니다.
 * **진짜 판단은 `GET /api/auth/me` 응답**이고, 이 파일은 로그인도 안 한 사람에게
 * 콘솔 뼈대를 그렸다가 지우는 깜빡임을 없애는 역할만 합니다.
 *
 * 데이터를 지키는 것은 백엔드입니다. 여기를 지나쳐 콘솔이 열려도 API 가
 * 401 · 403 을 주므로 화면에 남의 데이터가 뜨지 않습니다.
 */
export function proxy(request: NextRequest) {
  const signedIn = request.cookies.has(SESSION_COOKIE);
  const { pathname, search } = request.nextUrl;

  if (pathname === "/login") {
    if (!signedIn) return NextResponse.next();
    return NextResponse.redirect(new URL(safeNext(request) ?? CONSOLE_HOME, request.url));
  }

  if (signedIn) return NextResponse.next();

  // 로그인 후 원래 열려던 곳으로 돌려보내기 위해 경로를 들고 갑니다.
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  url.searchParams.set("next", `${pathname}${search}`);
  return NextResponse.redirect(url);
}

/**
 * `?next=` 를 그대로 믿지 않습니다. `//evil.com` 이나 `https://evil.com` 을 넣어
 * 두면 로그인 직후 남의 사이트로 튕기는 open redirect 가 됩니다.
 * **우리 사이트 안의 절대 경로만** 통과시킵니다.
 */
function safeNext(request: NextRequest): string | null {
  const value = request.nextUrl.searchParams.get("next");
  if (!value) return null;
  if (!value.startsWith("/") || value.startsWith("//")) return null;
  return value;
}

export const config = {
  // **`/api` 와 `public/` 정적 파일은 여기 없습니다.**
  // `/api/*` 까지 걸면 fetch 가 401 대신 로그인 화면 HTML 을 받아서, 재발급 후
  // 재시도하는 처리(`lib/api.ts`)가 통째로 안 돕니다.
  // `/` 랜딩과 훈련 챗봇 데모는 로그인 없이 열어 둡니다.
  matcher: ["/console", "/console/:path*", "/login"],
};
