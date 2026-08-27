"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError, apiJson } from "@/lib/api";
import { CONSOLE_HOME, type SessionResponse } from "@/lib/auth";

function messageFor(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
  }
  if (error.status === 429 && error.retryAfter !== null) {
    // 서버가 준 Retry-After 는 초 단위입니다. 분 단위로 보여 주면 "곧 될 것 같은데
    // 안 되는" 시간이 생겨서, 1분 미만은 초로 그대로 둡니다.
    const minutes = Math.ceil(error.retryAfter / 60);
    const wait = error.retryAfter < 60 ? `${error.retryAfter}초` : `${minutes}분`;
    return `로그인 시도가 너무 많습니다. ${wait} 후 다시 시도해 주세요.`;
  }
  return error.message;
}

/**
 * 아이디·비밀번호로 로그인합니다. **응답에는 토큰이 없고 쿠키로 옵니다.**
 *
 * 실패 메시지는 서버가 준 것을 그대로 씁니다. 백엔드가 "아이디가 없음"과
 * "비밀번호가 틀림"을 일부러 같은 문장으로 주고 있어서(계정 존재 여부가 새지
 * 않게), 프론트에서 나눠 쓰면 그 의도가 깨집니다.
 */
export default function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting) return;

    setIsSubmitting(true);
    setError(null);
    try {
      await apiJson<SessionResponse>("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login_id: loginId, password }),
      });
      // replace 인 이유: 뒤로 가기로 로그인 화면에 돌아오면 `proxy.ts` 가 다시
      // 콘솔로 보내서, 사용자에게는 뒤로 가기가 먹지 않는 것처럼 보입니다.
      router.replace(safeNext(searchParams.get("next")) ?? CONSOLE_HOME);
      // 새로 생긴 쿠키를 `proxy.ts` 가 보게 합니다.
      router.refresh();
    } catch (caught) {
      setError(messageFor(caught));
      setPassword("");
      setIsSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-10 flex flex-col gap-4">
      <label className="flex flex-col gap-2 text-sm">
        <span className="text-zinc-600 dark:text-zinc-400">아이디</span>
        <input
          name="login_id"
          value={loginId}
          onChange={(event) => setLoginId(event.target.value)}
          autoComplete="username"
          autoFocus
          required
          maxLength={50}
          className="rounded-lg border border-zinc-300 bg-transparent px-4 py-2.5 outline-none focus:border-zinc-500 dark:border-zinc-700 dark:focus:border-zinc-500"
        />
      </label>

      <label className="flex flex-col gap-2 text-sm">
        <span className="text-zinc-600 dark:text-zinc-400">비밀번호</span>
        <input
          type="password"
          name="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          required
          maxLength={1024}
          className="rounded-lg border border-zinc-300 bg-transparent px-4 py-2.5 outline-none focus:border-zinc-500 dark:border-zinc-700 dark:focus:border-zinc-500"
        />
      </label>

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
        >
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={isSubmitting}
        className="mt-2 rounded-lg bg-zinc-900 px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50 dark:bg-white dark:text-zinc-900"
      >
        {isSubmitting ? "확인 중…" : "로그인"}
      </button>
    </form>
  );
}

/**
 * `?next=` 를 그대로 믿지 않습니다. `//evil.com` 처럼 오리진을 바꾸는 값이 들어오면
 * 로그인 직후 남의 사이트로 보내는 open redirect 가 됩니다 (`proxy.ts` 와 같은 규칙).
 */
function safeNext(value: string | null): string | null {
  if (!value) return null;
  if (!value.startsWith("/") || value.startsWith("//")) return null;
  return value;
}
