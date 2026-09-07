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

/**
 * `03_auth.sql` 의 role 5단계. **순서가 권한이 넓은 쪽부터입니다** — 계정 발급
 * 화면의 선택지가 이 순서로 그려집니다.
 *
 * 값은 백엔드 `models/admin_user.py` 의 `ADMIN_ROLES` 와 같아야 합니다. 어긋나면
 * 화면에서는 고를 수 있는데 서버가 422 로 막습니다.
 */
export const ADMIN_ROLES = [
  "ADMIN",
  "OPERATOR",
  "CURATOR",
  "ANALYST",
  "VIEWER",
] as const;

export type AdminRole = (typeof ADMIN_ROLES)[number];

/**
 * 화면에 보여 줄 한국어 이름과 한 줄 설명.
 *
 * **설명이 붙어 있는 이유는 계정을 발급하는 사람이 고르는 자리이기 때문입니다.**
 * `CURATOR` 와 `ANALYST` 의 차이는 이름만 봐서는 안 보이고, 잘못 고르면 개인정보를
 * 볼 수 있는 계정을 무심코 내주게 됩니다. 내용은 `core/deps.py` 의
 * `ROLE_PERMISSIONS` 와 같이 고쳐야 합니다.
 */
export const ROLE_LABEL: Record<string, string> = {
  ADMIN: "관리자",
  OPERATOR: "운영",
  CURATOR: "지식 관리",
  ANALYST: "분석",
  VIEWER: "조회",
};

export const ROLE_HINT: Record<AdminRole, string> = {
  ADMIN: "전부. 계정 발급과 권한 변경까지 이 등급만 할 수 있습니다.",
  OPERATOR: "운영 데이터와 개인정보 복호화. 계정 관리만 빠집니다.",
  CURATOR: "지식베이스와 검색 점검. 개인정보는 볼 수 없습니다.",
  ANALYST: "지표와 검색 점검 조회. 쓰기가 없습니다.",
  VIEWER: "조회만. 개인정보는 가려서 보입니다.",
};
