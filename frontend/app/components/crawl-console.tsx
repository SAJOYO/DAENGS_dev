"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "./auth-provider";
import { apiJson, ApiError } from "@/lib/api";

/**
 * `GET /api/admin/crawl` 의 한 줄 (`schemas/crawl.py` 의 `CrawlRunOut`).
 *
 * `changed_slugs` 는 건수만 옵니다 — 조례 208건처럼 길어질 수 있고, 화면이 필요로 하는
 * 것은 "몇 건 바뀌었나" 입니다.
 */
type CrawlRun = {
  id: number;
  run_id: string | null;
  source_id: string;
  trigger: "due" | "manual" | "revision";
  status: "running" | "ok" | "failed" | "unavailable";
  docs_fetched: number;
  docs_changed: number;
  docs_failed: number;
  docs_skipped: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

type CrawlStatus = { runs: CrawlRun[]; running: number };

/**
 * 상태를 한국어로. **`unavailable` 을 `failed` 와 다르게 쓰는 것이 이 표의 요점입니다** —
 * 키 미설정·시드 URL 사망은 실패가 아니라 **사람이 고쳐야 하는 것**이라, 색과 문구가
 * 달라야 "재시도를 기다릴 일"과 "지금 손대야 할 일"이 갈립니다.
 */
const STATUS: Record<CrawlRun["status"], { label: string; className: string }> = {
  running: { label: "도는 중", className: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300" },
  ok: { label: "성공", className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300" },
  failed: { label: "실패", className: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300" },
  unavailable: { label: "손봐야 함", className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
};

// "개정" 은 Beat 가 시행일자 변화를 보고 깨운 수집 — 법령은 주기가 없어 이 길로만 받습니다 (RAG-054).
const TRIGGER: Record<CrawlRun["trigger"], string> = { due: "주기", manual: "수동", revision: "개정" };

/** 5초. 크롤 하나가 분 단위라 더 자주 물어도 볼 것이 없습니다. */
const POLL_MS = 5000;

function when(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "medium" });
}

/** 걸린 시간. 안 끝났으면 지금까지. */
function elapsed(run: CrawlRun): string {
  const end = run.finished_at ? new Date(run.finished_at) : new Date();
  const sec = Math.max(0, Math.round((end.getTime() - new Date(run.started_at).getTime()) / 1000));
  return sec < 60 ? `${sec}초` : `${Math.floor(sec / 60)}분 ${sec % 60}초`;
}

export default function CrawlConsole() {
  const { can } = useAuth();
  const canTrigger = can("ops:write");

  const [data, setData] = useState<CrawlStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // StrictMode 가 개발에서 effect 를 두 번 돌립니다. 언마운트된 뒤의 setState 를 막습니다
  // (`auth-provider.tsx` 와 같은 장치입니다).
  const alive = useRef(true);

  // `async` 로 쓰지 않고 `.then` 체인으로 두는 것은 하우스 패턴이자 린트 규칙
  // (`react-hooks/set-state-in-effect`) 때문입니다 — effect 본문에서 바로 setState 하는
  // 모양이 되면 안 됩니다.
  const load = useCallback(
    (signal?: AbortSignal) =>
      apiJson<CrawlStatus>("/api/admin/crawl", { signal })
        .then((next) => {
          if (!alive.current) return;
          setData(next);
          setError(null);
        })
        .catch((e: unknown) => {
          if (!alive.current || (e as Error)?.name === "AbortError") return;
          setError(e instanceof ApiError ? e.message : "실행 이력을 불러오지 못했습니다.");
        }),
    [],
  );

  // **폴링입니다.** 트리거는 202 로 접수증만 주고(크롤이 분 단위라 요청을 붙들 수 없습니다),
  // 진행은 `crawl_runs` 를 다시 읽어서 봅니다 (RAG-001 원칙 6).
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void load(controller.signal);
    const timer = setInterval(() => void load(controller.signal), POLL_MS);
    return () => {
      alive.current = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [load]);

  async function trigger(sourceIds: string[]) {
    setBusy(true);
    setNotice(null);
    try {
      const body = { source_ids: sourceIds };
      await apiJson<{ task_id: string }>("/api/admin/crawl", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setNotice(
        sourceIds.length > 0
          ? `${sourceIds.join(", ")} 수집을 요청했습니다.`
          : "주기 대상(due) 수집을 요청했습니다.",
      );
      await load();
    } catch (e) {
      // 503 은 **앱이 아니라 워커/브로커가 없는 것**이라 문구를 가릅니다.
      setNotice(
        e instanceof ApiError && e.status === 503
          ? "워커나 브로커가 떠 있지 않습니다. 서버에서 crawler-worker 를 확인하세요."
          : e instanceof ApiError
            ? e.message
            : "요청에 실패했습니다.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return <p className="text-sm text-red-600 dark:text-red-400">{error}</p>;
  }
  if (!data) {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">불러오는 중입니다…</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          소스 {data.runs.length}개
          {data.running > 0 && (
            <>
              {" · "}
              <span className="text-blue-600 dark:text-blue-400">{data.running}개 진행 중</span>
            </>
          )}
        </p>
        {canTrigger && (
          <button
            type="button"
            onClick={() => void trigger([])}
            disabled={busy}
            className="rounded-full bg-zinc-900 px-4 py-2 text-sm text-white transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
          >
            주기 대상 수집
          </button>
        )}
      </div>

      {notice && (
        <p className="rounded-lg bg-zinc-100 px-4 py-3 text-sm text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
          {notice}
        </p>
      )}

      {data.running > 0 && (
        // **0 이 아니면 둘 중 하나입니다** — 지금 돌고 있거나, 워커가 죽어서 남았거나.
        // 둘을 가르는 것은 이 표가 아니라 워커 상태라, 시작 시각을 같이 보여 주고
        // 사람이 판단하게 둡니다 (RAG-047).
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          진행 중으로 오래 남아 있으면 워커가 중간에 죽은 것일 수 있습니다. 시작 시각을 보세요.
        </p>
      )}

      {data.runs.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          아직 실행 이력이 없습니다. 주기 실행(Beat)이 돌거나 수동으로 트리거하면 여기에 남습니다.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[44rem] text-left text-sm">
            <thead className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
              <tr>
                <th className="py-2 pr-4 font-normal">소스</th>
                <th className="py-2 pr-4 font-normal">상태</th>
                <th className="py-2 pr-4 font-normal">받음 / 바뀜</th>
                <th className="py-2 pr-4 font-normal">마지막 실행</th>
                <th className="py-2 pr-4 font-normal">걸린 시간</th>
                <th className="py-2 font-normal sr-only">수집</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-900">
              {data.runs.map((run) => (
                <tr key={run.id}>
                  <td className="py-3 pr-4 font-mono text-xs">{run.source_id}</td>
                  <td className="py-3 pr-4">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS[run.status].className}`}>
                      {STATUS[run.status].label}
                    </span>
                    {run.error && (
                      <span
                        className="ml-2 text-xs text-zinc-500 dark:text-zinc-400"
                        title={run.error}
                      >
                        {run.error.length > 40 ? `${run.error.slice(0, 40)}…` : run.error}
                      </span>
                    )}
                  </td>
                  <td className="py-3 pr-4 tabular-nums">
                    {run.docs_fetched} / {run.docs_changed}
                    {run.docs_failed > 0 && (
                      <span className="ml-1 text-red-600 dark:text-red-400">
                        (실패 {run.docs_failed})
                      </span>
                    )}
                  </td>
                  <td className="py-3 pr-4 text-xs text-zinc-500 dark:text-zinc-400">
                    {when(run.started_at)}
                    <span className="ml-1">({TRIGGER[run.trigger]})</span>
                  </td>
                  <td className="py-3 pr-4 text-xs text-zinc-500 tabular-nums dark:text-zinc-400">
                    {elapsed(run)}
                  </td>
                  <td className="py-3">
                    {canTrigger && (
                      <button
                        type="button"
                        onClick={() => void trigger([run.source_id])}
                        disabled={busy || run.status === "running"}
                        className="rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
                      >
                        다시 수집
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
