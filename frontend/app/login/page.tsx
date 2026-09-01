import { Suspense } from "react";

import LoginForm from "../components/login-form";

export const metadata = {
  title: "로그인 — DAENGS 관리자",
};

export default function LoginPage() {
  return (
    <div className="flex flex-1 items-center justify-center px-6 py-16 font-sans">
      <div className="w-full max-w-sm">
        <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
          내부 운영 도구
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          DAENGS 관리자 콘솔
        </h1>

        {/* useSearchParams 를 쓰는 폼이라 Suspense 로 감싸야 빌드가 통과합니다. */}
        <Suspense fallback={<div className="mt-10 h-56" />}>
          <LoginForm />
        </Suspense>
      </div>
    </div>
  );
}
