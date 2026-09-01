"use client";

import AuthProvider from "../components/auth-provider";
import ConsoleShell from "../components/console-shell";

/**
 * 콘솔 아래 모든 화면은 로그인이 필요합니다.
 *
 * 1차로 `proxy.ts` 가 쿠키 없는 접근을 `/login` 으로 돌리고,
 * 여기서 `AuthProvider` 가 `/auth/me` 로 실제 세션과 권한을 확인합니다.
 */
export default function ConsoleLayout({ children }: LayoutProps<"/console">) {
  return (
    <AuthProvider>
      <ConsoleShell>{children}</ConsoleShell>
    </AuthProvider>
  );
}
