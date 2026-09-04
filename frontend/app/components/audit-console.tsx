"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiJson, ApiError } from "@/lib/api";

/**
 * `GET /api/admin/audit` 의 한 줄 (`schemas/admin_audit.py` 의 `AuditEntryOut`).
 *
 * **`detail` 에 개인정보가 없다는 것이 전제입니다.** 서버가 그렇게 지키고 있고
 * (`models/admin_audit_log.py` · `test_app_user_pii.py`), 이 화면은 그 전제 위에서
 * 모르는 값을 JSON 그대로 그립니다 — 전제가 깨지면 여기가 그것을 뿌립니다.
 */
type AuditEntry = {
  id: string;
  created_at: string;
  action: string;
  /** **`null` 이 정상입니다** — 없는 아이디로 두드린 로그인 실패. */
  actor: { id: string; login_id: string; name: string } | null;
  target_type: string | null;
  target_id: string | null;
  detail: Record<string, unknown> | null;
  ip: string | null;
  request_id: string | null;
};

type AuditPage = { entries: AuditEntry[]; next_cursor: string | null };

/**
 * action → 한국어 문구와 색.
 *
 * **모르는 action 도 그려야 합니다.** `action` 에는 DB CHECK 이 없고 카드마다 늘어납니다
 * (`models/admin_audit_log.py`). 여기 없는 값은 아래에서 원문 그대로 떨어집니다 —
 * 새 감사 action 이 생겨도 화면이 죽지 않게 하려는 것입니다.
 */
const ACTION: Record<string, { label: string; className: string }> = {
  "admin.login.success": { label: "로그인", className: "bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400" },
  "admin.login.failed_unknown_id": { label: "로그인 실패 · 없는 아이디", className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  "admin.login.failed_password": { label: "로그인 실패 · 비밀번호", className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  "admin.login.denied_suspended": { label: "로그인 거부 · 정지된 계정", className: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300" },
  "admin.account.created": { label: "계정 발급", className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" },
  "admin.account.role_changed": { label: "권한 변경", className: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300" },
  "admin.account.suspended": { label: "계정 정지", className: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300" },
  "admin.account.reactivated": { label: "계정 정지 해제", className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" },
  // **이 목록에서 유일하게 주체와 대상이 같습니다** (#222) — 남에게 한 일이 아니라
  // 자기 것을 바꾼 행위라, 위 계정 관리 넷과 색을 달리 둡니다.
  "admin.account.password_changed": { label: "비밀번호 변경", className: "bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400" },
  // **이 줄이 이 테이블이 생긴 첫째 이유입니다** (로드맵 §1 "누가 복호화를 봤나").
  // 색을 제일 세게 씁니다 — 훑을 때 눈에 걸려야 하는 것이 이것입니다.
  "admin.app_user.pii_revealed": { label: "개인정보 원문 조회", className: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300" },
  "admin.app_user.suspended": { label: "회원 정지", className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  "admin.app_user.reactivated": { label: "회원 정지 해제", className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" },
};

/** 갈래 필터. 값은 `action` 의 접두어라 서버의 `starts_with` 와 그대로 맞습니다. */
const SCOPES = [
  { value: "", label: "전부" },
  { value: "admin.login.", label: "로그인" },
  { value: "admin.account.", label: "관리자 계정" },
  { value: "admin.app_user.", label: "회원" },
];

function when(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "medium" });
}

/**
 * `detail` 을 한 줄로. **action 마다 모양이 다릅니다.**
 *
 * `{login_id}` · `{from,to}` · `{sessions_dropped}` · `{opened}` · `null` 이 지금 전부인데,
 * 한 모양으로 가정하면 절반이 안 읽힙니다. 모르는 모양은 JSON 그대로 떨어뜨려서
 * **새 action 이 생겨도 정보가 사라지지 않게** 합니다.
 *
 * **모양이 같아도 뜻이 같지는 않습니다** — `sessions_dropped` 가 그렇습니다. 그래서
 * 여기서 `entry.detail` 만 보지 않고 `entry.action` 까지 봅니다 (아래 주석).
 */
function describe(entry: AuditEntry): string | null {
  const d = entry.detail;
  if (!d) return null;

  if (typeof d.login_id === "string") return `아이디 ${d.login_id}`;
  if (typeof d.from === "string" && typeof d.to === "string") return `${d.from} → ${d.to}`;
  if (typeof d.sessions_dropped === "number") {
    // **같은 키인데 읽는 법이 다릅니다** (#222). 정지(`suspended`)는 주체와 대상이 달라
    // n 이 전부 남의 세션이지만, 비밀번호 변경은 자기 것이라 **지금 그 요청을 보낸
    // 본인 브라우저가 n 에 포함**됩니다. 밝히지 않으면 늘 하나씩 많게 읽힙니다 —
    // 다른 데 로그인이 없어도 1 이 남습니다 (09-04 개발 DB 실측이 정확히 그 1 이었습니다).
    const own = entry.action === "admin.account.password_changed" ? " (본인 것 포함)" : "";
    return `세션 ${d.sessions_dropped}개 끊음${own}`;
  }
  if (Array.isArray(d.opened)) {
    // 칸 **이름**만 옵니다. 값은 서버가 절대 안 넣습니다.
    return d.opened.length > 0 ? `열어 본 칸: ${d.opened.join(" · ")}` : "열 것이 없었음";
  }
  if (typeof d.role === "string") return `권한 ${d.role}`;
  return JSON.stringify(d);
}

/** 한 번에 가져오는 줄 수. 서버 기본값(50)과 같게 둡니다. */
const PAGE = 50;

/**
 * 감사 로그 조회 (콘솔 로드맵 A4-1 · #221).
 *
 * **이 화면을 연 것은 기록에 남지 않습니다.** 남기면 화면이 자기 기록으로 채워지고,
 * 그 행을 본 것도 남겨야 하는 재귀가 됩니다 (`services/audit.py` 의 읽기 절).
 *
 * `admin:manage` 를 못 가진 계정은 여기 오지도 못합니다 — 카드가 가리고 API 는 403.
 * 그래서 이 안에는 권한 분기가 없습니다.
 */
export default function AuditConsole() {
  const [scope, setScope] = useState("");
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // StrictMode 가 개발에서 effect 를 두 번 돌립니다 (`crawl-console.tsx` 와 같은 장치).
  const alive = useRef(true);

  const load = useCallback(
    (nextScope: string, signal?: AbortSignal) => {
      const params = new URLSearchParams({ limit: String(PAGE) });
      if (nextScope) params.set("action_prefix", nextScope);
      return apiJson<AuditPage>(`/api/admin/audit?${params}`, { signal })
        .then((page) => {
          if (!alive.current) return;
          setEntries(page.entries);
          setCursor(page.next_cursor);
          setError(null);
        })
        .catch((e: unknown) => {
          if (!alive.current || (e as Error)?.name === "AbortError") return;
          setError(e instanceof ApiError ? e.message : "감사 로그를 불러오지 못했습니다.");
        });
    },
    [],
  );

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void load(scope, controller.signal);
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [load, scope]);

  async function more() {
    if (!cursor) return;
    setBusy(true);
    try {
      const params = new URLSearchParams({ limit: String(PAGE), cursor });
      if (scope) params.set("action_prefix", scope);
      const page = await apiJson<AuditPage>(`/api/admin/audit?${params}`);
      // **이어 붙입니다.** 키셋 커서라 겹치거나 빠지지 않습니다 — 읽는 동안 새 행이
      // 생겨도 이미 본 쪽은 그대로입니다 (OFFSET 이면 어긋납니다).
      setEntries((prev) => [...(prev ?? []), ...page.entries]);
      setCursor(page.next_cursor);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "더 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return <p className="text-sm text-red-600 dark:text-red-400">{error}</p>;
  }
  if (!entries) {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">불러오는 중입니다…</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={scope}
          onChange={(e) => {
            setEntries(null);
            setScope(e.target.value);
          }}
          className="rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-black"
        >
          {SCOPES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        {/*
          **총 개수를 안 보여 줍니다.** 세는 값이 비싸고 읽는 사이에도 늘어서
          (로그인마다 행이 생깁니다) 곧 틀린 숫자가 됩니다.
        */}
        <p className="text-sm text-zinc-600 dark:text-zinc-400">{entries.length}줄 불러옴</p>
      </div>

      {entries.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">기록이 없습니다.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[52rem] text-left text-sm">
            <thead className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
              <tr>
                <th className="py-2 pr-4 font-normal">시각</th>
                <th className="py-2 pr-4 font-normal">한 일</th>
                <th className="py-2 pr-4 font-normal">누가</th>
                <th className="py-2 pr-4 font-normal">무엇을</th>
                <th className="py-2 font-normal">어디서</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-900">
              {entries.map((e) => {
                const known = ACTION[e.action];
                const note = describe(e);
                return (
                  <tr key={e.id}>
                    <td className="py-3 pr-4 text-xs whitespace-nowrap text-zinc-500 tabular-nums dark:text-zinc-400">
                      {when(e.created_at)}
                    </td>
                    <td className="py-3 pr-4">
                      {known ? (
                        <span className={`rounded-full px-2 py-0.5 text-xs ${known.className}`}>
                          {known.label}
                        </span>
                      ) : (
                        // 모르는 action 은 원문 그대로. 화면이 죽는 것보다 낫습니다.
                        <span className="font-mono text-xs">{e.action}</span>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-xs">
                      {e.actor ? (
                        <>
                          <span className="font-mono">{e.actor.login_id}</span>
                          <span className="ml-1.5 text-zinc-500 dark:text-zinc-400">
                            {e.actor.name}
                          </span>
                        </>
                      ) : (
                        /*
                          **빠뜨린 데이터가 아니라 그 사건의 성질입니다** — 없는 아이디로
                          두드린 로그인 실패는 가리킬 계정이 없습니다.
                        */
                        <span className="text-zinc-400 dark:text-zinc-500">주체 없음</span>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-xs text-zinc-600 dark:text-zinc-400">
                      {note}
                      {e.target_id && (
                        <span className="ml-1.5 font-mono text-zinc-400 dark:text-zinc-500">
                          {e.target_type}:{e.target_id.slice(0, 8)}
                        </span>
                      )}
                    </td>
                    <td className="py-3 font-mono text-xs text-zinc-500 dark:text-zinc-400">
                      {e.ip ?? "-"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {cursor && (
        <button
          type="button"
          onClick={() => void more()}
          disabled={busy}
          className="rounded-full border border-zinc-300 px-4 py-2 text-sm text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
        >
          더 보기
        </button>
      )}

      {/*
        **지우는 주기가 아직 없습니다** (로드맵 A5). 로그인 시도까지 들어와 행이 빨리
        느는데, 얼마나 빨리인지는 이 화면이 생겨야 볼 수 있었습니다 — 그것이 A5 가
        "증가량을 본 뒤" 로 미뤄져 있는 이유입니다.
      */}
      <p className="text-xs text-zinc-500 dark:text-zinc-400">
        로그인 시도까지 남기 때문에 줄이 빠르게 늡니다. 지우는 주기는 아직 정하지 않았습니다.
        이 화면을 연 것은 기록에 남지 않습니다.
      </p>
    </div>
  );
}
