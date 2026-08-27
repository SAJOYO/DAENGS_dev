"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { apiFetch, apiJson, setSessionExpiredHandler } from "@/lib/api";
import type { AdminSession, Permission } from "@/lib/auth";

type AuthState =
  /** 아직 `/auth/me` 응답을 못 받았습니다. 이때 화면을 그리면 권한 없는 메뉴가 깜빡입니다. */
  | { status: "loading"; admin: null }
  | { status: "authenticated"; admin: AdminSession }
  | { status: "anonymous"; admin: null };

type AuthContextValue = AuthState & {
  /** 권한 하나를 가졌는지. **메뉴를 가리는 용도일 뿐 차단이 아닙니다.** */
  can: (permission: Permission) => boolean;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * 지금 로그인한 관리자를 한 번 읽어 두고 아래로 내려 줍니다.
 *
 * **토큰을 까서 알아내는 것이 아니라 서버에 물어봅니다.** 토큰은 httpOnly 쿠키라
 * JS 가 읽을 수 없고, 읽더라도 JWE 라 열 수 없습니다. `role` 도 토큰 안의 값은
 * 최대 5분 낡을 수 있어서, 권한을 내린 사람에게 잠시 버튼이 보입니다.
 *
 * 서버 컴포넌트에서 부르지 않는 이유: 쿠키를 손으로 헤더에 실어야 하고, 절대 URL 이
 * 필요해서 `next.config.ts` 의 rewrites 를 못 타 개발·배포 주소가 갈립니다.
 */
export default function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<AuthState>({ status: "loading", admin: null });
  // StrictMode 가 개발에서 effect 를 두 번 돌립니다. 언마운트된 뒤의 setState 를 막습니다.
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    apiJson<AdminSession>("/api/auth/me")
      .then((admin) => {
        if (alive.current) setState({ status: "authenticated", admin });
      })
      .catch(() => {
        if (alive.current) setState({ status: "anonymous", admin: null });
      });
    return () => {
      alive.current = false;
    };
  }, []);

  // 재발급까지 실패했을 때(`lib/api.ts`) 상태를 내려놓습니다. 화면 전환은 아래 effect 가 합니다.
  useEffect(() => {
    setSessionExpiredHandler(() => {
      if (alive.current) setState({ status: "anonymous", admin: null });
    });
    return () => setSessionExpiredHandler(null);
  }, []);

  // `proxy.ts` 는 쿠키가 있는지만 봅니다. 쿠키는 있는데 계정이 정지됐거나 토큰이 죽은
  // 경우는 여기서 걸립니다.
  useEffect(() => {
    if (state.status === "anonymous") router.replace("/login");
  }, [state.status, router]);

  const signOut = useCallback(async () => {
    // 실패해도 화면은 로그인으로 보냅니다. 서버는 쿠키가 없어도 204 를 주고,
    // 여기서 멈춰 서면 사용자는 로그아웃이 안 된 것처럼 보는 화면에 갇힙니다.
    await apiFetch("/api/auth/logout", { method: "POST" }).catch(() => null);
    setState({ status: "anonymous", admin: null });
    router.replace("/login");
    // 쿠키가 사라진 것을 `proxy.ts` 가 다시 보게 합니다.
    router.refresh();
  }, [router]);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      can: (permission) => state.admin?.permissions.includes(permission) ?? false,
      signOut,
    }),
    [state, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth 는 AuthProvider 안에서만 쓸 수 있습니다.");
  return value;
}
