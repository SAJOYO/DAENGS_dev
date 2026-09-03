/**
 * 관리자 콘솔이 백엔드를 부를 때 쓰는 fetch 래퍼.
 *
 * **주소를 박지 않고 `/api/...` 만 씁니다.** 로컬은 `next.config.ts` 의 rewrites,
 * 배포는 nginx 의 `/api/` 블록이 같은 오리진을 만들어 줍니다 (D-015).
 * `http://daengback.~:8000` 으로 직접 부르면 오리진이 갈려서 로그인은 되는데
 * httpOnly 쿠키가 브라우저에 남지 않습니다.
 */

const REFRESH_PATH = "/api/auth/refresh";

/**
 * 401 을 받아도 재발급하지 않는 경로.
 *
 * 이 둘의 401 은 "세션이 만료됐다"가 아니라 **자격 증명이 틀렸다**는 뜻이라,
 * 재발급해 봐야 결과가 같습니다. 로그인 실패마다 refresh 를 부르면 다른 탭에서
 * 멀쩡히 쓰고 있던 세션의 토큰을 괜히 회전시키게 됩니다.
 */
const NO_RETRY_PATHS = new Set([REFRESH_PATH, "/api/auth/login"]);

export class ApiError extends Error {
  readonly status: number;
  /** 429 일 때 서버가 알려 준 재시도까지 남은 초. */
  readonly retryAfter: number | null;
  /**
   * 파싱된 응답 본문 그대로. **`message` 로 요약되지 않는 정보가 여기 남습니다.**
   *
   * `detailOf` 는 `detail` 이 **문자열일 때만** 문구로 씁니다. 그런데 `/life/walk-conditions` 는
   * 기상청 격자가 통째로 없을 때 **503 + `detail` 에 응답 본문 전체**를 싣습니다 —
   * "200 으로 '모른다'를 주면 클라이언트가 정상 응답으로 다루므로, 대신 어느 출처가
   * 죽었는지(`sources`) 보이게 한다"는 계약입니다 (RT-001 ⑥).
   * 이 필드가 없으면 점검 화면에서 **정확히 그 정보가 사라집니다.**
   *
   * 타입은 `unknown` 입니다. 어떤 모양인지는 부르는 화면이 압니다.
   */
  readonly body: unknown;

  constructor(
    status: number,
    message: string,
    retryAfter: number | null = null,
    body: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
    this.body = body;
  }
}

/**
 * 진행 중인 재발급. **동시에 여러 요청이 401 을 받아도 refresh 는 한 번만 갑니다.**
 *
 * refresh 토큰은 쓰는 즉시 폐기되고 새것으로 회전합니다(D-015). 두 요청이 각자
 * 재발급을 부르면 나중 것이 앞 것이 막 받은 토큰을 무효화하고, 서버는 이를
 * 재사용(탈취)으로 보고 그 계정의 세션을 **전부** 끊습니다.
 */
let inFlightRefresh: Promise<boolean> | null = null;

function refreshSession(): Promise<boolean> {
  if (inFlightRefresh) return inFlightRefresh;

  const run = fetch(REFRESH_PATH, { method: "POST" })
    .then((response) => response.ok)
    // 네트워크가 끊긴 것과 토큰이 죽은 것을 여기서 구분하지 않습니다.
    // 어느 쪽이든 "이번 요청은 못 살린다"로 같습니다.
    .catch(() => false);

  inFlightRefresh = run;
  void run.finally(() => {
    if (inFlightRefresh === run) inFlightRefresh = null;
  });
  return run;
}

/** 세션이 끝났을 때 부를 곳. AuthProvider 가 마운트되면서 등록합니다. */
let onSessionExpired: (() => void) | null = null;

export function setSessionExpiredHandler(handler: (() => void) | null): void {
  onSessionExpired = handler;
}

/**
 * 401 을 만나면 조용히 재발급하고 **원래 요청을 한 번만** 다시 보냅니다.
 *
 * 재시도가 한 번인 이유: 재발급에 성공했는데도 또 401 이면 토큰 문제가 아니라
 * 계정이 정지됐거나 권한이 없는 것이라, 더 시도해도 결과가 같습니다.
 *
 * `init.body` 는 두 번 읽힙니다. 문자열·FormData 는 괜찮지만 **스트림을 넘기면
 * 재시도가 빈 본문으로 갑니다.** 지금 콘솔에서 쓸 일은 없습니다.
 */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const first = await fetch(path, init);
  if (first.status !== 401) return first;

  if (NO_RETRY_PATHS.has(path)) return first;

  const refreshed = await refreshSession();
  if (!refreshed) {
    onSessionExpired?.();
    return first;
  }

  const retried = await fetch(path, init);
  if (retried.status === 401) onSessionExpired?.();
  return retried;
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
    return body.detail;
  }
  return fallback;
}

function retryAfterOf(response: Response): number | null {
  const header = response.headers.get("retry-after");
  if (!header) return null;
  const seconds = Number.parseInt(header, 10);
  return Number.isFinite(seconds) ? seconds : null;
}

/** JSON 응답을 받는 요청. 실패하면 서버가 준 `detail` 을 담아 던집니다. */
export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, init);
  const body: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    throw new ApiError(
      response.status,
      detailOf(body, "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."),
      retryAfterOf(response),
      body,
    );
  }
  return body as T;
}
