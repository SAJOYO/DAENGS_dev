"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiJson, ApiError } from "@/lib/api";

/** `schemas/metrics.py` 의 `NamedCount`. 서버가 **많은 순으로 정렬해서** 보냅니다. */
type NamedCount = { name: string; count: number };

/**
 * `GET /api/admin/metrics/chats` (`schemas/metrics.py` 의 `ChatMetricsOut`).
 *
 * **원문이 담길 칸이 없습니다.** 서버 스키마가 그렇게 만들어져 있고(D-037), 여기에
 * 필드를 더하고 싶어지는 날이 오면 그건 응답이 잘못된 것입니다.
 */
type ChatMetrics = {
  since: string;
  days: number;
  sessions: number;
  turns: {
    total: number;
    by_processing_status: NamedCount[];
    by_assistant_status: NamedCount[];
    top_error_codes: NamedCount[];
  };
  categories: { counts: NamedCount[]; tagged_total: number };
  summaries: NamedCount[];
};

/** `chat_turns.processing_status` — 우리 코드가 어디까지 갔나. */
const PROCESSING: Record<string, string> = {
  processing: "도는 중",
  completed: "처리됨",
  failed: "처리 실패",
};

/**
 * `chat_turns.assistant_status` 8값 (`db/init/07_chats.sql` 의 CHECK).
 *
 * **`FAILED` 는 위 `processing.failed` 와 다른 것입니다.** 저기는 우리 코드가 죽은 것이고
 * 여기는 처리는 됐는데 어시스턴트가 못 답한 것입니다 — state CHECK 상 같은 행에 못 옵니다.
 */
const ASSISTANT: Record<string, string> = {
  ANSWERED: "답함",
  PARTIAL: "일부만 답함",
  CLARIFY: "되물음",
  HANDOFF: "넘김",
  UNCERTAIN: "확신 못 함",
  REFUSED: "거절",
  PENDING: "보류",
  FAILED: "응답 실패",
};

const SUMMARY: Record<string, string> = {
  processing: "만드는 중",
  completed: "만들어짐",
  failed: "실패",
};

/**
 * `GET /api/admin/metrics/requests` (`schemas/metrics.py` 의 `RequestMetricsOut`, B2 · #297).
 *
 * **위 `ChatMetrics` 와 다른 표를 셉니다.** 저쪽은 제품 테이블(`chat_*`)이라 "무엇을
 * 물었나" 를 알고, 이쪽은 `request_metrics` 라 **"어떻게 처리됐나"** 를 압니다. 저장 안
 * 되는 요청(무상태 · 관리자 점검)은 저쪽에 없고 여기에 있어서, **두 숫자는 안 맞는 것이
 * 정상입니다.**
 */
type RequestMetrics = {
  since: string;
  days: number;
  latency: {
    total: number;
    /** 행이 없으면 `null` 입니다 — 0ms 가 아닙니다. "빠르다" 와 "잰 적 없다" 는 다릅니다. */
    avg_ms: number | null;
    p50_ms: number | null;
    p95_ms: number | null;
    max_ms: number | null;
  };
  by_status: NamedCount[];
  /** 합이 요청 수가 아닙니다 — 한 요청이 능력 둘을 부르면 둘 다 셉니다. */
  by_capability: NamedCount[];
  by_reason_code: NamedCount[];
  by_router_kind: NamedCount[];
};

const ROUTER: Record<string, string> = {
  deterministic: "규칙",
  llm: "모델",
};

const RANGES = [7, 30, 90];

/** 밀리초를 읽히게. 1초가 넘으면 초로 — 3200ms 보다 3.2초가 빨리 읽힙니다. */
function ms(value: number | null): string {
  if (value === null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}초` : `${value}ms`;
}

function when(iso: string): string {
  return new Date(iso).toLocaleDateString("ko-KR", { dateStyle: "medium" });
}

/**
 * 운영 지표 — 대화 집계 (콘솔 로드맵 B3 · #223).
 *
 * **비율은 여기서 계산합니다.** 서버가 개수와 분모만 보내는 것은 분모가 하나가 아니기
 * 때문입니다 — 능력별 분포는 태그 수가, 응답 결과는 `assistant_status` 가 있는 행이
 * 분모입니다. 미리 나눠서 보내면 그 분모가 무엇이었는지가 사라집니다.
 *
 * ⚠ **2026-09-04 기준 개발 DB 의 `chat_turns` 는 0건입니다.** 그래서 실제로 밟히는 것은
 * 아래 0 방어들이고, 숫자가 맞는지는 테스트가 봅니다 (#223 본문).
 */
export default function MetricsConsole() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<ChatMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  // **없어도 화면은 돕니다.** B2 표가 아직 없는 환경(마이그레이션 전)에서는 이 조회가
  // 실패하는데, 그때 대화 집계까지 같이 안 보이면 안 됩니다. 그래서 `setError` 로
  // 올리지 않고 `null` 로 둡니다 — 그 칸만 안 그립니다.
  const [requests, setRequests] = useState<RequestMetrics | null>(null);

  // StrictMode 가 개발에서 effect 를 두 번 돌립니다 (`crawl-console.tsx` 와 같은 장치).
  const alive = useRef(true);

  const load = useCallback((nextDays: number, signal?: AbortSignal) => {
    return apiJson<ChatMetrics>(`/api/admin/metrics/chats?days=${nextDays}`, { signal })
      .then((next) => {
        if (!alive.current) return;
        setData(next);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!alive.current || (e as Error)?.name === "AbortError") return;
        setError(e instanceof ApiError ? e.message : "지표를 불러오지 못했습니다.");
      });
  }, []);

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void load(days, controller.signal);
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [load, days]);

  // 기간을 같이 따라갑니다 — 위아래 숫자가 다른 기간이면 나란히 둔 뜻이 없습니다.
  useEffect(() => {
    const controller = new AbortController();
    apiJson<RequestMetrics>(`/api/admin/metrics/requests?days=${days}`, {
      signal: controller.signal,
    })
      .then((next) => {
        if (alive.current) setRequests(next);
      })
      .catch(() => {
        /* 곁다리 칸입니다. 실패하면 그 칸만 안 그립니다 (위 주석). */
      });
    return () => controller.abort();
  }, [days]);

  if (error) {
    return <p className="text-sm text-red-600 dark:text-red-400">{error}</p>;
  }
  if (!data) {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">불러오는 중입니다…</p>;
  }

  const answered =
    data.turns.by_assistant_status.find((c) => c.name === "ANSWERED")?.count ?? 0;
  // **분모는 turn 총수가 아니라 `assistant_status` 가 있는 행입니다.** 도는 중이거나
  // 처리가 죽은 turn 은 "어떻게 답했나" 에 들어갈 값이 없습니다 (state CHECK).
  const answerable = data.turns.by_assistant_status.reduce((n, c) => n + c.count, 0);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={days}
          onChange={(e) => {
            // 기간을 바꾸면 **둘 다** 비웁니다. effect 안에서 비우면 lint 가 막고
            // (`react-hooks/set-state-in-effect`), 옛 기간 숫자가 잠깐 남습니다.
            setData(null);
            setRequests(null);
            setDays(Number(e.target.value));
          }}
          className="rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-black"
        >
          {RANGES.map((d) => (
            <option key={d} value={d}>
              최근 {d}일
            </option>
          ))}
        </select>
        {/* 기간을 서버가 정하므로 화면이 되짚어 계산하지 않고 응답 값을 씁니다. */}
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          {when(data.since)}부터
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="대화방" value={data.sessions} />
        <Stat label="주고받은 대화" value={data.turns.total} />
        <Stat
          label="답한 비율"
          // **0 방어.** 지금 개발 DB 가 정확히 이 경로입니다.
          value={answerable === 0 ? "—" : `${Math.round((answered / answerable) * 100)}%`}
          note={answerable === 0 ? "아직 집계할 대화가 없습니다" : `${answerable}건 중 ${answered}건`}
        />
      </div>

      <Distribution
        title="어시스턴트가 어떻게 답했나"
        note="분모는 답이 나온 대화입니다 — 도는 중이거나 처리가 죽은 것은 빠집니다."
        counts={data.turns.by_assistant_status}
        labels={ASSISTANT}
        total={answerable}
      />

      <Distribution
        title="처리 상태"
        note="위와 다른 숫자입니다. 여기 '처리 실패'는 우리 코드가 죽은 것이고, 위 '응답 실패'는 처리는 됐는데 못 답한 것입니다."
        counts={data.turns.by_processing_status}
        labels={PROCESSING}
        total={data.turns.total}
      />

      {data.turns.top_error_codes.length > 0 && (
        <Distribution
          title="처리가 죽은 사유"
          counts={data.turns.top_error_codes}
          labels={{}}
          total={data.turns.top_error_codes.reduce((n, c) => n + c.count, 0)}
        />
      )}

      <Distribution
        title="무엇을 물었나"
        // ⚠ **분모가 turn 수가 아닙니다.** 대화 하나가 여러 능력에 걸립니다.
        note="대화 하나가 여러 갈래에 걸릴 수 있어, 합이 대화 수보다 큽니다."
        counts={data.categories.counts}
        labels={{}}
        total={data.categories.tagged_total}
      />

      <Distribution
        title="AI 요약"
        counts={data.summaries}
        labels={SUMMARY}
        total={data.summaries.reduce((n, c) => n + c.count, 0)}
      />

      {/*
        **요청 쪽 (B2 · #297).** 위가 "무엇을 물었나" 라면 여기는 "어떻게 처리됐나" 입니다.
        저장 안 되는 요청도 세므로 **위 숫자와 안 맞는 것이 정상**이고, 화면이 그것을
        말해 주지 않으면 "둘 중 하나가 틀렸다" 로 읽힙니다.
      */}
      {requests && (
        <section className="space-y-6 border-t border-zinc-200 pt-8 dark:border-zinc-800">
          <div>
            <h2 className="text-lg font-medium">요청이 어떻게 처리됐나</h2>
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              위 대화 숫자와 다른 표입니다 — 저장되지 않는 요청(콘솔 점검 등)도 여기 들어옵니다.
            </p>
          </div>

          {requests.latency.total === 0 ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              이 기간에 잰 요청이 없습니다.
            </p>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-4">
                <Stat label="요청" value={requests.latency.total} />
                <Stat label="중앙값" value={ms(requests.latency.p50_ms)} />
                {/*
                  **p95 가 이 화면에서 제일 중요한 숫자입니다.** 느린 꼬리는 평균에 거의
                  안 잡혀서, 평균만 보면 "괜찮다" 가 나오는 동안 스무 건 중 한 건이
                  몇 초씩 걸릴 수 있습니다.
                */}
                <Stat
                  label="p95"
                  value={ms(requests.latency.p95_ms)}
                  note="스무 건 중 한 건은 이보다 느립니다"
                />
                <Stat label="최대" value={ms(requests.latency.max_ms)} />
              </div>

              <Distribution
                title="응답 결과"
                counts={requests.by_status}
                labels={ASSISTANT}
                total={requests.latency.total}
              />

              <Distribution
                title="어떤 능력이 돌았나"
                note="한 요청이 여러 능력을 부를 수 있어 합이 요청 수와 다릅니다. 아무 능력도 안 돈 요청(사교적 응답·라우팅 실패)은 여기 없습니다."
                counts={requests.by_capability}
                labels={{}}
                total={requests.by_capability.reduce((n, c) => n + c.count, 0)}
              />

              {requests.by_reason_code.length > 0 && (
                <Distribution
                  title="거절·기권한 사유"
                  note="거절이 아니었던 요청은 여기 안 들어옵니다."
                  counts={requests.by_reason_code}
                  labels={{}}
                  total={requests.by_reason_code.reduce((n, c) => n + c.count, 0)}
                />
              )}

              {requests.by_router_kind.length > 0 && (
                <Distribution
                  title="목적지를 무엇이 골랐나"
                  counts={requests.by_router_kind}
                  labels={ROUTER}
                  total={requests.by_router_kind.reduce((n, c) => n + c.count, 0)}
                />
              )}
            </>
          )}
        </section>
      )}

      {/*
        **질문 원문은 없습니다** — D-037 이 관측에 원문을 금지했고, 서버 스키마에 담을
        칸조차 없습니다. 화면이 그것을 말해 주지 않으면 "왜 내용이 안 보이지" 가 됩니다.
      */}
      <p className="text-xs text-zinc-500 dark:text-zinc-400">
        질문과 답변의 내용은 여기에 남기지 않습니다. 세는 것만 합니다.
      </p>
    </div>
  );
}

function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: number | string;
  note?: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-200 p-5 dark:border-zinc-800">
      <p className="text-xs text-zinc-500 dark:text-zinc-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {note && <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">{note}</p>}
    </div>
  );
}

/**
 * 분포 하나. **분모(`total`)를 밖에서 받습니다** — 값마다 분모가 다르기 때문입니다.
 *
 * 막대는 CSS 너비로만 그립니다. 그래프 라이브러리를 새로 넣지 않는 것은 의존성이
 * 느는 것에 비해 얻는 것이 작아서입니다 (#223 작업 목록).
 */
function Distribution({
  title,
  note,
  counts,
  labels,
  total,
}: {
  title: string;
  note?: string;
  counts: NamedCount[];
  labels: Record<string, string>;
  total: number;
}) {
  return (
    <div>
      <h2 className="text-sm font-medium">{title}</h2>
      {note && <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">{note}</p>}

      {counts.length === 0 ? (
        <p className="mt-3 text-sm text-zinc-500 dark:text-zinc-400">
          이 기간에는 없습니다.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {counts.map((c) => {
            // **0 방어.** total 이 0 이면 counts 도 비어 여기 안 오지만, 분모를 밖에서
            // 받는 구조라 부르는 쪽이 틀릴 수 있습니다.
            const pct = total > 0 ? (c.count / total) * 100 : 0;
            return (
              <li key={c.name} className="flex items-center gap-3 text-sm">
                <span className="w-32 shrink-0 truncate" title={c.name}>
                  {labels[c.name] ?? c.name}
                </span>
                <span className="h-2 flex-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-900">
                  <span
                    className="block h-full rounded-full bg-zinc-400 dark:bg-zinc-600"
                    style={{ width: `${pct}%` }}
                  />
                </span>
                <span className="w-20 shrink-0 text-right text-xs text-zinc-500 tabular-nums dark:text-zinc-400">
                  {c.count} · {Math.round(pct)}%
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
