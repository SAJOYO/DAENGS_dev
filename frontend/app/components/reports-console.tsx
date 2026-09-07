"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiJson, ApiError } from "@/lib/api";

/**
 * `GET /api/admin/reports` 의 한 줄 (`schemas/answer_report.py` 의 `ReportListItem`).
 *
 * **원문이 없습니다.** 목록을 여는 것은 감사에 남기지 않으므로(D-053 ③) 서버가
 * 여기에 질문·답변을 싣지 않습니다 — 원문은 상세를 열어야 나오고, 그때 기록이 남습니다.
 */
type ReportItem = {
  id: string;
  created_at: string;
  status: "open" | "reviewed" | "dismissed";
  reason: string;
  /** 같은 답변이 몇 번 신고됐는지. **여러 사람이 신고할 수 있습니다.** */
  report_count: number;
  reviewer: { id: string; login_id: string; name: string } | null;
  reviewed_at: string | null;
};

type ReportPage = { reports: ReportItem[]; next_cursor: string | null };

/**
 * 상세의 `turn` (`schemas/answer_report.py` 의 `ReportedTurn`).
 *
 * **이 대화의 다른 turn 은 여기 오지 않습니다** (D-053 ①). 목록형 필드를 만들지
 * 마세요 — 만들면 그것을 채우는 코드가 따라오고, 결정이 화면에서 조용히 바뀝니다.
 */
type ReportedTurn = {
  id: string;
  created_at: string;
  user_content: string;
  assistant_content: string | null;
  assistant_status: string | null;
  public_response: Record<string, unknown> | null;
  request_id: string | null;
  agent_categories: string[];
  /** 이 대화의 완료 turn 총 수. **원문이 아니라 숫자입니다.** */
  session_turn_count: number;
  /** 그중 몇 번째인지 (1부터). **0 이면 순번을 매길 수 없다는 뜻입니다.** */
  position: number;
};

type ReportDetail = ReportItem & { turn: ReportedTurn };

const STATUS: Record<ReportItem["status"], { label: string; className: string }> = {
  open: {
    label: "미처리",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  reviewed: {
    label: "처리함",
    className: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  },
  dismissed: {
    label: "기각",
    className: "bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400",
  },
};

const FILTERS = [
  { value: "", label: "전부" },
  { value: "open", label: "미처리" },
  { value: "reviewed", label: "처리함" },
  { value: "dismissed", label: "기각" },
];

function when(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

/** 한 번에 가져오는 줄 수. 서버 기본값(50)과 같게 둡니다. */
const PAGE = 50;

/**
 * 신고 조회·처리 (콘솔 로드맵 A1 · D-053 · #237).
 *
 * --------------------------------------------------------------------------
 * **이 화면에서 제일 중요한 것은 무엇을 안 보여 주는가입니다.**
 *
 * 목록에는 원문이 없고, 상세에는 **신고된 답변 한 건**만 있습니다. "앞뒤 맥락 더
 * 보기" 같은 버튼을 달지 마세요 — 그것은 D-053 을 바꾸는 일이라 새 `D-` 가 먼저입니다.
 * 좁게 열었다가 넓히는 것은 카드 하나지만, 넓게 열었다가 좁히는 것은 이미 본 것을
 * 안 본 것으로 만들지 못합니다.
 *
 * 대신 `position` / `session_turn_count` 를 눈에 띄게 보여 줍니다. 이 숫자가 있어야
 * "이 답변은 앞 맥락에 기대서 이것만으로는 판단이 안 된다" 를 알 수 있고, 나중에
 * 범위를 넓힐지 정할 때 **추측이 아니라 실측**이 근거가 됩니다.
 * --------------------------------------------------------------------------
 *
 * `admin:manage` 를 못 가진 계정은 여기 오지도 못합니다 — 카드가 가리고 API 는 403.
 * 그래서 이 안에는 권한 분기가 없습니다.
 */
export default function ReportsConsole() {
  const [filter, setFilter] = useState("");
  const [reports, setReports] = useState<ReportItem[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  // StrictMode 가 개발에서 effect 를 두 번 돌립니다 (`audit-console.tsx` 와 같은 장치).
  const alive = useRef(true);

  const load = useCallback((nextFilter: string, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: String(PAGE) });
    if (nextFilter) params.set("status", nextFilter);
    return apiJson<ReportPage>(`/api/admin/reports?${params}`, { signal })
      .then((page) => {
        if (!alive.current) return;
        setReports(page.reports);
        setCursor(page.next_cursor);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!alive.current || (e as Error)?.name === "AbortError") return;
        setError(e instanceof ApiError ? e.message : "신고를 불러오지 못했습니다.");
      });
  }, []);

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void load(filter, controller.signal);
    return () => {
      alive.current = false;
      controller.abort();
    };
  }, [load, filter]);

  async function more() {
    if (!cursor) return;
    setBusy(true);
    try {
      const params = new URLSearchParams({ limit: String(PAGE), cursor });
      if (filter) params.set("status", filter);
      const page = await apiJson<ReportPage>(`/api/admin/reports?${params}`);
      // **이어 붙입니다.** 키셋 커서라 읽는 동안 새 신고가 들어와도 겹치거나 빠지지
      // 않습니다 (OFFSET 이면 어긋납니다).
      setReports((prev) => [...(prev ?? []), ...page.reports]);
      setCursor(page.next_cursor);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "더 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  /**
   * 상세를 엽니다. **이 호출이 감사 기록을 남깁니다** (`admin.report.turn_revealed`).
   *
   * 그래서 목록을 훑는 것만으로는 아무것도 안 남고, 여는 순간 남습니다. 미리 불러
   * 두거나 hover 로 부르지 마세요 — 열지도 않은 신고가 기록에 쌓입니다.
   */
  async function open(id: string) {
    if (openId === id) {
      setOpenId(null);
      setDetail(null);
      setDetailError(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    setDetailError(null);
    try {
      setDetail(await apiJson<ReportDetail>(`/api/admin/reports/${id}`));
    } catch (e) {
      setDetailError(e instanceof ApiError ? e.message : "신고를 열지 못했습니다.");
    }
  }

  async function resolve(id: string, status: "reviewed" | "dismissed") {
    setBusy(true);
    try {
      const updated = await apiJson<ReportItem>(`/api/admin/reports/${id}`, {
        method: "PATCH",
        // `apiJson` 은 헤더를 안 붙여 줍니다 — 빼면 FastAPI 가 본문을 파싱하지 않아
        // 422 가 나고, 그 detail 은 목록이라 화면에 기본 문구만 남습니다.
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      // **다시 안 불러옵니다.** 목록을 새로 부르면 이미 본 쪽이 필터에 따라 사라지고
      // 스크롤이 튑니다. 서버가 바뀐 행을 그대로 돌려주므로 그 자리만 갈아 끼웁니다.
      setReports((prev) =>
        (prev ?? []).map((r) => (r.id === id ? updated : r)),
      );
      setDetail((prev) => (prev && prev.id === id ? { ...prev, ...updated } : prev));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "처리하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  if (error) {
    return <p className="text-sm text-red-600 dark:text-red-400">{error}</p>;
  }
  if (!reports) {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">불러오는 중입니다…</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={filter}
          onChange={(e) => {
            setReports(null);
            setOpenId(null);
            setDetail(null);
            setFilter(e.target.value);
          }}
          className="rounded-lg border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-black"
        >
          {FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
        {/* 총 개수를 안 보여 줍니다 — 감사 목록과 같은 이유입니다. */}
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {reports.length}건 불러옴
        </p>
      </div>

      {reports.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          신고가 없습니다. 메일로 오는 신고는 여기 보이지 않습니다.
        </p>
      ) : (
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-900">
          {reports.map((r) => {
            const s = STATUS[r.status];
            const isOpen = openId === r.id;
            return (
              <li key={r.id} className="py-4">
                <div className="flex flex-wrap items-start gap-3">
                  <span className={`rounded-full px-2 py-0.5 text-xs ${s.className}`}>
                    {s.label}
                  </span>
                  <span className="text-xs whitespace-nowrap text-zinc-500 tabular-nums dark:text-zinc-400">
                    {when(r.created_at)}
                  </span>
                  <p className="min-w-0 flex-1 text-sm text-zinc-700 dark:text-zinc-300">
                    {r.reason}
                  </p>
                  {r.report_count > 1 && (
                    /*
                      **같은 답변을 여러 사람이 신고한 것입니다.** 서버가 한 사람의
                      중복만 막으므로(UNIQUE turn_id·app_user_id), 이 숫자가 2 이상이면
                      서로 다른 사람들입니다 — 그 답변이 얼마나 나쁜지의 신호입니다.
                    */
                    <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
                      신고 {r.report_count}건
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => void open(r.id)}
                    className="rounded-full border border-zinc-300 px-3 py-1 text-xs text-zinc-600 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
                  >
                    {isOpen ? "닫기" : "답변 열기"}
                  </button>
                </div>

                {r.reviewer && (
                  <p className="mt-1.5 text-xs text-zinc-500 dark:text-zinc-400">
                    <span className="font-mono">{r.reviewer.login_id}</span> {r.reviewer.name}
                    {r.reviewed_at && ` · ${when(r.reviewed_at)}`}
                  </p>
                )}

                {isOpen && (
                  <div className="mt-4 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
                    {detailError ? (
                      <p className="text-sm text-red-600 dark:text-red-400">{detailError}</p>
                    ) : !detail ? (
                      <p className="text-sm text-zinc-500 dark:text-zinc-400">여는 중입니다…</p>
                    ) : (
                      <Detail detail={detail} busy={busy} onResolve={resolve} />
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
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

      <p className="text-xs text-zinc-500 dark:text-zinc-400">
        답변을 여는 것은 감사 기록에 남습니다. 목록을 훑는 것은 남지 않습니다.
      </p>
    </div>
  );
}

/**
 * 신고된 답변 **한 건**.
 *
 * **순번을 눈에 띄게 둡니다.** 구석에 작게 넣으면 이 값을 넣은 이유가 사라집니다 —
 * 관리자가 "이건 7번째 답변이라 앞 맥락 없이는 판단이 안 되겠다" 를 알아야 합니다.
 */
function Detail({
  detail,
  busy,
  onResolve,
}: {
  detail: ReportDetail;
  busy: boolean;
  onResolve: (id: string, status: "reviewed" | "dismissed") => Promise<void>;
}) {
  const { turn } = detail;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        {turn.position > 0 ? (
          <span className="rounded-full bg-blue-100 px-2.5 py-1 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
            {turn.session_turn_count}턴 중 {turn.position}번째 답변
          </span>
        ) : (
          /*
            `position` 이 0 인 것은 **빠뜨린 값이 아닙니다** — 신고된 turn 이 완료
            상태가 아니라(처리 중이거나 실패) 순번을 매길 자리가 없다는 뜻입니다
            (`repositories/answer_report.py` 의 `turn_position`).
          */
          <span className="rounded-full bg-zinc-100 px-2.5 py-1 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
            순번을 매길 수 없음 · 대화에 완료된 답변 {turn.session_turn_count}건
          </span>
        )}
        {turn.assistant_status && (
          <span className="font-mono text-zinc-500 dark:text-zinc-400">
            {turn.assistant_status}
          </span>
        )}
        {turn.agent_categories.length > 0 && (
          <span className="text-zinc-500 dark:text-zinc-400">
            {turn.agent_categories.join(" · ")}
          </span>
        )}
        {turn.request_id && (
          <span className="font-mono text-zinc-400 dark:text-zinc-500">
            {turn.request_id}
          </span>
        )}
      </div>

      {turn.position > 1 && (
        /*
          **이 안내가 D-053 ① 의 대가를 화면에 드러내는 자리입니다.** 원문을 한 건만
          여는 대신 "앞에 뭔가 있었다"는 사실은 알려 줘야, 관리자가 판단을 보류할 수
          있습니다. 여기에 "더 보기" 를 달고 싶어지면 새 `D-` 가 먼저입니다.
        */
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          이 답변 앞에 {turn.position - 1}개의 문답이 더 있습니다. 앞 맥락에 기댄
          답변이면 이것만으로는 판단이 어려울 수 있습니다 — 열람 범위는 신고된 답변
          한 건입니다.
        </p>
      )}

      <div>
        <p className="text-xs text-zinc-500 dark:text-zinc-400">질문</p>
        <p className="mt-1 text-sm whitespace-pre-wrap text-zinc-800 dark:text-zinc-200">
          {turn.user_content}
        </p>
      </div>

      <div>
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          답변 · {when(turn.created_at)}
        </p>
        <p className="mt-1 text-sm whitespace-pre-wrap text-zinc-800 dark:text-zinc-200">
          {turn.assistant_content ?? "답변이 저장되지 않았습니다."}
        </p>
      </div>

      {turn.public_response && (
        <details className="text-xs">
          <summary className="cursor-pointer text-zinc-500 dark:text-zinc-400">
            무엇을 근거로 냈나 (public_response)
          </summary>
          {/*
            **이미 공개 계약인 `AssistantResponse` 그대로입니다** — 앱이 받는 것과 같은
            모양이라 여기 새로 나가는 것이 없습니다. 모양이 카드마다 바뀌므로 JSON 을
            그대로 떨어뜨립니다 (`audit-console.tsx` 의 `detail` 과 같은 판단).
          */}
          <pre className="mt-2 overflow-x-auto rounded-lg bg-zinc-50 p-3 font-mono text-[11px] leading-5 dark:bg-zinc-900">
            {JSON.stringify(turn.public_response, null, 2)}
          </pre>
        </details>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-4 dark:border-zinc-900">
        <button
          type="button"
          disabled={busy}
          onClick={() => void onResolve(detail.id, "reviewed")}
          className="rounded-full bg-zinc-900 px-4 py-1.5 text-xs text-white transition-colors hover:bg-zinc-700 disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
        >
          처리함
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void onResolve(detail.id, "dismissed")}
          className="rounded-full border border-zinc-300 px-4 py-1.5 text-xs text-zinc-600 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-900"
        >
          기각
        </button>
        {/*
          **미처리로 되돌리는 버튼이 없습니다.** 서버가 안 받습니다 — 처리한 사람과
          시각을 지우는 일이라 감사 기록과 어긋납니다 (`schemas/answer_report.py`).
        */}
        <p className="text-xs text-zinc-400 dark:text-zinc-500">
          되돌릴 수 없습니다. 다시 처리하면 마지막 사람으로 갱신됩니다.
        </p>
      </div>
    </div>
  );
}
