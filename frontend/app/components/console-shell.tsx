"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";

import { useAuth } from "./auth-provider";

/** `03_auth.sql` 의 role 5단계. 화면에는 한국어로 보여 줍니다. */
const ROLE_LABEL: Record<string, string> = {
  ADMIN: "관리자",
  OPERATOR: "운영",
  CURATOR: "지식 관리",
  ANALYST: "분석",
  VIEWER: "조회",
};

/**
 * 콘솔의 껍데기. 헤더와 로그아웃, 그리고 `/auth/me` 를 기다리는 동안의 빈 화면.
 *
 * `proxy.ts` 가 쿠키만 보고 통과시켰을 수 있으므로, 여기서 응답을 기다리는 동안은
 * 아무것도 그리지 않습니다. 먼저 그렸다가 지우면 권한 없는 메뉴가 한 번 보입니다.
 */
export default function ConsoleShell({ children }: { children: ReactNode }) {
  const { status, admin, signOut } = useAuth();
  const [isSigningOut, setIsSigningOut] = useState(false);

  if (status !== "authenticated" || !admin) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-zinc-500 dark:text-zinc-400">
        {status === "loading" ? "세션을 확인하는 중입니다…" : "로그인 화면으로 이동합니다…"}
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col font-sans">
      <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-black/80">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between gap-4 px-6">
          <Link href="/console" className="text-lg font-semibold tracking-tight">
            DAENGS
            <span className="ml-2 text-sm font-normal text-zinc-500 dark:text-zinc-400">
              관리자
            </span>
          </Link>
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden text-zinc-600 sm:inline dark:text-zinc-400">
              {admin.name}
            </span>
            <span className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
              {ROLE_LABEL[admin.role] ?? admin.role}
            </span>
            <button
              type="button"
              onClick={() => {
                setIsSigningOut(true);
                void signOut();
              }}
              disabled={isSigningOut}
              className="rounded-full border border-zinc-300 px-4 py-1.5 text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
            >
              로그아웃
            </button>
          </div>
        </div>
      </header>

      <main className="flex-1">{children}</main>
    </div>
  );
}
