"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiJson, ApiError } from "@/lib/api";

/**
 * `GET /api/admin/status` 의 한 항목 (`schemas/status.py` 의 `StatusItemOut`).
 *
 * **`label` 을 백엔드가 줍니다.** 여기에 `name → 한글 이름` 표를 두지 않는 이유는,
 * 항목이 늘 때 이 파일을 같이 고치지 않으면 낯선 id(`gait` 같은 것)가 그대로 화면에
 * 뜨기 때문입니다. 늘어나는 쪽은 백엔드고, 그쪽이 이름을 들고 오게 했습니다.
 */
type StatusItem = {
  name: string;
  label: string;
  state: "ok" | "degraded" | "down" | "absent";
  detail: string;
};

type StatusResponse = { items: StatusItem[]; checked_at: string };

/**
 * **`absent` 와 `down` 의 색과 문구가 다른 것이 이 화면의 요점입니다.**
 *
 * 로컬 서버와 GCP 는 같은 코드가 뜨는데 있는 것이 다릅니다 — GCP 에는 크롤러가 없고
 * (`docs/deploy/roadmap.md` §2-4), gait 는 어디서도 profile 로 꺼져 있습니다 (D-038).
 * 그것을 빨갛게 칠하면 화면이 늘 빨갛고, 빨간 게 늘 있으면 아무도 안 봅니다
 * (`docs/console/roadmap.md` §6). 크롤 콘솔이 `unavailable` 을 `failed` 와 다르게
 * 칠하는 것과 같은 판단입니다 (`crawl-console.tsx` 의 `STATUS`).
 */
const STATE: Record<StatusItem["state"], { label: string; className: string; dot: string }> = {
  ok: {
    label: "정상",
    className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
    dot: "bg-emerald-500",
  },
  degraded: {
    label: "덜 준비됨",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
    dot: "bg-amber-500",
  },
  down: {
    label: "죽었음",
    className: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
    dot: "bg-red-500",
  },
  absent: {
    // 고장이 아닙니다. 그래서 회색이고, 문구도 "없음" 이 아니라 "이 환경엔 없음" 입니다 —
    // "없음" 만 쓰면 있어야 하는 것이 사라진 것처럼 읽힙니다.
    label: "이 환경엔 없음",
    className: "bg-zinc-100 text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400",
    dot: "bg-zinc-400",
  },
};

/** 30초. 항목마다 백엔드가 1~2초 timeout 을 걸어 두었으므로 더 자주 물을 이유가 없습니다. */
const POLL_MS = 30_000;

function when(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "medium" });
}

export default function StatusConsole() {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // StrictMode 가 개발에서 effect 를 두 번 돌립니다. 언마운트된 뒤의 setState 를 막습니다
  // (`crawl-console.tsx` 와 같은 장치입니다).
  const alive = useRef(true);

  // `.then` 체인인 것은 하우스 패턴이자 린트 규칙(`react-hooks/set-state-in-effect`)
  // 때문입니다 — effect 본문에서 바로 setState 하는 모양이 되면 안 됩니다.
  const load = useCallback(
    (signal?: AbortSignal) =>
      apiJson<StatusResponse>("/api/admin/status", { signal })
        .then((next) => {
          if (!alive.current) return;
          setData(next);
          setError(null);
        })
        .catch((e: unknown) => {
          if (!alive.current || (e as Error)?.name === "AbortError") return;
          // **이 화면이 못 뜨는 것과 항목이 죽은 것은 다릅니다.** 항목이 죽는 것은
          // `down` 으로 표에 들어오고(백엔드가 언제나 200 입니다), 여기까지 오는 것은
          // API 자체에 못 닿은 것 — 즉 backend 나 nginx 가 죽은 것입니다.
          setError(
            e instanceof ApiError
              ? e.message
              : "상태를 불러오지 못했습니다. backend 자체가 떠 있는지 확인하세요.",
          );
        }),
    [],
  );

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

  if (error) {
    return <p className="text-sm text-red-600 dark:text-red-400">{error}</p>;
  }
  if (!data) {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">불러오는 중입니다…</p>;
  }

  // **`absent` 는 세지 않습니다.** 이 줄이 세는 것은 "사람이 지금 볼 자리" 이고,
  // 이 환경에 없는 것은 그 자리가 아닙니다.
  const down = data.items.filter((item) => item.state === "down");
  const degraded = data.items.filter((item) => item.state === "degraded");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {down.length > 0 ? (
            <span className="font-medium text-red-600 dark:text-red-400">
              죽은 항목 {down.length}개 — {down.map((item) => item.label).join(", ")}
            </span>
          ) : degraded.length > 0 ? (
            <span className="text-amber-700 dark:text-amber-400">
              죽은 항목은 없고, 덜 준비된 것 {degraded.length}개입니다.
            </span>
          ) : (
            <span>죽은 항목이 없습니다.</span>
          )}
        </p>
        <p className="text-xs text-zinc-400 dark:text-zinc-500">
          {when(data.checked_at)} 기준 · {POLL_MS / 1000}초마다 다시 봅니다
        </p>
      </div>

      <ul className="divide-y divide-zinc-200 rounded-xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
        {data.items.map((item) => {
          const state = STATE[item.state] ?? STATE.degraded;
          return (
            <li key={item.name} className="flex gap-4 px-5 py-4">
              <span
                aria-hidden
                className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${state.dot}`}
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <h2 className="font-medium">{item.label}</h2>
                  <span
                    className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs ${state.className}`}
                  >
                    {state.label}
                  </span>
                </div>
                <p className="mt-1 text-sm leading-6 break-words text-zinc-600 dark:text-zinc-400">
                  {item.detail}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
