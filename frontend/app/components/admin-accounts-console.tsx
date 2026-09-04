"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "./auth-provider";
import { apiJson, ApiError } from "@/lib/api";
import { ADMIN_ROLES, ROLE_HINT, ROLE_LABEL, type AdminRole } from "@/lib/auth";

/**
 * `GET /api/admin/admins` 의 한 줄 (`schemas/admin_account.py` 의 `AdminAccountOut`).
 *
 * **비밀번호와 관련된 필드가 없습니다.** 서버 스키마가 컬럼을 하나씩 적어서 그렇고,
 * 여기에 `password_hash` 를 더하고 싶어지는 날이 오면 그건 응답이 잘못된 것입니다.
 */
type AdminAccount = {
  id: string;
  login_id: string;
  name: string;
  role: string;
  status: "active" | "suspended";
  last_login_at: string | null;
  created_at: string;
};

/** `AdminAccountCreate` 의 최소 길이. 서버(422)와 같은 값이라 화면에서 먼저 걸립니다. */
const MIN_PASSWORD = 12;

function when(iso: string | null): string {
  if (!iso) return "없음";
  return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

const EMPTY_FORM = { login_id: "", password: "", name: "", role: "VIEWER" as AdminRole };

/**
 * 관리자 계정 목록과 발급 · 정지 · 권한 변경 (콘솔 로드맵 A3 · #207).
 *
 * **폴링하지 않습니다.** 크롤 화면(5초)이나 상태 화면(30초)과 다릅니다 — 저기서 변하는
 * 것은 서버가 만드는 값이고, 여기서 변하는 것은 **이 화면을 보는 사람이 만드는 값**뿐
 * 입니다. 쓰고 나서 다시 읽는 것으로 충분합니다.
 *
 * `admin:manage` 를 못 가진 계정은 이 화면에 오지도 못합니다 (`console/page.tsx` 의
 * 카드가 가리고, API 는 403 으로 막습니다). 그래서 여기 안에는 권한 분기가 없습니다 —
 * 대신 **자기 자신**을 가립니다. 서버가 자기 계정 변경을 409 로 막으므로(가드 ①),
 * 누를 수 있게 두면 반드시 실패하는 버튼이 됩니다.
 */
export default function AdminAccountsConsole() {
  const { admin: me } = useAuth();

  const [accounts, setAccounts] = useState<AdminAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [creating, setCreating] = useState(false);

  // StrictMode 가 개발에서 effect 를 두 번 돌립니다 (`crawl-console.tsx` 와 같은 장치).
  const alive = useRef(true);

  const load = useCallback(
    (signal?: AbortSignal) =>
      apiJson<AdminAccount[]>("/api/admin/admins", { signal })
        .then((next) => {
          if (!alive.current) return;
          setAccounts(next);
          setError(null);
        })
        .catch((e: unknown) => {
          if (!alive.current || (e as Error)?.name === "AbortError") return;
          setError(e instanceof ApiError ? e.message : "계정 목록을 불러오지 못했습니다.");
        }),
    [],
  );

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [load]);

  async function patch(account: AdminAccount, body: { role?: string; status?: string }) {
    setBusy(true);
    setNotice(null);
    try {
      await apiJson<AdminAccount>(`/api/admin/admins/${account.id}`, {
        method: "PATCH",
        // **`apiJson` 은 헤더를 안 붙여 줍니다.** 빼면 브라우저가 `text/plain` 을 달고
        // FastAPI 가 본문을 파싱하지 않아 422 가 나는데, 그 detail 은 문자열이 아니라
        // 목록이라 화면에는 기본 문구만 남습니다 (`crawl-console.tsx` 에서 겪은 것).
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setNotice(`${account.login_id} 계정을 바꿨습니다.`);
      await load();
    } catch (e) {
      // 409 는 **막힌 것**입니다 (자기 자신 · 마지막 ADMIN). 서버가 무엇을 해야 하는지까지
      // 문구에 담아 주므로 그대로 보여 줍니다.
      setNotice(e instanceof ApiError ? e.message : "변경에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      await apiJson<AdminAccount>("/api/admin/admins", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      // **비밀번호를 화면에 남기지 않습니다.** 발급한 사람이 이미 아는 값이고,
      // 폼에 남겨 두면 다음 발급 때 같은 값을 그대로 쓰게 됩니다.
      setNotice(
        `${form.login_id} 계정을 발급했습니다. 초기 비밀번호는 본인에게 직접 전달하세요.`,
      );
      setForm(EMPTY_FORM);
      setCreating(false);
      await load();
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "발급에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return <p className="text-sm text-red-600 dark:text-red-400">{error}</p>;
  }
  if (!accounts) {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">불러오는 중입니다…</p>;
  }

  const activeAdmins = accounts.filter((a) => a.role === "ADMIN" && a.status === "active").length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          계정 {accounts.length}개 · 관리자 {activeAdmins}명
        </p>
        <button
          type="button"
          onClick={() => setCreating((v) => !v)}
          className="rounded-full bg-zinc-900 px-4 py-2 text-sm text-white transition-colors hover:bg-zinc-700 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
        >
          {creating ? "닫기" : "계정 발급"}
        </button>
      </div>

      {notice && (
        <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
          {notice}
        </p>
      )}

      {creating && (
        <form
          onSubmit={(e) => void create(e)}
          className="space-y-4 rounded-xl border border-zinc-200 p-6 dark:border-zinc-800"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="text-zinc-600 dark:text-zinc-400">아이디</span>
              <input
                value={form.login_id}
                onChange={(e) => setForm({ ...form, login_id: e.target.value })}
                required
                minLength={3}
                maxLength={50}
                // 서버(`AdminAccountCreate`)의 pattern 과 같습니다. 여기서 먼저 걸어
                // 두면 대문자를 넣고 422 를 받는 대신 브라우저가 알려 줍니다.
                pattern="[a-z0-9._\-]+"
                title="영문 소문자·숫자와 . _ - 만 씁니다."
                className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 dark:border-zinc-700 dark:bg-black"
              />
            </label>
            <label className="block text-sm">
              <span className="text-zinc-600 dark:text-zinc-400">이름</span>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                required
                maxLength={50}
                className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 dark:border-zinc-700 dark:bg-black"
              />
            </label>
            <label className="block text-sm">
              <span className="text-zinc-600 dark:text-zinc-400">
                초기 비밀번호 ({MIN_PASSWORD}자 이상)
              </span>
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                required
                minLength={MIN_PASSWORD}
                maxLength={1024}
                className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 dark:border-zinc-700 dark:bg-black"
              />
            </label>
            <label className="block text-sm">
              <span className="text-zinc-600 dark:text-zinc-400">권한</span>
              <select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value as AdminRole })}
                className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 dark:border-zinc-700 dark:bg-black"
              >
                {ADMIN_ROLES.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABEL[role]}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <p className="text-xs text-zinc-500 dark:text-zinc-400">{ROLE_HINT[form.role]}</p>

          {/*
            **초기 비밀번호를 서버가 만들어 주지 않습니다.** 관리자는 가입이 아니라
            발급이라 이메일 인증 절차 자체가 없고(`db/init/03_auth.sql`), 지금 실제
            경로는 팀 채널로 건네는 것입니다 — `uv run seed-admin` 과 같습니다.
            받은 사람이 스스로 바꾸는 화면은 아직 없습니다 (#207 `## 남은 것`).
          */}
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            초기 비밀번호는 발급한 사람이 본인에게 직접 전달합니다. 메일로 나가지 않습니다.
          </p>

          <button
            type="submit"
            disabled={busy}
            className="rounded-full bg-zinc-900 px-4 py-2 text-sm text-white transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
          >
            발급
          </button>
        </form>
      )}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[44rem] text-left text-sm">
          <thead className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
            <tr>
              <th className="py-2 pr-4 font-normal">아이디</th>
              <th className="py-2 pr-4 font-normal">이름</th>
              <th className="py-2 pr-4 font-normal">권한</th>
              <th className="py-2 pr-4 font-normal">상태</th>
              <th className="py-2 pr-4 font-normal">마지막 로그인</th>
              <th className="py-2 font-normal sr-only">변경</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-900">
            {accounts.map((account) => {
              // 서버가 409 로 막는 자리입니다 (가드 ①). 누를 수 있게 두면 반드시
              // 실패하는 버튼이 되므로 여기서도 가립니다.
              const isMe = account.id === me?.admin_id;

              return (
                <tr key={account.id}>
                  <td className="py-3 pr-4 font-mono text-xs">
                    {account.login_id}
                    {isMe && (
                      <span className="ml-2 text-zinc-400 dark:text-zinc-500">(나)</span>
                    )}
                  </td>
                  <td className="py-3 pr-4">{account.name}</td>
                  <td className="py-3 pr-4">
                    {isMe ? (
                      <span className="text-zinc-500 dark:text-zinc-400">
                        {ROLE_LABEL[account.role] ?? account.role}
                      </span>
                    ) : (
                      <select
                        value={account.role}
                        disabled={busy}
                        onChange={(e) => void patch(account, { role: e.target.value })}
                        className="rounded-lg border border-zinc-300 bg-transparent px-2 py-1 text-xs disabled:opacity-50 dark:border-zinc-700"
                      >
                        {ADMIN_ROLES.map((role) => (
                          <option key={role} value={role}>
                            {ROLE_LABEL[role]}
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td className="py-3 pr-4">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${
                        account.status === "active"
                          ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                          : "bg-zinc-200 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
                      }`}
                    >
                      {account.status === "active" ? "사용 중" : "정지됨"}
                    </span>
                  </td>
                  <td className="py-3 pr-4 text-xs text-zinc-500 dark:text-zinc-400">
                    {when(account.last_login_at)}
                  </td>
                  <td className="py-3">
                    {!isMe && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void patch(account, {
                            status: account.status === "active" ? "suspended" : "active",
                          })
                        }
                        className="rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
                      >
                        {account.status === "active" ? "정지" : "정지 해제"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/*
        **정지가 즉시가 아닙니다.** access token 은 무상태라 최대 5분(ACCESS_TTL) 동안
        살아 있습니다 — 그것을 없애려면 요청마다 DB 를 봐야 하고, 그러지 않기로 한 것이
        D-015 입니다. 열려 있던 세션(refresh)은 정지하는 순간 끊깁니다.
      */}
      <p className="text-xs text-zinc-500 dark:text-zinc-400">
        정지하면 열려 있던 세션이 끊기지만, 이미 발급된 접근 토큰은 최대 5분 더 살아 있습니다.
        계정은 지우지 않습니다 — 지우면 감사 기록이 가리킬 곳을 잃습니다.
      </p>
    </div>
  );
}
