"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";

import { useAuth } from "./auth-provider";
import { ROLE_LABEL } from "@/lib/auth";

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
            {/*
              이름과 등급이 통째로 `/console/password` 로 가는 링크입니다 (#222).

              **이름만 링크로 만들면 안 됩니다.** 이름 쪽은 `sm:inline` 이라 좁은 화면에서
              사라지고, 그러면 폰에서 비밀번호를 바꿀 입구가 아예 없어집니다. 항상 보이는
              등급 배지까지 묶어야 어느 폭에서도 누를 것이 남습니다.

              대시보드(`console/page.tsx`)의 카드로 두지 않은 이유: 저 카드들은 권한으로
              갈리는 메뉴인데, 이 화면은 `read` 라 누구나 들어옵니다. 카드로 두면 "권한이
              있어서 보이는 것"과 "누구나 되는 것"이 같은 자리에 섞입니다.
            */}
            <Link
              href="/console/password"
              title="비밀번호 변경"
              className="flex items-center gap-3 rounded-full transition-opacity hover:opacity-70"
            >
              <span className="hidden text-zinc-600 sm:inline dark:text-zinc-400">
                {admin.name}
              </span>
              <span className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
                {ROLE_LABEL[admin.role] ?? admin.role}
              </span>
            </Link>
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
