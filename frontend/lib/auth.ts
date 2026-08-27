/**
 * 관리자 세션의 타입과 권한 이름.
 *
 * **화면에서 숨기는 것은 UX 일 뿐 보안이 아닙니다.** 실제 차단은 백엔드의
 * `core/deps.py` 가 하고, 여기 있는 값은 메뉴를 가리는 데에만 씁니다.
 * 권한 이름은 그 파일의 `Perm` 과 문자열이 같아야 합니다 — 한쪽만 고치면
 * 조용히 "권한 없음"으로 보입니다.
 */

/** `core/deps.py` 의 `Perm`. role 이 아니라 이것으로 메뉴를 가릅니다. */
export type Permission =
  | "admin:manage"
  | "pii:read"
  | "ops:write"
  | "kb:write"
  | "search:inspect"
  | "metrics:read"
  | "read";

/**
 * `GET /api/auth/me` 의 응답 (`schemas/auth.py` 의 `MeResponse`).
 *
 * **토큰이 JWE 라 프론트가 열어 볼 수 없습니다.** 자기 권한을 아는 통로는
 * 이 엔드포인트뿐이고, 서버는 매번 DB 를 다시 읽어 줍니다.
 */
export type AdminSession = {
  admin_id: string;
  login_id: string;
  name: string;
  role: string;
  permissions: Permission[];
  last_login_at: string | null;
};

/** `POST /api/auth/login` 의 응답. **토큰은 여기 없고 쿠키로 갔습니다.** */
export type SessionResponse = {
  admin_id: string;
  role: string;
  access_expires_at: string;
  refresh_expires_at: string;
};

/**
 * 로그인 여부를 판단하는 쿠키 이름 (`routers/auth.py` 의 `REFRESH_COOKIE`).
 *
 * **`daengs_access` 가 아닙니다.** access 는 max-age 가 5분이라 브라우저가
 * 지워 버립니다. 그것으로 판단하면 5분마다 로그인 화면으로 튕깁니다.
 * refresh 쪽이 살아 있으면 조용히 재발급받을 수 있는 상태입니다.
 */
export const SESSION_COOKIE = "daengs_refresh";

/** 로그인 후 돌아갈 기본 자리. */
export const CONSOLE_HOME = "/console";
