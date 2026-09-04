"use client";

import { useState } from "react";

import { useAuth } from "./auth-provider";
import { apiFetch, ApiError } from "@/lib/api";

/**
 * `AdminAccountCreate` · `PasswordChangeRequest` 의 최소 길이. 서버(422)와 같은 값이라
 * 화면에서 먼저 걸립니다 — 발급 화면(`admin-accounts-console.tsx`)과 같은 상수입니다.
 * 한쪽만 고치면 화면은 통과시키는데 서버가 422 로 막습니다.
 */
const MIN_PASSWORD = 12;

const EMPTY = { current: "", next: "", confirm: "" };

/**
 * 본인 비밀번호 변경 (콘솔 로드맵 A3-1 · #222).
 *
 * **여기서 하는 검사는 전부 UX 일 뿐입니다.** 실제로 막는 것은 서버입니다 — 현재
 * 비밀번호 대조도, 12자도, "같은 값으로는 못 바꾼다"도 `services/admin_account.py` 와
 * `schemas/admin_account.py` 가 정합니다. 화면이 먼저 거는 이유는 422 의 detail 이
 * 문자열이 아니라 목록이라 **서버가 왜 막았는지 사용자에게 안 보이기** 때문입니다
 * (`lib/api.ts` 의 `detailOf` 는 문자열일 때만 씁니다).
 *
 * 목록 화면들과 달리 **불러올 것이 없습니다.** `useEffect` 도 `alive` ref 도 없는
 * 이유가 그것입니다 — 이 화면은 읽지 않고 쓰기만 합니다.
 */
export default function OwnPasswordConsole() {
  const { admin, signOut } = useAuth();

  const [form, setForm] = useState(EMPTY);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** 성공한 뒤 `signOut()` 이 화면을 넘기는 동안 폼을 잠급니다. */
  const [done, setDone] = useState(false);

  function set(key: keyof typeof form, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setNotice(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();

    // 서버로 보내지 않고 여기서 끝냅니다. 확인 칸은 **서버가 모르는 값**이고
    // (`PasswordChangeRequest` 에 없습니다), 오타를 잡는 것이 목적입니다.
    if (form.next !== form.confirm) {
      setNotice("새 비밀번호와 확인이 다릅니다.");
      return;
    }
    // 서버도 422 로 막습니다. 다만 그 422 의 detail 이 목록이라 화면에는 기본 문구만
    // 남아서, 왜 막혔는지 여기서 말해 줍니다.
    if (form.next === form.current) {
      setNotice("새 비밀번호가 지금 쓰는 것과 같습니다.");
      return;
    }

    setBusy(true);
    setNotice(null);
    try {
      // **204 라 본문이 없습니다.** `apiJson` 을 쓰면 빈 본문을 파싱하려다 헛돌므로
      // `apiFetch` 로 직접 받습니다.
      const res = await apiFetch("/api/admin/admins/me/password", {
        method: "PATCH",
        // 빼면 브라우저가 `text/plain` 을 달고 FastAPI 가 본문을 파싱하지 않습니다.
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: form.current,
          new_password: form.next,
        }),
      });

      if (!res.ok) {
        const body: unknown = await res.json().catch(() => null);
        const detail =
          body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
            ? body.detail
            : "비밀번호를 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.";
        throw new ApiError(res.status, detail);
      }

      // **입력한 값을 즉시 지웁니다.** 성공하면 곧 로그인 화면으로 가지만, 그 사이
      // 폼에 새 비밀번호가 남아 있을 이유가 없습니다.
      setForm(EMPTY);
      setDone(true);
      setNotice("비밀번호를 바꿨습니다. 다시 로그인해 주세요.");

      // 서버가 `refresh_tokens` 를 이미 지웠지만 **브라우저의 쿠키는 그대로**이고,
      // access 쿠키는 최대 5분 더 유효합니다 (D-015). 그냥 `/login` 으로 보내면
      // `proxy.ts` 가 쿠키를 보고 다시 콘솔로 돌려보냅니다 — `signOut()` 이
      // `POST /api/auth/logout` 으로 쿠키까지 지우고 화면을 넘깁니다.
      await signOut();
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "비밀번호를 바꾸지 못했습니다.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="max-w-md space-y-5">
      {/*
        누르기 **전에** 알려 줍니다. 누른 뒤에 알려 주면 이미 로그아웃된 뒤입니다.
        다른 기기에서 보던 화면도 같이 끊깁니다 (`services/admin_account.py`).
      */}
      <p className="rounded-lg bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
        바꾸면 <strong className="font-medium">이 브라우저를 포함해 모든 기기에서 로그아웃됩니다.</strong>{" "}
        바로 다시 로그인해야 합니다.
      </p>

      {notice && (
        <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
          {notice}
        </p>
      )}

      <label className="block text-sm">
        <span className="text-zinc-600 dark:text-zinc-400">지금 쓰는 비밀번호</span>
        <input
          type="password"
          value={form.current}
          onChange={(e) => set("current", e.target.value)}
          required
          maxLength={1024}
          // **`minLength` 를 걸지 않습니다.** 옛 비밀번호가 12자 미만인 계정이
          // (`seed-admin` 으로 만든 최초 계정) 바로 그 이유로 못 바꾸게 됩니다.
          // 서버 스키마가 같은 이유로 여기만 최소 길이를 비워 뒀습니다.
          autoComplete="current-password"
          disabled={busy || done}
          className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 disabled:opacity-50 dark:border-zinc-700 dark:bg-black"
        />
      </label>

      <label className="block text-sm">
        <span className="text-zinc-600 dark:text-zinc-400">
          새 비밀번호 ({MIN_PASSWORD}자 이상)
        </span>
        <input
          type="password"
          value={form.next}
          onChange={(e) => set("next", e.target.value)}
          required
          minLength={MIN_PASSWORD}
          maxLength={1024}
          autoComplete="new-password"
          disabled={busy || done}
          className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 disabled:opacity-50 dark:border-zinc-700 dark:bg-black"
        />
      </label>

      <label className="block text-sm">
        <span className="text-zinc-600 dark:text-zinc-400">새 비밀번호 확인</span>
        <input
          type="password"
          value={form.confirm}
          onChange={(e) => set("confirm", e.target.value)}
          required
          minLength={MIN_PASSWORD}
          maxLength={1024}
          autoComplete="new-password"
          disabled={busy || done}
          className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 disabled:opacity-50 dark:border-zinc-700 dark:bg-black"
        />
      </label>

      <p className="text-xs leading-5 text-zinc-500 dark:text-zinc-400">
        {admin?.login_id} 계정의 비밀번호를 바꿉니다. 다른 사람의 비밀번호는 여기서 바꿀 수
        없고, 잊었을 때는 <strong className="font-medium">관리자에게 계정 재발급을 요청</strong>하세요.
      </p>

      <button
        type="submit"
        disabled={busy || done}
        className="rounded-full bg-zinc-900 px-4 py-2 text-sm text-white transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
      >
        {busy || done ? "바꾸는 중…" : "비밀번호 바꾸기"}
      </button>
    </form>
  );
}
